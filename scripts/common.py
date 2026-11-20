"""Shared helpers for streamclips scripts.

Every script imports this. It resolves ffmpeg/ffprobe, reads env keys without
printing them, and gives small JSON / path / time helpers.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = SKILL_DIR / "fonts"

KEYS = {
    "GROQ_API_KEY": "Groq (transcription + LLM helpers). Free at https://console.groq.com",
    "YOUTUBE_API_KEY": "YouTube Data API v3 (stream discovery + live chat). Free at https://console.cloud.google.com",
    "ELEVENLABS_API_KEY": "ElevenLabs (optional, voiceover only). https://elevenlabs.io",
}


class StepError(RuntimeError):
    """Raised for user-facing failures. Scripts print the message and exit 2."""


def die(msg: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def key(name: str, required: bool = True) -> str | None:
    val = os.environ.get(name, "").strip()
    if not val and required:
        raise StepError(
            f"{name} is not set. Ask the user to export it in their shell "
            f"(never paste it into files). Purpose: {KEYS.get(name, '')}"
        )
    return val or None


# ---------------------------------------------------------------- ffmpeg


def _has_filter(binary: str, name: str) -> bool:
    try:
        r = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return any(line.split()[1:2] == [name] for line in r.stdout.splitlines() if line.strip())


def ffmpeg_bin() -> str:
    """ffmpeg with libass. Order: $FFMPEG_BIN, static-ffmpeg package, PATH."""
    env = os.environ.get("FFMPEG_BIN")
    if env and Path(env).exists():
        return env
    try:
        import static_ffmpeg.run as sfr  # type: ignore

        ff, _ = sfr.get_or_fetch_platform_executables_else_raise()
        return str(ff)
    except Exception:
        pass
    path = shutil.which("ffmpeg")
    if path and _has_filter(path, "ass"):
        return path
    raise StepError(
        "No ffmpeg with the 'ass' (caption) filter found. Run: pip install static-ffmpeg "
        "(or set FFMPEG_BIN to an ffmpeg built with libass)."
    )


def ffprobe_bin() -> str:
    ff = ffmpeg_bin()
    cand = Path(ff).with_name("ffprobe" + Path(ff).suffix)
    if cand.exists():
        return str(cand)
    p = shutil.which("ffprobe")
    if p:
        return p
    raise StepError("ffprobe not found next to ffmpeg.")


def run(cmd: list[str], timeout: int = 1800, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1500:]
        raise StepError(f"command failed ({cmd[0]} rc={r.returncode}):\n{tail}")
    return r


def probe(path: str | Path) -> dict:
    r = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout)


def duration(path: str | Path) -> float:
    info = probe(path)
    return float(info["format"].get("duration") or 0.0)


def hw_encoder() -> list[str]:
    """Best available H.264 encoder args for this machine."""
    ff = ffmpeg_bin()
    r = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True)
    enc = r.stdout
    if sys.platform == "darwin" and "h264_videotoolbox" in enc:
        return ["-c:v", "h264_videotoolbox", "-b:v", "9M", "-maxrate", "11M", "-bufsize", "18M"]
    if "h264_nvenc" in enc and os.environ.get("STREAMCLIPS_NVENC", "1") == "1":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "9M"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]


# ---------------------------------------------------------------- misc


def slug(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:maxlen].strip("-") or "clip"


def today() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def hms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def parse_time(text: str) -> float:
    """'1:23:45', '83:45', '5025', '5025.5' -> seconds."""
    text = str(text).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = [float(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def load_yaml(path: str | Path) -> dict:
    try:
        import yaml  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise StepError("PyYAML missing. Run: pip install -r requirements.txt") from e
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def dump_yaml(path: str | Path, obj: dict) -> None:
    import yaml  # type: ignore

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), encoding="utf-8")


def find_campaign(start: str | Path | None = None) -> Path:
    """Walk up from `start` (or cwd) until a campaign.yaml is found."""
    p = Path(start or os.getcwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "campaign.yaml").exists():
            return cand
    raise StepError("No campaign.yaml found here or above. Pass --campaign <folder>.")


def main_guard(fn):
    """Wrap a script's main(): turn StepError into a clean JSON error."""
    try:
        fn()
    except StepError as e:
        die(str(e))
    except KeyboardInterrupt:
        die("interrupted", 130)
