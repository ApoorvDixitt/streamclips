# Hooks, captions and the trend layer

The hook is the on-screen line for the first ~2.5 s and the first line of the caption. One job: make a stranger stay 3 more seconds.
Rules adapted (with thanks) from `ArjunMal1311/socials-skills` hook-writer, `rediumvex/viral-hooks-skill`, and `xsrazy/fyp-skills`.

## Rules (hook_check.py enforces the hard ones)

- <= 9 words and <= 44 characters (fits two lines of Anton at 1080 px).
- Payoff or surprise first, context second: "Kreek leaves Jason mid-robbery" beats "Kreek was robbing a store when...".
- Must be literally supported by the transcript or the frames. Names, places, actions that are visible/said. No invented numbers.
- No filler openers: "Did you know", "In this video", "Hey guys", "Wait for it", "You won't believe", "POV:", "When you".
- No clickbait tells: "gone wrong", "insane", "shocking", "???", "!!!". Campaign briefs ban misleading titles; reviewers reject them.
- No links, no hashtags, no @mentions in the hook (they go in the caption).
- Write in sentence case; the renderer uppercases.
- Must work on mute: the frame-0 picture should already show the thing the hook names (adjust `start_t` or `zoom_at_rel` if not).

## Five angles: write two of each, pick the best three, run `hook_check.py`

1. **Result first**: what happened ("Kreek's getaway driver forgot the passenger").
2. **Surprise**: the thing that should not be true, stated flat ("The cops ignored a man on fire").
3. **Streamer's own words**: quote the funniest 3-6 words he actually said (quotes beat paraphrase).
4. **Viewer's question**: the question the viewer will type in comments ("Why did he just leave him?").
5. **Mild contrarian**: a small disagreement with the obvious ("Robbing the store was the easy part").

Rank by: a stranger would stop for it > it is true > it is specific. Give the user the top 3 with the frame-0 picture for each.

## Trend layer (optional, bounded)

Before finalising, you may do one quick web search: `"<game> <mission/place/character in the clip>"` plus `last 7 days`, or use a
trend-research skill (e.g. `last30days`) to see if the moment connects to something people are already talking about
(a meme, a mission everyone fails, a viral clip of another streamer at the same spot).

- Use it only when the transcript/frames support the connection. "Everyone is stuck on this mission" is fine if the clip shows that mission.
- Never import controversy, people or claims that are not in the clip. Never reference other creators in a creator-exclusive campaign.
- Keep it to one phrase in the hook or caption; the moment itself must still carry the clip.

## Caption per platform (package.py builds these from `campaign.yaml`)

- Line 1: the hook. Then a blank line, then the campaign's required text and exact hashtags from the brief (never reworded).
- TikTok/Reels: <= 2,200 chars, 3-5 hashtags total (campaign ones first, then 1-2 niche ones like `#gaming #streamer` if the brief allows).
  Avoid location hashtags (`#usa`); audience targeting guides say viewer behaviour, not tags, decides geography.
- YouTube Shorts: title = hook (<= 90 chars); description = full caption. Shorts needs `#Shorts` only if the brief does not forbid extra tags.
- Never put links in captions when the brief forbids them (most do).

## Voiceover lines (only when `audio.mode: voiceover_allowed`)

One factual sentence, <= 12 words, placed before or after the streamer speaks, never over the payoff. Same voice every time
(cached by `voiceover.py`). Example: "Kreek thought this robbery would be easy." Not: fake reactions, not narration of the obvious.
