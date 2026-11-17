"""Local web UI server. Python does the work; the page (ui.html) is the interface."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core

HERE = Path(__file__).parent
TOKEN = secrets.token_urlsafe(16)
OPENABLE = {".html", ".md", ".txt", ".srt"}
IDLE_EXIT_SECONDS = 150


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"  # idle | running | done | error
        self.pct = 0.0
        self.log: list[str] = []
        self.files: dict[str, str] = {}
        self.error = ""
        self.out_dir = core.DEFAULT_OUT
        self.last_ping = 0.0
        self.pinged = False

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "pct": self.pct, "log": list(self.log[-200:]),
                    "files": self.files, "error": self.error, "outDir": str(self.out_dir)}


S = State()


def start_job(target: str, model: str, out_dir: Path, captions: str = "uploaded"):
    with S.lock:
        if S.status == "running":
            return False
        S.status, S.pct, S.log, S.files, S.error = "running", 0.0, [], {}, ""
        S.out_dir = out_dir

    def log(m):
        with S.lock:
            S.log.append(m)

    def prog(f):
        with S.lock:
            S.pct = f

    def work():
        try:
            files = core.run(target, model, out_dir, log, prog, captions)
            with S.lock:
                S.files = {k: str(v) for k, v in files.items()}
                S.status = "done"
        except Exception as ex:  # noqa: BLE001
            with S.lock:
                S.error, S.status = str(ex), "error"
                S.log.append(f"ERROR: {ex}")

    threading.Thread(target=work, daemon=True).start()
    return True


def native_dialog(kind: str, initial: str) -> str:
    """Run a Tk file/folder dialog in a throwaway process (Tk dislikes non-main threads)."""
    code = (
        "import sys,tkinter as tk\nfrom tkinter import filedialog as fd\n"
        "r=tk.Tk();r.withdraw();r.attributes('-topmost',True)\n"
        "k,i=sys.argv[1],sys.argv[2]\n"
        "p=fd.askdirectory(initialdir=i) if k=='folder' else fd.askopenfilename(\n"
        " filetypes=[('Video/audio',sys.argv[3]),('All files','*.*')])\n"
        "print(p or '')"
    )
    exts = " ".join(f"*{e}" for e in sorted(core.VIDEO_EXTS))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run([sys.executable, "-c", code, kind, initial, exts], capture_output=True, text=True,
                       creationflags=flags)
    return r.stdout.strip().replace("/", os.sep)


def history():
    d = S.out_dir
    if not d.is_dir():
        return []
    items = sorted(d.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]
    return [{"name": p.stem, "path": str(p), "mtime": int(p.stat().st_mtime)} for p in items]


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silent (pythonw has no stderr)
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        host_ok = self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")
        return host_ok and self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        if self.path == "/":
            html = (HERE / "ui.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            data = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif not self._authed():
            self._json({"error": "forbidden"}, 403)
        elif self.path == "/api/status":
            self._json(S.snapshot())
        elif self.path == "/api/history":
            self._json(history())
        elif self.path.startswith("/api/transcript?"):
            from urllib.parse import parse_qs, urlparse
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
            p = S.out_dir / f"{Path(name).name}.json"
            if not name or not p.is_file() or not inside(p, S.out_dir):
                self._json({"error": "No readable copy of that transcript. Re-run it to enable the reader."}, 404)
            else:
                self._json(json.loads(p.read_text(encoding="utf-8")))
        elif self.path == "/api/ping":
            S.last_ping, S.pinged = time.time(), True
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authed():
            return self._json({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/start":
            target = (body.get("target") or "").strip()
            if not target:
                return self._json({"error": "Paste a link or choose a file first."}, 400)
            cap = body.get("captions") if body.get("captions") in ("uploaded", "auto", "off") else "uploaded"
            model = body.get("model") if body.get("model") in core.MODELS else "small.en"
            ok = start_job(target, model, Path(body.get("outDir") or core.DEFAULT_OUT), cap)
            return self._json({"ok": ok} if ok else {"error": "A job is already running."}, 200 if ok else 409)
        if self.path == "/api/browse":
            kind = "folder" if body.get("kind") == "folder" else "file"
            return self._json({"path": native_dialog(kind, body.get("initial") or str(Path.home()))})
        if self.path == "/api/open":
            p = Path(body.get("path", ""))
            if p.is_dir() and p.resolve() == S.out_dir.resolve():
                os.startfile(p)
            elif p.is_file() and p.suffix.lower() in OPENABLE and inside(p, S.out_dir):
                os.startfile(p)
            else:
                return self._json({"error": "Not allowed"}, 400)
            return self._json({"ok": True})
        if self.path == "/api/openfolder":
            S.out_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(S.out_dir)
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)


def open_window(url: str):
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        shutil.which("msedge") or "",
    ]
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1040,860"])
            return
    webbrowser.open(url)


def main():
    S.out_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    open_window(url)
    # Exit once the window has gone away (no heartbeat) and nothing is running.
    started = time.time()
    while True:
        time.sleep(3)
        idle = time.time() - (S.last_ping if S.pinged else started)
        with S.lock:
            busy = S.status == "running"
        if idle > (IDLE_EXIT_SECONDS if S.pinged else 60) and not busy:
            break
    server.shutdown()


if __name__ == "__main__":
    main()
