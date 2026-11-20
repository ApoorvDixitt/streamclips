# streamclips

A portable agent skill that turns a streamer's live streams and replays into campaign-compliant vertical clips for paid clipping
campaigns (Whop Content Rewards, ClipLaunch, Vyro and similar). Your coding agent (Kiro, Claude Code, Codex, Cursor, Gemini CLI,
anything that reads `SKILL.md`) does the judgment: which moments, what hook, when to post. The scripts do the work: discovery,
chat, download, transcription, moment scoring, rendering, QA, packaging. Nothing is posted automatically; you post from your phone
and the skill prepares everything.

```
discover -> chat -> signals -> (agent picks) -> render -> review -> package
                 live: watch.py buffers the stream and flags hot moments while it runs
```

## What you get per clip

`clip.mp4` (1080x1920, face panel + gameplay, hook text, word-pop captions, campaign overlay, bleeps, -14 LUFS), `thumb.jpg`,
`captions.srt`, paste-ready `post_<platform>.txt`, `checklist.md` with the campaign's rules and your posting slot, `review.json` proving
the checks, and a row in `index.csv`. Days are folders; `README.md` per day and `MEMORY.md` per campaign keep the agent's memory.

## Install (tell your agent: "install the streamclips skill from <repo url>")

```bash
git clone https://github.com/ApoorvDixitt/streamclips ~/streamclips
ln -s ~/streamclips ~/.kiro/skills/streamclips        # or ~/.claude/skills, ~/.codex/skills, ~/.cursor/skills, ~/.gemini/skills, ~/.agents/skills
cd ~/streamclips && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/doctor.py
```

Set in your shell (never in files): `GROQ_API_KEY` (free), `YOUTUBE_API_KEY` (free). `ELEVENLABS_API_KEY` is optional and paid
(voiceover or Scribe transcription, opt-in only).
The agent asks for them if `doctor.py` says they are missing. macOS, Windows and Linux; Python 3.10+.

## Use

Say to your agent: "new campaign, here is the brief" (paste it, attach the overlay), then "start clipping". The agent asks once where
your clips should live and creates `<that folder>/streamclips/<campaign>/`; each day gets a folder, each clip gets a folder with a
`README.md` that tells you exactly what to post and how. The agent finds the freshest replay or live stream, proposes how many clips the source deserves, shows you
frames, renders, reviews, and packages. See `SKILL.md` for the full loop and `references/` for every rule.

## Credits and licenses

MIT. Font: Anton (OFL, `fonts/OFL.txt`). Hook rules adapted from `ArjunMal1311/socials-skills`, `rediumvex/viral-hooks-skill`,
`xsrazy/fyp-skills` (MIT). Ideas (not code) from Clips Kitty and StreamClipper (AGPL) for chat-spike baselines and live buffering.
Uses yt-dlp, streamlink, static-ffmpeg, Groq Whisper, YouTube Data API, ElevenLabs.

Clip only creators and campaigns you have the right to clip. Read the campaign brief; it is the contract.
