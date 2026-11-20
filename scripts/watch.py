#!/usr/bin/env python3
"""Live watcher: record a stream in 10 s segments, collect chat, and flag hot moments while the stream runs.

  watch.py start  --campaign <dir> --url https://www.youtube.com/watch?v=ID [--chat-url https://www.twitch.tv/login] [--hours 10]
                  [--quality 1080p60,1080p,720p60,720p,best] [--keep-minutes 180]
  watch.py status --campaign <dir>
  watch.py stop   --campaign <dir> [--url ...]
  watch.py clip   --campaign <dir> --start <stream_seconds> --end <stream_seconds> [--url ...]   # cut from the live buffer -> sources/<id>/segments/

Runs detached so the agent session can end (POSIX: new session; Windows: new process group). State in
<campaign>/live/<platform>-<id>/: status.json, segments/ (epoch-named .ts), segments.json (index), hot.json, watch.log.

Time model: every segment is named by the wall-clock second it started. `capture_offset_s` = stream seconds already
elapsed when capture began (from the platform's start time). So stream_t = (segment_epoch - t0_epoch) + capture_offset_s,
which survives streamlink restarts. Chat `t` from chat.py is also stream seconds (YouTube API / Twitch), so hot.json and
`watch.py clip --start/--end` use the same clock. When chat comes from a different platform (--chat-url), chat.jsonl is
mirrored into the video's source folder so signals.py finds it.

Every 60 s the last 10 minutes of chat are scored into hot.json (bins with unique-chatter ratio >= 3x, spam filtered).
Buffer keeps the last --keep-minutes of segments.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from common import IS_WIN, StepError, ffmpeg_bin, find_campaign, main_guard, now_iso, out, read_json, write_json
from fetch import parse_url

SEG_S = 10
STOP_FILE = "stop"


def live_dir(campaign: Path, platform: str, vid: str) -> Path:
    d = campaign / "live" / f"{platform}-{vid}"
    (d / "segments").mkdir(parents=True, exist_ok=True)
    return d


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if IS_WIN:
        try:
            import ctypes

            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def watcher_dirs(campaign: Path, url: str | None = None) -> list[Path]:
    dirs = sorted((p for p in (campaign / "live").glob("*/") if (p / "status.json").exists()), key=lambda p: p.stat().st_mtime)
    if url:
        platform, vid = parse_url(url)
        dirs = [d for d in dirs if d.name == f"{platform}-{vid}"]
    return dirs


def stream_start_offset(platform: str, vid: str, now_epoch: float) -> float:
    """Seconds of stream already elapsed when capture starts."""
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
    (d / STOP_FILE).unlink(missing_ok=True)
    log = open(d / "watch.log", "a", encoding="utf-8")

    def L(msg):
        log.write(f"{now_iso()} {msg}\n")
        log.flush()

    t0 = time.time()
    offset = stream_start_offset(platform, vid, t0)
    status = {"pid": os.getpid(), "url": url, "chat_url": chat_url, "platform": platform, "id": vid, "started": now_iso(), "t0_epoch": t0,
              "capture_offset_s": offset, "state": "starting", "segments": 0, "chat_messages": 0, "hot_bins": 0,
              "deadline_epoch": t0 + hours * 3600, "stream_t_now": None}
    L(f"capture offset (stream seconds elapsed at start): {offset}")
    write_json(d / "status.json", status)
    # epoch-named segments: seg_<unix seconds>.ts -> survives restarts, maps to stream time
    seg_pattern = str(d / "segments" / "seg_%s.ts")
    sl = [sys.executable, "-m", "streamlink", "--ffmpeg-ffmpeg", ffmpeg_bin(), "--twitch-disable-ads", "--retry-streams", "15", "--retry-max", "40",
          "--stdout", url, quality]
    ff = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-c", "copy", "-f", "segment", "-segment_time", str(SEG_S),
          "-reset_timestamps", "1", "-strftime", "1", "-segment_format", "mpegts", seg_pattern]
    chat_target = chat_url or url
    chat_platform, chat_id = parse_url(chat_target)
    chat_cmd = [sys.executable, str(Path(__file__).with_name("chat.py")), "live", "--campaign", str(campaign), "--url", chat_target, "--duration", str(hours * 3600)]
    chat_src_dir = campaign / "sources" / f"{chat_platform}-{chat_id}"
    video_src_dir = campaign / "sources" / f"{platform}-{vid}"
    chat_file = chat_src_dir / "chat.jsonl"
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
        L(f"chat collector started ({chat_target})")

    start_video()
    start_chat()
    last_hot = 0.0
    restarts = 0
    chat_restarts = 0
    chat_lines = 0
    try:
        while not stop["flag"] and time.time() < status["deadline_epoch"] and not (d / STOP_FILE).exists():
            time.sleep(5)
            if procs["ff"].poll() is not None or procs["sl"].poll() is not None:
                restarts += 1
                L(f"video pipeline ended (sl={procs['sl'].poll()} ff={procs['ff'].poll()}); restart #{restarts}")
                for k in ("sl", "ff"):
                    try:
                        procs[k].terminate()
                    except Exception:
                        pass
                if restarts > 30:
                    status["state"] = "ended: stream offline"
                    break
                time.sleep(20)
                start_video()
            if procs["chat"].poll() is not None and time.time() < status["deadline_epoch"] - 60:
                chat_restarts += 1
                if chat_restarts <= 20:
                    L(f"chat collector exited rc={procs['chat'].poll()}; restarting in 30 s (#{chat_restarts})")
                    time.sleep(30)
                    start_chat()
                elif chat_restarts == 21:
                    L("chat collector failed 20 times; giving up on chat (video continues)")
            segs = sorted((d / "segments").glob("seg_*.ts"))
            # prune by age
            cutoff = time.time() - keep_minutes * 60
            for sp in segs:
                try:
                    if int(sp.stem.split("_")[1]) < cutoff:
                        sp.unlink(missing_ok=True)
                except (IndexError, ValueError):
                    pass
            segs = sorted((d / "segments").glob("seg_*.ts"))
            status["segments"] = len(segs)
            status["buffer_minutes"] = round(len(segs) * SEG_S / 60, 1)
            if segs:
                first = int(segs[0].stem.split("_")[1])
                status["buffer_stream_t"] = [round(first - t0 + offset, 1), round(time.time() - t0 + offset, 1)]
            status["stream_t_now"] = round(time.time() - t0 + offset, 1)
            if chat_file.exists():
                try:
                    with open(chat_file, "rb") as fh:
                        chat_lines = sum(1 for _ in fh)
                except OSError:
                    pass
                status["chat_messages"] = chat_lines
                if chat_src_dir != video_src_dir:
                    video_src_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copyfile(chat_file, video_src_dir / "chat.jsonl")  # signals.py reads chat next to the video
                    except OSError:
                        pass
            if time.time() - last_hot >= 60 and chat_file.exists():
                hot = hot_bins(chat_file, window_s=600)
                status["hot_bins"] = len(hot)
                write_json(d / "hot.json", {"updated": now_iso(), "hot": hot, "stream_t_now": status["stream_t_now"],
                                            "note": "t = stream seconds (same clock as watch.py clip --start/--end)"})
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
        time.sleep(1)
        for p in procs.values():
            try:
                if p.poll() is None:
                    p.kill()
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
    if (platform == "twitch" and vid.isdigit()) or (platform == "kick" and len(vid) == 36):
        raise StepError("that is a VOD url; watch.py is for live channels. Use fetch.py for replays.")
    d = live_dir(campaign, platform, vid)
    st = read_json(d / "status.json", {})
    if st.get("pid") and _alive(st["pid"]):
        raise StepError(f"watcher already running (pid {st['pid']}) for {a.url}")
    cmd = [sys.executable, str(Path(__file__).resolve()), "_worker", "--campaign", str(campaign), "--url", a.url, "--hours", str(a.hours),
           "--quality", a.quality, "--keep-minutes", str(a.keep_minutes)]
    if a.chat_url:
        cmd += ["--chat-url", a.chat_url]
    kw: dict = {"stdout": subprocess.DEVNULL, "stderr": open(d / "worker.err", "a"), "stdin": subprocess.DEVNULL,
                "env": {**os.environ, "PYTHONUNBUFFERED": "1"}}
    if IS_WIN:
        kw["creationflags"] = 0x00000200 | 0x00000008  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, **kw)
    time.sleep(3)
    st = read_json(d / "status.json", {})
    out({"ok": True, "pid": p.pid, "live_dir": str(d), "status_file": str(d / "status.json"), "capture_offset_s": st.get("capture_offset_s"),
         "note": "runs in the background; check with watch.py status; hot moments in hot.json; cut with watch.py clip --start --end (stream seconds)"})


def cmd_status(a) -> None:
    campaign = find_campaign(a.campaign)
    res = []
    for d in watcher_dirs(campaign):
        s = read_json(d / "status.json", {})
        s["alive"] = _alive(s.get("pid", 0))
        s["hot"] = read_json(d / "hot.json", {}).get("hot", [])[:5]
        res.append(s)
    out({"ok": True, "watchers": res})


def cmd_stop(a) -> None:
    campaign = find_campaign(a.campaign)
    stopped = []
    for d in watcher_dirs(campaign, a.url):
        st = read_json(d / "status.json", {})
        pid = st.get("pid")
        if pid and _alive(pid):
            (d / STOP_FILE).write_text("stop", encoding="utf-8")  # portable: the worker loop exits and terminates its children
            stopped.append(pid)
    time.sleep(7)
    for d in watcher_dirs(campaign, a.url):
        pid = read_json(d / "status.json", {}).get("pid")
        if pid and _alive(pid) and not IS_WIN:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                pass
    out({"ok": True, "stopped_pids": stopped})


def cmd_clip(a) -> None:
    """Concatenate buffered segments covering [start,end] (stream seconds) into sources/<platform>-<id>/segments/."""
    campaign = find_campaign(a.campaign)
    dirs = watcher_dirs(campaign, a.url)
    if not dirs:
        raise StepError("no watcher found" + (" for that url" if a.url else ""))
    if len(dirs) > 1 and not a.url:
        raise StepError("several watchers exist; pass --url to choose")
    d = dirs[-1]
    st = read_json(d / "status.json", {})
    t0, offset = float(st.get("t0_epoch", 0)), float(a.capture_offset if a.capture_offset is not None else st.get("capture_offset_s", 0.0))
    segs = sorted((d / "segments").glob("seg_*.ts"))
    if not segs:
        raise StepError("no segments buffered yet")
    newest = segs[-1]
    if _alive(st.get("pid", 0)):
        segs = segs[:-1]  # the newest file is still being written
    def seg_stream_t(p: Path) -> float:
        return int(p.stem.split("_")[1]) - t0 + offset
    chosen = [p for p in segs if seg_stream_t(p) + SEG_S > a.start - 0.5 and seg_stream_t(p) < a.end + 0.5]
    if not chosen:
        rng = (round(seg_stream_t(segs[0]), 1), round(seg_stream_t(segs[-1]) + SEG_S, 1)) if segs else None
        raise StepError(f"requested range not in buffer (buffer covers stream seconds {rng}); fetch it from the replay later")
    lst = d / "concat.txt"
    lst.write_text("".join("file '" + str(s).replace("'", "'\\''") + "'\n" for s in chosen), encoding="utf-8")
    sdir = campaign / "sources" / d.name
    (sdir / "segments").mkdir(parents=True, exist_ok=True)
    seg_start = seg_stream_t(chosen[0])
    seg_end = seg_stream_t(chosen[-1]) + SEG_S
    dst = sdir / "segments" / f"{int(seg_start):06d}-{int(seg_end):06d}.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    "-avoid_negative_ts", "make_zero", "-fflags", "+genpts", "-movflags", "+faststart", str(dst)], check=True, timeout=600)
    meta = read_json(sdir / "meta.json", {"platform": st.get("platform"), "id": st.get("id"), "url": st.get("url"), "segments": []})
    meta["segments"] = [x for x in meta["segments"] if x["file"] != dst.name] + [{"file": dst.name, "start_s": round(seg_start, 2), "end_s": round(seg_end, 2), "fetched": now_iso(), "from": "live_buffer"}]
    write_json(sdir / "meta.json", meta)
    out({"ok": True, "file": str(dst), "offset_s": round(seg_start, 2), "source_dir": str(sdir), "newest_segment_excluded": newest.name if segs and newest not in segs else None,
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
        p.add_argument("--url")
        p.set_defaults(fn=fn)
    c = sub.add_parser("clip")
    c.add_argument("--campaign")
    c.add_argument("--url")
    c.add_argument("--start", type=float, required=True)
    c.add_argument("--end", type=float, required=True)
    c.add_argument("--capture-offset", type=float, help="override stream seconds elapsed when capture began")
    c.set_defaults(fn=cmd_clip)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main_guard(main)
