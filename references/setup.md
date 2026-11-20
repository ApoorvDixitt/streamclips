# Setup

Goal: `python scripts/doctor.py` prints `"ok": true`. Works on macOS, Windows and Linux (Python 3.10+).

## 1. Install the skill where your agent looks for skills

Clone once, then symlink or copy the folder into the agent's skills directory (same `SKILL.md` format everywhere):

| Agent | Folder |
|---|---|
| Kiro | `~/.kiro/skills/streamclips` (or `<project>/.kiro/skills/streamclips`) |
| Claude Code | `~/.claude/skills/streamclips` |
| Codex CLI | `~/.codex/skills/streamclips` |
| Cursor | `~/.cursor/skills/streamclips` |
| Gemini CLI | `~/.gemini/skills/streamclips` |
| Shared (agentskills.io) | `~/.agents/skills/streamclips` |

```bash
git clone https://github.com/ApoorvDixitt/streamclips ~/streamclips
ln -s ~/streamclips ~/.kiro/skills/streamclips      # Windows: mklink /D or copy the folder
```

## 2. Python environment (agent does this)

```bash
cd <skill folder>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
# Apple Silicon only, optional local transcription fallback:
.venv/bin/pip install mlx-whisper
.venv/bin/python scripts/doctor.py
```

`static-ffmpeg` downloads an ffmpeg build with caption support on first use (~80 MB). Homebrew/apt ffmpeg often lacks
`libass`; `doctor.py` tells you. Set `FFMPEG_BIN` to override.

Always run scripts with the venv Python: `.venv/bin/python scripts/<name>.py` (Windows `.venv\Scripts\python`).

## 3. Keys: ask the user, never store them

`doctor.py` lists missing keys under `ask_user`. Ask for each one like this, one message:

> I need three keys set in your shell environment (not pasted here):
> - `GROQ_API_KEY` (free, console.groq.com) for transcription.
> - `YOUTUBE_API_KEY` (free, Google Cloud -> enable "YouTube Data API v3" -> credentials -> API key) for stream discovery and live chat.
> - `ELEVENLABS_API_KEY` is optional and paid: only if you want voiceovers or opt into Scribe transcription. Everything works without it.
> Where to save them, by OS:
> - macOS (zsh, the default): add `export GROQ_API_KEY="..."` lines to `~/.zshrc`, then `source ~/.zshrc`.
> - Linux (bash): same lines in `~/.bashrc`.
> - Windows: Settings -> System -> About -> Advanced system settings -> Environment Variables -> User variables -> New; or in PowerShell
>   `setx GROQ_API_KEY "..."`. Open a new terminal afterwards.
> Then start a new agent session (the agent only sees variables that existed when it started) and tell me "keys set".

Then re-run `doctor.py`. If the user pastes a key into chat anyway, ask them to rotate it afterwards and still put it in the environment.
The agent's own process must have the variables: if the agent was started before the keys were added, ask the user to restart the agent session.

Optional: `FFMPEG_BIN`, `STREAMCLIPS_GROQ_STT` (default `whisper-large-v3-turbo`), `STREAMCLIPS_MLX_MODEL`.

## 4. Where clips live (ask once)

Ask: "Where should your clips live? I will create a `streamclips` folder there." Any folder works (e.g. `~/Videos`, `D:\Clips`).
`campaign.py new --root <that folder>` creates `<folder>/streamclips/` with a short README, and one sub-folder per campaign:

```
<folder>/streamclips/
  README.md
  kreekcraft-gta6/          one campaign (campaign.yaml, brief/, MEMORY.md, index.csv, sources/, live/)
    2026-11-21/             one day
      README.md             the day's plan, clips table, posting plan
      01-left-jason-behind/ one clip: clip.mp4, README.md (what to do), post_tiktok.md, post_instagram_reels.md, post_youtube_shorts.md, ...
      02-.../
  another-campaign/
```

Never put campaigns inside the skill repo (`campaign.py` refuses). Remember the location in the campaign's `MEMORY.md` and in your
own project notes so the next session finds it.

## 5. First campaign (adapt to the brief, never assume)

Ask the user for the campaign brief (text, link or screenshot). Read it, then ask only for what the brief needs:

| The brief says | Ask for / do |
|---|---|
| a logo / watermark / overlay is required | the file (PNG with transparency is best) -> `overlay.required: true` |
| nothing about an overlay | no overlay; the face panel uses the freed space. Do not invent branding. |
| which creator / channels are allowed | the links; verify the handle (creators often stream on a secondary channel) |
| required caption text / hashtags | copy them exactly into `caption.*` |
| "keep original audio" or nothing about narration | `audio.mode: original` (voiceover off) |
| narration / commentary allowed or encouraged | `audio.mode: voiceover_allowed`, and the ElevenLabs key becomes relevant (optional, paid) |
| length limits, platforms, payout, geography, engagement, post caps, submission timing | map to the fields in `references/campaign.md`; leave defaults for anything unspecified |
| warm-up / account rules / analytics process | `rules.notes`, so they appear in every clip's post files |

Anything the brief does not mention stays at the default and is **not** added to the user's checklist. Show the filled `campaign.yaml`
back to the user and confirm what you inferred before clipping.

## 6. Verify end to end

Pick a recent replay from `discover.py`, run chat -> signals (chat-only) -> fetch one 10-minute window -> signals --segment ->
render one clip -> review -> package. Show the user the `frames.jpg` and the clip path. This is the acceptance test.

## Edge cases

- Python 3.13 works; `chat-downloader` style libraries do not, which is why chat is implemented natively.
- No Node/Deno: yt-dlp cannot read YouTube. Install Node.js.
- Corporate proxy / region blocks: streamlink and yt-dlp honour `HTTPS_PROXY`.
- Low disk: replays are fetched per window and `watch.py` prunes its buffer (`--keep-minutes`).
- 8 GB RAM machines: do not run two renders at once; local mlx-whisper `small` fits, `large` does not.
