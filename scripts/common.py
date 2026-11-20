"""Shared helpers for streamclips scripts.

Every script imports this. It resolves ffmpeg/ffprobe, reads env keys without
printing them, and gives small JSON / path / time helpers.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
FONTS_DIR = SKILL_DIR / "fonts"
IS_WIN = sys.platform.startswith("win")

KEYS = {
    "GROQ_API_KEY": "Groq Whisper transcription (free tier). https://console.groq.com",
    "YOUTUBE_API_KEY": "YouTube Data API v3: stream discovery + live chat (free). https://console.cloud.google.com",
    "ELEVENLABS_API_KEY": "ElevenLabs (optional, paid): voiceover or Scribe transcription when a campaign asks for it. https://elevenlabs.io",
}
OPTIONAL_KEYS = {"ELEVENLABS_API_KEY"}
# Optional user-local key file for people whose agent does not inherit shell variables (GUI-launched apps).
# Format: KEY=value per line. Never inside the repo or a campaign folder.
KEY_FILE = Path(os.environ.get("STREAMCLIPS_KEY_FILE", Path.home() / ".config" / "streamclips" / "env"))


class StepError(RuntimeError):
    """Raised for user-facing failures. Scripts print the message and exit 2."""


def die(msg: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _key_file_values() -> dict[str, str]:
    vals: dict[str, str] = {}
    try:
        if KEY_FILE.exists():
            for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return vals


def key(name: str, required: bool = True) -> str | None:
    val = os.environ.get(name, "").strip() or _key_file_values().get(name, "")
    if not val and required:
        raise StepError(
            f"{name} is not set. Ask the user to export it in their shell (or put KEY=value in {KEY_FILE}); "
            f"never paste it into chat or repo files. Purpose: {KEYS.get(name, '')}"
        )
    return val or None


def has_key(name: str) -> bool:
    return bool(key(name, required=False))


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

        with contextlib.redirect_stdout(sys.stderr):  # first-run download messages must not pollute JSON stdout
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


def ffpath(path: str | Path) -> str:
    """Quote a path for use inside an ffmpeg filter option (ass=, fontsdir=). Handles ':' '\\' and apostrophes on all OSes."""
    s = str(path).replace("\\", "/")
    s = re.sub(r"([\\:'])", r"\\\1", s)
    return "'" + s + "'"


def ytdlp_base() -> list[str]:
    """yt-dlp invocation. yt-dlp needs a JS runtime for YouTube; enable node when deno is absent."""
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings"]
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    return cmd


def run(cmd: list[str], timeout: int = 1800, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1500:]
        raise StepError(f"command failed ({Path(cmd[0]).name} rc={r.returncode}):\n{tail}")
    return r


def probe(path: str | Path) -> dict:
    r = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(r.stdout)


def duration(path: str | Path) -> float:
    info = probe(path)
    return float(info["format"].get("duration") or 0.0)


def video_stream(info: dict) -> dict:
    vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not vs:
        raise StepError("file has no video stream")
    return vs[0]


def fps_of(stream: dict) -> float:
    try:
        return float(Fraction(stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "0/1"))
    except (ValueError, ZeroDivisionError):
        return 0.0


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
    """H:MM:SS.cc (ASS style). Rounds to centiseconds first so 59.999 -> 1:00.00, never 0:60.00."""
    cs = int(round(max(0.0, float(seconds)) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{c:02d}"


def parse_time(text: str | float | int) -> float:
    """'1:23:45', '83:45', '5025', '5025.5' -> seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = [float(p) for p in text.split(":")]
    if len(parts) > 3 or any(p < 0 for p in parts):
        raise StepError(f"bad time: {text}")
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
    p = Path(start or os.getcwd()).expanduser().resolve()
    for cand in [p, *p.parents]:
        if (cand / "campaign.yaml").exists():
            return cand
    raise StepError("No campaign.yaml found here or above. Pass --campaign <folder>.")


def main_guard(fn):
    """Wrap a script's main(): every failure becomes one JSON error line, never a traceback."""
    try:
        fn()
    except StepError as e:
        die(str(e))
    except SystemExit:
        raise
    except KeyboardInterrupt:
        die("interrupted", 130)
    except subprocess.TimeoutExpired as e:
        die(f"timeout after {e.timeout}s: {Path(str(e.cmd[0])).name if isinstance(e.cmd, list) else e.cmd}")
    except subprocess.CalledProcessError as e:
        die(f"command failed rc={e.returncode}: {(e.stderr or '')[-800:] if isinstance(e.stderr, str) else ''}")
    except Exception as e:  # noqa: BLE001
        die(f"{type(e).__name__}: {e}")
