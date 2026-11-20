---
name: streamclips
description: Turn a streamer's live streams and replays into campaign-compliant vertical clips (TikTok, Reels, Shorts) for paid clipping campaigns such as Whop Content Rewards, ClipLaunch, Vyro. Use when the user mentions clipping, clip campaigns, GTA 6 / gaming stream clips, finding viral moments in a stream, hooks and captions for Shorts, or asks to "start clipping" a creator. Scripts do the heavy work (download, chat, transcription, moment scoring, render, QA, packaging); the agent does the judgment (which moments, hook, trends, posting time).
---

# streamclips

You are the editor-in-chief. The scripts in `scripts/` are your hands. Every script prints JSON, exits non-zero on failure,
and never prints secrets. Run them with the skill's Python: `python <skill>/scripts/<name>.py ...`
(use the venv created at setup; see `references/setup.md`). All paths below are relative to this skill folder.

## When a user talks to you

| User says | Do |
|---|---|
| "install / set up streamclips" | `references/setup.md`. Run `doctor.py`, ask for missing keys, re-run until `ok: true`. |
| "new campaign", pastes a brief | `references/campaign.md`. `campaign.py new ...`, fill `campaign.yaml` from the brief, `campaign.py validate`. Ask where the campaign folder should live. |
| "start clipping <creator/campaign>", "clips for today" | The daily loop below. |
| "is anything happening live?" | `discover.py` -> if live, `watch.py start`, then poll `watch.py status`; hot moments -> `watch.py clip` -> signals -> clip. |
| "post plan", "when do I post" | `references/posting.md`: research, then `package.py --post-at ... --audience ...`. |
| "views for clip X were N" | Edit `index.csv` views columns, then `learn.py`. Update `MEMORY.md`. |
| "show me progress" | `dashboard.py --campaign <dir> --open` (background), and summarise the day README. |

## The daily loop (one campaign)

1. **Orient.** `campaign.py show`, read `MEMORY.md` and the latest `YYYY-MM-DD/README.md`. `campaign.py day` creates today's folder.
2. **Discover.** `discover.py --campaign <dir>`. Read `recommendation.why` and `steps`. Decide, do not hard-code:
   - `source_priority: live_first` -> live watcher first, replays after.
   - `auto` (default) -> start from the freshest replay (<= `replay_max_age_days`) **now**, and if a stream is live, start `watch.py` in the
     background at the same time so you can clip fresh moments as they happen. Prefer replays that have chat replay (Twitch).
   - `replay_first` -> only replays unless nothing is recent.
   - Nothing live and no recent replay -> tell the user; ask whether to widen `replay_max_age_days` or use older VODs.
3. **Chat first, video second (replays).** `chat.py replay` for the whole VOD (cheap), then `signals.py --source <dir>` (chat-only pass).
   Fetch only the windows around the top chat bins: `fetch.py --start --end` (<= 60 min each), then `signals.py --segment` for audio +
   transcript + frame sheets. For a stream with no chat replay, fetch in 30-min chunks and rely on audio + speech.
4. **Select.** `references/selection.md`. Read `candidates.json`, **look at every `frames/<id>.jpg`**, read transcripts and chat
   samples. Propose "this source can give N clips: X strong, Y solid" with one line of reasoning each, and ask the user to confirm
   (or proceed if they said "just make them"). Skip promo/giveaway moments and anything the brief bans.
5. **Hook and copy.** `references/hooks.md`. Write 3 hooks per clip from the transcript and frames only. Optional trend layer: a quick
   web search for the moment's subject; use it only if the transcript/frames support it. `hook_check.py` must pass.
6. **Spec and render.** `render.py spec ... --hook "..."`, adjust `spec.json` (start/end on word boundaries, zoom at the payoff), `render.py build`.
7. **Review.** `review.py --clip-dir ...` must pass hard checks, and you must look at `frames.jpg` yourself (face visible, overlay clear,
   hook readable, captions not covering the action, nothing misleading). Re-render if needed. Never ship a clip you have not looked at.
8. **Package.** `references/posting.md` for the time/audience, then `package.py --clip-dir ... --post-at ... --audience ...`.
9. **Record.** Fill today's `README.md` (plan, clips table, posting plan, lessons) and add anything durable to `MEMORY.md`
   (facecam position, streamer habits, what the campaign reviewer rejected). Stop the watcher when done or leave it running if the user wants.

Parallel work: one source per sub-agent is fine (each writes to its own `sources/<platform>-<id>/`). Only one `watch.py` per stream.

## Rules you never break

- Only sources the campaign allows (`creator.*` + `extra_sources`). Verify the stream is the right game before clipping.
- Hook and caption must be literally true to what happened. No fake context, no misleading titles, no reaction that is not there.
- Transform every clip: layout + hook + captions (+ zoom/bleeps). Raw cut-outs are "reused content" and get accounts banned.
- No reversing, mirroring, pitch-shifting or speed tricks to dodge detection. They are detectable and they look bad.
- Respect `audio.mode`. `original` means the streamer's audio is untouched except bleeps/loudness. Voiceover only when allowed, short, factual.
- Overlay required -> it stays fully visible first frame to last. `review.py` checks pixels; you check the frame sheet.
- Keys live in the user's environment only. Never write them into files, logs or chat. If a key is missing, ask the user to export it.
- Do not post, log in, or run multiple accounts for the user. Posting is manual; you prepare the pack and the plan.
- Stay inside free/cheap tiers: Groq ~8 audio-hours/day (transcribe candidate windows, not whole streams); YouTube API 10k units/day
  (live chat polling every 30 s ~ 600 units/hour).

## Script map

| Script | Job | Reference |
|---|---|---|
| `doctor.py` | tools, packages, keys; tells you what to ask the user | setup.md |
| `campaign.py` | new / set / show / validate / day | campaign.md, packaging.md |
| `discover.py` | live + replays across YouTube (API), Twitch, Kick; recommendation | sourcing.md |
| `chat.py` | replay or live chat -> `chat.jsonl` (YouTube API -> yt-dlp -> Twitch) | sourcing.md |
| `fetch.py` | download a time range -> `segments/*.mp4` | sourcing.md |
| `watch.py` | live buffer + chat + hot moments; `clip` cuts from buffer | sourcing.md |
| `signals.py` | chat/audio/speech scoring -> `candidates.json`, frame sheets | selection.md |
| `transcribe.py` | Groq Whisper (mlx fallback), cleaned word timestamps | selection.md |
| `hook_check.py` | hook rules + transcript support | hooks.md |
| `render.py` | spec -> 1080x1920 clip | render.md |
| `voiceover.py` | ElevenLabs with cache (Drew by default) | render.md |
| `review.py` | hard/soft QA + frame sheet | review.md |
| `package.py` | thumb, SRT, post texts, checklist, index.csv, day README | packaging.md, posting.md |
| `learn.py` | views -> what worked -> weight suggestions | packaging.md |
| `dashboard.py` | local read-only progress page | packaging.md |

Problems: `references/troubleshooting.md`.
