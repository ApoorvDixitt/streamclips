# Packaging, daily folders, memory

## Per clip folder `YYYY-MM-DD/NN-<slug>/`

| File | Purpose |
|---|---|
| `clip.mp4` | the deliverable (1080x1920) |
| `thumb.jpg` | frame at 1.0 s with the hook on screen (cover image) |
| `captions.srt` | for platforms that accept uploaded subtitles / accessibility |
| `captions.ass` | the burned caption script (debug) |
| `post_tiktok.txt`, `post_instagram_reels.txt`, `post_youtube_shorts.txt` | paste-ready text per platform (required text + exact hashtags) |
| `checklist.md` | pre-post checklist from the campaign rules + posting time + submission deadline |
| `spec.json`, `review.json`, `frames.jpg` | how it was made and proven |
| `metadata.json` | machine-readable summary (status, post_at, audience, post_count) |

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
