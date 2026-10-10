# Phase 7 - Community judging

**Goal:** listeners judge published speeches head to head, and every speech gets crowd scores for
logos, pathos, ethos and persuasion next to its machine grade. The math is already built and tested
in `Arena/arena_ratings.py`; this phase builds the judging flow, the anti-abuse checks and the job
that runs the math.

| | |
| --- | --- |
| Lanes | D (flow and UI), C (aggregation job) |
| Hours | 20 to 28 |
| Needs | Phase 5 (published speeches) |

## Done when

- [ ] `python Arena/test_arena_ratings.py` passes (13 checks)
- [ ] `pytest server/tests/test_judging.py -q` passes
- [ ] A listener can judge 3 matchups in a row on the deployed `/judge/[motion]` page
- [ ] Judging your own speech returns 403; the same pair twice returns 409; submitting before both
      recordings could have been heard returns 422; the 21st judgment in an hour returns 429
- [ ] `python server/scripts/simulate_judges.py --raters 25` fills `arena_scores` for every speech
      on one motion, and no speech with 20+ judgments is still `provisional`
- [ ] The debate report page shows the crowd column with a provisional or settled badge

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/app/routes/judgments.py` | new | Next matchup, submit judgment, crowd scores |
| `server/app/workers/aggregate.py` | new | Runs `aggregate_motion` every 60 s |
| `server/run.py` | changed | Starts the aggregation loop |
| `server/scripts/simulate_judges.py` | new | Bot listeners for testing and demo seeding |
| `server/tests/test_judging.py` | new | Token, limits, row mapping |
| `web/app/judge/[motion]/page.tsx` | new | The judging flow |
| `web/components/JudgeMatchup.tsx` | new | Stance, two players, stance, three picks |
| `web/components/DebateReport.tsx` | changed | Crowd column |

## Contracts

`GET /api/arena/{motion_id}/next` returns a matchup; it never reveals names or scores:

```json
{"token": "base64.signature", "motion": "Should the frontier of AI be paced?",
 "a": {"speech_id": "uuid", "audio_url": "https://...signed", "seconds": 58.2},
 "b": {"speech_id": "uuid", "audio_url": "https://...signed", "seconds": 61.0}}
```

`POST /api/judgments`:

```json
{"token": "...", "pre_stance": 1, "post_stance": 2, "logos_pick": "a", "pathos_pick": "b",
 "trust_a": true, "trust_b": false, "listened_a_pct": 97.5, "listened_b_pct": 92.0}
```

`GET /api/speeches/{id}/arena` returns the `arena_scores` row (or `{"judgments": 0}`).

## Steps

### 1. Matchup tokens

The server signs which pair it served and when, so the client cannot pick its own pairs or skip
listening:

```python
import base64
import hashlib
import hmac
import json
import time

from .settings import settings


def sign(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    mac = hmac.new(settings.signing_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def verify(token: str) -> dict:
    body, mac = token.rsplit(".", 1)
    good = hmac.new(settings.signing_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        raise ValueError("bad token")
    return json.loads(base64.urlsafe_b64decode(body))
```

Token payload: `{"rater": player_id, "a": id, "b": id, "motion": id, "issued": time.time(),
"min_seconds": settings.min_listen_factor * (seconds_a + seconds_b)}`, with
`min_listen_factor: float = 0.9` added to `settings.py`. Speech length comes from the stored
report: `report["evidence"]["pace"]["speaking_seconds"]`.

### 2. Picking the next pair

In `routes/judgments.py`, `GET /api/arena/{motion_id}/next`:

1. Load published, approved speeches on the motion (opening round only), excluding the rater's own.
2. If the rater has fewer than 3 gold answers, or 1 time in 10 otherwise, serve a random row of
   `gold_pairs` for that motion (keep its stored a/b order).
3. Otherwise pick uniformly at random among yes/no pairs the rater has not judged. Random, not
   adaptive: adaptive pairing inflates the reliability statistic (plan, "Debate Arena").
4. Randomize which speech is A. Return signed URLs (10 minutes) and the token.

### 3. Accepting a judgment

`POST /api/judgments` checks, in this order:

| Check | Status |
| --- | --- |
| Token signature valid and `rater` equals the caller | 401 |
| Rater owns neither speech | 403 |
| `time.time() - issued >= min_seconds` and both listened percentages are 90 or more | 422 |
| Fewer than 20 judgments by this rater in the last hour | 429 |
| Insert succeeds (unique on rater, speech_a, speech_b) | 409 on conflict |

Store the row with `motion_id` from the token.

### 4. Aggregation job

`server/app/workers/aggregate.py`:

```python
import asyncio

from .. import grading  # noqa: F401
from ..db import supabase
from ..realtime import broadcast
from arena_ratings import Judgment, Speech, aggregate_motion

AGGREGATOR_VERSION = "arena_ratings@2026-10-10"


def to_speech(row) -> Speech:
    return Speech(row["id"], row["player_id"], row["motion_id"], row["side"])


def to_judgment(row) -> Judgment:
    return Judgment(row["rater_id"], row["speech_a"], row["speech_b"], row["pre_stance"], row["post_stance"],
                    row["logos_pick"], row["pathos_pick"], row["trust_a"], row["trust_b"],
                    listened_fully=min(row["listened_a_pct"], row["listened_b_pct"]) >= 90)


async def aggregate_once() -> None:
    motions = supabase.table("motions").select("id").eq("active", True).execute().data
    for mo in motions:
        speeches = supabase.table("speeches").select("id,player_id,motion_id,side").eq("motion_id", mo["id"]) \
            .eq("published", True).eq("round", "opening").execute().data
        rows = supabase.table("judgments").select("*").eq("motion_id", mo["id"]).order("created_at").execute().data
        if not speeches or not rows:
            continue
        gold = {(g["speech_a"], g["speech_b"]): g["expected_pick"] for g in
                supabase.table("gold_pairs").select("*").execute().data}
        result = aggregate_motion([to_speech(s) for s in speeches], [to_judgment(r) for r in rows], gold=gold)
        owners = {s["id"]: s["player_id"] for s in speeches}
        for speech_id, score in result["speeches"].items():
            supabase.table("arena_scores").upsert({"speech_id": speech_id, **score,
                                                   "aggregator_version": AGGREGATOR_VERSION}).execute()
            await broadcast(f"player:{owners[speech_id]}", "crowd_scores", {"speech_id": speech_id, **score})
        for player_id, rating in result["elo"].items():
            supabase.table("ratings").upsert({"player_id": player_id, "mode": "debate_crowd",
                                              "rating": rating}).execute()


async def loop() -> None:
    while True:
        try:
            await aggregate_once()
        except Exception as exc:
            print("aggregate failed:", exc)
        await asyncio.sleep(60)
```

`aggregate_motion` replays every judgment from scratch each minute, so results never drift and a
fix to the math re-scores history automatically. Thousands of judgments per motion run in well
under a second. Rows are ordered by `created_at` because the aggregator keeps the first judgment
when a rater repeats a pair.

### 5. Gold pairs

For each demo motion, the team records 4 deliberately strong and 4 deliberately weak speeches (Phase
9), publishes them from a team account, and inserts 4 `gold_pairs` rows pairing a strong speech
with a weak one from the other side, `expected_pick` pointing at the strong one.

### 6. Judging screen

`JudgeMatchup` steps, one screen each:

1. Motion and a 5-point stance scale (strongly no to strongly yes), before anything plays.
2. Player A. Track heard time with the `timeupdate` event, adding only forward progress of under
   1.5 s per event, so scrubbing to the end does not count. The Next button unlocks at 90%.
3. Player B, the same.
4. Stance again.
5. Better reasoning: A or B. More moving: A or B. Trust each to brief you on this: yes or no each.
6. Submit, then load the next matchup.

The listened percentages sent are `100 * heardSeconds / durationSeconds` for each player.

### 7. Crowd column on the debate report

Fetch `GET /api/speeches/{id}/arena` and subscribe to `player:{player_id}` for `crowd_scores`.
Show logos, pathos, ethos and persuasion as percentiles, the judgment count, and a badge: grey
"provisional" under 10 judgments, "settled" from 20. Show the machine-vs-crowd gap line from the
plan: compare the machine's logic factor score with the crowd's logos percentile.

### 8. Bots for testing and seeding

`server/scripts/simulate_judges.py --api ... --motion <id> --raters 25 --spam 3`: creates bot
listeners like `simulate_match.py`, and each judges matchups through the real API, waiting the
token's `min_seconds` (pass `--fast` only against a local server started with
`MIN_LISTEN_FACTOR=0`, never against the deployed one).
Honest bots pick with probability tied to each speech's machine overall score; spam bots pick at
random. Use it to fill the demo motions before the event, then let real people add to them.

### 9. Tests

`server/tests/test_judging.py`: token round trip and tamper detection; `to_judgment` sets
`listened_fully` False when either percentage is under 90; the submit route returns 403, 409, 422
and 429 in the cases above (stub `supabase` with the in-memory fake from Phase 6).

## Pitfalls

- Anonymous sign-ins are capped per IP by default (Phase 0, step 1). Raise the cap before the demo
  or the room cannot join.
- Never show names, scores or machine grades on the judging screen: they bias the vote.
- Judge XP rewards agreeing with the eventual consensus, which also rewards conformity. Cap it at
  50 XP a day.
