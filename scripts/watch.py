#!/usr/bin/env python3
"""Live watcher: record a stream in 10 s segments, collect chat, and flag hot moments while the stream runs.

  watch.py start  --campaign <dir> --url https://www.youtube.com/watch?v=ID [--chat-url https://www.twitch.tv/login] [--hours 10] [--quality 1080p60,1080p,720p60,720p,best]
  watch.py status --campaign <dir>
  watch.py stop   --campaign <dir>
  watch.py clip   --campaign <dir> --start <stream_seconds> --end <stream_seconds>     # cut a segment file from the live buffer -> sources/.../segments

Runs detached (nohup) so the agent session can end. State: <campaign>/live/<platform>-<id>/{status.json, segments/, chat.jsonl, hot.json, watch.log}.
Video via streamlink -> ffmpeg segment muxer. Chat via chat.py live (YouTube API first, yt-dlp fallback, or Twitch IRC via --chat-url).
Every 60 s it scores the last 10 minutes of chat and writes hot.json (bins with unique-chatter ratio >= 3x) so the agent can
fetch/clip a moment within minutes (references/sourcing.md: live mode).
Buffer: keeps the last --keep-minutes (default 180) of segments on disk.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import StepError, ffmpeg_bin, find_campaign, main_guard, now_iso, out, read_json, write_json
from fetch import parse_url

SEG_S = 10


def live_dir(campaign: Path, platform: str, vid: str) -> Path:
    d = campaign / "live" / f"{platform}-{vid}"
    (d / "segments").mkdir(parents=True, exist_ok=True)
    return d


def find_running(campaign: Path) -> list[Path]:
    res = []
    for st in (campaign / "live").glob("*/status.json"):
        s = read_json(st, {})
        pid = s.get("pid")
        if pid and _alive(pid):
            res.append(st.parent)
    return res


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stream_start_offset(platform: str, vid: str, now_epoch: float) -> float:
    """Seconds of stream already elapsed when capture starts (so chat t maps onto buffered segments)."""
    import datetime as dt

    try:
        if platform == "youtube":
            from chat import yt_api

            ls = yt_api("videos", part="liveStreamingDetails", id=vid)["items"][0].get("liveStreamingDetails", {})
            st = ls.get("actualStartTime")
        elif platform == "twitch":
            from chat import TWITCH_CLIENT_ID, _post

            r = _post("https://gql.twitch.tv/gql", {"query": 'query{user(login:"%s"){stream{createdAt}}}' % vid}, {"Client-ID": TWITCH_CLIENT_ID})
            st = ((r["data"]["user"] or {}).get("stream") or {}).get("createdAt")
        else:
            st = None
        if st:
            return round(now_epoch - dt.datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp(), 1)
    except Exception:
        pass
    return 0.0


# ------------------------------------------------------------ worker


def worker(campaign: Path, url: str, chat_url: str | None, hours: float, quality: str, keep_minutes: int) -> None:
    platform, vid = parse_url(url)
    d = live_dir(campaign, platform, vid)
    log = open(d / "watch.log", "a", encoding="utf-8")

    def L(msg):
        log.write(f"{now_iso()} {msg}\n")
        log.flush()

    t0 = time.time()
    offset = stream_start_offset(platform, vid, t0)
    status = {"pid": os.getpid(), "url": url, "platform": platform, "id": vid, "started": now_iso(), "t0_epoch": t0, "state": "starting",
              "capture_offset_s": offset, "segments": 0, "chat_messages": 0, "hot_bins": 0, "deadline_epoch": t0 + hours * 3600}
    L(f"capture offset (stream seconds elapsed at start): {offset}")
    write_json(d / "status.json", status)
    seg_pattern = str(d / "segments" / "seg_%06d.ts")
    sl = [sys.executable, "-m", "streamlink", "--ffmpeg-ffmpeg", ffmpeg_bin(), "--twitch-disable-ads", "--retry-streams", "15", "--retry-max", "40",
          "--stdout", url, quality]
    ff = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-c", "copy", "-f", "segment", "-segment_time", str(SEG_S),
          "-reset_timestamps", "1", "-segment_format", "mpegts", seg_pattern]
    chat_cmd = [sys.executable, str(Path(__file__).with_name("chat.py")), "live", "--campaign", str(campaign), "--url", chat_url or url, "--duration", str(hours * 3600)]
    procs: dict[str, subprocess.Popen] = {}
    stop = {"flag": False}

    def on_term(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    def start_video():
        p1 = subprocess.Popen(sl, stdout=subprocess.PIPE, stderr=log)
        p2 = subprocess.Popen(ff, stdin=p1.stdout, stderr=log)
        p1.stdout.close()
        procs["sl"], procs["ff"] = p1, p2
        L("video pipeline started")

    def start_chat():
        procs["chat"] = subprocess.Popen(chat_cmd, stdout=log, stderr=log)
        L(f"chat collector started ({chat_url or url})")

    start_video()
    start_chat()
    chat_platform, chat_id = parse_url(chat_url or url)
    chat_file = campaign / "sources" / f"{chat_platform}-{chat_id}" / "chat.jsonl"
    last_hot = 0.0
    restarts = 0
    try:
        while not stop["flag"] and time.time() < status["deadline_epoch"]:
            time.sleep(5)
            if procs["ff"].poll() is not None or procs["sl"].poll() is not None:
                restarts += 1
                L(f"video pipeline ended (sl={procs['sl'].poll()} ff={procs['ff'].poll()}); restart #{restarts}")
                if restarts > 30:
                    status["state"] = "ended: stream offline"
                    break
                time.sleep(20)
                start_video()
            if procs["chat"].poll() is not None and time.time() - t0 < hours * 3600 - 60:
                L("chat collector exited; restarting in 30 s")
                time.sleep(30)
                start_chat()
            segs = sorted((d / "segments").glob("seg_*.ts"))
            status["segments"] = len(segs)
            status["buffer_minutes"] = round(len(segs) * SEG_S / 60, 1)
            # prune old segments
            keep = int(keep_minutes * 60 / SEG_S)
            for old in segs[:-keep] if len(segs) > keep else []:
                old.unlink(missing_ok=True)
            if chat_file.exists():
                status["chat_messages"] = sum(1 for _ in open(chat_file, encoding="utf-8", errors="ignore"))
            if time.time() - last_hot >= 60 and chat_file.exists():
                hot = hot_bins(chat_file, window_s=600)
                status["hot_bins"] = len(hot)
                write_json(d / "hot.json", {"updated": now_iso(), "hot": hot, "note": "t = seconds from stream start; use watch.py clip --start --end"})
                last_hot = time.time()
            status["state"] = "running"
            status["updated"] = now_iso()
            write_json(d / "status.json", status)
    finally:
        for p in procs.values():
            try:
                p.terminate()
            except Exception:
                pass
        status["state"] = status["state"] if status["state"].startswith("ended") else "stopped"
        status["ended"] = now_iso()
        write_json(d / "status.json", status)
        L("watcher stopped")


def hot_bins(chat_file: Path, window_s: int = 600, bin_s: int = 10) -> list[dict]:
    msgs = []
    for line in open(chat_file, encoding="utf-8", errors="ignore"):
        try:
            msgs.append(json.loads(line))
        except Exception:
            continue
    if not msgs:
        return []
    t_max = max(m["t"] for m in msgs)
    recent = [m for m in msgs if m["t"] >= t_max - window_s]
    users = collections.defaultdict(set)
    texts = collections.defaultdict(list)
    for m in recent:
        b = int(m["t"] // bin_s) * bin_s
        users[b].add(m.get("user"))
        texts[b].append(m.get("text", ""))
    counts = {b: len(u) for b, u in users.items()}
    if not counts:
        return []
    base = max(2.0, sorted(counts.values())[len(counts) // 2])
    hot = []
    for b, n in counts.items():
        ratio = n / base
        digits = sum(1 for t in texts[b] if re.fullmatch(r"[\d\s!.]+", t or ""))
        spam = len(texts[b]) >= 6 and digits / len(texts[b]) >= 0.35
        if ratio >= 3 and not spam:
            hot.append({"t": b, "unique_chatters": n, "ratio": round(ratio, 1), "sample": [t for t in texts[b] if len(t) > 2][:5]})
    return sorted(hot, key=lambda h: -h["ratio"])[:10]


# ------------------------------------------------------------ commands


def cmd_start(a) -> None:
    campaign = find_campaign(a.campaign)
    platform, vid = parse_url(a.url)
    d = live_dir(campaign, platform, vid)
    st = read_json(d / "status.json", {})
    if st.get("pid") and _alive(st["pid"]):
        raise StepError(f"watcher already running (pid {st['pid']}) for {a.url}")
    cmd = [sys.executable, str(Path(__file__).resolve()), "_worker", "--campaign", str(campaign), "--url", a.url, "--hours", str(a.hours),
           "--quality", a.quality, "--keep-minutes", str(a.keep_minutes)]
    if a.chat_url:
        cmd += ["--chat-url", a.chat_url]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=open(d / "worker.err", "a"), stdin=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, "PYTHONUNBUFFERED": "1"})
    time.sleep(3)
    out({"ok": True, "pid": p.pid, "live_dir": str(d), "status_file": str(d / "status.json"),
         "note": "runs in the background; check with watch.py status; hot moments in hot.json; cut with watch.py clip"})


def cmd_status(a) -> None:
    campaign = find_campaign(a.campaign)
    res = []
    for st in (campaign / "live").glob("*/status.json"):
        s = read_json(st, {})
        s["alive"] = bool(s.get("pid")) and _alive(s["pid"])
        s["hot"] = read_json(st.parent / "hot.json", {}).get("hot", [])[:5]
        res.append(s)
    out({"ok": True, "watchers": res})


def cmd_stop(a) -> None:
    campaign = find_campaign(a.campaign)
    stopped = []
    for d in find_running(campaign):
        pid = read_json(d / "status.json")["pid"]
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            os.kill(pid, signal.SIGTERM)
        stopped.append(pid)
    out({"ok": True, "stopped_pids": stopped})


def cmd_clip(a) -> None:
    """Concatenate buffered segments covering [start,end] (stream seconds) into sources/<platform>-<id>/segments/."""
    campaign = find_campaign(a.campaign)
    dirs = find_running(campaign) or sorted((campaign / "live").glob("*/"), key=lambda p: p.stat().st_mtime)
    if not dirs:
        raise StepError("no watcher found")
    d = dirs[-1]
    st = read_json(d / "status.json", {})
    segs = sorted((d / "segments").glob("seg_*.ts"))
    if not segs:
        raise StepError("no segments buffered yet")
    # map stream seconds -> segment index: segment k starts at (k*SEG_S) from capture start; capture start ~ t0 relative to stream start
    t0_rel = float(st.get("capture_offset_s", a.capture_offset or 0))  # seconds of stream already elapsed when capture began
    first_idx = int(segs[0].stem.split("_")[1])
    i0 = max(first_idx, int((a.start - t0_rel) // SEG_S) - 1)
    i1 = int((a.end - t0_rel) // SEG_S) + 1
    chosen = [s for s in segs if i0 <= int(s.stem.split("_")[1]) <= i1]
    if not chosen:
        raise StepError(f"requested range not in buffer (buffer covers segments {first_idx}..{int(segs[-1].stem.split('_')[1])})")
    lst = d / "concat.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in chosen), encoding="utf-8")
    sdir = campaign / "sources" / d.name
    (sdir / "segments").mkdir(parents=True, exist_ok=True)
    seg_start = i0 * SEG_S + t0_rel
    seg_end = (i1 + 1) * SEG_S + t0_rel
    dst = sdir / "segments" / f"{int(seg_start):06d}-{int(seg_end):06d}.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    "-avoid_negative_ts", "make_zero", "-fflags", "+genpts", "-movflags", "+faststart", str(dst)], check=True, timeout=600)
    meta = read_json(sdir / "meta.json", {"platform": st.get("platform"), "id": st.get("id"), "url": st.get("url"), "segments": []})
    meta["segments"].append({"file": dst.name, "start_s": seg_start, "end_s": seg_end, "fetched": now_iso(), "from": "live_buffer"})
    write_json(sdir / "meta.json", meta)
    out({"ok": True, "file": str(dst), "offset_s": seg_start, "source_dir": str(sdir),
         "next": f"signals.py --campaign {campaign} --source sources/{d.name} --segment {dst.name}"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--campaign")
    s.add_argument("--url", required=True)
    s.add_argument("--chat-url", help="collect chat from a different platform's simulcast (e.g. Twitch IRC while recording YouTube)")
    s.add_argument("--hours", type=float, default=10)
    s.add_argument("--quality", default="1080p60,1080p,720p60,720p,best")
    s.add_argument("--keep-minutes", type=int, default=180)
    s.set_defaults(fn=cmd_start)
    w = sub.add_parser("_worker")
    w.add_argument("--campaign")
    w.add_argument("--url", required=True)
    w.add_argument("--chat-url")
    w.add_argument("--hours", type=float, default=10)
    w.add_argument("--quality", default="best")
    w.add_argument("--keep-minutes", type=int, default=180)
    w.set_defaults(fn=lambda a: worker(find_campaign(a.campaign), a.url, a.chat_url, a.hours, a.quality, a.keep_minutes))
    for name, fn in (("status", cmd_status), ("stop", cmd_stop)):
        p = sub.add_parser(name)
        p.add_argument("--campaign")
        p.set_defaults(fn=fn)
    c = sub.add_parser("clip")
    c.add_argument("--campaign")
    c.add_argument("--start", type=float, required=True)
    c.add_argument("--end", type=float, required=True)
    c.add_argument("--capture-offset", type=float, help="stream seconds elapsed when capture began (auto for YouTube/Twitch)")
    c.set_defaults(fn=cmd_clip)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main_guard(main)
