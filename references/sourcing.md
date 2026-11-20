# Sourcing: live vs replay, chat, downloads

## Order of truth

1. **YouTube Data API** (key): measured, stable. `discover.py` uses ~3 units per run (uploads playlist + videos.list). Live chat via
   `liveChatMessages.list` costs 5 units per call; `chat.py live --poll 30` ~ 600 units/hour, so a 10k-unit day covers ~16 hours.
2. **yt-dlp**: no key, breaks occasionally. Fallback for discovery (`/streams` tab) and live chat (tails the `.live_chat.json.part`).
3. **Twitch** (anonymous GQL/IRC): discovery, live chat (IRC), and **replay chat** (only platform that reliably keeps it). The GQL
   endpoint is unofficial: `chat.py` uses whole-second offsets because cursor paging fails Twitch's integrity check. If it breaks,
   fall back to audio + speech signals and tell the user.
4. **Kick**: discovery only; chat is deleted after the stream.

YouTube uploads of finished streams usually have **no chat replay**; the Twitch VOD of the same stream does. Use both platforms:
YouTube for measured discovery/live chat, Twitch for replay chat. Chat `t` is seconds from stream start on both.

## Decision: what to clip first

`discover.py` prints `recommendation.steps` ordered by `source_priority`:

- **live_first**: brief rewards speed or says "clip live" -> `watch.py start` immediately (record + chat + hot moments); clip from the
  buffer as hot bins appear; replays after the stream.
- **auto**: freshest replay (<= `replay_max_age_days`, >= 10 min, on-game) first, because candidates exist within minutes; in parallel,
  if the creator is live, start `watch.py` so today's clips include live moments. Prefer the Twitch replay when both exist (chat replay).
- **replay_first**: replays only; mention the live stream to the user.

Edge cases: a Twitch VOD of a stream that is **still live** is flagged `still_recording: true` and sorted after finished replays; its chat
replay works for the part already streamed, so it is a fine source for "what happened earlier today". Streams that switch games: filter candidates by what you see in the frame sheets. "Just chatting" intros: the first 10-20 min
are usually not clip material unless chat spikes. Multi-hour marathons (10-20 h): work in 1-hour chat windows, newest first.

## Replay workflow (cheap first)

```bash
chat.py replay --campaign C --url https://www.twitch.tv/videos/ID               # whole VOD, ~12 s per 30 min of chat
signals.py --campaign C --source sources/twitch-ID                               # chat-only candidates
fetch.py   --campaign C --url ... --start 2:10:00 --end 2:40:00                  # windows around the top bins, <= 60 min each
signals.py --campaign C --source sources/twitch-ID --segment 007800-009600.mp4   # audio + transcript + frames for that window
```

Fetch at 1080p when offered (Twitch: `--quality` default prefers 1080p60; YouTube: best <= 1080p); 720p face crops look soft. A 10-min
720p Twitch window is ~35 MB and takes ~1 min; YouTube sections cut on keyframes (a few seconds early, harmless) without re-encoding.
Delete `segments/` of finished sources when disk is tight. `fetch.py` refuses live channel URLs: use `watch.py` for those.

## Live workflow

```bash
watch.py start  --campaign C --url https://www.youtube.com/watch?v=ID --chat-url https://www.twitch.tv/login   # chat from the simulcast if you prefer IRC
watch.py status --campaign C                      # state, buffer minutes, chat count, top hot bins (t = stream seconds)
watch.py clip   --campaign C --start 22400 --end 22440       # cut from the buffer -> sources/<id>/segments/
signals.py --campaign C --source sources/<id> --segment ...  # then the normal select/render path
watch.py stop   --campaign C
```

`watch.py` is detached (survives the agent session; macOS/Linux and Windows), restarts streamlink on drops, prunes the buffer to
`--keep-minutes`, and rescans the last 10 minutes of chat every 60 s into `hot.json`. Segments are named by wall-clock second, and
`status.json` has `capture_offset_s` and `buffer_stream_t`, so `watch.py clip --start/--end` take **stream seconds**, the same clock as
chat `t` and `hot.json`. Keep 2-3 minutes behind live so the reaction has finished (the newest segment is still being written and is skipped).
With `--chat-url` (Twitch chat while recording YouTube) the chat is mirrored into the video's `sources/<id>/chat.jsonl` so `signals.py` finds it.
If the YouTube API quota is exhausted, `chat.py live` raises and `watch.py` restarts it; run it with `--collector yt-dlp` for a zero-quota
fallback (then chat `t` is relative to collector start and `chat_meta.json` says so; convert with `t + (collector_start_epoch - t0_epoch) + capture_offset_s`).

## Quota and rate budget per day (free tiers)

| Resource | Budget | Typical use |
|---|---|---|
| YouTube API | 10,000 units | discover 3/run; live chat 5/poll (30 s poll = 600/h); `search` 100 (avoid) |
| Groq Whisper | ~8 audio-hours | ~25 s window per candidate -> hundreds of candidates/day |
| Groq LLM | generous | not used by scripts; the agent is the LLM |
| Twitch GQL/IRC | unofficial | replay chat ~1 call per 50 messages; be gentle (no parallel hammering) |
