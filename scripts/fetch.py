#!/usr/bin/env python3
"""Download a time range of a replay (or a whole short video) into the campaign's sources cache.

  fetch.py --campaign <dir> --url <twitch-or-youtube-url> --start 2:00:00 --end 2:30:00 [--quality 720p60,720p,best]

Writes sources/<platform>-<id>/segments/<start>-<end>.mp4 (timestamps reset to 0) and updates meta.json.
Keep ranges <= 60 min; fetch around chat spikes first (see references/sourcing.md).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from common import StepError, duration, ffmpeg_bin, find_campaign, hms, main_guard, now_iso, out, parse_time, read_json, write_json


def parse_url(url: str) -> tuple[str, str]:
    m = re.search(r"twitch\.tv/videos/(\d+)", url)
    if m:
        return "twitch", m.group(1)
    m = re.search(r"(?:v=|youtu\.be/|/live/|/shorts/)([A-Za-z0-9_-]{11})", url)
    if m:
        return "youtube", m.group(1)
    m = re.search(r"kick\.com/(?:video/)?([A-Za-z0-9_-]+)", url)
    if m:
        return "kick", m.group(1)
    raise StepError(f"unsupported url: {url}")


def source_dir(campaign: Path, platform: str, vid: str) -> Path:
    d = campaign / "sources" / f"{platform}-{vid}"
    (d / "segments").mkdir(parents=True, exist_ok=True)
    return d


def streamlink_fetch(url: str, start: float, end: float, quality: str, dst_ts: Path) -> None:
    cmd = [sys.executable, "-m", "streamlink", "--ffmpeg-ffmpeg", ffmpeg_bin(), "--hls-start-offset", hms(start).split(".")[0],
           "--hls-duration", hms(end - start).split(".")[0], url, quality, "-o", str(dst_ts), "--force"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if not dst_ts.exists() or dst_ts.stat().st_size < 100_000:
        raise StepError("streamlink produced no data:\n" + (r.stderr or r.stdout)[-1200:])


def ytdlp_fetch(url: str, start: float, end: float, dst: Path) -> None:
    sec = f"*{int(start)}-{int(end)}"
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--download-sections", sec,
           "--force-keyframes-at-cuts", "--merge-output-format", "mp4", "--ffmpeg-location", str(Path(ffmpeg_bin()).parent), "-o", str(dst), url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if not dst.exists():
        raise StepError("yt-dlp failed:\n" + (r.stderr or r.stdout)[-1200:])


def remux(src: Path, dst: Path) -> None:
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-c", "copy", "-avoid_negative_ts", "make_zero",
                    "-fflags", "+genpts", "-movflags", "+faststart", str(dst)], check=True, timeout=1800)
    src.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign")
    ap.add_argument("--url", required=True)
    ap.add_argument("--start", default="0")
    ap.add_argument("--end")
    ap.add_argument("--quality", default="1080p60,1080p,720p60,720p,best")
    a = ap.parse_args()
    campaign = find_campaign(a.campaign)
    platform, vid = parse_url(a.url)
    sdir = source_dir(campaign, platform, vid)
    start = parse_time(a.start)
    if not a.end:
        raise StepError("--end is required (keep ranges <= 60 min; fetch around chat spikes first)")
    end = parse_time(a.end)
    if end - start > 3900:
        raise StepError("range longer than 65 min; split it")
    name = f"{int(start):06d}-{int(end):06d}"
    final = sdir / "segments" / f"{name}.mp4"
    if final.exists() and final.stat().st_size > 100_000:
        out({"ok": True, "cached": True, "file": str(final), "offset_s": start, "duration_s": duration(final)})
        return
    if platform == "twitch":
        tmp = final.with_suffix(".ts")
        streamlink_fetch(a.url, start, end, a.quality, tmp)
        remux(tmp, final)
    else:
        tmp = final.with_name(name + ".raw.mp4")
        ytdlp_fetch(a.url, start, end, tmp)
        remux(tmp, final)
    dur = duration(final)
    meta = read_json(sdir / "meta.json", {"platform": platform, "id": vid, "url": a.url, "segments": []})
    meta["segments"] = [s for s in meta["segments"] if s["file"] != final.name] + [{"file": final.name, "start_s": start, "end_s": start + dur, "fetched": now_iso()}]
    write_json(sdir / "meta.json", meta)
    out({"ok": True, "file": str(final), "offset_s": start, "duration_s": dur, "source_dir": str(sdir)})


if __name__ == "__main__":
    main_guard(main)
