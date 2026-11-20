# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No ffmpeg with the 'ass' filter` | system ffmpeg lacks libass | `pip install static-ffmpeg` (auto-downloads a full build) or set `FFMPEG_BIN` |
| `GROQ_API_KEY is not set` | env var missing in the agent's process | user exports it, restarts the agent session, re-run `doctor.py` |
| Groq 429 repeatedly | free tier ~8 audio-hours/day exhausted | wait for the daily reset; transcribe fewer candidates (`--no-transcribe` on scans, `final` only on clips you render); Apple Silicon can use `--engine mlx` offline; Windows/Linux have no offline engine yet |
| Twitch VOD is sub-only / age-gated | streamlink needs an auth header | not supported; use the YouTube upload of the stream |
| Kick API 403 | Cloudflare challenge | retry later; Kick live recording via `watch.py` still works through streamlink |
| `YouTube kept no chat replay` | uploads of finished streams have no chat | use the Twitch replay of the same stream, or audio+speech only |
| Twitch replay chat `failed integrity check` | Twitch changed its GQL protections | `chat.py` already uses offsets; if it persists, fall back to audio+speech and tell the user |
| `discover.py` shows no live/replays | wrong channel (secondary channel), handle typo, or API quota | check the brief's links; `campaign.py set creator.youtube_handle=@...`; wait for quota reset (midnight PT) |
| yt-dlp `Sign in to confirm you're not a bot` | YouTube bot check | fetch the Twitch VOD of the same stream instead, or wait an hour; cookies are deliberately not used |
| `clip range falls outside the fetched segment` | candidate window extends past the downloaded range | `fetch.py` a wider range, re-run `signals.py --segment` |
| `hook too long to fit in 2 lines` | > ~44 chars | shorten; `hook_check.py` |
| Face panel shows hat / HUD | `facecam_region` off | recalibrate from `frames/<id>.jpg` (render.md) |
| Red/white sliver at the left edge of gameplay | alerts spill right of the cam | increase margin: edit `facecam_region.w` slightly or use `fill` for that clip |
| Captions stacked / overlapping | old render | fixed: phrases end when the next begins; re-render |
| Overlay check < 0.85 on some frames | something drawn over the logo or wrong `y_pct` | keep the overlay band empty; adjust `overlay.y_pct`/`width_pct` |
| Loudness far from -14 | very quiet/loud source | fine if within +-2.5 LU; otherwise check the source audio stream |
| `watch.py` state `starting` and not alive | worker crashed | read `live/<id>/worker.err`; common: unsupported URL, missing key, stream offline |
| Watcher buffer empty | streamlink could not open the stream (age gate, offline) | `watch.log`; try `--quality best`; check the stream is live |
| `watch.py clip`: range not in buffer | moment older than `--keep-minutes` or before capture start | fetch it from the replay later (Twitch VOD) |
| Clips rejected by the campaign | rule mismatch | compare the clip's `post_<platform>.md` rules with the brief; record the reason in `MEMORY.md`; adjust `campaign.yaml` |
| Account shadow-banned / 200-view jail | new account skipped warm-up, posted via tools/API, switched countries | follow posting.md account rules; post from the mobile app |
