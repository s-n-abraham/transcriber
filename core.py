"""Core logic: fetch audio (YouTube URL or local file), transcribe, format."""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

DEFAULT_OUT = Path.home() / "Documents" / "Transcripts"
# English-only models: more accurate than the multilingual ones at the same size.
MODELS = ["tiny.en", "base.en", "small.en", "medium.en"]
BEAMS = {"tiny.en": 1, "base.en": 2, "small.en": 4, "medium.en": 5}  # more beams = more accurate, slower
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v",
              ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}

Log = Callable[[str], None]
Progress = Callable[[float], None]


@dataclass
class Source:
    path: Path
    title: str
    url: str | None = None
    uploader: str | None = None
    duration: float | None = None


@dataclass
class Paragraph:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    source: Source
    language: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    segments: list[tuple[float, float, str]] = field(default_factory=list)


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


def ts(seconds: float, always_hours: bool = False) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if (h or always_hours) else f"{m:02d}:{s:02d}"


def srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return (name or "transcript")[:100]


# --------------------------------------------------------------------------- input
def _ydl(extra: dict | None = None):
    import yt_dlp
    opts = {"noplaylist": True, "quiet": True, "noprogress": True, "no_warnings": True}
    opts.update(extra or {})
    return yt_dlp.YoutubeDL(opts)


def _source_from_info(info: dict, target: str, path: Path | None) -> Source:
    return Source(path=path, title=info.get("title") or "video", url=info.get("webpage_url") or target,
                  uploader=info.get("uploader") or info.get("channel"), duration=info.get("duration"))


def _pick_caption_lang(info: dict, allow_auto: bool):
    """English captions only. Returns (lang_key, is_auto) or None."""
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    for key in manual:
        if key == "en" or key.startswith("en-"):
            return key, False
    if allow_auto:
        spoken_en = (info.get("language") or "en").lower().startswith("en")
        if "en-orig" in auto:
            return "en-orig", True
        if "en" in auto and spoken_en:  # for a non-English video, "en" would be a machine translation
            return "en", True
    return None


def _parse_json3(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    segs = []
    for ev in data.get("events", []):
        parts = ev.get("segs")
        if not parts:
            continue
        text = re.sub(r"\s+", " ", "".join(p.get("utf8", "") for p in parts)).strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000
        segs.append((start, start + ev.get("dDurationMs", 2000) / 1000, text))
    return segs


def try_captions(target: str, info: dict, allow_auto: bool, workdir: Path, log: Log) -> Transcript | None:
    pick = _pick_caption_lang(info, allow_auto)
    if not pick:
        log("No usable English captions - using speech recognition.")
        return None
    lang, auto = pick
    log(f"Found {'auto-generated' if auto else 'uploaded'} captions ({lang}) - skipping speech recognition.")
    with _ydl({"skip_download": True, "writesubtitles": not auto, "writeautomaticsub": auto,
               "subtitleslangs": [lang], "subtitlesformat": "json3",
               "outtmpl": str(workdir / "%(id)s.%(ext)s")}) as ydl:
        ydl.download([target])
    files = list(workdir.glob("*.json3"))
    segs = _parse_json3(files[0]) if files else []
    if not segs:
        log("Captions came back empty - using speech recognition.")
        return None
    t = Transcript(source=_source_from_info(info, target, None), language="en")
    t.segments = segs
    t.paragraphs = build_paragraphs(segs)
    return t


def fetch_audio(target: str, info: dict, workdir: Path, log: Log) -> Source:
    log("Downloading audio...")
    # Audio-only, no ffmpeg needed: faster-whisper decodes m4a/webm itself.
    with _ydl({"format": "worstaudio[ext=m4a]/bestaudio[ext=m4a]/bestaudio/best",
               "outtmpl": str(workdir / "%(id)s.%(ext)s")}) as ydl:
        info = ydl.extract_info(target, download=True)
        path = Path(ydl.prepare_filename(info))
    return _source_from_info(info, target, path)


def local_source(target: str) -> Source:
    p = Path(target.strip().strip('"'))
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    return Source(path=p, title=p.stem)


# --------------------------------------------------------------------------- transcription
def _lower_priority():
    """Run below normal priority so the laptop stays responsive while transcribing."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        except Exception:  # noqa: BLE001
            pass


def transcribe(source: Source, model_name: str, log: Log, progress: Progress) -> Transcript:
    from faster_whisper import WhisperModel

    _lower_priority()
    # Physical cores only: hyper-threads add heat and load but little speed.
    threads = max(1, (os.cpu_count() or 2) // 2)
    log(f"Loading model '{model_name}' (first use downloads it)...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
    log("Transcribing...")
    segments, info = model.transcribe(
        str(source.path), language="en", task="transcribe", vad_filter=True,
        beam_size=BEAMS.get(model_name, 4), condition_on_previous_text=False,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    total = info.duration or source.duration or 0
    t = Transcript(source=source, language=info.language)
    for seg in segments:
        text = seg.text.strip()
        if text:
            t.segments.append((seg.start, seg.end, text))
        if total:
            progress(min(seg.end / total, 1.0))
    t.paragraphs = build_paragraphs(t.segments)
    progress(1.0)
    return t


def build_paragraphs(segments, gap: float = 1.6, max_chars: int = 650, max_secs: float = 75) -> list[Paragraph]:
    """Group segments into readable paragraphs on pauses / sentence ends / length."""
    paras: list[Paragraph] = []
    cur: list[str] = []
    start = end = 0.0
    for s, e, text in segments:
        if cur:
            joined_len = sum(len(x) + 1 for x in cur)
            ends_sentence = cur[-1].rstrip().endswith((".", "?", "!", "…"))
            if (s - end > gap) or (ends_sentence and (joined_len > max_chars or e - start > max_secs)):
                paras.append(Paragraph(start, end, " ".join(cur)))
                cur = []
        if not cur:
            start = s
        cur.append(text)
        end = e
    if cur:
        paras.append(Paragraph(start, end, " ".join(cur)))
    return paras


# --------------------------------------------------------------------------- output
def _yt_link(url: str | None, seconds: float) -> str | None:
    if url and ("youtube.com" in url or "youtu.be" in url):
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={int(seconds)}s"
    return None


def to_markdown(t: Transcript) -> str:
    src = t.source
    lines = [f"# {src.title}", ""]
    meta = []
    if src.uploader:
        meta.append(f"**Channel:** {src.uploader}")
    if src.url:
        meta.append(f"**Source:** {src.url}")
    if src.duration:
        meta.append(f"**Duration:** {ts(src.duration)}")
    meta.append(f"**Language:** {t.language}")
    lines += ["  \n".join(meta), "", "---", ""]
    for p in t.paragraphs:
        link = _yt_link(src.url, p.start)
        stamp = f"[{ts(p.start)}]({link})" if link else f"`{ts(p.start)}`"
        lines += [f"**{stamp}**", "", p.text, ""]
    return "\n".join(lines)


def to_text(t: Transcript) -> str:
    out = [t.source.title, "=" * len(t.source.title), ""]
    for p in t.paragraphs:
        out += [f"[{ts(p.start)}]", p.text, ""]
    return "\n".join(out)


def to_srt(t: Transcript) -> str:
    out = []
    for i, (s, e, text) in enumerate(t.segments, 1):
        out += [str(i), f"{srt_ts(s)} --> {srt_ts(e)}", text, ""]
    return "\n".join(out)


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#fbfaf7;--fg:#222;--muted:#777;--accent:#2f6fed;--line:#e6e2d9;--card:#fff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e6e6e6;--muted:#9aa0a6;--accent:#7aa7ff;--line:#2a2d33;--card:#1d2025}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:18px/1.75 Georgia,'Segoe UI',serif}}
main{{max-width:760px;margin:0 auto;padding:48px 24px 96px}}
h1{{font:700 2rem/1.25 'Segoe UI',sans-serif;margin:0 0 .5rem}}
.meta{{font:.85rem 'Segoe UI',sans-serif;color:var(--muted);padding-bottom:1.25rem;border-bottom:1px solid var(--line);margin-bottom:1.5rem}}
.meta span{{margin-right:1.2rem}}
.meta a{{color:var(--accent)}}
.search{{position:sticky;top:0;background:var(--bg);padding:10px 0;margin-bottom:1rem}}
.search input{{width:100%;padding:.6rem .8rem;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:.95rem 'Segoe UI',sans-serif}}
.p{{display:grid;grid-template-columns:64px 1fr;gap:12px;margin:0 0 1.2rem}}
.t{{font:.8rem/2.2 'Segoe UI',sans-serif;color:var(--accent);text-decoration:none;white-space:nowrap}}
.p p{{margin:0}}
mark{{background:#ffe27a;color:#000;border-radius:2px}}
@media(max-width:560px){{.p{{grid-template-columns:1fr;gap:0}}}}
@media print{{.search{{display:none}}}}
</style></head><body><main>
<h1>{title}</h1>
<div class="meta">{meta}</div>
<div class="search"><input id="q" type="search" placeholder="Search transcript..." autocomplete="off"></div>
<div id="body">
{body}
</div>
__SCRIPT__
</body></html>"""

_SCRIPT = r"""<script>
const q=document.getElementById('q');
const ps=[...document.querySelectorAll('.p p')];
const orig=ps.map(p=>p.textContent);
const esc=s=>s.replace(/[.*+?^$()|[\]\\{}]/g,'\\$&');
const h=s=>s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
q.addEventListener('input',()=>{
  const v=q.value.trim();
  const re=v?new RegExp(esc(v),'gi'):null;
  ps.forEach((p,i)=>{
    const row=p.parentElement;
    if(!re){p.textContent=orig[i];row.style.display='';return}
    const hit=re.test(orig[i]);re.lastIndex=0;
    row.style.display=hit?'':'none';
    if(hit)p.innerHTML=h(orig[i]).replace(re,m=>'<mark>'+m+'</mark>');
  });
});
</script>"""


def to_html(t: Transcript) -> str:
    src = t.source
    meta = []
    if src.uploader:
        meta.append(f"<span>{html.escape(src.uploader)}</span>")
    if src.duration:
        meta.append(f"<span>{ts(src.duration)}</span>")
    meta.append(f"<span>Language: {html.escape(t.language)}</span>")
    if src.url:
        u = html.escape(src.url, quote=True)
        meta.append(f'<span><a href="{u}">Original video</a></span>')
    rows = []
    for p in t.paragraphs:
        link = _yt_link(src.url, p.start)
        stamp = (f'<a class="t" href="{html.escape(link, quote=True)}">{ts(p.start)}</a>' if link
                 else f'<span class="t">{ts(p.start)}</span>')
        rows.append(f'<div class="p">{stamp}<p>{html.escape(p.text)}</p></div>')
    return _HTML.format(title=html.escape(src.title), meta="".join(meta), body="\n".join(rows))


def to_json(t: Transcript) -> str:
    src = t.source
    return json.dumps({
        "title": src.title, "uploader": src.uploader, "url": src.url, "duration": src.duration,
        "language": t.language,
        "paragraphs": [{"start": p.start, "end": p.end, "text": p.text, "link": _yt_link(src.url, p.start)}
                       for p in t.paragraphs],
    }, ensure_ascii=False)


def save_all(t: Transcript, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = safe_name(t.source.title)
    files = {
        "html": (out_dir / f"{base}.html", to_html(t)),
        "md": (out_dir / f"{base}.md", to_markdown(t)),
        "txt": (out_dir / f"{base}.txt", to_text(t)),
        "srt": (out_dir / f"{base}.srt", to_srt(t)),
        "json": (out_dir / f"{base}.json", to_json(t)),
    }
    for path, content in files.values():
        path.write_text(content, encoding="utf-8")
    return {k: v[0] for k, v in files.items()}


def run(target: str, model: str, out_dir: Path, log: Log, progress: Progress, captions: str = "uploaded") -> dict[str, Path]:
    """captions: "uploaded" = only creator-uploaded English captions; "auto" = also auto-generated; "off"."""
    with tempfile.TemporaryDirectory(prefix="transcriber_") as tmp:
        work = Path(tmp)
        if is_url(target):
            target = target.strip()
            log("Reading video info...")
            with _ydl() as ydl:
                info = ydl.extract_info(target, download=False)
            log(f"Title: {info.get('title')}")
            t = None
            if captions != "off":
                t = try_captions(target, info, captions == "auto", work, log)
            if t is None:
                lang = (info.get("language") or "en").lower()
                if not lang.startswith("en"):
                    log(f"Warning: this video looks like it is in '{lang}', not English. Results may be poor.")
                t = transcribe(fetch_audio(target, info, work, log), model, log, progress)
        else:
            source = local_source(target)
            log(f"Title: {source.title}")
            t = transcribe(source, model, log, progress)
        progress(1.0)
        files = save_all(t, out_dir)
    log(f"Done. Saved to {out_dir}")
    return files
