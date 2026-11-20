#!/usr/bin/env python3
"""Quality check of a rendered clip against the campaign rules. Writes review.json and a frame sheet for the agent to look at.

  review.py --clip-dir <campaign>/2026-11-21/01-left-jason-behind

Checks: duration limits, 1080x1920 @30fps, overlay visible on every sampled frame (pixel match), loudness (-14 LUFS +-2),
hook present in first seconds, captions present, profanity masked, caption text rules (from package later).
PASS/FAIL per check; the agent must still watch the frame sheet (frames.jpg) before approving.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import numpy as np

from common import StepError, duration, ffmpeg_bin, find_campaign, load_yaml, main_guard, out, probe, read_json, write_json

W, H = 1080, 1920


def measure_loudness(path: Path) -> float | None:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    m = re.search(r"\{.*\}", r.stderr, re.S)
    if not m:
        return None
    try:
        return float(json.loads(m.group(0))["input_i"])
    except Exception:
        return None


def grab(path: Path, t: float) -> np.ndarray:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, timeout=120)
    arr = np.frombuffer(r.stdout, dtype=np.uint8)
    if arr.size < W * H * 3:
        raise StepError(f"could not grab frame at {t}s")
    return arr[: W * H * 3].reshape(H, W, 3)


def overlay_reference(spec: dict) -> tuple[np.ndarray, np.ndarray, int, int] | None:
    """Return (rgb, alpha, x, y) of the overlay as rendered, or None if the campaign has no overlay."""
    f = spec.get("overlay", {}).get("file")
    if not f or not Path(f).exists():
        return None
    from PIL import Image

    im = Image.open(f).convert("RGBA")
    bb = im.getchannel("A").point(lambda p: 255 if p > 8 else 0).getbbox() or (0, 0, im.width, im.height)
    im = im.crop(bb)
    ow = int(spec["overlay"]["width_pct"] * W) // 2 * 2
    oh = int(ow * im.height / im.width) // 2 * 2
    im = im.resize((ow, oh))
    oy = int(spec["overlay"]["y_pct"] * H) - oh // 2 if spec["overlay"].get("position", "top") == "top" else H - 140 - oh
    ox = (W - ow) // 2
    a = np.asarray(im)
    return a[:, :, :3].astype(np.float32), a[:, :, 3].astype(np.float32) / 255.0, ox, oy


def overlay_visible(frame: np.ndarray, ref) -> float:
    rgb, alpha, ox, oy = ref
    h, w = alpha.shape
    region = frame[oy:oy + h, ox:ox + w, :].astype(np.float32)
    mask = alpha > 0.6
    if mask.sum() < 50:
        return 0.0
    diff = np.abs(region[mask] - rgb[mask]).mean(axis=1)
    return float((diff < 60).mean())  # share of opaque overlay pixels that match


def frame_sheet(path: Path, times: list[float], dst: Path, safe: dict | None = None) -> None:
    """Frames side by side; with `safe`, draws the platform-UI safe box (green) so the eye can check nothing readable is outside it."""
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    for t in times:
        cmd += ["-ss", f"{t:.2f}", "-i", str(path)]
    n = len(times)
    box = ""
    if safe:
        x, y = safe["left"], safe["top"]
        w, h = W - safe["left"] - safe["right"], H - safe["top"] - safe["bottom"]
        box = f"drawbox=x={x}:y={y}:w={w}:h={h}:color=lime@0.9:t=6,"
    fc = "".join(f"[{i}:v]{box}scale=270:-2,setsar=1[v{i}];" for i in range(n)) + "".join(f"[v{i}]" for i in range(n)) + f"hstack=inputs={n}[o]"
    cmd += ["-filter_complex", fc, "-map", "[o]", "-frames:v", "1", "-q:v", "3", str(dst)]
    subprocess.run(cmd, capture_output=True, timeout=300)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip-dir", required=True)
    a = ap.parse_args()
    cdir = Path(a.clip_dir).expanduser().resolve()
    spec = read_json(cdir / "spec.json")
    clip = cdir / "clip.mp4"
    if not spec or not clip.exists():
        raise StepError("clip-dir needs spec.json and clip.mp4 (run render.py build first)")
    campaign = find_campaign(spec.get("campaign") or cdir)
    cfg = load_yaml(campaign / "campaign.yaml")
    checks = []

    def add(name, ok, detail="", hard=True):
        checks.append({"check": name, "pass": bool(ok), "detail": detail, "hard": hard})

    dur = duration(clip)
    lim = cfg["clip"]
    add("duration", lim["min_seconds"] <= dur <= lim["max_seconds"], f"{dur:.1f}s (limits {lim['min_seconds']}-{lim['max_seconds']}s)")
    tgt = lim.get("target_seconds", [15, 35])
    add("duration_in_target", tgt[0] <= dur <= tgt[1], f"target {tgt[0]}-{tgt[1]}s", hard=False)
    info = probe(clip)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    add("format_1080x1920", int(vs["width"]) == W and int(vs["height"]) == H, f"{vs['width']}x{vs['height']}")
    add("fps_30", abs(eval(vs.get("r_frame_rate", "0/1")) - 30) < 0.6, vs.get("r_frame_rate"), hard=False)
    add("has_audio", any(s["codec_type"] == "audio" for s in info["streams"]))
    lufs = measure_loudness(clip)
    add("loudness_-14LUFS", lufs is not None and -16.5 <= lufs <= -11.5, f"{lufs:.1f} LUFS" if lufs is not None else "unmeasured", hard=False)
    times = [0.5, min(2.0, dur - 0.2), dur * 0.35, dur * 0.6, dur * 0.85, max(0.2, dur - 0.4)]
    ref = overlay_reference(spec)
    if cfg.get("overlay", {}).get("required"):
        if ref is None:
            add("overlay_visible_all_frames", False, "overlay file missing")
        else:
            scores = [round(overlay_visible(grab(clip, t), ref), 2) for t in times]
            add("overlay_visible_all_frames", min(scores) >= 0.85, f"match per sampled frame: {scores}")
    add("hook_present", bool(spec.get("hook")) and spec.get("hook_seconds", 0) >= 1.5, spec.get("hook", ""))
    add("captions_present", spec.get("captions") == "none" or (spec.get("rendered", {}).get("words_used", 0) >= 3),
        f"{spec.get('rendered', {}).get('words_used', 0)} words")
    ass = (cdir / "captions.ass").read_text(encoding="utf-8") if (cdir / "captions.ass").exists() else ""
    unmasked = re.findall(r"\b(shit|fuck\w*|bitch\w*)\b", ass, re.I)
    add("profanity_masked", not unmasked or not lim.get("mask_profanity", True), f"unmasked: {unmasked[:3]}" if unmasked else "")
    add("not_raw_recording", spec.get("layout") in ("face-top", "fill", "fit") and bool(spec.get("hook")) and spec.get("captions") != "none",
        "edit = layout + hook + captions (brief bans raw screen recordings)")
    # safe zone: every readable element must sit inside the box the platform UIs leave free
    from render import SAFE, ZONE, TEXT_ML, TEXT_MR

    issues = []
    if ref is not None:
        _, alpha, ox, oy = ref
        oh, ow = alpha.shape
        if ox < SAFE["left"] or ox + ow > W - SAFE["right"] or oy < SAFE["top"] or oy + oh > H - SAFE["bottom"]:
            issues.append(f"overlay box x{ox}-{ox + ow} y{oy}-{oy + oh}")
    if spec.get("layout") == "face-top" and spec.get("facecam_region"):
        if ZONE["face_top"] < SAFE["top"] or ZONE["face_top"] + ZONE["face_h"] > H - SAFE["bottom"]:
            issues.append("face panel")
    if ZONE["hook_y"] < SAFE["top"] or H - ZONE["caption_margin_v"] > H - SAFE["bottom"]:
        issues.append("text zones")
    if TEXT_ML < SAFE["left"] or TEXT_MR < SAFE["right"]:
        issues.append("text margins")
    add("inside_safe_zone", not issues, "outside: " + ", ".join(issues) if issues else f"all readable elements within x{SAFE['left']}-{W - SAFE['right']} y{SAFE['top']}-{H - SAFE['bottom']}")
    frame_sheet(clip, times, cdir / "frames.jpg")
    frame_sheet(clip, times, cdir / "frames_safe.jpg", SAFE)
    hard_fail = [c for c in checks if not c["pass"] and c["hard"]]
    soft_fail = [c for c in checks if not c["pass"] and not c["hard"]]
    report = {"ok": not hard_fail, "clip": str(clip), "duration_s": round(dur, 2), "checks": checks,
              "frames": str(cdir / "frames.jpg"), "frames_safe": str(cdir / "frames_safe.jpg"),
              "agent_must": ["look at frames_safe.jpg: everything readable (overlay, face, hook, captions) is inside the green box; face panel shows the streamer; hook readable; captions not covering the action",
                             "read transcript.json: captions complete? misheard names? fix spec.json words and re-build if needed",
                             "confirm the hook is literally true to the transcript/frames (brief bans fabricated context)",
                             "confirm the moment is entertaining on its own (setup -> payoff -> reaction)"]}
    write_json(cdir / "review.json", report)
    out({**report, "hard_failures": [c["check"] for c in hard_fail], "soft_failures": [c["check"] for c in soft_fail]})
    if hard_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main_guard(main)
