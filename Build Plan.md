# Debate Arena Build Plan

Oct 10, 2026 · single player first, then community. The two-player duel is parked (last section);
the NetworkMaxx plan is archived in `docs/implementation/archive/`.

## Summary

Debate Arena rates how well you argue out loud. It is built in two stages, and stage 2 does not
start until stage 1 meets its bar.

**Stage 1, single player.** Pick a prompt and a side, speak for 60 seconds, and get a speech card in
under 20 seconds: an overall score and letter, Logos / Pathos / Delivery, every metric with the
evidence behind it, and the three changes that would raise your score most. Retry and see what
moved.

**Stage 2, community.** Every day there is one featured prompt. People answer it, every answer is
graded by the same rubric, and a leaderboard ranks the community. Community members also rate each
other's speeches by listening to two answers from opposite sides and picking which argued better;
those votes become crowd scores and a crowd leaderboard next to the machine one.

Every machine number is measured from the audio by our code or labeled by Gemini with a verified
quote as evidence; code, not the model, turns labels into scores. Every crowd number comes from
`Arena/arena_ratings.py`, which is built and tested.

### What is reused

| Piece | Status | Role |
| --- | --- | --- |
| `softskills-grader/` | Built, 26 tests | Rates every speech: pace, diction, tone, logic, emotion; retries busy APIs |
| `Arena/arena_ratings.py` | Built, 13 tests | Community votes into logos, pathos, ethos, persuasion percentiles and Elo, with side-bias removal and rater weighting |
| `supabase/migrations/0001_init.sql` | Written, not applied | `profiles`, `motions`, `speeches`, `judgments`, `gold_pairs`, `arena_scores`, `ratings` are exactly what this plan needs |
| `tools/checks/check_supabase.py` | Built | First-hour check of anonymous sign-in and the server's token check |
| `web/` home screen | Built, on GitHub Pages | Front door; ARGUE opens practice |
| `web/js/radar.js` + `web/progress.html` | Built, 6 tests (example data) | The speech radar: grades each part of your speech, first attempt against best |
| Archived Phase 5 and Phase 7 docs | Written | Step-by-step code for grading, publishing, the safety check and the whole judging flow; this plan says which steps to take |

## Stage 1: single player

### The loop

1. **Pick a prompt.** A prompt is a debatable motion ("Should homework be banned in elementary
   school?"). Pick yes or no. Prompts are motions with sides because both the grader's stance check
   and community judging need a side.
2. **Speak.** 10-second get-ready countdown, then 60 seconds; stop early is allowed, but under 40
   words the grader returns "not gradable" with the reason.
3. **Card** in under 20 seconds.
4. **Retry.** Same prompt and side; the new card shows deltas against your best ("+7 Delivery").

### The speech card

| Section | Source | Shows |
| --- | --- | --- |
| Overall | `report.overall` | Score 0 to 100 and letter |
| **Logos** | logic factor | Thesis found, each claim with its support type and status, counterargument and rebuttal, fallacies, stance matches your side |
| **Pathos** | emotion factor | Each emotional appeal with its quote |
| **Delivery** | pace, diction, tone factors | Words per minute, pauses, fillers, stumbles, pitch variation, monotone stretches, emphasis |
| Fix these first | computed (below) | The three changes worth the most points, each with a one-line tip |
| Transcript | `report.transcript` + evidence | Fillers, long pauses and monotone stretches highlighted; click to play that moment |
| Speech radar | the five factor scores | This speech (outline) over your previous best on this prompt (filled area), one axis per part |
| Progress | your past speeches | Best on this prompt, deltas per section, last 10 overalls as a sparkline |

**"Fix these first" is computed, not generated.** Each metric's lost points are
`factor.weight_pct / 100 × metric.weight / (sum of metric weights in the factor) × (100 − metric.score)`,
and each deduction's are `factor.weight_pct / 100 × points_lost`. These add up to 100 minus the
overall (on `sample_report.json`: 4.28 lost against an overall of 95.7, the gap being rounding), so
"worth 2.6 points" is literal. Sort, drop zeros, take the top 3, and show the
tip written for that metric in `server/app/tips.py` ("Long pauses: you paused 2 s or more 4 times;
plan your next point while finishing the current one"). Ethos is not on the solo card: nothing in
the audio measures credibility honestly. It appears once the crowd rates you.

### The speech radar

One axis per graded part of the speech, each 0 to 100 straight from the report's factor scores:
Logic (logos), Emotion (pathos), and Tone, Diction and Pace (delivery). Two shapes are overlaid:

| Where | Filled area | Outline |
| --- | --- | --- |
| Speech card | Your previous best speech on this prompt | This speech |
| Me page | Your first graded attempt (per prompt, or across all prompts) | Your best on each part, with the attempt it came from |

On the Me page "best" is per part, so the outline can combine several attempts; the tooltip says
which attempt each part came from, and a list beside the chart ranks the parts by how much they
improved. Ungradable attempts (under 40 words) are skipped. Hovering a part shows both scores;
a table view shows the same numbers as rows. Colors are the reference palette's dark violet and
dark yellow, validated for color-blind separation and contrast on the panel surface.

Built in `web/js/radar.js` (pure helpers `factorScores` and `firstAndBest`, plus `renderRadar`),
tested by `node --test web/test/*.test.mjs`, and shown with example data in `web/progress.html`.
Phase 2 swaps the example data for `GET /api/me`.

### What "good" means before stage 2 starts

| Check | Bar |
| --- | --- |
| Speed | Card on screen within 20 s of stopping, 9 times out of 10 |
| Repeatable | The same file uploaded twice gives the identical report (grader cache by audio hash) |
| Separates quality | Each teammate records a deliberately strong and a deliberately weak take on the same prompt; strong beats weak by 15+ points every time |
| Agrees with ears | Three teammates rank 6 speeches by ear without seeing scores; the grader's order has rank correlation 0.6 or more with their consensus |
| Actionable | Following the top tip and re-recording raises the overall score in at least 4 of 6 tries |
| Not gameable | Reading a fluent but off-side speech fails the stance check; a 40-word speech is "not gradable" |

Tune `softskills-grader/config/standards.yaml` until these pass; every change bumps its version, so
old cards still explain their numbers.

## Stage 2: community

### Prompt of the day and the leaderboard

- One prompt is featured each day (UTC). Any prompt, featured or not, stays open for practice.
- Speeches submitted on the featured prompt while it is featured can be **ranked**: each player gets
  3 ranked attempts per prompt and their best counts. Practice attempts are unlimited and never
  ranked.
- Ranking needs **publishing**: a consent checkbox, then the Gemini safety check from archived
  Phase 5 step 4. Rejected speeches stay private and show the reason category.
- Players pick a display name (3 to 20 letters, digits or underscores) the first time they publish.

| Leaderboard | Ranks by | Notes |
| --- | --- | --- |
| **Today, by grade** | Each player's best ranked machine overall on the featured prompt | Ties go to the earlier speech. Live as soon as a speech is graded |
| **Today, by crowd** | Crowd score: mean of the logos, pathos and persuasion percentiles | Only speeches with 10+ judgments; "settled" badge from 20 |
| **Season** | Mean of your best ranked score on your last 3 featured prompts | Tiers: Bronze under 50, Silver 50, Gold 65, Platinum 75, Diamond 85 |
| **Past prompts** | The same two tabs, frozen at the end of the day for grade; crowd keeps updating | Judging continues after the day ends |

Grade and crowd stay separate rankings. They answer different questions (did you meet the rubric,
did listeners find you convincing), and the gap between them is a coaching line on your card: "the
grader rates your logic 88; listeners put your reasoning at the 61st percentile".

### Community rating

The flow, the math and the anti-abuse rules are archived Phase 7, unchanged:

1. A listener states their stance on the prompt (5-point scale) before hearing anything.
2. They hear two published answers from opposite sides, in random order, with no names or scores.
   The buttons unlock after 90% of each has actually played (scrubbing does not count).
3. Stance again, then three picks: better reasoning (A or B), more moving (A or B), and would you
   trust each speaker to brief you on this (yes or no each).

| Crowd score | Computed as |
| --- | --- |
| Logos | Bradley-Terry strength from reasoning picks, "my side" bias removed, percentile within the prompt |
| Pathos | Same, from "more moving" picks |
| Persuasion | A listener who moved toward a side is a head-to-head win for that side's speech; percentile |
| Ethos | Wilson lower bound of "I'd trust them" votes |

Why picks instead of 1 to 5 stars: people use star scales differently, and a handful of star votes
swings wildly; choosing the better of two is more consistent, and the aggregator corrects for
listeners favoring their own side. In simulation, rank correlation with true quality was 0.75 at 10
judgments per speech and 0.84 at 20.

Guards, all from archived Phase 7: no judging your own speech, one vote per rater per pair, a signed
matchup token with a minimum listening time, 20 judgments per hour per rater, and gold pairs
(strong vs weak team speeches) that weight each rater by accuracy, floor 0.2. Raters earn judge XP
when their pick matches the eventual consensus, capped at 50 XP a day.

**Volume is the constraint.** A judgment takes about 2.5 minutes and covers two speeches, so 10
judgments per speech costs about 12 listener-minutes per speech. The judge screen therefore shows
what you unlock ("judge 3 to see today's crowd board"), and before the demo the team seeds every
featured prompt with gold pairs and bot listeners (`simulate_judges.py`, archived Phase 7 step 8).
Pairs need both sides: if one side has fewer than 2 published speeches, the team records some.

## Architecture

```
web/ (static, GitHub Pages)  --supabase-js: anonymous sign-in-->  Supabase Auth
        |  Authorization: Bearer <access token>
        v
FastAPI server (Fly.io)  --> softskills-grader --> ElevenLabs Scribe, Gemini
        |                --> Arena/arena_ratings.py (aggregation job, every 60 s)
        v
Supabase Postgres (tables) + Storage (private `audio` bucket, signed URLs)
```

| Layer | Choice | Why |
| --- | --- | --- |
| Frontend | Static `web/` with plain JS modules; `@supabase/supabase-js` from cdn.jsdelivr.net | Already deployed on GitHub Pages; no build step |
| Auth | Supabase anonymous sign-in, display name in `profiles` | No sign-up wall; the server resolves the token with `auth.get_user` and never trusts a player id in a body |
| Server | FastAPI on Fly.io, one machine | Keeps keys off the browser; runs the Python grader and the aggregator |
| Data | Supabase Postgres + Storage | Schema already written |
| Live updates | Poll every 3 s while grading; leaderboard refreshes every 30 s | Supabase Realtime is optional here; nothing needs sub-second updates |

Stage 1 can run against the deployed server from the start, so nothing is rebuilt for stage 2.

### Schema changes

`supabase/migrations/0002_prompts.sql`, on top of `0001_init.sql`:

```sql
alter table public.motions  add column if not exists featured_on date unique;   -- prompt of the day
alter table public.speeches add column if not exists ranked boolean not null default false;
create index if not exists speeches_board_idx
  on public.speeches (motion_id, ranked, published, overall desc);
```

The conversation and match tables in `0001_init.sql` stay unused.

### Routes

| Route | Stage | Purpose |
| --- | --- | --- |
| `GET /api/prompts` | 1 | All prompts; today's has `featured: true` |
| `POST /api/speeches` | 1 | Form: `motion_id`, `side`, `audio`. Returns `{speech_id}` with 202; grading runs in the background |
| `GET /api/speeches/{id}` | 1 | Status, report, signed audio URL, top-3 fixes, deltas vs your best |
| `GET /api/me` | 1 | Display name, per-prompt bests, recent overalls, each speech's five factor scores in recording order (for the radar), season tier, judge XP |
| `POST /api/speeches/{id}/publish` | 2 | `{consent, ranked}`. Safety check; `ranked` only on today's prompt and under 3 ranked attempts (409 otherwise) |
| `PUT /api/me/name` | 2 | Set display name |
| `GET /api/leaderboard/{motion_id}?by=grade\|crowd` | 2 | Ranked rows: place, name, score, letter, judgments, provisional |
| `GET /api/leaderboard/season` | 2 | Tiers |
| `GET /api/arena/{motion_id}/next` | 2 | A signed matchup (archived Phase 7) |
| `POST /api/judgments` | 2 | Store a judgment (archived Phase 7 checks: 401, 403, 422, 429, 409) |
| `GET /api/speeches/{id}/arena` | 2 | Crowd scores for the card |

Leaderboard by grade is one query: published, approved, ranked speeches on the prompt, best per
player:

```sql
select distinct on (player_id) player_id, id, overall, created_at
from public.speeches
where motion_id = $1 and ranked and published and moderation_status = 'approved'
order by player_id, overall desc, created_at;
```

The server sorts those rows by `overall desc, created_at` and joins display names.

### Screens

| Screen | Stage | Contents |
| --- | --- | --- |
| Home (exists) | 1 | The pixel arena; ARGUE goes to practice. Shows today's prompt in stage 2 |
| Practice | 1 | Prompt picker, side toggle, mic check, countdown, recorder |
| Card | 1 | The speech card above; in stage 2 also "Publish" and the crowd column |
| Me | 1 | Speech radar (first vs best, prompt picker), improvement list, sparkline; in stage 2 also tier and judge XP |
| Today | 2 | Featured prompt, "Answer it" (ranked attempts left), the two leaderboards |
| Judge | 2 | The archived Phase 7 judging steps, then the next matchup |

## Build schedule

Lanes: **A** frontend, **B** server, **C** grader calibration. Feature freeze at hour 33.

| Phase | Builds | Lane | Hours | Done when |
| --- | --- | --- | --- | --- |
| 0. Foundations | Supabase project (anonymous sign-ins on, raise their limit from 30 per hour per IP), apply 0001 + 0002, server scaffold (settings, auth, db, storage), Fly deploy, `.gitignore`, supabase-js sign-in on the static page | B + A | 0-3 | `check_supabase.py` passes; the deployed page shows the signed-in id and `GET /api/me` returns it |
| 1. Record and grade | Prompts seeded, practice screen, upload, grading worker, basic card | A + B | 3-9 | A recording on the deployed page shows a card in under 20 s; the same file twice gives the identical report |
| 2. Make solo good | Full card (sections, radar, transcript highlights, click to play), "fix these first", deltas, Me page with the radar on live data | A + B | 9-16 | The card renders every metric and deduction from `sample_report.json` and a real speech; the Me radar draws from `GET /api/me` |
| 2c. Calibrate | Team recordings, tune `standards.yaml`, write `tips.py` | C | 4-16 | Every row of "What good means" passes. **Gate: stage 2 starts only after this** |
| 3. Prompt of the day | `featured_on` rotation, publish with consent and safety, ranked attempts, names, grade leaderboard, season tiers, Today screen | B + A | 16-22 | Three teammates publish ranked answers and see the board order by best score; the 4th ranked attempt returns 409 |
| 4. Community rating | Judge screen, tokens and checks, aggregation job, crowd column, crowd leaderboard, gold pairs, bot seeding | B + A + C | 20-29 | Archived Phase 7 "Done when" list, using today's prompt as the motion |
| 5. Demo | Seed prompts and crowd, rehearse, backup video, saved card JSON | All | 29-36 | Rehearsed twice under 3 minutes; the backup plays from disk |

If the gate slips, stage 2 shrinks in this order: crowd leaderboard (keep crowd scores on the card),
then season tiers, then judge XP. Never cut calibration or the backup video.

### Which archived steps to take

| Archived doc | Take | Skip or change |
| --- | --- | --- |
| Phase 0 (foundations) | Steps 1 (minus ElevenLabs agent setup), 3, 4, 5, 7, 8, 9 | Step 6 (Next.js app): use the static `web/`; the Speech Engine port in step 8 |
| Phase 5 (solo debate) | Steps 2 (grading worker), 3 (routes), 4 (safety check), 7 (screen contents), 8 (tests) | Voice mask and AI opponent (steps 5, 6); `Countdown` uses a local timer, not a server deadline |
| Phase 7 (community judging) | All steps, motion = prompt | The `ratings` upsert uses mode `debate_crowd`, as written |
| Phase 8 (game layer) | `TIERS` and `tier_for` | Conversation XP, rematch and daily challenges |

## Demo script (3 minutes)

1. **0:00 to 0:20, hook.** Everyone has opinions; nobody gets feedback on how they argue them out
   loud. Every point here is measured or voted.
2. **0:20 to 1:20, solo.** A teammate answers today's prompt live. The card lands; open "fix these
   first", play the flagged filler from the transcript.
3. **1:20 to 2:10, community.** Show today's leaderboard; the new speech appears in its place. A QR
   code puts the room on the judge screen; two judgments come in live.
4. **2:10 to 3:00, credibility.** Grade the same recording twice: identical report. Show a card
   where grader and crowd disagree, and the coaching line that explains the gap.

## Risks and fallbacks

| Risk | Mitigation |
| --- | --- |
| Venue Wi-Fi or an API fails | Backup video; saved card and leaderboard JSON render from disk |
| Gemini or Scribe busy | The grader retries and falls back to another Gemini model (PR #4) |
| Too few judgments for crowd scores | Seed with gold pairs and bot listeners; "judge 3 to unlock"; provisional badge below 10 |
| One side of a prompt has no answers | Team records answers on both sides of every featured prompt |
| Abuse: offensive speech or name | Safety check before publishing; names restricted to letters, digits, underscores; unpublished speeches are never served |
| Grinding the leaderboard | 3 ranked attempts per prompt; practice is unlimited but unranked |
| The venue shares one IP | Raise the anonymous sign-in limit in Phase 0 |
| Recordings | Private bucket, signed URLs, delete after the event unless published |

## Parked: two-player duel

Kept for later, not scheduled. Player 1 opens for 60 s, Player 2 goes after (30 s prep with Player
1's claims shown, 60 s answering them), Player 1 gets an optional 30 s reply; rematch swaps who
goes first. `Arena/match_judge.judge_match("duel", ...)` scores it with no code changes: pass
Player 1's reports as `{"opening": opening, "rebuttal": reply}` and Player 2's single speech as both
`opening` and `rebuttal`, which makes Player 2's score 0.8 speech + 0.2 clash against Player 1's
claims. `judge_series` sums the two games of a rematch. Once stage 1 exists, the duel only needs its
own screens and a verdict route.
