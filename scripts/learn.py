#!/usr/bin/env python3
"""Learn from results: read index.csv views, report what worked, and suggest signal weights / posting slots.

  learn.py --campaign <dir> [--apply]        # --apply writes signals.weights into campaign.yaml when >= 10 clips have views

The user fills views_* columns in index.csv (or the agent does from screenshots). This script never guesses views.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st

from common import dump_yaml, find_campaign, load_yaml, main_guard, out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campaign")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    folder = find_campaign(a.campaign)
    cfg = load_yaml(folder / "campaign.yaml")
    rows = list(csv.DictReader(open(folder / "index.csv", encoding="utf-8")))
    scored = []
    for r in rows:
        views = sum(int(float(r.get(k) or 0)) for k in ("views_tiktok", "views_reels", "views_shorts"))
        if views <= 0:
            continue
        try:
            comp = json.loads(r.get("signals") or "{}")
        except Exception:
            comp = {}
        scored.append({"clip_id": r["clip_id"], "views": views, "duration_s": float(r.get("duration_s") or 0), "hook": r.get("hook", ""),
                       "chat": float(comp.get("chat", 0)), "audio": float(comp.get("audio", 0)), "speech": float(comp.get("speech", 0)),
                       "layout": r.get("layout"), "approved": r.get("approved")})
    if not scored:
        out({"ok": True, "clips_with_views": 0, "note": "fill views_tiktok/views_reels/views_shorts in index.csv first"})
        return
    scored.sort(key=lambda x: -x["views"])
    med = st.median(x["views"] for x in scored)
    top = [x for x in scored if x["views"] >= 2 * med] or scored[:3]
    rest = [x for x in scored if x not in top]

    def avg(xs, k):
        return round(st.mean([x[k] for x in xs]), 3) if xs else None

    report = {
        "clips_with_views": len(scored), "median_views": med,
        "top": [{"clip_id": x["clip_id"], "views": x["views"], "hook": x["hook"], "duration_s": x["duration_s"]} for x in top[:5]],
        "signal_means_top_vs_rest": {k: [avg(top, k), avg(rest, k)] for k in ("chat", "audio", "speech")},
        "duration_top_vs_rest": [avg(top, "duration_s"), avg(rest, "duration_s")],
        "approval_rate": round(sum(1 for x in scored if str(x["approved"]).lower() in ("1", "true", "yes")) / len(scored), 2),
    }
    suggestion = None
    if len(scored) >= 10 and rest:
        w = {k: max(0.1, (avg(top, k) or 0) - (avg(rest, k) or 0) + 0.3) for k in ("chat", "audio", "speech")}
        s = sum(w.values())
        suggestion = {k: round(v / s, 2) for k, v in w.items()}
        report["suggested_weights"] = suggestion
        if a.apply:
            cfg.setdefault("signals", {})["weights"] = suggestion
            dump_yaml(folder / "campaign.yaml", cfg)
            report["applied"] = True
    else:
        report["note"] = "need >= 10 clips with views before changing weights"
    out({"ok": True, **report})


if __name__ == "__main__":
    main_guard(main)
