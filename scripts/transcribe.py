#!/usr/bin/env python3
"""Word-timestamped transcription of a time window. Groq Whisper first, local mlx-whisper fallback.

  transcribe.py --file segment.mp4 --start 120 --end 180 [--language en] [--out words.json]

Returns {"text", "words":[{"word","start","end"}], "segments", "engine"} with times relative to --start.
Cleans Whisper artifacts: trailing hallucinations ("Thank you."), repeated lines, segments past the audio end.
Groq free tier covers roughly 8 audio-hours/day, so transcribe candidate windows, not whole streams.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from common import StepError, ffmpeg_bin, key, main_guard, out, parse_time, write_json

HALLUCINATIONS = re.compile(r"^(thank you\.?|thanks for watching\.?|subscribe\.?|bye\.?|you|\.)$", re.I)


def extract_wav(media: str, start: float, end: float) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="sc_")) / "win.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", media,
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(tmp)], check=True, timeout=600)
    return tmp


def groq_transcribe(wav: Path, language: str | None) -> dict:
    api = key("GROQ_API_KEY")
    b = uuid.uuid4().hex
    parts = []

    def field(n, v):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode())

    field("model", os.environ.get("STREAMCLIPS_GROQ_STT", "whisper-large-v3-turbo"))
    field("response_format", "verbose_json")
    field("timestamp_granularities[]", "word")
    field("timestamp_granularities[]", "segment")
    field("temperature", "0")
    if language:
        field("language", language)
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="win.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + wav.read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", data=b"".join(parts),
                                 headers={"Authorization": f"Bearer {api}", "Content-Type": f"multipart/form-data; boundary={b}", "User-Agent": "streamclips/0.1"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")[:300]
            if e.code == 429 or e.code >= 500:
                wait = float(e.headers.get("retry-after", 2 ** attempt + 1))
                time.sleep(min(wait, 60))
                continue
            raise StepError(f"Groq transcription failed {e.code}: {body}")
    raise StepError("Groq transcription: rate limited repeatedly (free tier ~8 audio-hours/day). Try later or use --engine mlx.")


def mlx_transcribe(wav: Path, language: str | None) -> dict:
    try:
        import mlx_whisper  # type: ignore
    except ImportError as e:
        raise StepError("mlx-whisper not installed (Apple Silicon only: pip install mlx-whisper) and Groq unavailable.") from e
    model = os.environ.get("STREAMCLIPS_MLX_MODEL", "mlx-community/whisper-small-mlx")
    r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=model, word_timestamps=True, language=language)
    words = [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"]), "probability": float(w.get("probability", 1))}
             for s in r["segments"] for w in s.get("words", [])]
    return {"text": r["text"], "segments": r["segments"], "words": words}


def clean(result: dict, window_len: float) -> dict:
    segs, last = [], None
    for s in result.get("segments", []):
        text = (s.get("text") or "").strip()
        if not text or float(s.get("start", 0)) >= window_len - 0.2:
            continue
        if float(s.get("no_speech_prob", 0)) > 0.75:
            continue
        if HALLUCINATIONS.match(text) and (last is None or float(s["start"]) - float(last["end"]) > 3):
            continue
        if last and text.lower() == (last.get("text") or "").strip().lower() and float(s["start"]) - float(last["end"]) < 1.5:
            continue
        segs.append({"start": round(float(s["start"]), 2), "end": round(min(float(s["end"]), window_len), 2), "text": text})
        last = s
    keep_ranges = [(s["start"], s["end"]) for s in segs]
    words = []
    for w in result.get("words", []):
        ws, we = float(w.get("start", 0)), float(w.get("end", 0))
        if ws >= window_len:
            continue
        if keep_ranges and not any(a - 0.3 <= ws <= b + 0.3 for a, b in keep_ranges):
            continue
        we = max(we, ws + 0.08)
        if we - ws > 2.5:  # Whisper sometimes stretches one word over a silence
            we = ws + 0.6
        words.append({"word": w["word"].strip(), "start": round(ws, 2), "end": round(min(we, window_len), 2)})
    return {"text": " ".join(s["text"] for s in segs), "segments": segs, "words": words}


def transcribe_window(media: str, start: float, end: float, language: str | None = "en", engine: str = "auto") -> dict:
    wav = extract_wav(media, start, end)
    try:
        if engine in ("auto", "groq") and os.environ.get("GROQ_API_KEY"):
            try:
                raw, used = groq_transcribe(wav, language), "groq"
            except StepError:
                if engine == "groq":
                    raise
                raw, used = mlx_transcribe(wav, language), "mlx"
        elif engine == "groq":
            raw, used = groq_transcribe(wav, language), "groq"
        else:
            raw, used = mlx_transcribe(wav, language), "mlx"
    finally:
        try:
            wav.unlink()
            wav.parent.rmdir()
        except Exception:
            pass
    res = clean(raw, end - start)
    res["engine"] = used
    res["window"] = {"start": start, "end": end}
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--start", default="0")
    ap.add_argument("--end", required=True)
    ap.add_argument("--language", default="en", help="ISO code or 'auto'")
    ap.add_argument("--engine", choices=["auto", "groq", "mlx"], default="auto")
    ap.add_argument("--out")
    a = ap.parse_args()
    lang = None if a.language == "auto" else a.language
    res = transcribe_window(a.file, parse_time(a.start), parse_time(a.end), lang, a.engine)
    if a.out:
        write_json(a.out, res)
        out({"ok": True, "out": a.out, "engine": res["engine"], "words": len(res["words"]), "text": res["text"][:300]})
    else:
        out(res)


if __name__ == "__main__":
    main_guard(main)
