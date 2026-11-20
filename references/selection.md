# Selecting moments

`signals.py` writes `sources/<id>/candidates.json`. Each candidate:

| Field | Meaning |
|---|---|
| `score` 0..1 | weighted mix of components (+ agreement bonus when chat and audio fire together) |
| `components.chat` | unique chatters vs rolling 10-min baseline (6x = 1.0), boosted by reaction words (laugh/shock/hype), zeroed for giveaway/number spam |
| `components.audio` | loudness z-score vs 60 s median (4 = 1.0), boosted when the spike is bright (screams, shouting) |
| `components.speech` | reaction words / exclamations in the transcript |
| `promo: true` | like/subscribe/giveaway talk; score halved. Almost never a clip. |
| `peak_t`, `start_t`, `end_t` | stream seconds; start = peak - 8, end = peak + 14 by default |
| `chat_sample`, `chat_label` | what chat said and why (laugh, shock, hype) |
| `transcript`, `words` | fast scan transcription (Groq turbo). Good enough to judge the moment; the final clip gets a thorough pass at build time |
| `frames` | 2x2 sheet at peak -6/-2/+1/+5 s. **Look at it.** |
| `agent` | your notes/decisions; preserved when signals re-runs |

## How many clips?

`suggested_clip_count` gives strong (>= 0.55) and solid (0.35-0.55). Turn that into a proposal:

- Strong + visually clear + brief-preferred type -> make it.
- Solid -> decide from frames + transcript: is there a setup, a payoff and a reaction? Does it stand alone without context?
- Below 0.35 -> skip, unless chat literally says "clip it/that" or the frames show something extraordinary.
- One stream rarely deserves more than 6-10 clips a day; the brief's `max_posts_per_clip` and the audience's tolerance matter more than volume.
- Same moment from two angles (chat spike + audio spike 10 s apart) is one clip, not two.

Tell the user: "From <source> I'd make N clips: <id> (<why>), ... Skipping <id> because <reason>." Then proceed or wait as instructed.

## What to skip (common false positives)

- Giveaways / "type 1" / sub-goal celebrations (chat spam filter catches most, you catch the rest).
- Map/menu screens with a loud reaction to something off-screen (frames show a map).
- Long silences, loading screens, ads.
- Anything the brief bans: other games, other creators on screen, misleading context, mature content beyond the game's own.
- Moments where the streamer reads an offensive chat message aloud (campaign "banned words" risk).

## Refine the window before rendering

- Start on a word boundary ~1-2 s before the setup line; end 1-2 s after the reaction ends (laugh tail), never mid-word.
- Target length from `clip.target_seconds` (15-35 s for gaming). Longer than 40 s needs a second payoff.
- `zoom_at_rel` = the payoff moment (crash, scream, punchline), not the start.
- If the reaction is on the face and the payoff is on screen at the same time, keep `face-top`; if the face is hidden (menu overlay), use `fill`.

## Agent vision checklist (per frame sheet)

1. Is the streamer's face visible and expressive in at least one frame? (face-top layout needs it)
2. Is the on-screen action readable at 9:16 crop (centre of the frame)? If the action is at the edge, note a custom crop in `spec.json`.
3. Any third-party content (other streamer's cam, copyrighted music video, donation text with slurs)? Skip or trim.
4. Does what you see match the hook you are about to write? If not, rewrite the hook, not the truth.
