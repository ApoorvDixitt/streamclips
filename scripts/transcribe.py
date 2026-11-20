#!/usr/bin/env python3
"""Word-timestamped transcription of a time window. Free first.

  transcribe.py --file segment.mp4 --start 120 --end 180 [--mode scan|final] [--language en]
                [--names Kreek Jason Lucia] [--engine auto|groq|mlx|scribe] [--out words.json]

Modes
  scan   one fast pass (Groq whisper-large-v3-turbo). For scoring many candidates.
  final  for the clip that will be rendered: ffmpeg denoise (free) + names-only vocabulary prompt + two Groq passes
         (large-v3 and turbo) merged to fill gaps, plus a `gaps` list (speech energy with no words) for the agent to check.
         Measured on a noisy GTA 6 window: 26 words raw -> 42 words with denoise + names prompt.

Engines: groq (free tier, default), mlx (local Apple Silicon fallback when offline), scribe (ElevenLabs, opt-in only:
`--engine scribe`; more accurate on game audio and labels speakers, but costs credits and needs a plan, so never automatic).

Output (times relative to --start): {"text","words":[{"word","start","end"}],"events":[],"gaps":[[s,e],...],"engine","mode"}.
Cleans Whisper artifacts: trailing hallucinations ("Thank you."), repeated lines, words stretched over silences.
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

import numpy as np

from common import StepError, ffmpeg_bin, key, main_guard, out, parse_time, write_json

HALLUCINATIONS = re.compile(r"^(thank you\.?|thanks for watching\.?|subscribe\.?|bye\.?|you|\.|crashing.*|screaming.*|yelling.*|\[.*\]|\(.*\))$", re.I)
UA = "streamclips/0.1"
DENOISE = "highpass=f=100,lowpass=f=7000,afftdn=nf=-28:tn=1,dynaudnorm=f=150:g=15"


def extract_wav(media: str, start: float, end: float, denoise: bool = False) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="sc_")) / ("clean.wav" if denoise else "win.wav")
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", media, "-vn", "-ac", "1", "-ar", "16000"]
    if denoise:
        cmd += ["-af", DENOISE]
    subprocess.run(cmd + ["-c:a", "pcm_s16le", str(tmp)], check=True, timeout=600)
    return tmp


def _multipart(fields: dict, file_field: str, data: bytes, fname: str, ctype: str) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        for item in (v if isinstance(v, list) else [v]):
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{item}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    return b"".join(parts), b


def _post(url: str, body: bytes, boundary: str, headers: dict, timeout: int = 180):
    req = urllib.request.Request(url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA, **headers})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:300]
            if e.code == 429 or e.code >= 500:
                time.sleep(min(float(e.headers.get("retry-after", 2 ** attempt + 1)), 60))
                continue
            raise StepError(f"{url.split('/')[2]} {e.code}: {msg}")
    raise StepError(f"{url.split('/')[2]}: rate limited repeatedly (Groq free tier ~8 audio-hours/day). Try later or --engine mlx.")


# ------------------------------------------------------------ engines


def groq_pass(wav: Path, language: str | None, model: str, names: list[str]) -> dict:
    fields = {"model": model, "response_format": "verbose_json", "timestamp_granularities[]": ["word", "segment"], "temperature": "0"}
    if language:
        fields["language"] = language
    if names:
        fields["prompt"] = ", ".join(dict.fromkeys(names)) + "."  # vocabulary only; descriptive prompts make Whisper hallucinate
    body, b = _multipart(fields, "file", wav.read_bytes(), wav.name, "audio/wav")
    return _post("https://api.groq.com/openai/v1/audio/transcriptions", body, b, {"Authorization": f"Bearer {key('GROQ_API_KEY')}"})


def mlx_pass(wav: Path, language: str | None) -> dict:
    try:
        import mlx_whisper  # type: ignore
    except ImportError as e:
        raise StepError("No transcription engine: set GROQ_API_KEY (free) or install mlx-whisper (Apple Silicon).") from e
    model = os.environ.get("STREAMCLIPS_MLX_MODEL", "mlx-community/whisper-small-mlx")
    r = mlx_whisper.transcribe(str(wav), path_or_hf_repo=model, word_timestamps=True, language=language)
    r["words"] = [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])} for s in r["segments"] for w in s.get("words", [])]
    return r


def scribe_pass(wav: Path, language: str | None, names: list[str]) -> dict:
    """Opt-in paid engine (ElevenLabs Scribe v3). Returns Whisper-shaped dict."""
    fields: dict = {"model_id": "scribe_v3", "timestamps_granularity": "word", "tag_audio_events": "true", "diarize": "false"}
    if language:
        fields["language_code"] = {"en": "eng", "hi": "hin", "es": "spa", "pt": "por", "fr": "fra", "de": "deu"}.get(language, language)
    if names:
        fields["keyterms"] = names[:100]
    body, b = _multipart(fields, "file", wav.read_bytes(), wav.name, "audio/wav")
    r = _post("https://api.elevenlabs.io/v1/speech-to-text", body, b, {"xi-api-key": key("ELEVENLABS_API_KEY")})
    words = [{"word": w["text"], "start": w["start"], "end": w["end"]} for w in r.get("words", []) if w["type"] == "word"]
    events = [{"event": w["text"].strip("[] "), "start": w["start"], "end": w["end"]} for w in r.get("words", []) if w["type"] == "audio_event"]
    return {"text": r.get("text", ""), "segments": [], "words": words, "events": events}


# ------------------------------------------------------------ clean / merge / gaps


def clean(raw: dict, window_len: float) -> dict:
    segs, last = [], None
    for s in raw.get("segments", []):
        text = (s.get("text") or "").strip()
        if not text or float(s.get("start", 0)) >= window_len - 0.2 or float(s.get("no_speech_prob", 0)) > 0.75:
            continue
        if HALLUCINATIONS.match(text) and (last is None or float(s["start"]) - float(last["end"]) > 3):
            continue
        # Whisper sometimes emits the same segment twice back-to-back (glitch). A speaker really repeating a line has a pause.
        if last and text.lower() == (last.get("text") or "").strip().lower() and float(s["start"]) - float(last["end"]) < 0.25:
            continue
        segs.append({"start": round(float(s["start"]), 2), "end": round(min(float(s["end"]), window_len), 2), "text": text})
        last = s
    keep = [(s["start"], s["end"]) for s in segs]
    words = []
    for w in raw.get("words", []):
        ws, we = float(w.get("start", 0)), float(w.get("end", 0))
        if ws >= window_len or (keep and not any(a - 0.05 <= ws <= b + 0.05 for a, b in keep)):
            continue
        txt = w["word"].strip()
        if not txt or HALLUCINATIONS.match(txt) and txt.startswith(("[", "(")):
            continue
        we = max(we, ws + 0.08)
        if we - ws > 2.5:  # a word stretched over a silence
            we = ws + 0.6
        words.append({"word": txt, "start": round(ws, 2), "end": round(min(we, window_len), 2)})
    text = " ".join(s["text"] for s in segs) if segs else " ".join(w["word"] for w in words)
    return {"text": text, "segments": segs, "words": words, "events": raw.get("events", [])}


def merge(base: dict, other: dict) -> dict:
    """Fill gaps (>1.2 s without words) in `base` with words from `other` that fall inside those gaps."""
    words = sorted(base["words"], key=lambda w: w["start"])
    added = 0
    bounds = [(-1.0, words[0]["start"] if words else 1e9)] + [(words[i]["end"], words[i + 1]["start"]) for i in range(len(words) - 1)] + ([(words[-1]["end"], 1e9)] if words else [])
    def norm(t: str) -> str:
        return re.sub(r"[^a-z']", "", t.lower())

    for a, b in bounds:
        if b - a < 1.2:
            continue
        fill = [w for w in other["words"] if a + 0.15 <= w["start"] and w["end"] <= b - 0.15]
        # the two passes time the same word slightly differently: never re-add a word the base already has within 1.2 s
        fill = [w for w in fill if not any(norm(w["word"]) == norm(x["word"]) and abs(x["start"] - w["start"]) < 1.2 for x in words)]
        if len(fill) < 3:  # one or two stray words are timing jitter between passes, not a missed phrase
            continue
        words += fill
        added += len(fill)
    words.sort(key=lambda w: w["start"])
    return {**base, "words": words, "text": " ".join(w["word"] for w in words), "merged_from_second_pass": added,
            "second_pass_text": other.get("text", "")}


def speech_gaps(wav: Path, words: list[dict], window_len: float, min_gap: float = 1.5) -> list[list[float]]:
    """Stretches with voice-band energy but no words: the agent should check these (missed speech vs game noise)."""
    raw = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(wav), "-f", "f32le", "-"], capture_output=True, timeout=300).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    step = 1600  # 0.1 s
    n = len(x) // step
    if n < 5:
        return []
    fr = x[: n * step].reshape(n, step)
    rms = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    thr = np.median(rms) + 6
    loud = rms > thr
    covered = np.zeros(n, dtype=bool)
    for w in words:
        a, b = int(max(0, w["start"] - 0.2) * 10), int(min(window_len, w["end"] + 0.2) * 10)
        covered[a:b + 1] = True
    gaps, start = [], None
    for i in range(n):
        if loud[i] and not covered[i]:
            start = i if start is None else start
        else:
            if start is not None and (i - start) / 10 >= min_gap:
                gaps.append([round(start / 10, 1), round(i / 10, 1)])
            start = None
    if start is not None and (n - start) / 10 >= min_gap:
        gaps.append([round(start / 10, 1), round(n / 10, 1)])
    return gaps


def transcribe_window(media: str, start: float, end: float, language: str | None = "en", mode: str = "scan",
                      names: list[str] | None = None, engine: str = "auto") -> dict:
    window_len = end - start
    names = names or []
    have_groq = bool(os.environ.get("GROQ_API_KEY"))
    if engine == "auto":
        engine = "groq" if have_groq else "mlx"
    tmpdirs: list[Path] = []
    try:
        if engine == "scribe":
            wav = extract_wav(media, start, end)
            tmpdirs.append(wav.parent)
            res = clean(scribe_pass(wav, language, names), window_len)
            used = "scribe"
        elif engine == "mlx":
            wav = extract_wav(media, start, end, denoise=(mode == "final"))
            tmpdirs.append(wav.parent)
            res = clean(mlx_pass(wav, language), window_len)
            used = "mlx"
        else:  # groq
            if mode == "scan":
                wav = extract_wav(media, start, end)
                tmpdirs.append(wav.parent)
                res = clean(groq_pass(wav, language, "whisper-large-v3-turbo", names), window_len)
                used = "groq/turbo"
            else:
                wav = extract_wav(media, start, end, denoise=True)
                tmpdirs.append(wav.parent)
                a = clean(groq_pass(wav, language, "whisper-large-v3", names), window_len)
                b = clean(groq_pass(wav, language, "whisper-large-v3-turbo", names), window_len)
                base, other = (a, b) if len(a["words"]) >= len(b["words"]) else (b, a)
                res = merge(base, other)
                used = "groq/large-v3+turbo denoised"
        if mode == "final":
            res["gaps"] = speech_gaps(wav, res["words"], window_len)
        else:
            res["gaps"] = []
    finally:
        for d in tmpdirs:
            for f in d.glob("*"):
                f.unlink(missing_ok=True)
            try:
                d.rmdir()
            except Exception:
                pass
    res.update({"engine": used, "mode": mode, "window": {"start": start, "end": end}})
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--start", default="0")
    ap.add_argument("--end", required=True)
    ap.add_argument("--language", default="en", help="ISO code or 'auto'")
    ap.add_argument("--mode", choices=["scan", "final"], default="scan")
    ap.add_argument("--names", nargs="*", default=[], help="vocabulary hint: streamer, characters, places")
    ap.add_argument("--engine", choices=["auto", "groq", "mlx", "scribe"], default="auto")
    ap.add_argument("--out")
    a = ap.parse_args()
    lang = None if a.language == "auto" else a.language
    res = transcribe_window(a.file, parse_time(a.start), parse_time(a.end), lang, a.mode, a.names, a.engine)
    if a.out:
        write_json(a.out, res)
        out({"ok": True, "out": a.out, "engine": res["engine"], "words": len(res["words"]), "gaps": res["gaps"], "text": res["text"][:300]})
    else:
        out(res)


if __name__ == "__main__":
    main_guard(main)
