import asyncio, json, os, shutil, subprocess, tempfile, time
from pathlib import Path
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
STORAGE = Path("storage").resolve()
STORAGE.mkdir(exist_ok=True)
CONFIG_FILE = STORAGE.parent / "config.json"

def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {"author": "", "translator": "", "audiobook_by": ""}

def save_config(data):
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# check ffmpeg on startup
FFMPEG_OK = shutil.which("ffmpeg") is not None
if not FFMPEG_OK:
    print("WARNING: ffmpeg not found. Install: winget install \"FFmpeg (Essentials Build)\"")

# ── helpers ──────────────────────────────────────────────────────────

def novel_path(name):
    p = STORAGE / name.replace(" ", "_").replace("/", "_")
    (p / "chapters").mkdir(parents=True, exist_ok=True)
    (p / "volumes").mkdir(parents=True, exist_ok=True)
    return p

def chapter_files(novel, chapters):
    p = novel_path(novel) / "chapters"
    return [p / f"{int(i):04d}.flac" for i in chapters if (p / f"{int(i):04d}.flac").exists()]

# ── video studio ─────────────────────────────────────────────────────

STUDIO = STORAGE / "_studio"
MEDIA_DIR = STUDIO / "media"
STUDIO.mkdir(exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}
# ponytail: 3 xfade presets — add more transition types here if wanted
VIDEO_TRANSITIONS = {"fade": "fade", "slide": "slideleft", "wipe": "wipeleft"}
XFADE_D = 0.5  # crossfade length (s)
CUT_D = 0.04   # "Cut" = one frame at 30fps

def media_file(name):
    # refs are MEDIA_DIR-relative: {Project}/Media/{file} or a flat
    # legacy filename in MEDIA_DIR root
    name = name.replace("\\", "/")
    p = (MEDIA_DIR / name).resolve()
    if p.exists() and p.is_relative_to(MEDIA_DIR):
        return p
    f = MEDIA_DIR / secure_filename(name)
    return f if f.exists() else None

def resolve_audio(name):
    # project media: media/{Project}/Media/{file}; legacy: flat MEDIA_DIR
    # file, pre-project refs, or novel audio under storage/ (with or
    # without an explicit "storage/" prefix — /api/studio-media returns
    # novel_audio paths already relative to STORAGE, unprefixed)
    name = name.replace("\\", "/")
    rel = name[len("storage/"):] if name.startswith("storage/") else name
    p = (STORAGE / rel).resolve()
    if p.exists() and p.is_relative_to(STORAGE):
        return p
    return media_file(name)

# ── TTS ──────────────────────────────────────────────────────────────

# ponytail: emulate emotions via prosody instead of SSML express-as
STYLE_PRESETS = {
    "cheerful":   {"rate": "+20%", "pitch": "+30Hz", "volume": "+0%"},
    "empathetic": {"rate":  "-5%", "pitch": "-15Hz", "volume": "+0%"},
    "excited":    {"rate": "+25%", "pitch": "+50Hz", "volume": "+5%"},
    "friendly":   {"rate":  "+5%", "pitch": "+15Hz", "volume": "+0%"},
    "sad":        {"rate": "-15%", "pitch": "-30Hz", "volume": "-10%"},
    "angry":      {"rate": "+10%", "pitch": "+40Hz", "volume": "+20%"},
    "whispering": {"rate": "-10%", "pitch": "+0Hz",  "volume": "-50%"},
    "shouting":   {"rate":  "+5%", "pitch": "+35Hz", "volume": "+50%"},
    "serious":    {"rate": "-10%", "pitch": "-20Hz", "volume": "+0%"},
}

async def generate_tts(text, output_path, voice="en-US-AriaNeural", style=None):
    import edge_tts
    if style and style != "none" and style in STYLE_PRESETS:
        p = STYLE_PRESETS[style]
        communicate = edge_tts.Communicate(text, voice, rate=p["rate"], pitch=p["pitch"], volume=p["volume"])
    else:
        communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))

# ── routes ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/novels")
def list_novels():
    novels = []
    for d in STORAGE.iterdir():
        if d.is_dir():
            chaps = sorted((d / "chapters").glob("*.flac"))
            vols = sorted((d / "volumes").glob("*.aac"))
            novels.append({
                "name": d.name,
                "chapters": [int(f.stem) for f in chaps],
                "volumes": [f.stem for f in vols],
            })
    return jsonify(sorted(novels, key=lambda x: x["name"]))

@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json()
    novel = data["novel_name"]
    chapter = int(data["chapter"])
    text = data["text"]
    voice = data.get("voice", "en-US-AriaNeural")

    if not FFMPEG_OK:
        return jsonify({"ok": False, "error": "ffmpeg not installed. Run: winget install \"FFmpeg (Essentials Build)\""}), 500

    style = data.get("style")
    if style == "none":
        style = None

    # ponytail: prepend chapter number, strip _ so TTS doesn't say "underscore"
    text = f"Chapter {chapter}. {text}".replace("_", " ")

    np_ = novel_path(novel)
    out = np_ / "chapters" / f"{chapter:04d}.flac"

    try:
        asyncio.run(generate_tts(text, out, voice, style))
        return jsonify({"ok": True, "file": str(out)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/novel", methods=["POST"])
def create_novel():
    data = request.get_json()
    novel_path(data["name"])
    return jsonify({"ok": True})

@app.route("/api/settings", methods=["GET", "PUT"])
def settings():
    if request.method == "PUT":
        data = request.get_json()
        cfg = load_config()
        cfg.update({k: data.get(k, cfg.get(k, "")) for k in ("author", "translator", "audiobook_by")})
        save_config(cfg)
        return jsonify({"ok": True})
    return jsonify(load_config())

@app.route("/api/compile", methods=["POST"])
def compile_volume():
    data = request.get_json()
    novel = data["novel_name"]
    chapters = data["chapters"]

    if not FFMPEG_OK:
        return jsonify({"ok": False, "error": "ffmpeg not installed. Run: winget install \"FFmpeg (Essentials Build)\""}), 500

    voice = data.get("voice", "en-US-AriaNeural")
    style = data.get("style")
    if style == "none":
        style = None

    cfg = load_config()
    author = data.get("author") or cfg.get("author", "")
    translator = data.get("translator") or cfg.get("translator", "")
    audiobook_by = data.get("audiobook_by") or cfg.get("audiobook_by", "")

    np_ = novel_path(novel)
    chapters_dir = np_ / "chapters"

    files = chapter_files(novel, chapters)
    if not files:
        return jsonify({"ok": False, "error": "No chapter files found"}), 400

    # ── Generate intro TTS (strip _ so TTS doesn't say "underscore") ──
    def clean(s): return s.replace("_", " ")
    intro_parts = [f"{clean(novel)}"]
    if author:
        intro_parts.append(f"Author, {clean(author)}")
    if translator:
        intro_parts.append(f"Translated by {clean(translator)}")
    if audiobook_by:
        intro_parts.append(f"Audiobook by {clean(audiobook_by)}")
    intro_text = ". ".join(intro_parts) + "."
    intro_file = chapters_dir / "_intro.flac"
    try:
        asyncio.run(generate_tts(intro_text, intro_file, voice, style))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Intro TTS failed: {e}"}), 500

    # ── Build concat list (intro first, then chapters) ──
    all_files = [intro_file] + files
    concat_file = chapters_dir / "_concat.txt"
    concat_file.write_text("\n".join(f"file '{f.name}'" for f in all_files))

    vol_name = f"{novel}_vol{data.get('volume_number', 1)}"
    out = np_ / "volumes" / f"{vol_name}.aac"

    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:a", "aac", "-b:a", "192k",
            str(out)
        ], check=True, capture_output=True, cwd=chapters_dir)
        concat_file.unlink(missing_ok=True)
        intro_file.unlink(missing_ok=True)
        return jsonify({"ok": True, "file": str(out)})
    except subprocess.CalledProcessError as e:
        concat_file.unlink(missing_ok=True)
        intro_file.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": e.stderr.decode()}), 500

@app.route("/api/download")
def download():
    path = request.args.get("path", "")
    full = Path(path)
    if not full.exists() or not full.is_relative_to(STORAGE.resolve()):
        return "Not found", 404
    return send_file(str(full), as_attachment=True)

@app.route("/api/upload", methods=["POST"])
def upload_media():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in IMAGE_EXTS | AUDIO_EXTS:
        return jsonify({"ok": False, "error": "Unsupported type — use images (jpg/png/webp/gif) or audio (mp3/wav/m4a/flac/aac/ogg)"}), 400
    safe = secure_filename(f.filename) or f"media_{int(time.time())}{ext}"
    project = secure_filename(request.form.get("project", "") or "")
    dest = MEDIA_DIR / project / "Media" if project else MEDIA_DIR
    if project:
        dest.mkdir(parents=True, exist_ok=True)
    f.save(str(dest / safe))
    return jsonify({"ok": True, "file": (f"{project}/Media/{safe}" if project else safe)})

@app.route("/api/studio-project", methods=["POST"])
def studio_project():
    name = secure_filename((request.get_json() or {}).get("name", "") or "")
    if not name:
        return jsonify({"ok": False, "error": "No name"}), 400
    (MEDIA_DIR / name / "Media").mkdir(parents=True, exist_ok=True)
    (MEDIA_DIR / name / "Videos").mkdir(parents=True, exist_ok=True)
    return jsonify({"ok": True, "project": name})

@app.route("/api/studio-media")
def studio_media():
    # per-project folders: _studio/media/{Project}/Media + Videos; each
    # project lists only its own files. Legacy flat uploads in MEDIA_DIR
    # root are still listed under "flat" so old files stay usable.
    projects = sorted(p.name for p in MEDIA_DIR.iterdir() if p.is_dir())
    library, videos = {}, {}
    for name in projects:
        p = MEDIA_DIR / name
        (p / "Media").mkdir(exist_ok=True)
        (p / "Videos").mkdir(exist_ok=True)
        files = sorted(f for f in (p / "Media").iterdir() if f.is_file())
        library[name] = {
            "images": [f.name for f in files if f.suffix.lower() in IMAGE_EXTS],
            "audio": [f.name for f in files if f.suffix.lower() in AUDIO_EXTS],
        }
        videos[name] = [f.name for f in sorted((p / "Videos").glob("*.mp4"))]
    flat = sorted(f for f in MEDIA_DIR.iterdir() if f.is_file())
    novel_audio = []
    for d in sorted(STORAGE.iterdir()):
        if not d.is_dir():
            continue
        for sub, kind in (("chapters", "flac"), ("volumes", "aac")):
            for f in sorted((d / sub).glob(f"*.{kind}")):
                novel_audio.append({
                    "novel": d.name,
                    "label": f"Ch {int(f.stem)}" if kind == "flac" and f.stem.isdigit() else f.stem,
                    "path": f.relative_to(STORAGE).as_posix(),
                    "kind": kind,
                })
    return jsonify({
        "projects": projects,
        "library": library,
        "videos": videos,
        "novel_audio": novel_audio,
        "flat": {
            "images": [f.name for f in flat if f.suffix.lower() in IMAGE_EXTS],
            "audio": [f.name for f in flat if f.suffix.lower() in AUDIO_EXTS],
        },
    })

@app.route("/api/media/<path:name>")
def serve_media(name):
    p = (MEDIA_DIR / name).resolve()
    if p.exists() and p.is_relative_to(MEDIA_DIR):
        return send_file(str(p))
    f = MEDIA_DIR / secure_filename(name)
    if not f.exists():
        f = STUDIO / secure_filename(name)
    if not f.exists() or f.parent not in (MEDIA_DIR, STUDIO):
        return "Not found", 404
    return send_file(str(f))

@app.route("/api/audio-file")
def serve_audio_file():
    # Streams any audio the render pipeline can also resolve — uploaded
    # media (plain filename) or novel chapter/volume audio (storage-
    # relative path with slashes) — so the browser can read its duration
    # for the timeline and (optionally) preview it.
    path = request.args.get("path", "")
    f = resolve_audio(path)
    if not f:
        return "Not found", 404
    return send_file(str(f))

@app.route("/api/media-duration")
def media_duration():
    # Used by the Video Studio timeline to size an audio clip correctly
    # right after it's dropped — same path resolution as /api/audio-file.
    path = request.args.get("path", "")
    f = resolve_audio(path)
    if not f:
        return jsonify({"ok": False, "error": "Not found"}), 404
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(f)],
            capture_output=True, text=True, check=True
        )
        dur = float(result.stdout.strip())
        return jsonify({"ok": True, "duration": round(dur, 3)})
    except (subprocess.CalledProcessError, ValueError) as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/render-video", methods=["POST"])
def render_video():
    data = request.get_json()
    if not FFMPEG_OK:
        return jsonify({"ok": False, "error": "ffmpeg not installed. Run: winget install \"FFmpeg (Essentials Build)\""}), 500

    safe = secure_filename(data.get("name", "") or "") or "video"
    project = secure_filename(data.get("project", "") or "")
    out_dir = MEDIA_DIR / project / "Videos" if project else STUDIO
    if project:
        out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{safe}.mp4"
    slides = data.get("slides", [])

    # ── Audio timeline: a list of {file, start} clips, each with its own
    # offset (seconds) into the final mix, dropped/repositioned in the UI.
    # Falls back to the old single "audio" field for older callers.
    audio_clips_in = data.get("audio_clips")
    if not audio_clips_in:
        legacy = data.get("audio")
        audio_clips_in = [{"file": legacy, "start": 0}] if legacy else []

    if not slides:
        return jsonify({"ok": False, "error": "No slides — drag images onto the track"}), 400
    if not audio_clips_in:
        return jsonify({"ok": False, "error": "No audio clips — drag at least one onto the Audio Tracks"}), 400

    resolved_audio = []
    for clip in audio_clips_in:
        af = resolve_audio(clip.get("file", "") or "")
        if not af:
            return jsonify({"ok": False, "error": f"Audio file not found: {clip.get('file')}"}), 400
        vol = clip.get("volume")
        resolved_audio.append((
            af,
            max(0.0, float(clip.get("start") or 0)),
            probe_duration(af),
            float(vol) if vol is not None else 1.0,
            max(0.0, float(clip.get("fadeIn") or 0)),
            max(0.0, float(clip.get("fadeOut") or 0)),
        ))

    # transition INTO each slide (slide 0 has none)
    trans = [0.0] + [CUT_D if (s.get("transition") in (None, "", "none")) else XFADE_D for s in slides[1:]]

    # segment length = solo time + blend in + blend out
    durs = [max(0.5, float(s.get("duration") or 3)) for s in slides]
    seg_d = []
    for i, d in enumerate(durs):
        seg = d + trans[i] + (trans[i + 1] if i + 1 < len(slides) else 0.0)
        seg_d.append(round(seg, 3))

    imgs = []
    lines = []
    for i, s in enumerate(slides):
        img = media_file(s.get("image", ""))
        if not img:
            return jsonify({"ok": False, "error": f"Image not found: {s.get('image')}"}), 400
        imgs.append(img)
        lines.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v{i}]"
        )

    prev = "v0"
    cum_d = 0.0
    cum_t = 0.0
    total = seg_d[0]
    for i in range(1, len(slides)):
        cum_d += seg_d[i - 1]
        cum_t += trans[i]
        offset = round(cum_d - cum_t, 3)
        raw = slides[i].get("transition") or "none"
        ttype = "fade" if raw == "none" else VIDEO_TRANSITIONS.get(raw, "fade")
        lines.append(f"[{prev}][v{i}]xfade=transition={ttype}:duration={round(trans[i], 3)}:offset={offset}[x{i}]")
        total = round(total + seg_d[i] - trans[i], 3)
        prev = f"x{i}"

    # ── Mix the positioned audio clips. Sources can be wildly different
    # formats (mono 24kHz TTS flac vs. a stereo 44.1kHz upload), and
    # amix/adelay require every input to already match — otherwise the
    # muxed audio stream can come out malformed and some players (esp.
    # Windows' built-in ones) will refuse to open the file at all. So
    # normalize each clip to stereo/44.1kHz first, then delay it to its
    # start time, then mix, pad with silence, and trim to the video length.
    n_audio = len(resolved_audio)
    audio_labels = []
    for j, (af, start, dur, vol, fin, fout) in enumerate(resolved_audio):
        delay_ms = int(round(start * 1000))
        fx = "aformat=sample_rates=44100:channel_layouts=stereo,"
        if vol != 1:
            fx += f"volume={round(vol, 2)},"
        if fin > 0:
            fx += f"afade=t=in:st=0:d={round(fin, 2)},"
        if fout > 0 and dur > 0:
            fx += f"afade=t=out:st={round(max(0.0, dur - fout), 2)}:d={round(fout, 2)},"
        lines.append(
            f"[{len(slides) + j}:a]{fx}adelay={delay_ms}|{delay_ms}[a{j}]"
        )
        audio_labels.append(f"[a{j}]")
    lines.append(f"{''.join(audio_labels)}amix=inputs={n_audio}:normalize=0,apad[amixed]")
    lines.append(f"[amixed]atrim=0:{total}[aout]")

    cmd = ["ffmpeg", "-y"]
    for i in range(len(slides)):
        cmd += ["-loop", "1", "-t", str(seg_d[i]), "-i", str(imgs[i])]
    for clip_info in resolved_audio:
        cmd += ["-i", str(clip_info[0])]
    cmd += ["-filter_complex", ";".join(lines),
            "-map", f"[{prev}]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            "-t", str(total),
            str(out)]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return jsonify({"ok": True, "file": str(out), "name": out.name, "path": out.relative_to(STORAGE).as_posix()})
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": e.stderr.decode(errors="replace")}), 500

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)