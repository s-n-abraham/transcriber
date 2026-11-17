"""Transcriber entry point.

No arguments  -> opens the graphical interface.
With a target -> command line:  python app.py <youtube-url-or-file> [--model small] [--lang en] [--out DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import core


def cli(args) -> int:
    last = [-1]

    def progress(f):
        pct = int(f * 100)
        if pct != last[0] and pct % 5 == 0:
            last[0] = pct
            print(f"  {pct}%", flush=True)

    files = core.run(args.target, args.model, Path(args.out), lambda m: print(m, flush=True), progress, args.captions)
    for k, v in files.items():
        print(f"{k:5} {v}")
    return 0


def main():
    if len(sys.argv) > 1:
        ap = argparse.ArgumentParser(description="Transcribe a YouTube link or a local video/audio file.")
        ap.add_argument("target")
        ap.add_argument("--model", default="small.en", choices=core.MODELS)
        ap.add_argument("--captions", default="uploaded", choices=["uploaded", "auto", "off"],
                        help="uploaded: only creator-made English captions (default); auto: also auto-generated; off: never")
        ap.add_argument("--out", default=str(core.DEFAULT_OUT))
        sys.exit(cli(ap.parse_args()))
    import webui
    webui.main()


if __name__ == "__main__":
    main()
