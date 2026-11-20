# Campaign folder and campaign.yaml

One folder per campaign inside `<user's chosen location>/streamclips/`. Everything about the campaign lives inside it.

```
<location>/streamclips/<campaign-slug>/
  campaign.yaml          rules the scripts enforce + defaults the agent follows
  brief/                 original brief text, overlay PNG, any reference files
  MEMORY.md              durable notes (streamer facts, layout quirks, what got approved/rejected, decisions)
  index.csv              one row per clip: source, time, hook, score, status, posts, views, payout
  sources/<platform>-<id>/   chat.jsonl, candidates.json, frames/, segments/, meta.json   (cache; safe to delete old ones)
  live/<platform>-<id>/      watcher state, rolling buffer, hot.json
  voiceover_cache/           ElevenLabs lines, reused forever
  YYYY-MM-DD/                one folder per day
    README.md                plan, clips table, posting plan, lessons   (campaign.py day creates it)
    01-<slug>/               one folder per clip (see packaging.md)
```

## Create

```bash
python scripts/campaign.py new --root ~/Videos --name "KreekCraft GTA 6 Clipping" --creator KreekCraft \
  --brief brief.txt --overlay overlay.png --youtube @Kreek --twitch kreekcraft --game "Grand Theft Auto VI"
```

Then map the brief into `campaign.yaml` with `campaign.py set key=value ...` and run `campaign.py validate`. Lists must be JSON
(`'caption.hashtags=["#A","#B"]'`; a plain `#A #B` string is split on spaces for hashtags/platforms only). Numbers are numbers,
`true/false/null` are booleans/null. `--youtube`, `--twitch`, `--kick` accept handles/logins or full channel URLs. Show the user the
resulting yaml and confirm anything you inferred. Non-English streams: set `clip.language` (ISO code) and add names to `clip.names`;
captions use a Latin font, so non-Latin scripts are not supported yet (use `clip.captions: none` and say so in MEMORY.md).

## Field guide (what each field changes)

| Field | Effect |
|---|---|
| `creator.youtube_handle` / `youtube_channel_id` / `twitch_login` / `kick_slug` | where `discover.py` looks. Note: a creator may stream on a secondary channel (KreekCraft streams GTA 6 on `@Kreek`, not `@KreekCraft`). Check the brief's links. |
| `creator.extra_sources` | extra allowed URLs/channels. Anything else is forbidden. |
| `game` | title filter for replays (GTA matches "GTA", "Grand Theft"). |
| `source_priority` | `auto` / `live_first` / `replay_first`, see sourcing.md. Set `live_first` when the brief says "clip live" or pays for speed. |
| `replay_max_age_days` | how old a replay may be and still count as fresh (default 2). |
| `platforms` | which `post_<platform>.md` files are produced (tiktok, instagram_reels, youtube_shorts, x). |
| `payout.*` | cpm, min/max payout, min engagement rate. All `null` until the brief sets them; when set, the post files show the view threshold. |
| `clip.min_seconds/max_seconds/target_seconds` | hard limits (review fails) and the sweet spot (soft). Shorts/Reels/TikTok all accept <= 60 s; 15-35 s retains best for gaming reactions. |
| `clip.layout` | `face-top` when the streamer has a facecam, `fill` when there is none, `fit` for talk/IRL. |
| `clip.facecam_region` | fractions of the source frame `{x,y,w,h}`. Calibrate from a frame sheet (render.md). `null` disables the face panel. |
| `clip.captions` | `word_pop` (default), `phrase`, `none`. |
| `clip.names` / `clip.language` | vocabulary hint for transcription (characters, places, catchphrases; the creator's name is added automatically) and the spoken language. Names-only prompts improve Whisper; descriptive prompts make it hallucinate, so keep it to names. |
| `clip.mask_profanity` / `bleep_profanity` | brief says "banned words" -> keep both true. |
| `audio.mode` | `original` unless the brief allows added narration. `voiceover_allowed` enables `render.py spec --voiceover`. |
| `audio.voice_id` | ElevenLabs voice (Drew `q0IMILNRPxOgtBTS4taI` by default). |
| `overlay.*` | `required: true` makes review fail if the logo is covered. Defaults (`width_pct` 0.72, `y_pct` 0.159) keep it inside the platform-UI safe zone; review fails if you move it outside. |
| `caption.required_text` / `hashtags` / `no_links` / `template` | copied **exactly** into every post text; rewording gets clips rejected. |
| `rules.max_posts_per_clip`, `submit_within_minutes`, `keep_public_days`, `likes_visible`, `tier1_min_share`, `new_account_warmup` | all `null` by default; set only what the brief says. Set rules appear in every `post_<platform>.md`; null rules never do. |
| `rules.banned` / `preferred_moments` | your selection guide (free text from the brief). `rules.banned_words` (single words/short phrases) also fails `hook_check.py`. `rules.notes` lines are printed verbatim in the post files. |
| `audience.posting_windows_et` | default US windows; posting.md explains how to refine per day. |
| `user_timezone` | IANA name (e.g. `Asia/Kolkata`, `America/New_York`); ask the user once. `validate` fails while empty. |
| `listing` | the campaign page URL; printed in the post files next to the submission step. |
| `creator.kick_slug` | Kick creators: discovery + live recording via `watch.py` (streamlink), audio/speech signals; Kick has no chat replay. |

## Reading a brief: checklist

From the brief, extract and confirm: allowed sources; platforms; CPM, min/max payout; engagement rate; required caption text and
hashtags (exact spelling); overlay/watermark requirement; length limits; audience geography; submission timing; account rules
(warm-up, likes visible, public for N days, max reposts); banned content (misleading titles, fake reactions, reused clips, low effort);
preferred moment types; whether original audio must be kept; approval/analytics process. Put anything that does not fit a field
into `rules.notes` and `MEMORY.md`.

Example (KreekCraft / ClipLaunch): CPM $1, min $5, max $300/clip; overlay mandatory start to finish; caption must contain
"Watch KreekCraft on YouTube and Twitch" + `#GTA6 #KreekCraft #GrandTheftAutoVI`, no links; TikTok/Reels/Shorts only;
>= 40% US/UK/CA/AU audience; >= 0.8% engagement; likes visible; public 30 days; max 5 posts per clip; submit within 30 min;
no raw recordings; prioritise Kreek's reactions -> `layout: face-top`, `audio.mode: original`.
