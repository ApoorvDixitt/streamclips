# Rendering

`render.py spec` -> `spec.json` -> (edit) -> `render.py build` -> `clip.mp4` (1080x1920, 30 fps, H.264 hardware-encoded where available, AAC, -14 LUFS).

## Canvas zones (face-top layout)

```
y 0-120      platform UI safe margin
y 120-330    overlay band (logo centred at y = overlay.y_pct * 1920, width overlay.width_pct * 1080)
y 350-720    face panel (facecam crop, white 6 px frame, centred)
y 740-1620   gameplay (source cropped to 1080:880, lanczos scaled; crop shifted right of the facecam strip + alerts)
  y ~830     hook box (2 lines max, auto font 84 -> 58)
  y ~1500    captions (3-word phrases, active word yellow + 12 % scale, 88 px Anton, 9 px outline)
y 1800-1920  bottom safe margin (TikTok/Shorts UI)
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

## Captions

- `word_pop` (default): phrases of up to 3 words, the spoken word turns yellow and pops; phrases never stack because each phrase
  ends when the next begins. Profanity masked `s***` when `clip.mask_profanity` and bleeped (1 kHz tone, <= 0.9 s) when `clip.bleep_profanity`.
- Words come from the candidate (`words` in `candidates.json`); if missing, `build` transcribes the exact clip window.
- Whisper quirks handled: hallucinated trailing "Thank you.", repeated lines, words stretched over silences (capped at 0.6 s).
- `phrase` shows whole phrases without the pop; `none` disables (not recommended: captions are part of the "edited" requirement).

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
| action at the frame edge | add `"game_crop_x": <source px>`? not supported yet: use `--layout fit` or note it in MEMORY.md |
| no face this clip | `"layout": "fill"` |
