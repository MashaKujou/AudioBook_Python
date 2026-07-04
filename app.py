import asyncio, json, os, shutil, subprocess, tempfile
from pathlib import Path
from flask import Flask, request, jsonify, render_template, send_file

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

@app.route("/api/compile", methods=["POST"])
def compile_volume():
    data = request.get_json()
    novel = data["novel_name"]
    chapters = data["chapters"]

    if not FFMPEG_OK:
        return jsonify({"ok": False, "error": "ffmpeg not installed. Run: winget install \"FFmpeg (Essentials Build)\""}), 500

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
        asyncio.run(generate_tts(intro_text, intro_file))
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

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)