#!/usr/bin/env python3
"""Check hook lines against the rules in references/hooks.md and the clip's transcript.

  hook_check.py --hooks "line one" "line two" [--transcript-file words.json | --transcript "text"] [--campaign <dir>]
  hook_check.py --spec <dir>/spec.json          # checks the hook inside a render spec

Checks: length (<= 9 words, <= 44 chars), no filler openers, no links/hashtags, not ALL-CAPS shouting, no banned words,
and transcript support (at least one content word must appear in the transcript; names/places count).
Exit 1 if the best hook fails. Output ranks the passing hooks.
"""
from __future__ import annotations

import argparse
import re

from common import find_campaign, load_yaml, main_guard, out, read_json

FILLERS = re.compile(r"^(did you know|in this video|hey guys|wait for it|you won'?t believe|watch till the end|pov:?|when you)", re.I)
STOP = set("the a an and or but of to in on at for with he she it they we you i this that is was were be just so then than his her him them my our your its from into out up down over under about after before again off not no yes oh".split())
CLICKBAIT = re.compile(r"(gone wrong|you won'?t believe|shocking|insane|must see|\?\?\?|!!!)", re.I)


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2 and w not in STOP}


def check(hook: str, transcript: str, banned: list[str]) -> dict:
    problems, warnings = [], []
    words = hook.split()
    if len(words) > 9:
        problems.append(f"{len(words)} words (max 9)")
    if len(hook) > 44:
        problems.append(f"{len(hook)} chars (max 44, fits 2 lines)")
    if FILLERS.match(hook):
        problems.append("filler opener")
    if re.search(r"https?://|www\.|#\w+|(^|\s)@\w+", hook):
        problems.append("link, hashtag or @mention in hook")
    if hook.isupper() and len(hook) > 12:
        warnings.append("all caps: the renderer already uppercases; write it normally")
    if CLICKBAIT.search(hook):
        problems.append("clickbait phrasing (gone wrong / insane / shocking / ???); briefs ban misleading titles")
    for b in banned:
        if b and b.lower() in hook.lower():
            problems.append(f"banned word: {b}")
    support = None
    if transcript and re.search(r"[a-z]", hook.lower()):
        hw, tw = content_words(hook), content_words(transcript)
        overlap = hw & tw
        support = round(len(overlap) / max(1, len(hw)), 2)
        if not overlap:
            problems.append("no content word from the hook appears in the transcript (fabricated context risk)")
        elif support < 0.3:
            warnings.append(f"weak transcript support ({support}); make sure the frames show it")
    score = 1.0 - 0.5 * len(problems) - 0.15 * len(warnings)
    score += 0.1 if 3 <= len(words) <= 7 else 0
    score += 0.1 * (support or 0)
    return {"hook": hook, "ok": not problems, "problems": problems, "warnings": warnings, "transcript_support": support, "score": round(score, 2)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hooks", nargs="*", default=[])
    ap.add_argument("--transcript")
    ap.add_argument("--transcript-file")
    ap.add_argument("--spec")
    ap.add_argument("--campaign")
    a = ap.parse_args()
    transcript = a.transcript or ""
    hooks = list(a.hooks)
    spec = read_json(a.spec) if a.spec else None
    if spec:
        hooks = hooks or [spec.get("hook", "")]
        transcript = transcript or " ".join(w["word"] for w in spec.get("words", []))
    banned: list[str] = []
    try:
        cfg = load_yaml(find_campaign(a.campaign or (spec or {}).get("campaign")) / "campaign.yaml")
        rules = cfg.get("rules", {}) or {}
        banned = [b for b in (rules.get("banned_words") or []) if isinstance(b, str) and len(b.split()) <= 3]
    except Exception:
        pass
    if a.transcript_file:
        d = read_json(a.transcript_file, {})
        transcript = transcript or d.get("text") or " ".join(w["word"] for w in d.get("words", []))
    if not hooks:
        ap.error("give --hooks or --spec")
    results = sorted((check(h, transcript, banned) for h in hooks), key=lambda r: -r["score"])
    out({"ok": results[0]["ok"], "ranked": results})
    if not results[0]["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main_guard(main)
