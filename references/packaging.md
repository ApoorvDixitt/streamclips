# Packaging, daily folders, memory

## Per clip folder `YYYY-MM-DD/NN-<slug>/`

The user opens `README.md` first; it says what the clip is and what to do, in plain words. Then one `post_<platform>.md` per platform
with numbered steps and the exact caption in a copy box. No `.txt` files.

| File | Purpose |
|---|---|
| `README.md` | what this clip is, what to do, what every file is, caption notes (possible uncaptioned speech) |
| `post_tiktok.md`, `post_instagram_reels.md`, `post_youtube_shorts.md` | when to post, audience, numbered upload steps, caption to copy, the campaign rules for that post, submission deadline |
| `clip.mp4` | the deliverable (1080x1920, 30 fps, -14 LUFS) |
| `thumb.jpg` | cover image (frame at 1 s with the hook) |
| `captions.srt` | optional subtitle upload |
| `transcript.json` | final transcription (engine, text, second-pass text, gaps) so the agent can verify captions |
| `frames.jpg`, `frames_safe.jpg` | six frames; the green box = area no platform UI covers |
| `spec.json`, `captions.ass`, `review.json`, `metadata.json` | build inputs and proofs (for the agent) |

Numbering: `campaign.py day` returns `next_index`; name folders `01-<slug>`, `02-<slug>` in creation order.

## Daily `README.md`

Created by `campaign.py day`; `package.py` appends a row to the clips table. You fill the rest at the end of the session:
plan and decision (live/replay/both), posting plan (per clip: platform, local time, audience window, submit-by), lessons.
Keep it factual and short; it is what tomorrow's session reads first.

## `MEMORY.md` (campaign-wide)

Update when you learn something durable: facecam region, streamer schedule (when he goes live, in the user's timezone), running
jokes/characters worth clipping, segments to avoid, what the campaign reviewer approved or rejected and why, best-performing clip types,
posting slots that worked. Dated bullets under the right heading. Remove stale notes.

## `index.csv`

One row per packaged clip. `package.py` fills source/time/hook/score/status. The user (or you, from screenshots) fills:
`posted_*` (post URLs or yes/no), `post_count`, `views_*`, `approved`, `payout_usd`, `notes`. Then `learn.py` reports what worked and,
with >= 10 clips, suggests `signals.weights`. Keep `post_count` <= `rules.max_posts_per_clip`.

## Daily rhythm the user should see

- Morning: discover -> candidates -> proposal -> clips -> posting plan in README.
- Posting times: you research (posting.md), the user posts from the mobile app, then tells you the links. Remind them of the submit-within-N-minutes rule.
- Evening: views into index.csv, `learn.py`, MEMORY.md updates, stop watchers, delete `segments/` you no longer need.

## Dashboard (optional)

`dashboard.py --campaign <dir> --open` serves a read-only page on 127.0.0.1 (live status, hot moments, clips with thumbnails and review
state, candidates, index). Start it in the background only when the user asks to watch progress; everything is also visible in the folders.
