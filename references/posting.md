# Posting plan (the user posts manually; you plan it)

## Research, every day, before `package.py`

1. Read `audience.tier1` and `audience.posting_windows_et` from `campaign.yaml` and the campaign's own targeting guide if it has one.
2. Do one quick web search for the current week: "best time to post <platform> <audience country> <month year>" and the game's news for today
   (patch, event, update, a moment everyone is talking about). Prefer platform or large-dataset sources over blogs selling tools.
3. Check `index.csv` and `MEMORY.md`: which of your own slots got views. Your data beats generic data after ~2 weeks.
4. Pick one slot per clip, spread across the day (one post per platform per hour at most; never batch 10 at once).
5. Convert to the user's timezone (`user_timezone`) and record with `package.py --post-at "2026-11-21T21:30+05:30" --audience "US East midday"`.

## Default windows (US-first campaigns), use until your own data says otherwise

| Audience window | Local (ET) | IST (user default) | Notes |
|---|---|---|---|
| US weekday midday | 11:00-14:00 | 21:30-00:30 | lunch scroll; good for Shorts/Reels |
| US weekday evening | 17:00-21:00 | 03:30-07:30 | biggest TikTok/Reels window |
| UK evening | 18:00-21:00 GMT | 23:30-02:30 | catches UK + US East after-work |
| Weekend gaming | 10:00-14:00 ET Sat/Sun | 20:30-00:30 | gaming/entertainment holds up on weekends |

Timing moves results ~10-20 %; the first 1-3 seconds of the clip move the rest. During a launch week, speed matters more than the slot:
post within 1-3 hours of the moment.

## Platform notes

- **TikTok**: evenings; 15-35 s; native upload; description = hook + required text + hashtags; keep likes/comments public.
- **Instagram Reels**: midday and evenings; cover = `thumb.jpg`; first comment can repeat hashtags if the brief allows.
- **YouTube Shorts**: title = hook; description = caption; Shorts need <= 60 s (we enforce via `clip.max_seconds`).
- **X**: only if the campaign allows; native video, >= 10 s, hook that invites replies/DM shares; one post per hour; no links in the post.

## Account rules many campaigns impose (set `rules.new_account_warmup: true` and `rules.notes` only if the brief has them)

- New accounts: 3-day warm-up (scroll, like, comment, follow in-niche; one niche post on day 3; post from the mobile app for the first ~10 posts, no schedulers).
- Keep the account single-niche and single-language; profile complete; 2FA on.
- One consistent country for the account. Some guides recommend a US VPN for non-US clippers; evidence that it raises reach is weak,
  switching countries day to day does hurt trust. If the user uses one, it must be the same server every time. Never for multiple accounts.
- Likes visible, posts public for the required period, no reposting the same clip more than `max_posts_per_clip`.

## Submission timing

Campaign reviewers often require submitting the post link within N minutes of posting (`rules.submit_within_minutes`, often 30).
Each `post_<platform>.md` carries the deadline and the campaign URL (`listing`). Analytics screen recordings are requested once a clip passes the minimum payout views; tell the
user when a clip in `index.csv` crosses it (`payout.min_payout_usd` / `payout.cpm_usd` * 1000 views).
