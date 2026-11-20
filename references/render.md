# Rendering

`render.py spec` -> `spec.json` -> (edit) -> `render.py build` -> `clip.mp4` (1080x1920, 30 fps, H.264 hardware-encoded where available, AAC, -14 LUFS).

## Safe zone and canvas zones (face-top layout)

Every readable element stays inside the universal safe box **x 60-930, y 230-1420** (what TikTok, Reels and Shorts UIs leave free:
top tabs, the right like/comment column, the bottom caption/description block). Gameplay and the blurred background may fill the
whole frame; text, face and overlay may not. `review.py` enforces this and draws the box in `frames_safe.jpg`.

```
y 0-230      top UI margin (nothing readable)
y 240-370    overlay band (logo centred at y 305, width 0.72 * 1080 = 778 px -> x 151..929)   [only if the campaign has an overlay]
y 390-720    face panel (330 px high, white 6 px frame, centred)                                 [no overlay: panel starts at y 240 and is 480 px high]
y 740-1560   gameplay (source cropped to 1080:820, lanczos; crop shifted right of the facecam strip + alerts)
  y 800      hook box top (<= 2 lines, auto font 84 -> 58, margins 70 / 160 so it never enters the right column)
  y ~1300    captions (bottom edge at 1380; 3-word phrases, active word yellow + 12 %, 84 px Anton, 9 px outline)
y 1420-1920  bottom UI margin (gameplay may continue under it; no text)
background   blurred, darkened copy of the frame
```

`fill`: gameplay crop fills 9:16, overlay on top, no face panel. `fit`: whole 16:9 frame centred over blur (talk shows, IRL).

## Facecam calibration (do this once per creator, store in MEMORY.md)

1. Run `signals.py --segment` to get a frame sheet, open `frames/<id>.jpg` (1280x720 tiles of the source).
2. Measure the cam box as fractions of the source frame: `x,y` top-left, `w,h`. Include the whole face with a little headroom; exclude
   follower counters/alerts below it. KreekCraft (Nov 2026): `{"x":0.0,"y":0.07,"w":0.17,"h":0.30}`.
3. `campaign.py set 'clip.facecam_region={"x":..,"y":..,"w":..,"h":..}'` then render a test clip and look at `frames.jpg`.
4. If the streamer moves the cam between streams, re-check the first clip of the day. Cam hidden by a menu? Use `--layout fill` for that clip.

Source quality: the face panel upscales ~2.5x; fetch 1080p60 whenever the platform offers it.

## Captions (free-first, agent-verified)

- `render.py build` always re-transcribes the exact clip window in `final` mode: ffmpeg denoise (high/low-pass, `afftdn`, level) + a
  names-only vocabulary prompt + two Groq Whisper passes (large-v3 and turbo) merged phrase-wise. Measured on a noisy GTA 6 window:
  18 words (first scan) -> 47 words. Free.
- `transcript.json` holds `text`, `second_pass_text`, `gaps_rel` (speech energy with no words) and how to fix. The agent compares the two
  texts, fixes names, drops game/NPC lines that confuse the clip, edits `words` in `spec.json`, sets `words_locked: true`, re-builds.
  The agent cannot hear audio: it only adds words it is certain of (frames, chat, repeated lines).
- Paid option (never automatic): ElevenLabs Scribe v3 (`transcribe.py --engine scribe`) hears more words on game audio (55 vs 47 in the same
  test) and labels speakers; voice isolation did not add streamer words in our test. Use only when the user has a plan and asks for it.
- `transcript.json.words` are in stream seconds, the same shape as `spec.json.words`: edit, copy back, set `words_locked: true`, re-build.
- Right-side facecams are cropped out too (`facecam_region.x >= 0.5` shifts the gameplay crop left). `face-top` without a calibrated region
  falls back to `fill` automatically, so there is never a blank panel.
- `word_pop` (default): phrases of up to 3 words, the spoken word turns yellow and pops; phrases never overlap. Profanity masked `s***`
  (`clip.mask_profanity`) and bleeped with a 1 kHz tone <= 0.9 s (`clip.bleep_profanity`).
- Whisper quirks handled: hallucinated trailing "Thank you.", duplicated segments (glitch, < 0.25 s apart), words stretched over silences
  (capped at 0.6 s), descriptive-prompt hallucinations (we only prompt with names).

## Hook text

Auto-wrapped to <= 2 lines; `render.py spec` rejects filler openers; `wrap_hook` raises if the text cannot fit (shorten it).
`hook_seconds` = campaign `clip.hook_seconds` + 0.6 s (fade). Keep 2-3 s; longer blocks the action.

## Zoom

`zoom_at_rel` (seconds from clip start): 1.0 -> 1.16 over 8 frames, hold 1.4 s, back. Put it on the payoff. Disable per clip with `"zoom": false`.

## Audio

- `original`: source audio untouched except bleeps and loudness normalisation.
- `voiceover`: ElevenLabs line (cached) mixed in at `voiceover.at`, source ducked to 25 % while it plays. Only when the campaign allows.
- Loudness: single-pass `loudnorm` to -14 LUFS / -1.5 dBTP (phone speakers, platform normalisation).

## Overlay

PNG with alpha; cropped to its visible bounding box, scaled to `width_pct`, placed in the top band (or `position: bottom`).
Nothing else is drawn in that band, so it stays unobstructed. `review.py` verifies it pixel-wise on six frames.

## Encoders

macOS: `h264_videotoolbox` 9 Mbit/s. NVIDIA: `h264_nvenc` (set `STREAMCLIPS_NVENC=0` to disable). Else `libx264 crf 19`.
A 24 s clip renders in ~15 s on an M1.

## Common spec edits

| Want | Edit |
|---|---|
| start on the setup line | `start_t` to the word's `start` - 0.3 |
| end after the laugh | `end_t` = last reaction word `end` + 1.5 |
| different hook | `hook` (re-run `hook_check.py --spec`) |
| action at the frame edge | use `--layout fit` for that clip (whole frame visible) |
| no face this clip | `"layout": "fill"` |
