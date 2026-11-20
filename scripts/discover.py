#!/usr/bin/env python3
"""List the creator's live streams and recent replays, and recommend what to clip first.

  discover.py --campaign <dir> [--max-age-days 2]

Order of truth: YouTube Data API (measured, quota-cheap: ~3 units) -> yt-dlp (no key) -> Twitch GQL -> Kick.
Recommendation follows campaign.yaml source_priority (auto | live_first | replay_first); see references/sourcing.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import urllib.parse
import urllib.request

from common import StepError, find_campaign, key, load_yaml, main_guard, out, ytdlp_base

UA = "streamclips/0.1"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # Twitch's public web client id (anonymous, read-only)


def _get(url: str, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _post(url: str, body, headers: dict | None = None, timeout: int = 30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"User-Agent": UA, "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def iso_dur(s: str) -> float:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0.0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return round((dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600, 1)


# ------------------------------------------------------------ YouTube


def yt_api(path: str, **params):
    params["key"] = key("YOUTUBE_API_KEY")
    return _get("https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode(params, doseq=True))


def youtube_sources(cfg: dict, max_items: int = 30) -> tuple[list[dict], list[str]]:
    c = cfg["creator"]
    notes: list[str] = []
    try:
        cid = c.get("youtube_channel_id")
        if not cid and c.get("youtube_handle"):
            r = yt_api("channels", part="id", forHandle=c["youtube_handle"])
            cid = r["items"][0]["id"] if r.get("items") else None
            notes.append(f"resolved {c['youtube_handle']} -> {cid}")
        if not cid:
            return [], notes + ["no YouTube channel configured"]
        uploads = "UU" + cid[2:]
        pl = yt_api("playlistItems", part="contentDetails", playlistId=uploads, maxResults=min(50, max_items))
        ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        if not ids:
            return [], notes + ["uploads playlist empty"]
        vids = yt_api("videos", part="snippet,contentDetails,liveStreamingDetails,statistics", id=",".join(ids))
        res = []
        for v in vids.get("items", []):
            sn, ls = v["snippet"], v.get("liveStreamingDetails", {})
            state = sn.get("liveBroadcastContent")  # live | upcoming | none
            if state == "live":
                typ = "live"
            elif state == "upcoming":
                typ = "upcoming"
            elif ls.get("actualEndTime"):
                typ = "replay"  # finished live stream (chat replay may exist)
            else:
                typ = "vod"
            res.append({
                "platform": "youtube", "id": v["id"], "url": f"https://www.youtube.com/watch?v={v['id']}",
                "type": typ, "title": sn["title"], "published_at": sn["publishedAt"],
                "started_at": ls.get("actualStartTime"), "duration_s": iso_dur(v["contentDetails"].get("duration", "")),
                "viewers": int(ls["concurrentViewers"]) if ls.get("concurrentViewers") else None,
                "views": int(v.get("statistics", {}).get("viewCount") or 0),
                "live_chat_id": ls.get("activeLiveChatId"), "age_hours": age_hours(ls.get("actualStartTime") or sn["publishedAt"]),
                "chat_replay": "unknown",
            })
        return res, notes
    except StepError:
        raise
    except Exception as e:  # fall back to yt-dlp
        notes.append(f"YouTube API failed ({type(e).__name__}: {str(e)[:120]}), using yt-dlp")
        return youtube_ytdlp(cfg), notes


def youtube_ytdlp(cfg: dict) -> list[dict]:
    c = cfg["creator"]
    base = f"https://www.youtube.com/{c['youtube_handle']}" if c.get("youtube_handle") else f"https://www.youtube.com/channel/{c.get('youtube_channel_id','')}"
    if not c.get("youtube_handle") and not c.get("youtube_channel_id"):
        return []
    try:
        r = subprocess.run(ytdlp_base() + ["--flat-playlist", "-J", "--playlist-end", "20", base + "/streams"],
                           capture_output=True, text=True, timeout=120)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return []
    res = []
    for e in data.get("entries", []) or []:
        st = e.get("live_status")
        typ = "live" if st == "is_live" else "upcoming" if st == "is_upcoming" else "replay"
        res.append({"platform": "youtube", "id": e.get("id"), "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                    "type": typ, "title": e.get("title"), "published_at": None, "started_at": None,
                    "duration_s": e.get("duration") or 0, "viewers": e.get("concurrent_view_count"), "views": e.get("view_count"),
                    "live_chat_id": None, "age_hours": None, "chat_replay": "unknown"})
    return res


# ------------------------------------------------------------ Twitch / Kick


def twitch_sources(cfg: dict) -> tuple[list[dict], list[str]]:
    login = cfg["creator"].get("twitch_login")
    if not login:
        return [], []
    q = ('query{user(login:"%s"){stream{id viewersCount title createdAt game{name}} '
         'videos(first:20,type:ARCHIVE){edges{node{id title lengthSeconds createdAt viewCount game{name}}}}}}' % login)
    try:
        r = _post("https://gql.twitch.tv/gql", {"query": q}, {"Client-ID": TWITCH_CLIENT_ID})
        u = r["data"]["user"]
    except Exception as e:
        return [], [f"Twitch GQL failed: {type(e).__name__}"]
    if not u:
        return [], [f"Twitch user not found: {login}"]
    res = []
    live_created = None
    if u.get("stream"):
        s = u["stream"]
        live_created = dt.datetime.fromisoformat(s["createdAt"].replace("Z", "+00:00"))
        res.append({"platform": "twitch", "id": login, "url": f"https://www.twitch.tv/{login}", "type": "live", "title": s["title"],
                    "published_at": s["createdAt"], "started_at": s["createdAt"], "duration_s": None, "viewers": s["viewersCount"],
                    "views": None, "game": (s.get("game") or {}).get("name"), "live_chat_id": None, "age_hours": age_hours(s["createdAt"]), "chat_replay": "n/a"})
    for e in u["videos"]["edges"]:
        n = e["node"]
        created = dt.datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00"))
        recording = bool(live_created and abs((created - live_created).total_seconds()) < 600)
        res.append({"platform": "twitch", "id": n["id"], "url": f"https://www.twitch.tv/videos/{n['id']}", "type": "replay", "title": n["title"],
                    "published_at": n["createdAt"], "started_at": n["createdAt"], "duration_s": n["lengthSeconds"], "viewers": None,
                    "views": n["viewCount"], "game": (n.get("game") or {}).get("name"), "live_chat_id": None, "age_hours": age_hours(n["createdAt"]),
                    "chat_replay": "yes", "still_recording": recording})
    return res, []


def kick_sources(cfg: dict) -> tuple[list[dict], list[str]]:
    s = cfg["creator"].get("kick_slug")
    if not s:
        return [], []
    try:
        d = _get(f"https://kick.com/api/v2/channels/{s}", {"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        return [], [f"Kick API failed: {type(e).__name__}"]
    res = []
    ls = d.get("livestream")
    if ls:
        res.append({"platform": "kick", "id": s, "url": f"https://kick.com/{s}", "type": "live", "title": ls.get("session_title"),
                    "published_at": ls.get("created_at"), "started_at": ls.get("created_at"), "duration_s": None, "viewers": ls.get("viewer_count"),
                    "views": None, "live_chat_id": None, "age_hours": None, "chat_replay": "no"})
    return res, []


# ------------------------------------------------------------ recommend


def recommend(cfg: dict, sources: list[dict], max_age_days: float) -> dict:
    game = (cfg.get("game") or "").lower()
    live = [s for s in sources if s["type"] == "live"]
    replays = [s for s in sources if s["type"] in ("replay", "vod") and (s.get("age_hours") is None or s["age_hours"] <= max_age_days * 24)
               and (s.get("duration_s") or 0) >= 600]
    if game:
        def on_game(s):
            t = (s.get("title") or "").lower() + " " + (s.get("game") or "").lower()
            return any(w in t for w in [game, "gta", "grand theft"]) if "grand theft" in game or "gta" in game else game in t
        replays_game = [s for s in replays if on_game(s)]
        if replays_game:
            replays = replays_game
    # finished replays first (a still-recording VOD is the live stream; its chat replay covers only the part already streamed),
    # then prefer Twitch replays (chat replay available), then recency
    replays.sort(key=lambda s: (1 if s.get("still_recording") else 0, 0 if s.get("chat_replay") == "yes" else 1, s.get("age_hours") or 1e9))
    prio = cfg.get("source_priority", "auto")
    plan = []
    if prio == "live_first":
        plan = [("live", s) for s in live] + [("replay", s) for s in replays]
        why = "campaign says live first"
    elif prio == "replay_first":
        plan = [("replay", s) for s in replays] + [("live", s) for s in live]
        why = "campaign says replay first"
    else:
        plan = [("replay", s) for s in replays[:2]] + [("live", s) for s in live] + [("replay", s) for s in replays[2:]]
        why = "auto: start clips from the freshest replay now; run the live watcher in parallel if a stream is live"
    return {
        "why": why,
        "live_now": live,
        "replays_in_window": replays,
        "steps": [{"mode": m, "platform": s["platform"], "id": s["id"], "url": s["url"], "title": s.get("title"),
                   "duration_s": s.get("duration_s"), "age_hours": s.get("age_hours"), "chat_replay": s.get("chat_replay"),
                   "still_recording": s.get("still_recording", False), "live_chat_id": s.get("live_chat_id")} for m, s in plan],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign")
    ap.add_argument("--max-age-days", type=float)
    a = ap.parse_args()
    folder = find_campaign(a.campaign)
    cfg = load_yaml(folder / "campaign.yaml")
    max_age = a.max_age_days or float(cfg.get("replay_max_age_days", 2))
    sources, notes = [], []
    for fn in (youtube_sources, twitch_sources, kick_sources):
        try:
            s, n = fn(cfg)
        except StepError as e:
            s, n = [], [str(e)]
        sources += s
        notes += n
    out({"ok": True, "campaign": str(folder), "notes": notes, "sources": sources, "recommendation": recommend(cfg, sources, max_age)})


if __name__ == "__main__":
    main_guard(main)
