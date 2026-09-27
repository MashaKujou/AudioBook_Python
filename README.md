# TTS Forge

Light novel TTS audiobook generator. Converts chapter text to spoken audio using Microsoft Edge's free TTS engine, then compiles chapters into AAC audiobook volumes.

## How It Works

```
                    ┌──────────────┐
 chapter text ─────▶│  edge-tts    │─────▶ chapters/0001.flac ───┐
                    └──────────────┘                             │
                                                    ┌──────────▼──────────┐
                                                    │       ffmpeg        │
                          intro narration ─────────▶│  concat → AAC 192k  │
                                                    └─────────────────────┘
                                                              │
                                                    volumes/Novel_vol1.aac
```

1. **Generate** — paste chapter text, pick a voice + emotion style. Backend calls `edge-tts` (async), saves lossless FLAC to `storage/{novel}/chapters/`.
2. **Compile** — select chapters, fill credits (author/translator/narrator). Backend generates an intro FLAC, then runs `ffmpeg` to concat intro + chapters into one AAC file at `storage/{novel}/volumes/`.

Emotion styles are emulated via TTS prosody tuning (rate/pitch/volume) — cheerful, sad, angry, whispering, shouting, and 4 others.

## Setup

### 1. Install Python dependencies

```bash
pip install flask edge-tts
```

### 2. Install ffmpeg (required for volume compilation)

| OS | Command |
|---|---|
| Windows | `winget install "FFmpeg (Essentials Build)"` |
| macOS | `brew install ffmpeg` |
| Linux | `apt install ffmpeg` or `dnf install ffmpeg` |

The app checks for ffmpeg on startup. If missing, a warning is printed and compile/generate endpoints return 500.

### 3. Run

```bash
python app.py
```

Opens at **http://127.0.0.1:5000**. Debug mode, single-user local server — no auth, no multi-user.

## Project Structure

```
TTS/
├── app.py                  # Flask backend — all routes, TTS logic, ffmpeg compilation
├── templates/
│   └── index.html          # Single-page frontend — HTML, CSS, JS inline
├── config.json             # Created at runtime — author/translator/narrator defaults
├── storage/                # All generated audio lives here
│   └── {novel_name}/
│       ├── chapters/       # 0001.flac, 0002.flac, ... (per-chapter FLAC)
│       └── volumes/        # {novel}_vol1.aac (compiled audiobooks)
│   └── _studio/            # Video Studio: uploads + rendered MP4s
│       └── media/          # {Project}/Media (uploads) + {Project}/Videos (renders)
└── README.md
```

## API Endpoints

| Method | Route | What it does |
|---|---|---|
| `GET` | `/` | Serves the web UI |
| `GET` | `/api/novels` | Lists all novels with chapters and volumes |
| `POST` | `/api/novel` | Creates a new novel (`{"name": "..."}`) |
| `POST` | `/api/generate` | Runs TTS on text → FLAC (`novel_name`, `chapter`, `text`, `voice`, `style`) |
| `POST` | `/api/compile` | Compiles FLACs + intro → AAC (`novel_name`, `chapters[]`, `voice`, `style`, `author`, `translator`, `audiobook_by`) |
| `POST` | `/api/upload` | Uploads Video Studio media (multipart `file`, `project`) |
| `POST` | `/api/studio-project` | Creates a project folder (`{"name": "..."}` → `media/{Project}/Media` + `Videos`) |
| `GET` | `/api/studio-media` | Lists projects with their own media files and rendered videos |
| `GET` | `/api/media/<name>` | Serves a media file inline (thumbnails, playback) |
| `GET` | `/api/media-duration` | Returns an audio file's duration in seconds (`?path=...`) |
| `POST` | `/api/render-video` | Renders slides + audio clips → MP4 (`name`, `project`, `slides[{image, duration, transition}]`, `audio_clips[{file, start, fadeIn, fadeOut, volume}]`) |
| `GET` | `/api/download` | Downloads a file (`?path=storage/...`) |
| `GET` | `/api/settings` | Returns saved credits defaults |
| `PUT` | `/api/settings` | Saves credits defaults |

## Voices (12 options)

US English: Aria, Guy, Jenny, Christopher · UK English: Sonia, Ryan · Japanese: Nanami, Keita · Chinese: Xiaoxiao · Korean: SunHi · French: Denise · German: Katja

## Configuration

No manual config needed. The app creates `config.json` at first settings save via the UI. Stores three fields used for the intro narration during compilation:

```json
{
  "author": "",
  "translator": "",
  "audiobook_by": ""
}
```

## Video Studio

Third tab. Create a project (＋ New Project → `storage/_studio/media/{Project}/Media` + `Videos`), upload images + audio into it, and drag them onto the timeline. One dark timeline container, media row on top and audio row below: drag images onto the media row (drag a card's right edge to resize its duration; the dropdown between cards picks the transition: Cut / Fade / Slide / Wipe) and drag audio onto the audio row (hold and drag clips to reposition; corner handles set fade-in/out; a slider sets per-clip volume; overlapping clips stack into lanes). Waveforms and slide-boundary markers keep everything scannable. Render builds one MP4 (H.264, 1920×1080, 30fps) into the project's `Videos` folder.

## Notes

- No `requirements.txt` — two deps, install manually.
- Storage files are committed to git (no `.gitignore`). Add one if you don't want audio files tracked.
- Single-user local tool. Not designed for multi-user or production deployment.
- Chapter text underscores are stripped before TTS (so `under_score` → "under score").

## Credits

Source: [AudioBook_Python](https://github.com/MashaKujou/AudioBook_Python)
