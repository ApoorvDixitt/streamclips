#!/usr/bin/env python3
"""Collect chat into sources/<platform>-<id>/chat.jsonl  (one JSON per line: t, user, text).

  chat.py replay --campaign <dir> --url https://www.twitch.tv/videos/ID [--start 0 --end 3600]
  chat.py replay --campaign <dir> --url https://www.youtube.com/watch?v=ID        (only if YouTube kept a chat replay)
  chat.py live   --campaign <dir> --url https://www.youtube.com/watch?v=ID [--poll 30] [--duration 36000]
  chat.py live   --campaign <dir> --url https://www.twitch.tv/LOGIN [--duration 36000]

`t` is seconds from stream start. Priority: YouTube Data API (measured) -> yt-dlp -> Twitch (see references/sourcing.md).
Live mode runs until --duration or Ctrl+C and is safe to run in the background.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import re
import signal
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import StepError, find_campaign, key, main_guard, now_iso, out, parse_time, write_json, ytdlp_base
from fetch import parse_url, source_dir

UA = "streamclips/0.1"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_COMMENTS_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"


def _post(url, body, headers=None, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"User-Agent": UA, "Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Writer:
    def __init__(self, path: Path, live: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.live = live
        # replay: rewrite (a re-run must not duplicate every message); live: append (collector restarts continue the file)
        self.f = open(path, "a" if live else "w", encoding="utf-8")
        self.n = 0
        self.seen: set[str] = set()

    def add(self, t: float, user: str, text: str, mid: str | None = None):
        if mid and mid in self.seen:
            return
        if mid:
            self.seen.add(mid)
        self.f.write(json.dumps({"t": round(float(t), 2), "user": user, "text": text}, ensure_ascii=False) + "\n")
        self.n += 1
        if self.n % 10 == 0 or self.live:
            self.f.flush()

    def close(self):
        self.f.close()


# ------------------------------------------------------------ Twitch replay


def twitch_replay(vid: str, start: float, end: float | None, w: Writer) -> dict:
    off = int(start)
    calls = 0
    errors = 0
    last_err = None
    while end is None or off < end:
        body = [{"operationName": "VideoCommentsByOffsetOrCursor", "variables": {"videoID": vid, "contentOffsetSeconds": off},
                 "extensions": {"persistedQuery": {"version": 1, "sha256Hash": TWITCH_COMMENTS_HASH}}}]
        try:
            r = _post("https://gql.twitch.tv/gql", body, {"Client-ID": TWITCH_CLIENT_ID})
        except Exception as e:
            last_err = str(e)
            time.sleep(2)
            calls += 1
            if calls > 2000:
                break
            continue
        calls += 1
        if r[0].get("errors"):
            last_err = r[0]["errors"][0].get("message")
            errors += 1
            if errors >= 20:
                break
            off += 5
            continue
        errors = 0
        comments = (r[0]["data"].get("video") or {}).get("comments")
        if not comments:
            break
        edges = comments["edges"]
        if not edges:
            break
        new = 0
        last = off
        for e in edges:
            n = e["node"]
            if n["id"] not in w.seen:
                new += 1
            txt = "".join(f.get("text", "") for f in n["message"]["fragments"])
            w.add(n["contentOffsetSeconds"], (n.get("commenter") or {}).get("displayName") or "?", txt, n["id"])
            last = max(last, int(n["contentOffsetSeconds"]))
        if not comments["pageInfo"].get("hasNextPage") and last <= off:
            break
        if new and last <= off:
            # more than one page of comments inside this second: follow the cursor once before moving on
            try:
                r2 = _post("https://gql.twitch.tv/gql", [{"operationName": "VideoCommentsByOffsetOrCursor",
                           "variables": {"videoID": vid, "cursor": edges[-1]["cursor"]},
                           "extensions": {"persistedQuery": {"version": 1, "sha256Hash": TWITCH_COMMENTS_HASH}}}], {"Client-ID": TWITCH_CLIENT_ID})
                calls += 1
                for e in (((r2[0].get("data") or {}).get("video") or {}).get("comments") or {}).get("edges", []):
                    n = e["node"]
                    w.add(n["contentOffsetSeconds"], (n.get("commenter") or {}).get("displayName") or "?",
                          "".join(f.get("text", "") for f in n["message"]["fragments"]), n["id"])
            except Exception:
                pass
        off = last if (new and last > off) else off + 3
        if calls % 50 == 0:
            w.f.flush()
    if w.n == 0 and last_err:
        raise StepError(f"Twitch replay chat returned nothing ({last_err}). Twitch may have changed its endpoint; use audio+speech signals.")
    return {"calls": calls, "last_error": last_err, "covered_to_s": off}


# ------------------------------------------------------------ YouTube replay (yt-dlp)


def ytdlp_live_chat_file(url: str, outbase: Path, timeout: int | None) -> list[Path]:
    cmd = ytdlp_base() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(outbase), url]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    return [Path(p) for p in glob.glob(str(outbase) + ".live_chat.json*")]


def parse_ytdlp_chat(files: list[Path], w: Writer, t0: dt.datetime | None) -> int:
    n = 0
    for fp in files:
        for line in open(fp, encoding="utf-8", errors="ignore"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            act = d.get("replayChatItemAction", {})
            items = act.get("actions", [])
            off_ms = act.get("videoOffsetTimeMsec")
            for it in items:
                r = it.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                if not r:
                    continue
                text = "".join(x.get("text", x.get("emoji", {}).get("shortcuts", [""])[0] if isinstance(x.get("emoji"), dict) else "") for x in r.get("message", {}).get("runs", []))
                user = r.get("authorName", {}).get("simpleText", "?")
                if off_ms is not None:
                    t = int(off_ms) / 1000
                elif t0 and r.get("timestampUsec"):
                    t = int(r["timestampUsec"]) / 1e6 - t0.timestamp()
                else:
                    continue
                w.add(t, user, text, r.get("id"))
                n += 1
    return n


# ------------------------------------------------------------ YouTube live (Data API)


def yt_api(path, **params):
    params["key"] = key("YOUTUBE_API_KEY")
    return _get("https://www.googleapis.com/youtube/v3/" + path + "?" + urllib.parse.urlencode(params, doseq=True))


def youtube_live(vid: str, w: Writer, poll_s: float, duration_s: float, meta_path: Path) -> dict:
    try:
        v = yt_api("videos", part="liveStreamingDetails,snippet", id=vid)["items"]
    except urllib.error.HTTPError as e:
        raise StepError(f"YouTube API {e.code} on videos.list ({'quota exceeded' if e.code == 403 else 'error'})")
    if not v:
        raise StepError("video not found")
    ls = v[0].get("liveStreamingDetails", {})
    chat_id = ls.get("activeLiveChatId")
    if not chat_id:
        raise StepError("no activeLiveChatId: stream is not live (or chat disabled)")
    if not ls.get("actualStartTime"):
        raise StepError("stream has not started yet (upcoming); start the collector once it is live")
    t0 = dt.datetime.fromisoformat(ls["actualStartTime"].replace("Z", "+00:00"))
    write_json(meta_path, {"platform": "youtube", "id": vid, "t0": t0.isoformat(), "collector": "youtube_api", "started": now_iso()})
    token = None
    deadline = time.time() + duration_s
    calls = 0
    units = 0
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("flag", True))
    while time.time() < deadline and not stop["flag"]:
        params = {"part": "snippet,authorDetails", "liveChatId": chat_id, "maxResults": 2000}
        if token:
            params["pageToken"] = token
        try:
            r = yt_api("liveChat/messages", **params)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")[:200]
            if e.code == 403 and "quota" in body.lower():
                raise StepError(f"YouTube API quota exceeded after {calls} calls ({units} units)")  # caller falls back to yt-dlp
            if e.code in (403, 404):
                return {"calls": calls, "quota_units": units, "ended": f"chat ended ({e.code})"}
            time.sleep(5)
            continue
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(10)
            continue
        calls += 1
        units += 5
        for m in r.get("items", []):
            sn = m["snippet"]
            if not sn.get("hasDisplayContent", True):
                continue
            t = dt.datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp() - t0.timestamp()
            w.add(t, m.get("authorDetails", {}).get("displayName", "?"), sn.get("displayMessage", ""), m["id"])
        w.f.flush()
        if r.get("offlineAt"):
            return {"calls": calls, "quota_units": units, "ended": "stream went offline"}
        token = r.get("nextPageToken")
        time.sleep(max(poll_s, r.get("pollingIntervalMillis", 3000) / 1000))
    return {"calls": calls, "quota_units": units, "ended": "duration reached" if not stop["flag"] else "stopped"}


def youtube_live_ytdlp(url: str, vid: str, w: Writer, duration_s: float, sdir: Path, meta_path: Path) -> dict:
    """Zero-quota fallback: yt-dlp streams live chat into a .part file we tail."""
    outbase = sdir / "ytdlp_live"
    t0 = dt.datetime.now(dt.timezone.utc)
    write_json(meta_path, {"platform": "youtube", "id": vid, "t0": t0.isoformat(), "collector": "yt-dlp", "started": now_iso()})
    proc = subprocess.Popen(ytdlp_base() + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(outbase), url],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + duration_s
    seen_lines: dict[str, int] = {}
    try:
        while time.time() < deadline and proc.poll() is None:
            time.sleep(20)
            for fp in [Path(p) for p in glob.glob(str(outbase) + ".live_chat.json*")]:
                lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
                done = seen_lines.get(str(fp), 0)
                if len(lines) > done:
                    tmp = fp.with_suffix(".delta")
                    tmp.write_text("\n".join(lines[done:]), encoding="utf-8")
                    parse_ytdlp_chat([tmp], w, t0)
                    tmp.unlink(missing_ok=True)
                    seen_lines[str(fp)] = len(lines)
            w.f.flush()
    finally:
        proc.terminate()
    return {"ended": "done", "collector": "yt-dlp", "note": "t is relative to collector start, not stream start"}


# ------------------------------------------------------------ Twitch live (anonymous IRC)


def twitch_live(login: str, w: Writer, duration_s: float, meta_path: Path) -> dict:
    try:
        r = _post("https://gql.twitch.tv/gql", {"query": 'query{user(login:"%s"){stream{createdAt}}}' % login}, {"Client-ID": TWITCH_CLIENT_ID})
        created = ((r["data"]["user"] or {}).get("stream") or {}).get("createdAt")
    except Exception:
        created = None
    t0 = dt.datetime.fromisoformat(created.replace("Z", "+00:00")) if created else dt.datetime.now(dt.timezone.utc)
    write_json(meta_path, {"platform": "twitch", "id": login, "t0": t0.isoformat(), "collector": "twitch_irc", "started": now_iso()})
    ctx = ssl.create_default_context()
    s = ctx.wrap_socket(socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=30), server_hostname="irc.chat.twitch.tv")
    s.sendall(f"PASS SCHMOOPIIE\r\nNICK justinfan{int(time.time()) % 100000}\r\nJOIN #{login}\r\n".encode())
    s.settimeout(5)
    deadline = time.time() + duration_s
    buf = b""
    n = 0
    while time.time() < deadline:
        try:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            continue
        lines = buf.split(b"\r\n")
        buf = lines.pop()
        for raw in lines:
            line = raw.decode(errors="ignore")
            if line.startswith("PING"):
                s.sendall(b"PONG :tmi.twitch.tv\r\n")
                continue
            m = re.match(r"^:(\w+)!\S+ PRIVMSG #\w+ :(.*)$", line)
            if m:
                w.add(time.time() - t0.timestamp(), m.group(1), m.group(2))
                n += 1
                if n % 50 == 0:
                    w.f.flush()
    s.close()
    return {"ended": "done", "messages": n}


# ------------------------------------------------------------ main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for m in ("replay", "live"):
        p = sub.add_parser(m)
        p.add_argument("--campaign")
        p.add_argument("--url", required=True)
        p.add_argument("--start", default="0")
        p.add_argument("--end")
        p.add_argument("--poll", type=float, default=30, help="live: seconds between YouTube API polls (5 quota units each)")
        p.add_argument("--duration", type=float, default=12 * 3600)
        p.add_argument("--collector", choices=["auto", "youtube_api", "yt-dlp", "twitch_irc"], default="auto")
    a = ap.parse_args()
    campaign = find_campaign(a.campaign)
    platform, vid = parse_url(a.url)
    sdir = source_dir(campaign, platform, vid)
    chat_path = sdir / "chat.jsonl"
    meta_path = sdir / "chat_meta.json"
    w = Writer(chat_path, live=(a.mode == "live"))
    try:
        if a.mode == "replay":
            if platform == "twitch":
                info = twitch_replay(vid, parse_time(a.start), parse_time(a.end) if a.end else None, w)
                write_json(meta_path, {"platform": "twitch", "id": vid, "t0": "stream_start", "collector": "twitch_gql_replay", "started": now_iso(), **info})
            elif platform == "youtube":
                files = ytdlp_live_chat_file(a.url, sdir / "ytdlp_replay", timeout=1800)
                if not files:
                    raise StepError("YouTube kept no chat replay for this video. Use the Twitch replay of the same stream if it exists.")
                n = parse_ytdlp_chat(files, w, None)
                info = {"messages": n, "collector": "yt-dlp_replay"}
                write_json(meta_path, {"platform": "youtube", "id": vid, "t0": "stream_start", "started": now_iso(), **info})
            else:
                raise StepError("Kick deletes chat after a stream; no replay chat available.")
        else:
            if platform == "youtube":
                if a.collector in ("auto", "youtube_api"):
                    try:
                        info = youtube_live(vid, w, a.poll, a.duration, meta_path)
                    except StepError as e:
                        if a.collector == "youtube_api":
                            raise
                        info = {"fallback_reason": str(e), **youtube_live_ytdlp(a.url, vid, w, a.duration, sdir, meta_path)}
                else:
                    info = youtube_live_ytdlp(a.url, vid, w, a.duration, sdir, meta_path)
            elif platform == "twitch":
                if vid.isdigit():
                    raise StepError("that is a Twitch VOD url; use chat.py replay")
                info = twitch_live(vid, w, a.duration, meta_path)
            else:
                raise StepError("Kick live chat is not implemented; use the Twitch or YouTube simulcast (--chat-url).")
    finally:
        w.close()
    out({"ok": True, "chat_file": str(chat_path), "messages_written": w.n, **info})


if __name__ == "__main__":
    main_guard(main)
