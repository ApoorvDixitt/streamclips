#!/usr/bin/env python3
"""Find candidate moments from chat bursts, audio spikes and speech, and write candidates.json + frame sheets.

  signals.py --campaign <dir> --source sources/twitch-123                       # chat-only pass over the whole stream
  signals.py --campaign <dir> --source sources/twitch-123 --segment 007200-009000.mp4 [--no-transcribe] [--top 12]

Scores are 0..1 and explainable (components are kept). Spam (giveaway "1", emote floods) is filtered by
counting unique chatters against a rolling baseline. See references/selection.md for how to read the output.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np

from common import StepError, duration, ffmpeg_bin, find_campaign, load_yaml, main_guard, now_iso, out, read_json, write_json

BIN = 10.0
REACTIONS = {
    "laugh": re.compile(r"(lma+o|lmfao|\blo+l\b|haha|😂|🤣|💀|kekw|omegalul|\blul\b|\bdead\b|\bcrying\b|im dying|i'm dying)", re.I),
    "shock": re.compile(r"(\bomg\b|oh my god|no way|\bwtf\b|what the|\bbruh\b|\bholy\b|\bnoo+\b|oh no|😱|\bnah\b|huh\?|wait what|\bayo\b)", re.I),
    "hype": re.compile(r"(let'?s go+|lesgo|\bpog|insane|\bcooked\b|clip (it|that)|\bclip\b|🔥|\bgoat\b|\bw\b|\bcrazy\b|\bwild\b|sheesh)", re.I),
}
SPAM_TOKEN = re.compile(r"^[\W_]*([0-9]+|[a-z])[\W_]*$", re.I)
DIGITS = re.compile(r"^[\d\s!.]+$")
GIVEAWAY = re.compile(r"(giveaway|typed? 1\b|type 1\b|!enter|!giveaway)", re.I)
PROMO = re.compile(r"(hit (the )?like|subscribe|giveaway|follow (me|the channel)|prime sub|gifted subs?|tiktok|turn on notifications)", re.I)
DEFAULT_WEIGHTS = {"chat": 0.5, "audio": 0.35, "speech": 0.15}


# ------------------------------------------------------------ chat


def load_chat(path: Path) -> list[dict]:
    """Read chat.jsonl, dropping exact duplicates (a re-run collector appends)."""
    msgs, seen = [], set()
    if not path.exists():
        return msgs
    for line in open(path, encoding="utf-8"):
        try:
            m = json.loads(line)
        except Exception:
            continue
        k = (round(float(m.get("t", 0)), 1), m.get("user"), m.get("text"))
        if k in seen:
            continue
        seen.add(k)
        msgs.append(m)
    return msgs


def chat_curve(msgs: list[dict], t_min: float, t_max: float) -> dict:
    """Per-bin unique chatters, reaction share, spam flag, baseline ratio."""
    nb = int(math.ceil((t_max - t_min) / BIN)) + 1
    users = [set() for _ in range(nb)]
    texts = [[] for _ in range(nb)]
    for m in msgs:
        t = float(m.get("t", 0))
        if t < t_min or t > t_max:
            continue
        i = int((t - t_min) // BIN)
        users[i].add(m.get("user", "?"))
        texts[i].append((m.get("text") or "").strip())
    uniq = np.array([len(u) for u in users], dtype=float)
    spam = np.zeros(nb, dtype=bool)
    react = np.zeros(nb)
    labels = [""] * nb
    for i, tx in enumerate(texts):
        if not tx:
            continue
        toks = [t.lower() for t in tx]
        common, cnt = collections.Counter(toks).most_common(1)[0]
        digit_only = sum(1 for t in toks if DIGITS.match(t))
        if len(tx) >= 6 and ((cnt / len(tx) >= 0.5 and (SPAM_TOKEN.match(common) or len(common) <= 2)) or digit_only / len(tx) >= 0.35
                             or any(GIVEAWAY.search(t) for t in toks)):
            spam[i] = True  # giveaway / number spam
        hits = collections.Counter()
        for t in tx:
            for k, rx in REACTIONS.items():
                if rx.search(t):
                    hits[k] += 1
        react[i] = min(1.0, sum(hits.values()) / max(len(tx), 1) * 1.5)
        labels[i] = ",".join(k for k, _ in hits.most_common(2))
    # rolling baseline of unique chatters: median over +-5 min
    base = np.zeros(nb)
    w = int(300 / BIN)
    for i in range(nb):
        lo, hi = max(0, i - w), min(nb, i + w + 1)
        seg = uniq[lo:hi]
        base[i] = max(2.0, float(np.median(seg[seg > 0])) if np.any(seg > 0) else 2.0)
    ratio = uniq / base
    ratio[spam] *= 0.15
    score = np.clip(ratio / 6.0, 0, 1) * (0.7 + 0.3 * react)
    return {"t_min": t_min, "bins": nb, "uniq": uniq, "ratio": ratio, "react": react, "spam": spam, "labels": labels, "score": score, "texts": texts}


# ------------------------------------------------------------ audio


def audio_curve(media: Path, offset: float, step: float = 0.5) -> dict:
    """RMS dB per `step` seconds and a voice-band energy ratio, via ffmpeg -> f32 pipe."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=1800).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if x.nbytes > 1.5e9:
        raise StepError("segment too long for audio analysis in one pass; fetch windows of <= 60 min")
    n = int(16000 * step)
    if len(x) < n * 4:
        raise StepError("audio too short for analysis")
    frames = x[: len(x) // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    # voice/scream band ratio via FFT (300-4000 Hz vs total)
    freqs = np.fft.rfftfreq(n, 1 / 16000)
    band = (freqs >= 300) & (freqs <= 4000)
    hi = (freqs > 2000) & (freqs <= 6000)
    win = np.hanning(n).astype(np.float32)
    voice = np.empty(len(frames), dtype=np.float32)
    bright = np.empty(len(frames), dtype=np.float32)
    for i0 in range(0, len(frames), 2000):  # chunked: a 60-min segment never needs > ~250 MB
        sp = np.abs(np.fft.rfft(frames[i0:i0 + 2000] * win, axis=1))
        tot = sp.sum(axis=1) + 1e-9
        voice[i0:i0 + 2000] = sp[:, band].sum(axis=1) / tot
        bright[i0:i0 + 2000] = sp[:, hi].sum(axis=1) / tot
    # loudness z-score against a rolling 60 s median
    k = int(60 / step)
    med = np.array([np.median(db[max(0, i - k): i + k + 1]) for i in range(len(db))])
    mad = np.median(np.abs(db - med)) + 1.0
    z = (db - med) / mad
    t = offset + np.arange(len(db)) * step + step / 2
    return {"t": t, "db": db, "z": z, "voice": voice, "bright": bright, "step": step}


def audio_bins(ac: dict, t_min: float, nb: int) -> tuple[np.ndarray, np.ndarray]:
    score = np.zeros(nb)
    peak_t = np.full(nb, np.nan)
    for i in range(nb):
        lo, hi = t_min + i * BIN, t_min + (i + 1) * BIN
        m = (ac["t"] >= lo) & (ac["t"] < hi)
        if not np.any(m):
            continue
        z = ac["z"][m]
        br = ac["bright"][m]
        j = int(np.argmax(z))
        s = np.clip(z[j] / 4.0, 0, 1) * (0.8 + 0.4 * float(br[j] > 0.25))  # screams/shouts are brighter
        score[i] = min(1.0, s)
        peak_t[i] = ac["t"][m][j]
    return score, peak_t


# ------------------------------------------------------------ frames / transcript


def frame_sheet(media: Path, local_peak: float, dst: Path) -> bool:
    """2x2 sheet of frames at peak-6, -2, +1, +5 seconds (fast seeks, one ffmpeg call)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dur = duration(media)
    times = [min(max(0.0, t), max(0.0, dur - 0.15)) for t in (local_peak - 6, local_peak - 2, local_peak + 1, local_peak + 5)]
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    for t in times:
        cmd += ["-ss", f"{t:.2f}", "-i", str(media)]
    fc = "".join(f"[{i}:v]scale=640:-2,setsar=1[v{i}];" for i in range(4)) + "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[o]"
    cmd += ["-filter_complex", fc, "-map", "[o]", "-frames:v", "1", "-q:v", "4", str(dst)]
    subprocess.run(cmd, capture_output=True, timeout=300)
    return dst.exists()


# ------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign")
    ap.add_argument("--source", required=True, help="sources/<platform>-<id> folder (relative to campaign or absolute)")
    ap.add_argument("--segment", help="segment file name inside <source>/segments (enables audio + transcript)")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-gap", type=float, default=45, help="seconds between candidate peaks")
    ap.add_argument("--lead", type=float, default=8)
    ap.add_argument("--tail", type=float, default=14)
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--language", help="default: campaign clip.language")
    a = ap.parse_args()
    campaign = find_campaign(a.campaign)
    cfg = load_yaml(campaign / "campaign.yaml")
    weights = {**DEFAULT_WEIGHTS, **(cfg.get("signals", {}) or {}).get("weights", {})}
    language = a.language or (cfg.get("clip", {}) or {}).get("language") or "en"
    sdir = Path(a.source) if Path(a.source).is_absolute() else campaign / a.source
    if not sdir.exists():
        raise StepError(f"source folder not found: {sdir}")
    msgs = load_chat(sdir / "chat.jsonl")
    seg_path = offset = seg_dur = None
    if a.segment:
        seg_path = sdir / "segments" / Path(a.segment).name
        if not seg_path.exists():
            raise StepError(f"segment not found: {seg_path}")
        m = re.match(r"(\d+)-(\d+)", seg_path.stem)
        offset = float(m.group(1)) if m else 0.0
        seg_dur = duration(seg_path)
        t_min, t_max = offset, offset + seg_dur
    else:
        if not msgs:
            raise StepError("no chat.jsonl and no --segment: nothing to analyse. Run chat.py first or pass a segment.")
        t_min, t_max = 0.0, max(m["t"] for m in msgs)
    msgs_in_window = [m for m in msgs if t_min <= float(m.get("t", 0)) <= t_max]
    cc = chat_curve(msgs, t_min, t_max) if msgs_in_window else None
    nb = cc["bins"] if cc else int(math.ceil((t_max - t_min) / BIN)) + 1
    chat_s = cc["score"] if cc else np.zeros(nb)
    audio_s, audio_peak = (np.zeros(nb), np.full(nb, np.nan))
    if seg_path:
        ac = audio_curve(seg_path, offset)
        audio_s, audio_peak = audio_bins(ac, t_min, nb)
    have = {"chat": bool(msgs_in_window), "audio": seg_path is not None, "speech": False}
    wsum = sum(w for k, w in weights.items() if have[k] and k != "speech") or 1.0
    total = (weights["chat"] * chat_s * have["chat"] + weights["audio"] * audio_s * have["audio"]) / wsum
    # agreement bonus when both signals fire in the same or neighbouring bin
    if have["chat"] and have["audio"]:
        agree = np.minimum(chat_s, np.maximum.reduce([audio_s, np.roll(audio_s, 1), np.roll(audio_s, -1)]))
        total = np.clip(total + 0.25 * agree, 0, 1)
    # peak picking with min gap
    order = np.argsort(-total)
    picked: list[int] = []
    for i in order:
        if total[i] <= 0.05:
            break
        if all(abs(i - j) * BIN >= a.min_gap for j in picked):
            picked.append(int(i))
        if len(picked) >= a.top:
            break
    cands = []
    max_len = float(cfg.get("clip", {}).get("max_seconds", 59))
    for rank, i in enumerate(sorted(picked, key=lambda k: -total[k]), 1):
        bin_t = t_min + i * BIN
        # chat lags the moment by a few seconds; audio peak is exact when available
        peak = float(audio_peak[i]) if not np.isnan(audio_peak[i]) else bin_t + BIN / 2 - 4.0
        start = max(t_min, peak - a.lead)
        end = min(t_max, peak + a.tail, start + max_len)
        cid = f"c{int(peak):06d}"
        c = {
            "id": cid, "rank": rank, "score": round(float(total[i]), 3),
            "components": {"chat": round(float(chat_s[i]), 3), "audio": round(float(audio_s[i]), 3), "speech": 0.0},
            "peak_t": round(peak, 2), "start_t": round(start, 2), "end_t": round(end, 2),
            "chat_label": cc["labels"][i] if cc else "", "chat_unique": int(cc["uniq"][i]) if cc else 0,
            "chat_ratio": round(float(cc["ratio"][i]), 2) if cc else 0, "chat_spam": bool(cc["spam"][i]) if cc else False,
            "chat_sample": [t for t in (cc["texts"][i] if cc else []) if len(t) > 2][:8],
            "segment": seg_path.name if seg_path else None, "offset_s": offset,
        }
        if seg_path:
            local_peak = peak - offset
            sheet = sdir / "frames" / f"{cid}.jpg"
            if frame_sheet(seg_path, local_peak, sheet):
                c["frames"] = sheet.relative_to(campaign).as_posix()
            if not a.no_transcribe:
                from transcribe import transcribe_window

                names = [cfg.get("creator", {}).get("name", "")] + list((cfg.get("clip", {}) or {}).get("names", []) or [])
                try:
                    tr = transcribe_window(str(seg_path), max(0.0, start - offset), min(seg_dur, end - offset + 4),
                                           None if language == "auto" else language, "scan", [n for n in names if n])
                    c["transcript"] = tr["text"]
                    c["words"] = [{**w, "start": round(w["start"] + start, 2), "end": round(w["end"] + start, 2)} for w in tr["words"]]
                    c["transcript_engine"] = tr["engine"]
                    hits = sum(1 for k, rx in REACTIONS.items() if rx.search(tr["text"]))
                    excl = tr["text"].count("!") + len(re.findall(r"\b[A-Z]{3,}\b", tr["text"]))
                    c["components"]["speech"] = round(min(1.0, 0.3 * hits + 0.08 * excl), 3)
                    c["score"] = round(min(1.0, c["score"] * (1 - weights["speech"]) + weights["speech"] * c["components"]["speech"]), 3)
                    if PROMO.search(tr["text"]):
                        c["promo"] = True  # like/subscribe/giveaway talk: rarely a clip
                        c["score"] = round(c["score"] * 0.5, 3)
                except StepError as e:
                    c["transcript_error"] = str(e)[:200]
        cands.append(c)
    cands.sort(key=lambda c: -c["score"])
    for r, c in enumerate(cands, 1):
        c["rank"] = r
    strong = [c for c in cands if c["score"] >= 0.55]
    solid = [c for c in cands if 0.35 <= c["score"] < 0.55]
    existing = read_json(sdir / "candidates.json", {"candidates": []}).get("candidates", [])
    final: list[dict] = []
    used: set[int] = set()
    for c in cands:
        match_i = next((i for i, v in enumerate(existing) if i not in used and (v["id"] == c["id"] or abs(v["peak_t"] - c["peak_t"]) < 20)), None)
        if match_i is not None:
            used.add(match_i)
            old = existing[match_i]
            if old.get("agent"):
                c["agent"] = old["agent"]  # agent notes/decisions survive re-runs
            if old.get("segment") and not c.get("segment"):
                # a chat-only pass must not replace a candidate that already has audio/transcript/frames
                old["components"]["chat"] = c["components"]["chat"]
                old["chat_sample"], old["chat_label"] = c["chat_sample"], c["chat_label"]
                c = old
        final.append(c)
    final += [v for i, v in enumerate(existing) if i not in used]
    merged = sorted(final, key=lambda c: -c["score"])
    write_json(sdir / "candidates.json", {"updated": now_iso(), "weights": weights, "have": have,
                                           "window": {"t_min": t_min, "t_max": t_max}, "candidates": merged})
    out({
        "ok": True, "candidates_file": str(sdir / "candidates.json"), "analysed_window_s": [t_min, t_max], "signals_used": have,
        "suggested_clip_count": {"strong": len(strong), "solid": len(solid),
                                 "note": "strong = make now; solid = agent judges from frames/transcript; below 0.35 skip unless chat says 'clip it'"},
        "candidates": [{k: c[k] for k in ("id", "rank", "score", "components", "peak_t", "start_t", "end_t", "chat_label", "chat_unique", "chat_ratio", "chat_spam")}
                       | {"transcript": (c.get("transcript") or "")[:160], "chat_sample": c["chat_sample"][:4], "frames": c.get("frames")} for c in cands],
    })


if __name__ == "__main__":
    main_guard(main)
