#!/usr/bin/env python3
"""Package a reviewed clip: thumbnail, SRT, per-platform post text, checklist, metadata; update index.csv and the day README.

  package.py --clip-dir <campaign>/2026-11-21/01-left-jason-behind [--title "..."] [--post-at "2026-11-21T21:30+05:30"] [--audience "US evening"]

Requires review.json with ok=true (run review.py first). Caption text is built from campaign.yaml caption.template.
The posting time is a suggestion the agent fills after research (references/posting.md); this script just records it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
from pathlib import Path

from common import StepError, ffmpeg_bin, find_campaign, hms, load_yaml, main_guard, now_iso, out, read_json, write_json

PLATFORM_LIMITS = {"tiktok": 2200, "instagram_reels": 2200, "youtube_shorts": 100}  # chars; shorts title limit


def srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round((t - int(t)) * 1000))
    return f"{int(t) // 3600:02d}:{int(t) % 3600 // 60:02d}:{int(t) % 60:02d},{ms:03d}"


def write_srt(words: list[dict], start: float, end: float, dst: Path) -> int:
    rel = [(w["word"], w["start"] - start, w["end"] - start) for w in words if start <= w["start"] <= end]
    lines, i, n = [], 0, 0
    while i < len(rel):
        grp = rel[i:i + 4]
        s, e = grp[0][1], min(max(grp[-1][2], grp[-1][1] + 0.4), end - start)
        if i + 4 < len(rel):
            e = min(e, rel[i + 4][1])
        n += 1
        lines.append(f"{n}\n{srt_time(s)} --> {srt_time(e)}\n{' '.join(t for t, _, _ in grp)}\n")
        i += 4
    dst.write_text("\n".join(lines), encoding="utf-8")
    return n


def build_caption(cfg: dict, hook: str, platform: str) -> str:
    cap = cfg["caption"]
    tags = " ".join(cap.get("hashtags", []))
    text = cap.get("template", "{hook}\n\n{required_text} {hashtags}").replace("\\n", "\n").format(
        hook=hook, required_text=cap.get("required_text", ""), hashtags=tags).strip()
    if cap.get("no_links") and re.search(r"https?://|www\.", text):
        raise StepError("caption contains a link but the campaign forbids links")
    limit = PLATFORM_LIMITS.get(platform, 2200)
    if platform == "youtube_shorts":
        title = hook if len(hook) <= 90 else hook[:87] + "..."
        return f"TITLE: {title}\n\nDESCRIPTION:\n{text}"
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip-dir", required=True)
    ap.add_argument("--title")
    ap.add_argument("--post-at", help="ISO time with offset, e.g. 2026-11-21T21:30+05:30 (agent-researched)")
    ap.add_argument("--audience", default="")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    cdir = Path(a.clip_dir).expanduser().resolve()
    spec, review = read_json(cdir / "spec.json"), read_json(cdir / "review.json")
    if not spec or not (cdir / "clip.mp4").exists():
        raise StepError("spec.json / clip.mp4 missing: render first")
    if not review or not review.get("ok"):
        raise StepError("review.json missing or failed: run review.py and fix hard failures first")
    campaign = find_campaign(spec["campaign"])
    cfg = load_yaml(campaign / "campaign.yaml")
    hook = a.title or spec.get("hook") or ""
    clip = cdir / "clip.mp4"
    # thumbnail: frame while the hook is on screen
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", "1.0", "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(cdir / "thumb.jpg")],
                   capture_output=True, timeout=120)
    n_srt = write_srt(spec.get("words", []), spec["start_t"], spec["end_t"], cdir / "captions.srt")
    posts = {}
    for p in cfg.get("platforms", []):
        txt = build_caption(cfg, hook, p)
        (cdir / f"post_{p}.txt").write_text(txt + "\n", encoding="utf-8")
        posts[p] = txt
    rules = cfg.get("rules", {})
    ov = cfg.get("overlay", {})
    checklist = [
        f"- [ ] Post from the mobile app (first 10 posts on a new account; see warm-up rules)",
        f"- [ ] Caption pasted from post_<platform>.txt (required text + exact hashtags, no links)" if cfg["caption"].get("required_text") else "- [ ] Caption pasted from post_<platform>.txt",
        f"- [ ] Overlay visible start to end (review passed: {review.get('ok')})" if ov.get("required") else "- [ ] Branding per brief",
        f"- [ ] Likes visible, post public, keep up >= {rules.get('keep_public_days', 30)} days" if rules.get("likes_visible") else "- [ ] Post public",
        f"- [ ] Submit the post link to the campaign within {rules.get('submit_within_minutes', 30)} minutes of posting",
        f"- [ ] Do not post this clip more than {rules.get('max_posts_per_clip', 5)} times across accounts",
        f"- [ ] Record analytics screen recording once views pass the campaign minimum (payout min ${cfg.get('payout', {}).get('min_payout_usd', 0)})",
        f"- [ ] Posting time: {a.post_at or 'TBD (agent researches; see references/posting.md)'}  audience: {a.audience or 'TBD'}",
    ]
    md = f"""# {hook}

Source: {spec.get('source_dir', '')} @ {hms(spec['start_t'])} - {hms(spec['end_t'])}  ({spec['end_t'] - spec['start_t']:.1f}s)
Candidate: {spec.get('candidate')}  Layout: {spec.get('layout')}  Audio: {spec.get('audio_mode')}

## Files
- clip.mp4, thumb.jpg, captions.srt ({n_srt} cues), spec.json, review.json, frames.jpg
- post_<platform>.txt: {', '.join(posts)}

## Checklist before posting
{chr(10).join(checklist)}

## Notes
{a.notes}
"""
    (cdir / "checklist.md").write_text(md, encoding="utf-8")
    clip_id = cdir.name
    date = cdir.parent.name
    meta = {
        "clip_id": clip_id, "date": date, "campaign": cfg.get("name"), "hook": hook, "source": spec.get("source_dir"), "candidate": spec.get("candidate"),
        "start_t": spec["start_t"], "end_t": spec["end_t"], "duration_s": review.get("duration_s"), "layout": spec.get("layout"), "audio_mode": spec.get("audio_mode"),
        "platforms": list(posts), "post_at": a.post_at, "audience": a.audience, "packaged": now_iso(), "status": "ready_to_post", "post_count": 0,
    }
    write_json(cdir / "metadata.json", meta)
    # index.csv
    idx = campaign / "index.csv"
    rows = list(csv.DictReader(open(idx, encoding="utf-8"))) if idx.exists() else []
    header = rows[0].keys() if rows else None
    from campaign import INDEX_HEADER

    cand = read_json(Path(spec["source_dir"]) / "candidates.json", {}).get("candidates", [])
    c = next((x for x in cand if x["id"] == spec.get("candidate")), {})
    row = {k: "" for k in INDEX_HEADER}
    row.update({"clip_id": clip_id, "date": date, "source_platform": Path(spec["source_dir"]).name.split("-")[0], "source_id": Path(spec["source_dir"]).name.split("-", 1)[-1],
                "start_s": spec["start_t"], "end_s": spec["end_t"], "duration_s": review.get("duration_s"), "hook": hook, "score": c.get("score", ""),
                "signals": json.dumps(c.get("components", {})), "layout": spec.get("layout"), "audio_mode": spec.get("audio_mode"), "status": "ready_to_post",
                "post_count": 0, "notes": a.notes})
    rows = [r for r in rows if r.get("clip_id") != clip_id] + [row]
    with open(idx, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in INDEX_HEADER})
    # day README table line
    readme = cdir.parent / "README.md"
    if readme.exists():
        txt = readme.read_text(encoding="utf-8")
        line = f"| {clip_id.split('-')[0]} | {clip_id} | {hook} | {Path(spec['source_dir']).name} @ {hms(spec['start_t'])} | {c.get('score', '')} | ready_to_post |"
        if line not in txt:
            marker = "|---|--------|------|---------------|-------|--------|"
            txt = txt.replace(marker, marker + "\n" + line, 1) if marker in txt else txt + "\n" + line
            readme.write_text(txt, encoding="utf-8")
    out({"ok": True, "clip_dir": str(cdir), "files": sorted(p.name for p in cdir.iterdir()), "posts": posts, "index": str(idx)})


if __name__ == "__main__":
    main_guard(main)
