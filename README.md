# Transcriber

Turn a YouTube link or a video/audio file on your PC into a clean, readable **English** transcript.
Runs entirely on your own computer: no account, no API key, nothing is uploaded.

- Paste a YouTube link, or browse to a video/audio file
- Uses YouTube's own captions when they exist (instant), otherwise transcribes the audio with [Whisper](https://github.com/SYSTRAN/faster-whisper)
- Reads the result in the app on a full-page "paper" view with search, timestamps, text size and copy
- Saves **HTML, Markdown, TXT and SRT** (subtitles) to `Documents\Transcripts`

## Install (Windows 10 / 11)

1. **Download** this repo: green **Code** button → **Download ZIP**, then unzip it anywhere
   (or `git clone` it).
2. Double-click **`install.bat`**. It will:
   - find Python (and offer to install Python 3.12 with `winget` if missing),
   - set up a private environment and the required packages,
   - put a **Transcriber** shortcut on your desktop,
   - optionally download the speech model now (~465 MB, one time).
3. Open **Transcriber** from the desktop shortcut.

That's it. You can also skip step 2 and just double-click `Transcriber.bat`, which runs the same setup on first launch.

**Requirements:** Windows 10/11, Python 3.10–3.13, internet for the first install, ~1 GB free disk.
No GPU needed (runs on the CPU). An internet connection is only needed afterwards to fetch YouTube videos.

## Using it

| Setting | What it does |
|---|---|
| **Model** | Tiny (fastest, rough) · Base (fast) · **Small (accurate, default)** · Medium (most accurate, slow). English-only models. Each is downloaded once the first time you pick it. |
| **YouTube captions** | *Creator-made only* (default, accurate) · *Include auto-generated* (fastest) · *Never* (always transcribe the audio). |
| **Save to** | Where transcripts are written. |

Videos with creator-made captions finish in seconds. Otherwise the audio is transcribed on your CPU,
which can take longer than the video on an older laptop. Pick **Base** or **Tiny** for speed.

## Command line

```bash
Transcriber.bat "https://youtu.be/VIDEO_ID" --model small.en --captions uploaded
Transcriber.bat "C:\path\to\video.mp4" --model base.en
```

Options: `--model tiny.en|base.en|small.en|medium.en`, `--captions uploaded|auto|off`, `--out FOLDER`.

## Troubleshooting

- **"Python was not found"** – install Python 3.12 from python.org (tick *Add python.exe to PATH*) and run `install.bat` again.
- **Looks stuck on "Loading model"** – the first use of each model downloads it; on a slow connection this can take a while.
- **Non-English video** – the app is English-only; speech in other languages will come out poorly.
- **Reset everything** – delete the `.venv` folder and run `install.bat` again.

## How it works

`core.py` (fetching, captions, Whisper, formatting) · `webui.py` (a small local web server on 127.0.0.1) ·
`ui.html` (the interface) · `app.py` (entry point: GUI, or CLI when given a link/file).

Built with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and [yt-dlp](https://github.com/yt-dlp/yt-dlp).
Only transcribe content you have the right to use.
