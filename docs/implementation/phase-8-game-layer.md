# Phase 8 - Game layer: ranks, XP, rematch, daily challenge, share card

**Goal:** the loop feels like a game: each mode has a rank, rounds earn XP, a rematch shows exactly
what improved, one daily challenge is checked by code, and every card can be shared as an image.

| | |
| --- | --- |
| Lane | A (server pieces are small; A can write them) |
| Hours | 26 to 31 |
| Needs | Phase 4 (cards), Phase 6 (ratings) |

## Done when

- [ ] `pytest server/tests/test_progress.py -q` passes
- [ ] After 3 coffee-chat rounds, `/me` shows a tier computed from the average of the last 3
- [ ] "Rematch" on a card starts the same scenario and the new card shows per-stat deltas
- [ ] Today's challenge shows pass or fail on the card, computed from card metrics
- [ ] Pasting a card link into a chat app shows the card image preview

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/app/progress.py` | new | Tiers, XP, challenges |
| `server/app/workers/postcall.py` | changed | Calls `record_progress` after saving a card |
| `server/app/routes/me.py` | new | `GET /api/me/progress` |
| `server/app/routes/sessions.py` | changed | `rematch_of` on create; deltas on card |
| `server/tests/test_progress.py` | new | Tiers, XP, challenge checks |
| `web/app/me/page.tsx` | new | Ranks, XP, ratings, history |
| `web/app/card/[id]/opengraph-image.tsx` | new | Share image |

## Steps

### 1. Tiers, XP and challenges

`server/app/progress.py`:

```python
from datetime import date

TIERS = [(85, "Diamond"), (75, "Platinum"), (65, "Gold"), (50, "Silver"), (0, "Bronze")]


def tier_for(rolling: float) -> str:
    return next(name for floor, name in TIERS if rolling >= floor)


def xp_for(badges: list, outcome: str | None) -> int:
    """badges: the `badge` column of the session's turns rows."""
    return 10 + (25 if outcome == "won" else 0) + 5 * sum(1 for b in badges if b == "Brilliant!!")


CHALLENGES = [
    ("Ask 3 follow-up questions", lambda m: (m.get("follow_ups") or 0) >= 3),
    ("No question-like endings on statements", lambda m: m.get("rising_statement_share") == 0),
    ("Land your main point 5 out of 5", lambda m: (m.get("main_message_score") or 0) == 100),
    ("Keep your talk share between 45% and 65%", lambda m: 0.45 <= (m.get("talk_share") or 0) <= 0.65),
]


def todays_challenge(today: date | None = None):
    today = today or date.today()
    return CHALLENGES[today.toordinal() % len(CHALLENGES)]


def challenge_result(metrics: dict, today: date | None = None) -> dict:
    name, check = todays_challenge(today)
    return {"name": name, "passed": bool(check(metrics))}
```

`record_progress(player_id, mode, card, outcome)`: read the player's last 3 card overalls for the
mode, set `ranks.rolling_overall` to their mean and `ranks.tier` to `tier_for(...)`, add
`xp_for(...)` to `ranks.xp`. Put `main_message_score` into the metrics dict before checking the
challenge (`card["judged"]` and `measured["main_message"]["score"]`).

### 2. Rematch

`POST /api/sessions` accepts `rematch_of` (a session id the caller owns, same scenario). Store it in
the new session's row (add a nullable `rematch_of uuid` column in a `0002_rematch.sql` migration).
`GET /card` then also returns `deltas = {stat: new - old}` for every stat present in both cards.
Show deltas next to each stat in green or red; this is the screenshot moment.

### 3. Profile page

`GET /api/me/progress` returns `ranks` rows, `ratings` rows (duel, team, ffa, debate_crowd), and the
last 10 cards' overall with dates. `/me` renders a tier badge per mode, XP, the four ratings, and a
small line of the last 10 overalls.

### 4. Share image

`web/app/card/[id]/opengraph-image.tsx` uses `ImageResponse` from `next/og` to draw the card (name,
overall, six stats, scenario name). Fetch the card through a public, read-only server route
`GET /api/public/cards/{id}` that returns only overall, stats, scenario name and display name: no
audio, transcript or events.

### 5. Tests

`server/tests/test_progress.py`: `tier_for` at 49.9, 50, 74.9, 75 and 85; `xp_for` with a win and 2
Brilliant badges is 45; each challenge passes and fails on a hand-built metrics dict;
`todays_challenge` rotates over 4 consecutive days.

## Pitfalls

- The public card route must never return audio URLs or transcripts.
- XP never changes scores. It unlocks personas and cosmetics only.
