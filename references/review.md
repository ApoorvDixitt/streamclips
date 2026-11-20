# Review (QA before packaging)

`review.py --clip-dir <clip folder>` writes `review.json` and `frames.jpg` (six frames: start, 2 s, 35 %, 60 %, 85 %, end).
Exit 1 on any hard failure. Fix and re-render; do not package a failing clip.

| Check | Hard | What it means |
|---|---|---|
| duration | yes | within `clip.min_seconds`..`max_seconds` |
| duration_in_target | no | inside `clip.target_seconds` (retention sweet spot) |
| format_1080x1920 | yes | exact canvas |
| fps_30 | no | 30 fps output |
| has_audio | yes | audio track present |
| loudness_-14LUFS | no | measured integrated loudness within +-2.5 LU of -14 |
| overlay_visible_all_frames | yes (if overlay.required) | >= 85 % of opaque overlay pixels match the reference on every sampled frame; a dip means something covered the logo |
| hook_present | yes | a hook event exists in the rendered caption script |
| captions_present | yes (unless captions: none) | >= 3 caption events in the rendered caption script |
| profanity_masked | yes (if mask_profanity) | no unmasked swear in the caption file |
| not_raw_recording | yes | layout + hook + captions present (campaign "no low-effort content") |
| inside_safe_zone | yes | overlay, face panel, hook and caption zones all inside x 60-930 / y 230-1420 |
| no_uncaptioned_speech | no | render found no speech-energy stretches without words (`transcript.gaps_rel`) |

## What the agent checks by eye (frames.jpg), every clip

1. Face panel shows the streamer's face (not a hat, not the HUD). If wrong, recalibrate `clip.facecam_region`.
2. Overlay fully visible, nothing under it, not clipped by the frame edge.
3. Hook readable (two lines max, not over the face, not over a key on-screen element).
4. Captions sit in the gameplay area and do not hide the payoff; the active word colour is visible.
5. The gameplay crop contains the action (not the empty side of the screen); no stray alert bars at the left edge.
6. Truthfulness: hook == what is shown. Any doubt -> rewrite the hook.
7. Nothing the brief bans is on screen (other creators, slurs in chat overlays, unrelated game).
8. Watch the clip once with sound if anything is uncertain (open the file). Bleeps land on the swear, not on a normal word.

Then `package.py`. Record issues you fixed in the day README so the next day starts smarter.
