#!/usr/bin/env python3
"""Check tools, Python packages and API keys. Never prints key values.

Usage: python scripts/doctor.py
Exit 0 = ready. Optional parts (ElevenLabs key, mlx-whisper) are warnings, never errors.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import shutil
import sys

from common import KEYS, KEY_FILE, OPTIONAL_KEYS, FONTS_DIR, StepError, ffmpeg_bin, has_key, main_guard

REQUIRED_PKGS = ["yaml", "numpy", "PIL", "yt_dlp", "streamlink", "static_ffmpeg", "requests"]
OPTIONAL_PKGS = {"mlx_whisper": "local transcription fallback (Apple Silicon only)"}


def check_pkg(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
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
        ok = check_pkg(p)
        add(f"pkg:{p}", ok, "" if ok else "pip install -r requirements.txt")
    for p, why in OPTIONAL_PKGS.items():
        add(f"pkg:{p}", check_pkg(p), why, level="warn")

    try:
        ff = ffmpeg_bin()
        add("ffmpeg(libass)", True, ff)
    except StepError as e:
        add("ffmpeg(libass)", False, str(e))

    js = "deno" if shutil.which("deno") else "node" if shutil.which("node") else None
    add("js-runtime for yt-dlp (deno or node)", js is not None, js or "install Deno (https://deno.com) or Node.js; yt-dlp needs one for YouTube", level="warn")
    add("font:Anton", (FONTS_DIR / "Anton-Regular.ttf").exists(), "" if (FONTS_DIR / "Anton-Regular.ttf").exists() else "bundled font missing", level="warn")

    for k, why in KEYS.items():
        present = has_key(k)
        optional = k in OPTIONAL_KEYS
        add(f"key:{k}", present, ("optional: " if optional else "") + why, level="warn" if optional else "error")
        if not present and not optional:
            report["ask_user"].append(
                f"Set {k} ({why}). macOS/Linux: add `export {k}=\"...\"` to ~/.zshrc or ~/.bashrc; Windows: setx {k} \"...\". "
                f"Or put `{k}=...` in {KEY_FILE} (works even when the agent app does not read shell profiles). "
                f"Then restart the agent session and re-run doctor. Never paste the key into chat or repo files."
            )
    if not has_key("ELEVENLABS_API_KEY"):
        report["optional"] = ["ELEVENLABS_API_KEY: only for voiceover or Scribe transcription when a campaign asks for it (paid). Not needed to start."]

    disk = shutil.disk_usage(os.path.expanduser("~"))
    free_gb = disk.free / 1e9
    add("disk>=15GB free", free_gb >= 15, f"{free_gb:.1f} GB free", level="warn")

    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main_guard(main)
