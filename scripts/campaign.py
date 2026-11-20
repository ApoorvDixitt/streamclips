#!/usr/bin/env python3
"""Create and manage a campaign folder.

  campaign.py new  --root ~/Videos --name "KreekCraft GTA 6" [--brief brief.txt] [--overlay logo.png]   -> ~/Videos/streamclips/<slug>/
                   [--youtube @Kreek] [--twitch kreekcraft] [--game "Grand Theft Auto VI"]
  campaign.py set  --campaign <dir> clip.max_seconds=45 audio.mode=original caption.hashtags='["#A","#B"]'
  campaign.py show --campaign <dir>
  campaign.py validate --campaign <dir>
  campaign.py day  --campaign <dir> [--date 2026-11-21]      -> creates/returns the daily folder

The agent fills campaign.yaml from the brief (see references/campaign.md), then validates.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from common import StepError, dump_yaml, find_campaign, load_yaml, main_guard, out, slug, today

DEFAULTS = {
    "name": "",
    "slug": "",
    "listing": "",
    "created": "",
    "creator": {"name": "", "youtube_channel_id": "", "youtube_handle": "", "twitch_login": "", "kick_slug": "", "extra_sources": []},
    "game": "",
    "source_priority": "auto",  # auto | live_first | replay_first
    "replay_max_age_days": 2,
    "platforms": ["tiktok", "instagram_reels", "youtube_shorts"],
    "payout": {"cpm_usd": 1.0, "min_payout_usd": 5.0, "max_payout_usd": 300.0, "min_engagement_rate": 0.008},
    "clip": {
        "min_seconds": 8,
        "max_seconds": 59,
        "target_seconds": [15, 35],
        "hook_seconds": 2,
        "layout": "face-top",  # face-top | fill | fit
        "facecam_region": None,  # {"x":0,"y":0,"w":0.19,"h":0.44} as fractions of the source frame
        "captions": "word_pop",  # word_pop | phrase | none
        "mask_profanity": True,
        "bleep_profanity": True,
        "zoom_at_peak": True,
        "names": [],  # vocabulary for transcription: characters, places, catchphrases (creator name is added automatically)
        "language": "en",
    },
    "audio": {"mode": "original", "voice_id": "q0IMILNRPxOgtBTS4taI", "voice_name": "Drew", "voiceover_max_words": 12},
    "overlay": {"file": "", "required": False, "position": "top", "width_pct": 0.72, "y_pct": 0.159},
    "caption": {
        "required_text": "",
        "hashtags": [],
        "no_links": True,
        "template": "{hook}\n\n{required_text} {hashtags}",
    },
    "rules": {
        "max_posts_per_clip": 5,
        "submit_within_minutes": 30,
        "keep_public_days": 30,
        "likes_visible": True,
        "tier1_min_share": 0.40,
        "banned": [],
        "preferred_moments": [],
        "notes": [],
    },
    "audience": {"tier1": ["US", "UK", "CA", "AU"], "posting_windows_et": ["11:00-14:00", "17:00-21:00"]},
    "user_timezone": "Asia/Kolkata",
    "brief_text_file": "",
}

MEMORY_TEMPLATE = """# Campaign memory: {name}

Living notes the agent updates after every session. Keep it short and factual.

## Streamer facts
- Channels: YouTube {yt} · Twitch {tw}
- Facecam position / layout quirks:
- Recurring segments, catchphrases, running jokes:

## What performed (update from index.csv views)
- 

## Decisions
- {created}: campaign created.

## Open questions / blockers
- 
"""

INDEX_HEADER = [
    "clip_id", "date", "source_platform", "source_id", "start_s", "end_s", "duration_s", "hook",
    "score", "signals", "layout", "audio_mode", "status", "posted_tiktok", "posted_reels", "posted_shorts",
    "post_count", "views_tiktok", "views_reels", "views_shorts", "approved", "payout_usd", "notes",
]


def _deep_set(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _coerce(v: str):
    s = v.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "none", ""):
        return None
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        return int(s) if s.lstrip("-").isdigit() else float(s)
    except Exception:
        return s


def campaigns_root(location: str | Path) -> Path:
    """<user's preferred location>/streamclips/ holds every campaign. Refuses to use the skill repo itself."""
    loc = Path(location).expanduser().resolve()
    if (loc / "SKILL.md").exists() or any((p / "SKILL.md").exists() for p in loc.parents):
        raise StepError(f"{loc} is inside the skill repo, not a place for campaigns. Ask the user for a different location (e.g. ~/Videos).")
    root = loc if loc.name == "streamclips" else loc / "streamclips"
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text("# streamclips campaigns\n\nOne folder per campaign. Open a campaign folder, then a date folder, then a clip folder; "
                          "each clip folder has a README.md that tells you exactly what to post and how.\n", encoding="utf-8")
    return root


def cmd_new(a) -> None:
    root = campaigns_root(a.root)
    sl = a.slug or slug(a.name)
    folder = root / sl
    if folder.exists() and (folder / "campaign.yaml").exists():
        raise StepError(f"campaign already exists: {folder}")
    (folder / "brief").mkdir(parents=True, exist_ok=True)
    (folder / "sources").mkdir(exist_ok=True)
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg.update({"name": a.name, "slug": sl, "created": today(), "game": a.game or ""})
    yt = a.youtube or ""
    if yt.startswith("@"):
        cfg["creator"]["youtube_handle"] = yt
    elif yt.startswith("UC"):
        cfg["creator"]["youtube_channel_id"] = yt
    cfg["creator"]["twitch_login"] = a.twitch or ""
    cfg["creator"]["name"] = a.creator or a.name.split()[0]
    if a.brief:
        dst = folder / "brief" / "brief.txt"
        shutil.copy(a.brief, dst)
        cfg["brief_text_file"] = "brief/brief.txt"
    if a.overlay:
        dst = folder / "brief" / Path(a.overlay).name
        shutil.copy(a.overlay, dst)
        cfg["overlay"].update({"file": f"brief/{dst.name}", "required": True})
    dump_yaml(folder / "campaign.yaml", cfg)
    (folder / "MEMORY.md").write_text(
        MEMORY_TEMPLATE.format(name=a.name, yt=yt or "?", tw=a.twitch or "?", created=today()), encoding="utf-8"
    )
    with open(folder / "index.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(INDEX_HEADER)
    out({"ok": True, "campaign": str(folder), "next": "Fill campaign.yaml from the brief (references/campaign.md), then run campaign.py validate"})


def cmd_set(a) -> None:
    folder = find_campaign(a.campaign)
    cfg = load_yaml(folder / "campaign.yaml")
    for kv in a.pairs:
        if "=" not in kv:
            raise StepError(f"expected key=value, got {kv}")
        k, v = kv.split("=", 1)
        _deep_set(cfg, k.strip(), _coerce(v))
    dump_yaml(folder / "campaign.yaml", cfg)
    out({"ok": True, "updated": [p.split("=", 1)[0] for p in a.pairs]})


def cmd_show(a) -> None:
    folder = find_campaign(a.campaign)
    out({"campaign": str(folder), **load_yaml(folder / "campaign.yaml")})


def validate(folder: Path) -> list[str]:
    cfg = load_yaml(folder / "campaign.yaml")
    problems: list[str] = []
    c = cfg.get("creator", {})
    if not (c.get("youtube_channel_id") or c.get("youtube_handle") or c.get("twitch_login") or c.get("kick_slug")):
        problems.append("creator: set at least one of youtube_channel_id / youtube_handle / twitch_login / kick_slug")
    ov = cfg.get("overlay", {})
    if ov.get("required") and not (folder / ov.get("file", "")).exists():
        problems.append(f"overlay.file not found: {ov.get('file')}")
    cap = cfg.get("caption", {})
    if cap.get("no_links") and "http" in (cap.get("template") or ""):
        problems.append("caption.template contains a link but caption.no_links is true")
    clip = cfg.get("clip", {})
    if clip.get("min_seconds", 0) >= clip.get("max_seconds", 1):
        problems.append("clip.min_seconds must be < clip.max_seconds")
    if cfg.get("source_priority") not in ("auto", "live_first", "replay_first"):
        problems.append("source_priority must be auto | live_first | replay_first")
    if cfg.get("audio", {}).get("mode") not in ("original", "voiceover_allowed"):
        problems.append("audio.mode must be original | voiceover_allowed")
    if clip.get("layout") not in ("face-top", "fill", "fit"):
        problems.append("clip.layout must be face-top | fill | fit")
    if not (folder / "index.csv").exists():
        problems.append("index.csv missing")
    return problems


def cmd_validate(a) -> None:
    folder = find_campaign(a.campaign)
    problems = validate(folder)
    out({"ok": not problems, "campaign": str(folder), "problems": problems})
    if problems:
        raise SystemExit(1)


DAY_README = """# {date} — {name}

## Plan
- Sources checked:
- Decision (live / replay / both) and why:

## Clips made today
| # | folder | hook | source @ time | score | status |
|---|--------|------|---------------|-------|--------|

## Posting plan (fill after research)
| clip | platform | post at (local) | audience window | submitted by |
|------|----------|-----------------|-----------------|--------------|

## Notes / lessons
- 
"""


def cmd_day(a) -> None:
    folder = find_campaign(a.campaign)
    cfg = load_yaml(folder / "campaign.yaml")
    date = a.date or today()
    day = folder / date
    day.mkdir(exist_ok=True)
    readme = day / "README.md"
    if not readme.exists():
        readme.write_text(DAY_README.format(date=date, name=cfg.get("name", "")), encoding="utf-8")
    existing = sorted(p.name for p in day.iterdir() if p.is_dir())
    out({"ok": True, "day_folder": str(day), "existing_clips": existing, "next_index": len(existing) + 1})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--root", required=True, help="user's preferred location; campaigns go in <root>/streamclips/<slug>")
    n.add_argument("--name", required=True)
    n.add_argument("--slug")
    n.add_argument("--creator")
    n.add_argument("--brief")
    n.add_argument("--overlay")
    n.add_argument("--youtube", help="@handle or UC... channel id")
    n.add_argument("--twitch")
    n.add_argument("--game")
    n.set_defaults(fn=cmd_new)
    s = sub.add_parser("set")
    s.add_argument("--campaign")
    s.add_argument("pairs", nargs="+")
    s.set_defaults(fn=cmd_set)
    for name, fn in (("show", cmd_show), ("validate", cmd_validate)):
        p = sub.add_parser(name)
        p.add_argument("--campaign")
        p.set_defaults(fn=fn)
    d = sub.add_parser("day")
    d.add_argument("--campaign")
    d.add_argument("--date")
    d.set_defaults(fn=cmd_day)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main_guard(main)
