#!/usr/bin/env python3
"""Check tools, Python packages and API keys. Never prints key values.

Usage: python scripts/doctor.py [--json]
Exit 0 = ready for replay clipping. Missing optional parts are listed as warnings.
"""
from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys

from common import KEYS, FONTS_DIR, StepError, ffmpeg_bin, main_guard

REQUIRED_PKGS = ["yaml", "numpy", "PIL", "yt_dlp", "streamlink", "static_ffmpeg", "requests"]
OPTIONAL_PKGS = {"mlx_whisper": "local transcription fallback (Apple Silicon only)"}


def check_pkg(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def main() -> None:
    report: dict = {"ok": True, "python": sys.version.split()[0], "platform": platform.platform(), "checks": [], "ask_user": []}

    def add(name, ok, detail="", level="error"):
        report["checks"].append({"name": name, "ok": ok, "detail": detail, "level": level})
        if not ok and level == "error":
            report["ok"] = False

    if sys.version_info < (3, 10):
        add("python>=3.10", False, "Python 3.10 or newer is required")
    else:
        add("python>=3.10", True)

    for p in REQUIRED_PKGS:
        add(f"pkg:{p}", check_pkg(p), "" if check_pkg(p) else "pip install -r requirements.txt")
    for p, why in OPTIONAL_PKGS.items():
        add(f"pkg:{p}", check_pkg(p), why, level="warn")

    try:
        ff = ffmpeg_bin()
        add("ffmpeg(libass)", True, ff)
    except StepError as e:
        add("ffmpeg(libass)", False, str(e))

    add("yt-dlp", shutil.which("yt-dlp") is not None or check_pkg("yt_dlp"), "pip install yt-dlp")
    add("streamlink", shutil.which("streamlink") is not None or check_pkg("streamlink"), "pip install streamlink")
    add("node/deno (yt-dlp needs a JS runtime for YouTube)", bool(shutil.which("node") or shutil.which("deno")),
        "install Node.js from https://nodejs.org", level="warn")
    add("font:Anton", (FONTS_DIR / "Anton-Regular.ttf").exists(), "bundled font missing", level="warn")

    for k, why in KEYS.items():
        present = bool(os.environ.get(k, "").strip())
        level = "warn" if k == "ELEVENLABS_API_KEY" else "error"
        add(f"env:{k}", present, why, level=level)
        if not present:
            report["ask_user"].append(
                f"Please set {k} in your shell environment ({why}). "
                f"Example (macOS/Linux zsh/bash): export {k}=\"...\"  then re-run doctor. "
                f"Do not paste the key into chat or files."
            )

    disk = shutil.disk_usage(os.path.expanduser("~"))
    free_gb = disk.free / 1e9
    add("disk>=15GB free", free_gb >= 15, f"{free_gb:.1f} GB free", level="warn")

    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main_guard(main)
