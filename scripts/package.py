#!/usr/bin/env python3
"""Package a reviewed clip for the user: README.md (what to do), one post_<platform>.md per platform, thumbnail, SRT, metadata;
update index.csv and the day README.

  package.py --clip-dir <campaign>/2026-11-21/01-left-jason-behind [--title "..."] [--post-at "2026-11-21T21:30+05:30"] [--audience "US East midday"] [--notes "..."]

Requires review.json with ok=true (run review.py first). Caption text comes from campaign.yaml caption.template.
The posting time is a suggestion the agent fills after research (references/posting.md).
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

PLATFORM_NAMES = {"tiktok": "TikTok", "instagram_reels": "Instagram Reels", "youtube_shorts": "YouTube Shorts", "x": "X (Twitter)"}
PLATFORM_STEPS = {
    "tiktok": ["Open TikTok on your phone, tap +, choose Upload, pick `clip.mp4`.",
               "Do not add TikTok's own captions (ours are burned in). Pick the cover from the first second.",
               "Paste the caption below. Keep comments and likes visible. Post."],
    "instagram_reels": ["Open Instagram, tap +, choose Reel, pick `clip.mp4`.",
                        "Skip Instagram's auto-captions. Set the cover from the first second (or use `thumb.jpg`).",
                        "Paste the caption below. Share to Reels (and feed if allowed). Keep likes visible."],
    "youtube_shorts": ["Open YouTube on your phone, tap +, choose Upload a video, pick `clip.mp4` (it is under 60 s, so it posts as a Short).",
                       "Title = the first line below. Description = the rest. Visibility Public.",
                       "Do not enable auto-dubbing or auto-chapters."],
    "x": ["Open X, start a post, attach `clip.mp4` (native upload, no link).", "Paste the caption below. Post."],
}


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


def build_caption(cfg: dict, hook: str) -> str:
    cap = cfg["caption"]
    tags = " ".join(cap.get("hashtags", []))
    text = cap.get("template", "{hook}\n\n{required_text} {hashtags}").replace("\\n", "\n").format(
        hook=hook, required_text=cap.get("required_text", ""), hashtags=tags).strip()
    if cap.get("no_links") and re.search(r"https?://|www\.", text):
        raise StepError("caption contains a link but the campaign forbids links")
    return text


def local_time(post_at: str | None, tz_name: str) -> str:
    if not post_at:
        return "to be decided (ask the agent for today's posting plan)"
    try:
        t = dt.datetime.fromisoformat(post_at)
        try:
            from zoneinfo import ZoneInfo

            t = t.astimezone(ZoneInfo(tz_name))
        except Exception:
            pass
        return t.strftime("%a %d %b %Y, %H:%M (%Z)")
    except Exception:
        return post_at


def post_md(platform: str, cfg: dict, hook: str, caption: str, meta: dict) -> str:
    name = PLATFORM_NAMES.get(platform, platform)
    rules = cfg.get("rules", {})
    steps = PLATFORM_STEPS.get(platform, ["Upload `clip.mp4` and paste the caption below."])
    if platform == "youtube_shorts":
        title = hook if len(hook) <= 90 else hook[:87] + "..."
        copy_block = f"**Title (copy):**\n\n```\n{title}\n```\n\n**Description (copy):**\n\n```\n{caption}\n```"
    else:
        copy_block = f"**Caption (copy everything inside the box):**\n\n```\n{caption}\n```"
    return f"""# Post to {name}

**When:** {local_time(meta.get('post_at'), cfg.get('user_timezone', 'UTC'))}  ·  **Audience:** {meta.get('audience') or 'see day README'}

## Steps
{chr(10).join(f'{i}. {s}' for i, s in enumerate(steps, 1))}
{len(steps) + 1}. Within **{rules.get('submit_within_minutes', 30)} minutes** of posting, copy the post link and submit it to the campaign. Then paste the link into `../../index.csv` (column `posted_{platform.split('_')[-1]}`) or tell the agent.

{copy_block}

## Rules for this post
- Post from the phone app (not a scheduler) if this account is new (first ~10 posts).
- Keep it public for at least {rules.get('keep_public_days', 30)} days. Likes must stay visible.
- This clip may be posted at most {rules.get('max_posts_per_clip', 5)} times in total across all accounts/platforms.
- Do not edit the caption text or hashtags: the campaign checks them exactly.
- Once the post passes about {int(1000 * float(cfg.get('payout', {}).get('min_payout_usd', 0) or 0) / max(float(cfg.get('payout', {}).get('cpm_usd', 1) or 1), 0.01)):,} views, record the analytics screen recording the campaign asks for.
"""


def clip_readme(cfg: dict, spec: dict, review: dict, meta: dict, posts: list[str], n_srt: int, tr: dict) -> str:
    dur = review.get("duration_s") or (spec["end_t"] - spec["start_t"])
    gaps = (spec.get("transcript") or {}).get("gaps_rel", [])
    checks = ", ".join(f"{c['check']} {'OK' if c['pass'] else 'FAIL'}" for c in review.get("checks", []) if c.get("hard"))
    return f"""# {spec.get('hook', meta['clip_id'])}

**Campaign:** {cfg.get('name')}  ·  **Length:** {dur:.0f} s  ·  **Status:** ready to post  ·  **Packaged:** {meta['packaged'][:16]}

## What this is
A {dur:.0f}-second vertical clip from {cfg.get('creator', {}).get('name', 'the streamer')}'s stream
({Path(spec.get('source_dir', '')).name} at {hms(spec['start_t'])} - {hms(spec['end_t'])}), edited for TikTok / Reels / Shorts:
face panel + gameplay, hook text for the first seconds, word-by-word captions, campaign overlay, swearing bleeped and masked, loudness normalised.

## What to do
1. Watch `clip.mp4` once.
2. Open the file for the platform you are posting to and follow it step by step:
{chr(10).join(f'   - `post_{p}.md`' for p in posts)}
3. After posting, submit the link to the campaign within {cfg.get('rules', {}).get('submit_within_minutes', 30)} minutes and record the link in `../../index.csv` (or tell the agent).

## Files
| File | What it is |
|---|---|
| `clip.mp4` | the video to upload (1080x1920, 30 fps) |
| `thumb.jpg` | cover image (frame with the hook) if the platform asks for one |
| `post_<platform>.md` | step-by-step posting instructions + the exact caption to copy |
| `captions.srt` | subtitles file ({n_srt} cues), only if you want to upload captions separately |
| `frames.jpg`, `frames_safe.jpg` | 6 frames of the clip; the green box in `frames_safe.jpg` is the area no app UI covers |
| `transcript.json` | what was said (engine: {tr.get('engine', '?')}); `gaps_rel` lists seconds where speech may be missing captions |
| `review.json` | automatic checks: {checks} |
| `spec.json`, `captions.ass`, `metadata.json` | how the clip was built (for the agent) |

## Caption notes
{'- Possible uncaptioned speech at (seconds into the clip): ' + ', '.join(f'{a:.0f}-{b:.0f}' for a, b in gaps) + '. The agent judged these as game noise or left them; tell the agent if a line is missing.' if gaps else '- Captions cover all detected speech.'}

## Notes
{meta.get('notes') or '-'}
"""


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
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", "1.0", "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(cdir / "thumb.jpg")],
                   capture_output=True, timeout=120)
    n_srt = write_srt(spec.get("words", []), spec["start_t"], spec["end_t"], cdir / "captions.srt")
    for old in cdir.glob("post_*.txt"):
        old.unlink()
    caption = build_caption(cfg, hook)
    clip_id, date = cdir.name, cdir.parent.name
    meta = {
        "clip_id": clip_id, "date": date, "campaign": cfg.get("name"), "hook": hook, "source": spec.get("source_dir"), "candidate": spec.get("candidate"),
        "start_t": spec["start_t"], "end_t": spec["end_t"], "duration_s": review.get("duration_s"), "layout": spec.get("layout"), "audio_mode": spec.get("audio_mode"),
        "platforms": list(cfg.get("platforms", [])), "post_at": a.post_at, "audience": a.audience, "notes": a.notes, "packaged": now_iso(),
        "status": "ready_to_post", "post_count": 0, "caption": caption,
    }
    for p in cfg.get("platforms", []):
        (cdir / f"post_{p}.md").write_text(post_md(p, cfg, hook, caption, meta), encoding="utf-8")
    tr = read_json(cdir / "transcript.json", {})
    (cdir / "README.md").write_text(clip_readme(cfg, spec, review, meta, list(cfg.get("platforms", [])), n_srt, tr), encoding="utf-8")
    (cdir / "checklist.md").unlink(missing_ok=True)  # superseded by README.md + post_*.md
    write_json(cdir / "metadata.json", meta)
    # index.csv
    from campaign import INDEX_HEADER

    idx = campaign / "index.csv"
    rows = list(csv.DictReader(open(idx, encoding="utf-8"))) if idx.exists() else []
    cand = read_json(Path(spec["source_dir"]) / "candidates.json", {}).get("candidates", [])
    c = next((x for x in cand if x["id"] == spec.get("candidate")), {})
    row = {k: "" for k in INDEX_HEADER}
    src = Path(spec["source_dir"]).name
    row.update({"clip_id": clip_id, "date": date, "source_platform": src.split("-")[0], "source_id": src.split("-", 1)[-1],
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
        line = f"| {clip_id.split('-')[0]} | {clip_id} | {hook} | {src} @ {hms(spec['start_t'])} | {c.get('score', '')} | ready_to_post |"
        if line not in txt:
            marker = "|---|--------|------|---------------|-------|--------|"
            txt = txt.replace(marker, marker + "\n" + line, 1) if marker in txt else txt + "\n" + line
            readme.write_text(txt, encoding="utf-8")
    out({"ok": True, "clip_dir": str(cdir), "files": sorted(p.name for p in cdir.iterdir()), "caption": caption, "index": str(idx),
         "user_opens": str(cdir / "README.md")})


if __name__ == "__main__":
    main_guard(main)
