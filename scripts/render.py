#!/usr/bin/env python3
"""Render a 1080x1920 clip from a spec: layout, hook, word-pop captions, overlay, bleeps, zoom, loudness, optional voiceover.

  render.py spec  --campaign <dir> --source sources/twitch-123 --candidate c008209 --hook "Kreek just left Jason behind"
                  [--start 8201 --end 8224] [--zoom-at 8211] [--out-dir 2026-11-21/01-left-jason] [--voiceover "text"]
  render.py build --spec <dir>/spec.json

`spec` writes spec.json from candidates.json + campaign defaults; the agent may edit it, then `build` renders clip.mp4.
Layouts: face-top (face panel + gameplay), fill (gameplay fills 9:16), fit (whole frame over blur). See references/render.md.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from common import FONTS_DIR, StepError, duration, ffmpeg_bin, find_campaign, hms, hw_encoder, load_yaml, main_guard, out, probe, read_json, run, slug, write_json

W, H = 1080, 1920
FPS = 30
FONT = "Anton"
PROFANITY = re.compile(r"^(shit\w*|fuck\w*|bitch\w*|ass(hole)?s?|dick\w*|pussy|cunt\w*|nigg\w*|fag\w*|motherf\w*|bastard\w*|whore\w*|slut\w*|cocks?|tits|retard\w*|goddamn)$", re.I)
FILLER_HOOK = re.compile(r"^(did you know|in this video|hey guys|wait for it|you won'?t believe)", re.I)
# Universal safe zone (inside TikTok / Reels / Shorts UI): x 60..930, y 230..1420 on the 1080x1920 canvas.
# Measured guidance: TikTok caption block ~bottom 22 %, like/share column ~right 14 %, top tabs ~10-12 %.
SAFE = {"left": 60, "right": 150, "top": 230, "bottom": 500}
# vertical zones (see references/render.md). Readable things (overlay, face, hook, captions) stay inside SAFE.
ZONE = {"overlay_y": 305, "face_top": 390, "face_h": 330, "game_top": 740, "game_h": 820, "caption_margin_v": 540, "hook_y": 800}
TEXT_ML, TEXT_MR = SAFE["left"] + 10, SAFE["right"] + 10  # ASS margins so text never enters the right UI column


def mask_word(w: str) -> str:
    core = re.sub(r"[^A-Za-z']", "", w)
    if core and PROFANITY.match(core):
        tail = re.sub(r"[A-Za-z']", "", w)
        return core[0] + "*" * (len(core) - 1) + tail
    return w


def ass_time(t: float) -> str:
    return hms(t)


def wrap_hook(text: str, max_width_px: int = W - TEXT_ML - TEXT_MR - 40) -> tuple[list[str], int]:
    """Pick a font size (84..58) and wrap to <=2 lines. Anton averages ~0.5 em per char."""
    text = " ".join(text.split())
    for size in (84, 76, 68, 62, 58):
        per_line = int(max_width_px / (size * 0.52))
        words, lines, cur = text.split(), [], ""
        for w in words:
            if len(cur) + len(w) + (1 if cur else 0) <= per_line:
                cur = f"{cur} {w}".strip()
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) <= 2 and all(len(l) <= per_line for l in lines):
            return lines, size
    raise StepError("hook too long to fit in 2 lines; shorten it (<= ~40 chars)")


def build_ass(words: list[dict], clip_start: float, clip_len: float, hook: str | None, hook_seconds: float, captions: str, mask: bool) -> str:
    ev = []
    if captions != "none" and words:
        rel = [(mask_word(w["word"]) if mask else w["word"], max(0.0, w["start"] - clip_start), max(0.0, w["end"] - clip_start)) for w in words
               if clip_start - 0.2 <= w["start"] <= clip_start + clip_len]
        rel = [(t.upper(), s, e) for t, s, e in rel if t.strip()]
        i = 0
        while i < len(rel):
            grp = rel[i:i + 3]
            grp_end_cap = rel[i + 3][1] if i + 3 < len(rel) else clip_len  # never overlap the next phrase
            for k, (_, s, e) in enumerate(grp):
                nxt = grp[k + 1][1] if k + 1 < len(grp) else min(max(e, s + 0.35), s + 1.0, grp_end_cap)
                if nxt <= s:
                    nxt = s + 0.12
                if captions == "word_pop":
                    parts = [("{\\c&H00FFFF&\\fscx112\\fscy112}" + t + "{\\c&HFFFFFF&\\fscx100\\fscy100}") if j == k else t for j, (t, _, _) in enumerate(grp)]
                    ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(min(nxt, clip_len))},Cap,,0,0,0,,{' '.join(parts)}")
                else:  # phrase
                    if k == 0:
                        end = min(max(grp[-1][2], grp[-1][1] + 0.4), clip_len, grp_end_cap)
                        ev.append(f"Dialogue: 0,{ass_time(s)},{ass_time(end)},Cap,,0,0,0,,{' '.join(t for t, _, _ in grp)}")
            i += 3
    hook_style = ""
    if hook:
        lines, size = wrap_hook(hook)
        hook_style = f"Style: Hook,{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,0,0,0,0,100,100,0,0,3,16,0,8,{TEXT_ML},{TEXT_MR},{ZONE['hook_y']},1\n"
        txt = "\\N".join(l.upper() for l in lines)
        ev.append(f"Dialogue: 1,{ass_time(0)},{ass_time(min(hook_seconds, clip_len))},Hook,,0,0,0,,{{\\fad(120,160)\\t(0,160,\\fscx106\\fscy106)}}{txt}")
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,9,3,2,{TEXT_ML},{TEXT_MR},{ZONE['caption_margin_v']},1
{hook_style}[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(ev) + "\n"


def overlay_bbox(png: Path) -> tuple[int, int, int, int]:
    from PIL import Image

    im = Image.open(png).convert("RGBA")
    bb = im.getchannel("A").point(lambda p: 255 if p > 8 else 0).getbbox()
    return bb or (0, 0, im.width, im.height)


def build_filter(spec: dict, src_w: int, src_h: int, ass_path: Path, overlay_png: Path | None, clip_len: float, bleeps: list[tuple[float, float]], vo: Path | None) -> tuple[str, list[str]]:
    layout = spec["layout"]
    fc = spec.get("facecam_region")
    zoom = spec.get("zoom_at_rel")
    inputs: list[str] = []
    f = [f"[0:v]fps={FPS},format=yuv420p,split=3[bgsrc][gsrc][fsrc]"]
    f.append(f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=42,eq=brightness=-0.15:saturation=1.1[bg]")
    # gameplay crop: avoid the facecam strip when it sits on the left
    if layout == "face-top":
        gh = ZONE["game_h"]
        aspect = W / gh
        ch = src_h
        cw = int(min(src_w, ch * aspect)) // 2 * 2
        cx = (src_w - cw) // 2
        if fc and fc["x"] < 0.5 and fc["y"] < 0.5:
            cx = max(cx, int(math.ceil((fc["x"] + fc["w"]) * src_w + 0.06 * src_w)))  # alerts spill right of the cam
            cx = min(cx, src_w - cw)
        f.append(f"[gsrc]crop={cw}:{ch}:{cx}:0,scale={W}:{gh}:flags=lanczos,setsar=1[g0]")
        gy = ZONE["game_top"]
    elif layout == "fill":
        ch = src_h
        cw = int(ch * W / H) // 2 * 2
        cx = (src_w - cw) // 2
        f.append(f"[gsrc]crop={cw}:{ch}:{cx}:0,scale={W}:{H}:flags=lanczos,setsar=1[g0]")
        gy, gh = 0, H
    else:  # fit
        gh = int(W * src_h / src_w) // 2 * 2
        f.append(f"[gsrc]scale={W}:{gh}:flags=lanczos,setsar=1[g0]")
        gy = (H - gh) // 2
    if zoom is not None and spec.get("zoom", True):
        n0 = int(zoom * FPS)
        n1, n2 = n0 + 8, n0 + 8 + int(1.4 * FPS)
        z = f"if(between(on,{n0},{n1}),1+0.16*(on-{n0})/8,if(between(on,{n1},{n2}),1.16,if(between(on,{n2},{n2 + 8}),1.16-0.16*(on-{n2})/8,1)))"
        f.append(f"[g0]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{gh}:fps={FPS}[g]")
    else:
        f.append("[g0]null[g]")
    f.append(f"[bg][g]overlay=0:{gy}[c1]")
    last = "c1"
    if layout == "face-top" and fc:
        fw, fh = int(fc["w"] * src_w) // 2 * 2, int(fc["h"] * src_h) // 2 * 2
        fx, fy = int(fc["x"] * src_w), int(fc["y"] * src_h)
        ph = ZONE["face_h"]
        pw = int(fw * ph / fh) // 2 * 2
        px = (W - pw) // 2
        f.append(f"[fsrc]crop={fw}:{fh}:{fx}:{fy},scale={pw}:{ph}:flags=lanczos,setsar=1,"
                 f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.9:t=6[face]")
        f.append(f"[{last}][face]overlay={px}:{ZONE['face_top']}[c2]")
        last = "c2"
    if overlay_png:
        x0, y0, x1, y1 = overlay_bbox(overlay_png)
        ow = int(spec["overlay"]["width_pct"] * W) // 2 * 2
        oh = int(ow * (y1 - y0) / (x1 - x0)) // 2 * 2
        oy = int(spec["overlay"]["y_pct"] * H) - oh // 2 if spec["overlay"].get("position", "top") == "top" else H - 140 - oh
        inputs += ["-loop", "1", "-i", str(overlay_png)]
        f.append(f"[1:v]crop={x1 - x0}:{y1 - y0}:{x0}:{y0},scale={ow}:{oh}[ov]")
        f.append(f"[{last}][ov]overlay=(W-w)/2:{oy}:shortest=1[c3]")
        last = "c3"
    f.append(f"[{last}]ass='{str(ass_path).replace(chr(39), chr(92) + chr(39))}':fontsdir='{FONTS_DIR}'[v]")
    # audio
    expr = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in bleeps) or "0"
    chain = [f"[0:a]aresample=48000,volume='if({expr},0,1)':eval=frame[a0]"]
    mix = ["[a0]"]
    if bleeps:
        chain.append(f"aevalsrc='0.2*sin(2*PI*1000*t)*({expr})':s=48000:d={clip_len:.2f}[beep]")
        mix.append("[beep]")
    if vo:
        vo_idx = 2 if overlay_png else 1
        inputs += ["-i", str(vo)]
        delay = int(spec.get("voiceover", {}).get("at", 0.0) * 1000)
        vo_len = duration(vo)
        a, b = delay / 1000, delay / 1000 + vo_len
        chain[0] = f"[0:a]aresample=48000,volume='if({expr},0,if(between(t,{a:.2f},{b:.2f}),0.25,1))':eval=frame[a0]"
        chain.append(f"[{vo_idx}:a]aresample=48000,adelay={delay}|{delay},volume=1.0[vo]")
        mix.append("[vo]")
    if len(mix) > 1:
        chain.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first[am]")
    else:
        chain.append("[a0]anull[am]")
    chain.append("[am]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    return ";".join(f + chain), inputs


def out_dir_for(spec_path: Path) -> Path:
    return spec_path.parent


def cmd_spec(a) -> None:
    campaign = find_campaign(a.campaign)
    cfg = load_yaml(campaign / "campaign.yaml")
    sdir = Path(a.source) if Path(a.source).is_absolute() else campaign / a.source
    cands = read_json(sdir / "candidates.json", {}).get("candidates", [])
    c = next((x for x in cands if x["id"] == a.candidate), None)
    if not c:
        raise StepError(f"candidate {a.candidate} not in {sdir / 'candidates.json'}")
    if not c.get("segment"):
        raise StepError("candidate has no segment yet: run fetch.py for its time range, then signals.py --segment")
    start = float(a.start) if a.start else c["start_t"]
    end = float(a.end) if a.end else c["end_t"]
    clip = cfg["clip"]
    if end - start < clip["min_seconds"] or end - start > clip["max_seconds"]:
        raise StepError(f"clip length {end - start:.1f}s outside campaign limits {clip['min_seconds']}-{clip['max_seconds']}s")
    if FILLER_HOOK.match(a.hook or ""):
        raise StepError("hook starts with a filler opener; see references/hooks.md")
    out_dir = Path(a.out_dir) if a.out_dir and Path(a.out_dir).is_absolute() else campaign / (a.out_dir or f"{cfg.get('created')}/draft-{slug(a.hook or c['id'])}")
    out_dir.mkdir(parents=True, exist_ok=True)
    words = c.get("words") or []
    spec = {
        "campaign": str(campaign), "source_dir": str(sdir), "source": str(sdir / "segments" / c["segment"]), "offset_s": c["offset_s"],
        "candidate": c["id"], "start_t": round(start, 2), "end_t": round(end, 2), "peak_t": c["peak_t"],
        "hook": a.hook, "hook_seconds": float(cfg["clip"].get("hook_seconds", 2)) + 0.6,
        "layout": a.layout or clip.get("layout", "face-top"), "facecam_region": clip.get("facecam_region"),
        "zoom": bool(clip.get("zoom_at_peak", True)), "zoom_at_rel": round((float(a.zoom_at) if a.zoom_at else c["peak_t"]) - start, 2),
        "captions": clip.get("captions", "word_pop"), "mask_profanity": bool(clip.get("mask_profanity", True)), "bleep_profanity": bool(clip.get("bleep_profanity", True)),
        "audio_mode": "voiceover" if a.voiceover else "original",
        "voiceover": {"text": a.voiceover, "at": 0.0, "voice_id": cfg["audio"].get("voice_id")} if a.voiceover else None,
        "overlay": {**cfg["overlay"], "file": str(campaign / cfg["overlay"]["file"]) if cfg["overlay"].get("file") else None},
        "words": words, "words_locked": False, "creator_name": cfg.get("creator", {}).get("name", ""), "names": list(clip.get("names", []) or []),
        "language": clip.get("language", "en"), "out": str(out_dir / "clip.mp4"),
    }
    if spec["audio_mode"] == "voiceover" and cfg["audio"].get("mode") != "voiceover_allowed":
        raise StepError("campaign audio.mode is 'original': voiceover not allowed for this campaign")
    write_json(out_dir / "spec.json", spec)
    out({"ok": True, "spec": str(out_dir / "spec.json"), "clip_len_s": round(end - start, 2), "words": len(words),
         "note": "edit spec.json if needed (start_t/end_t/hook/zoom_at_rel), then: render.py build --spec ..."})


def cmd_build(a) -> None:
    spec_path = Path(a.spec)
    spec = read_json(spec_path)
    if not spec:
        raise StepError(f"spec not found: {spec_path}")
    src = Path(spec["source"])
    if not src.exists():
        raise StepError(f"source missing: {src}")
    info = probe(src)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    src_w, src_h = int(vs["width"]), int(vs["height"])
    local_start = spec["start_t"] - spec["offset_s"]
    clip_len = spec["end_t"] - spec["start_t"]
    if local_start < 0 or local_start + clip_len > duration(src) + 0.5:
        raise StepError("clip range falls outside the fetched segment; fetch a wider range")
    words = spec.get("words") or []
    if spec.get("captions", "word_pop") != "none" and not spec.get("words_locked"):
        # Final captions always get the thorough free pass on the exact clip window (denoise + 2 Whisper passes merged).
        from transcribe import transcribe_window

        names = [n for n in ([spec.get("creator_name", "")] + list(spec.get("names") or [])) if n]
        tr = transcribe_window(str(src), local_start, local_start + clip_len, spec.get("language") or "en", "final", names)
        words = [{**w, "start": round(w["start"] + spec["start_t"], 2), "end": round(w["end"] + spec["start_t"], 2)} for w in tr["words"]]
        spec["words"] = words
        spec["transcript"] = {"engine": tr["engine"], "text": tr["text"], "second_pass_text": tr.get("second_pass_text", ""),
                              "gaps_rel": tr.get("gaps", []), "merged_from_second_pass": tr.get("merged_from_second_pass", 0)}
        write_json(out_dir_for(spec_path) / "transcript.json", {**tr, "clip_start_t": spec["start_t"],
                   "how_to_fix": "Edit spec.json 'words' (text/start/end, times are stream seconds), set \"words_locked\": true, re-run render.py build."})
    bleeps = []
    if spec.get("bleep_profanity", True):
        for w in words:
            if PROFANITY.match(re.sub(r"[^A-Za-z']", "", w["word"])) and spec["start_t"] <= w["start"] <= spec["end_t"]:
                s = w["start"] - spec["start_t"] - 0.05
                bleeps.append((max(0.0, s), min(clip_len, s + min(0.9, max(0.35, w["end"] - w["start"] + 0.1)))))
    out_dir = spec_path.parent
    ass_path = out_dir / "captions.ass"
    ass_path.write_text(build_ass(words, spec["start_t"], clip_len, spec.get("hook"), spec.get("hook_seconds", 2.6), spec.get("captions", "word_pop"), spec.get("mask_profanity", True)), encoding="utf-8")
    overlay_png = Path(spec["overlay"]["file"]) if spec.get("overlay", {}).get("file") else None
    if overlay_png and not overlay_png.exists():
        raise StepError(f"overlay file missing: {overlay_png}")
    vo = None
    if spec.get("audio_mode") == "voiceover" and spec.get("voiceover", {}).get("text"):
        from voiceover import ensure_voice, tts

        ensure_voice(spec["voiceover"]["voice_id"])
        vo = tts(spec["voiceover"]["text"], spec["voiceover"]["voice_id"], Path(spec["campaign"]) / "voiceover_cache")
    fc, extra_inputs = build_filter(spec, src_w, src_h, ass_path, overlay_png, clip_len, bleeps, vo)
    out_path = Path(spec["out"])
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{local_start:.3f}", "-t", f"{clip_len:.3f}", "-i", str(src), *extra_inputs,
           "-filter_complex", fc, "-map", "[v]", "-map", "[aout]", "-t", f"{clip_len:.3f}", *hw_encoder(), "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out_path)]
    run(cmd, timeout=1800)
    spec["rendered"] = {"file": str(out_path), "duration_s": round(duration(out_path), 2), "bleeps": bleeps, "words_used": len(words)}
    write_json(spec_path, spec)
    gaps = (spec.get("transcript") or {}).get("gaps_rel", [])
    out({"ok": True, **spec["rendered"], "captions_ass": str(ass_path), "transcript_engine": (spec.get("transcript") or {}).get("engine"),
         "caption_gaps_s": gaps,
         "agent_check": "Read transcript.json: compare text vs second_pass_text (disagreements = likely mishearings), fix misheard words (names), "
                        "remove NPC/game-dialogue lines if they confuse the clip, add words for real speech inside caption_gaps_s only when frames/chat make "
                        "them certain. Edit spec.json words, set words_locked true, re-build.",
         "next": "review.py --clip-dir " + str(out_dir)})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spec")
    s.add_argument("--campaign")
    s.add_argument("--source", required=True)
    s.add_argument("--candidate", required=True)
    s.add_argument("--hook", required=True)
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--zoom-at")
    s.add_argument("--layout", choices=["face-top", "fill", "fit"])
    s.add_argument("--out-dir")
    s.add_argument("--voiceover")
    s.set_defaults(fn=cmd_spec)
    b = sub.add_parser("build")
    b.add_argument("--spec", required=True)
    b.set_defaults(fn=cmd_build)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main_guard(main)
