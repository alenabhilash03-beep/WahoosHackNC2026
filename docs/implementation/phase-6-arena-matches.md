# Phase 6 - Arena matches: duels, teams, free-for-all

**Goal:** 2 to 8 players join a lobby, argue the same motion at the same time, and the grader picks
the winner in about 30 seconds after the last speech. Ranked play updates an Elo rating per mode.
The judging rules are already built and tested in `Arena/match_judge.py`; this phase builds the
lobby, the timed phases, uploads, grading and the verdict screen around it.

| | |
| --- | --- |
| Lanes | B (server), A (lobby and match screens) |
| Hours | 18 to 26 |
| Needs | Phase 5 (speeches table, `grade_speech`) |

## Done when

- [ ] `python Arena/test_match_judge.py` passes (27 checks)
- [ ] `pytest server/tests/test_match_runner.py -q` passes (phase transitions with a fake clock)
- [ ] `python server/scripts/simulate_match.py --mode ffa --players 5` finishes with a verdict, a
      placement for every bot and rating rows for all 5
- [ ] Two browsers play a casual duel end to end on the deployed site in under 4 minutes
- [ ] A bot that never uploads is marked forfeited and placed last with score 0
- [ ] Two runner loops started at once judge each match exactly once (one `match_results` row)
- [ ] A ranked duel plays two games with sides swapped and updates ratings once, from the series

## How a match runs

| Status | Lasts | What happens | Ends when |
| --- | --- | --- | --- |
| `lobby` | until start | Players join by code or queue; presence shows who is in | Host starts, or the lobby fills |
| `prep` | 60 s | Motion and sides revealed | Deadline |
| `speaking` | 60 s | Everyone records their opening at once; uploads accepted until deadline + 15 s | Deadline + 15 s; missing players forfeit |
| `rebuttal_listen` | 75 s | Rebuttal matches only: each player hears the opposing opening(s) | Deadline |
| `rebuttal` | 30 s | Everyone records a rebuttal; uploads until deadline + 15 s | Deadline + 15 s |
| `grading` | up to 90 s | Each speech is graded as soon as it arrives | All graded, or deadline |
| `done` | - | Verdict stored and broadcast; ratings updated | - |

Grading starts on upload, not at the end of a phase, so the verdict usually lands seconds after the
last upload.

## Files

| Path | New or changed | Purpose |
| --- | --- | --- |
| `server/app/settings.py` | changed | `match_margin: float = 3.0` |
| `server/app/routes/matches.py` | new | Create, quick match, join, start, upload, state |
| `server/app/workers/match_runner.py` | new | Phase clock, forfeits, judging, ratings, series |
| `server/run.py` | changed | Starts the runner loop |
| `server/scripts/simulate_match.py` | new | Bots play a full match against the deployed API |
| `server/tests/test_match_runner.py` | new | Transitions and judging with a fake clock |
| `web/app/arena/page.tsx` | new | Create a lobby, join by code, quick match |
| `web/app/match/[code]/page.tsx` | new | The whole match, one screen per status |
| `web/components/LobbyRoster.tsx`, `Countdown.tsx`, `VerdictReveal.tsx` | new | UI pieces |

## Contracts

| Route | Body | Returns |
| --- | --- | --- |
| `POST /api/matches` | `{"mode": "duel", "size": 2, "ranked": false, "rebuttal": true}` | match state |
| `POST /api/matches/quick` | `{"mode": "duel"}` | match state (joined or created) |
| `POST /api/matches/{code}/join` | - | match state |
| `POST /api/matches/{id}/start` | - | match state (host only, at least 2 players, or 3 for ffa) |
| `POST /api/matches/{id}/speeches` | form: `round` (opening or rebuttal), `audio` | `{"speech_id"}` |
| `GET /api/matches/{id}` | - | match state |

Match state:

```json
{
  "id": "uuid", "code": "K7QM2X", "mode": "duel", "ranked": false, "rebuttal": true, "size": 2,
  "status": "speaking", "phase_ends_at": "2026-10-11T15:04:05Z", "game": 1, "series_id": null,
  "motion": {"id": "uuid", "text": "Should the frontier of AI be paced?"},
  "players": [{"player_id": "uuid", "display_name": "ana", "team": null, "side": "yes", "seat": 0,
               "forfeited": false, "uploaded": ["opening"], "graded": ["opening"]}],
  "opponent_openings": [{"player_id": "uuid", "audio_url": "https://...signed"}],
  "verdict": null
}
```

`opponent_openings` is filled only during `rebuttal_listen` and only with the players on the other
side. `verdict` is the `judge_match` (or `judge_series`) output once `status` is `done`.

Realtime channel `match:{id}`:

| Event | Payload |
| --- | --- |
| presence (client `track`) | `{"player_id", "display_name"}` |
| `phase` | `{"status", "phase_ends_at", "motion"?, "sides"?}` |
| `graded` | `{"player_id", "round"}` |
| `verdict` | the verdict |
| `next_game` | `{"code"}` (ranked duel, game 2) |

## Steps

### 1. Routes

`server/app/routes/matches.py`:

```python
import random
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import storage
from ..auth import current_player
from ..db import one, supabase
from ..realtime import broadcast
from ..workers.debate import grade_speech
from ..workers.match_runner import ALPHABET, GRACE_S, PHASE_S, after_speech_graded, match_state

router = APIRouter(prefix="/api/matches", tags=["matches"])
MIN_PLAYERS = {"duel": 2, "team": 4, "ffa": 3}
MAX_PLAYERS = {"duel": 2, "team": 6, "ffa": 8}


def now():
    return datetime.now(timezone.utc)


class Create(BaseModel):
    mode: str
    size: int = 2
    ranked: bool = False
    rebuttal: bool = False


@router.post("")
def create(body: Create, player_id: str = Depends(current_player)):
    if body.mode not in MIN_PLAYERS or not MIN_PLAYERS[body.mode] <= body.size <= MAX_PLAYERS[body.mode]:
        raise HTTPException(422, "bad mode or size")
    if body.mode == "team" and body.size % 2:
        raise HTTPException(422, "team matches need an even size")
    if body.mode == "ffa" and body.rebuttal:
        raise HTTPException(422, "free-for-all has no rebuttal round")
    code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    m = supabase.table("matches").insert({"code": code, "mode": body.mode, "size": body.size,
                                          "ranked": body.ranked, "rebuttal": body.rebuttal,
                                          "created_by": player_id}).execute().data[0]
    supabase.table("match_players").insert({"match_id": m["id"], "player_id": player_id, "seat": 0}).execute()
    return match_state(m["id"], player_id)


@router.post("/{code}/join")
async def join(code: str, player_id: str = Depends(current_player)):
    m = one("matches", code=code.upper())
    if not m or m["status"] != "lobby":
        raise HTTPException(404, "no open lobby with that code")
    seats = supabase.table("match_players").select("player_id,seat").eq("match_id", m["id"]).execute().data
    if any(s["player_id"] == player_id for s in seats):
        return match_state(m["id"], player_id)
    if len(seats) >= m["size"]:
        raise HTTPException(409, "lobby is full")
    supabase.table("match_players").insert({"match_id": m["id"], "player_id": player_id,
                                            "seat": len(seats)}).execute()
    if len(seats) + 1 == m["size"]:
        await start_match(m)                     # full lobbies start themselves
    return match_state(m["id"], player_id)


@router.post("/{match_id}/start")
async def start(match_id: str, player_id: str = Depends(current_player)):
    m = one("matches", id=match_id)
    if not m or m["created_by"] != player_id:
        raise HTTPException(403, "only the host can start")
    await start_match(m)
    return match_state(match_id, player_id)


async def start_match(m: dict) -> None:
    players = supabase.table("match_players").select("*").eq("match_id", m["id"]).order("seat").execute().data
    if len(players) < MIN_PLAYERS[m["mode"]]:
        raise HTTPException(409, f"need at least {MIN_PLAYERS[m['mode']]} players")
    motions = supabase.table("motions").select("id,text").eq("active", True).execute().data
    motion = m.get("motion_id") and one("motions", id=m["motion_id"]) or random.choice(motions)
    order = players[:]
    random.shuffle(order)
    first_side = random.choice(["yes", "no"])
    other = "no" if first_side == "yes" else "yes"
    for i, p in enumerate(order):
        if m["mode"] == "team":
            team = "red" if i % 2 == 0 else "blue"
            side = first_side if team == "red" else other
        else:
            team, side = None, first_side if i % 2 == 0 else other   # duel and ffa alternate sides
        supabase.table("match_players").update({"team": team, "side": side}) \
            .eq("match_id", m["id"]).eq("player_id", p["player_id"]).execute()
    ends = now() + timedelta(seconds=PHASE_S["prep"])
    updated = supabase.table("matches").update({"status": "prep", "motion_id": motion["id"],
                                                "phase_ends_at": ends.isoformat()}) \
        .eq("id", m["id"]).eq("status", "lobby").execute().data
    if updated:
        await broadcast(f"match:{m['id']}", "phase", {"status": "prep", "phase_ends_at": ends.isoformat(),
                                                      "motion": motion})


@router.post("/{match_id}/speeches")
async def upload(match_id: str, background: BackgroundTasks, round: str = Form(...),
                 audio: UploadFile = File(...), player_id: str = Depends(current_player)):
    m = one("matches", id=match_id)
    me = one("match_players", match_id=match_id, player_id=player_id)
    if not m or not me or me["forfeited"]:
        raise HTTPException(403, "not in this match")
    expected = {"opening": "speaking", "rebuttal": "rebuttal"}.get(round)
    deadline = datetime.fromisoformat(m["phase_ends_at"]) + timedelta(seconds=GRACE_S)
    if m["status"] != expected or now() > deadline:
        raise HTTPException(409, f"not accepting {round} speeches now")
    if one("match_speeches", match_id=match_id, player_id=player_id, round=round):
        raise HTTPException(409, "already uploaded")
    sp = supabase.table("speeches").insert({"player_id": player_id, "motion_id": m["motion_id"],
                                            "side": me["side"], "round": round,
                                            "audio_path": "pending"}).execute().data[0]
    path = storage.put(f"matches/{match_id}/{player_id}.{round}.webm", await audio.read())
    supabase.table("speeches").update({"audio_path": path}).eq("id", sp["id"]).execute()
    supabase.table("match_speeches").insert({"match_id": match_id, "player_id": player_id,
                                             "round": round, "speech_id": sp["id"]}).execute()

    async def grade_then_check():
        await grade_speech(sp["id"])
        await broadcast(f"match:{match_id}", "graded", {"player_id": player_id, "round": round})
        await after_speech_graded(match_id)

    background.add_task(grade_then_check)
    return {"speech_id": sp["id"]}


@router.get("/{match_id}")
def state(match_id: str, player_id: str = Depends(current_player)):
    return match_state(match_id, player_id)
```

Quick match (`POST /api/matches/quick`): look for a `lobby` match with the same mode, `ranked =
true`, a free seat, and an average player rating within `150 + 50 * floor(waited_s / 15)` of the
caller's; join it, or create one with `size` 2 (duel), 4 (team) or 6 (ffa). The client calls it
again every 15 s with `{"mode", "waited_s"}` until the lobby fills.

### 2. The runner

`server/app/workers/match_runner.py`:

```python
"""Advances matches through their phases and judges them. Every transition is a conditional
update on the current status, so two runner loops can never advance or judge a match twice."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .. import grading  # noqa: F401
from .. import storage
from ..db import one, supabase
from ..realtime import broadcast
from ..settings import settings
from match_judge import Entry, Unit, elo_multi, judge_match, judge_series

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PHASE_S = {"prep": 60, "speaking": 60, "rebuttal_listen": 75, "rebuttal": 30, "grading": 90}
GRACE_S = 15
UPLOAD_PHASES = {"speaking": "opening", "rebuttal": "rebuttal"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _move(m: dict, to: str) -> bool:
    """Advance only if nobody else did. Returns True if this call made the transition."""
    ends = (now() + timedelta(seconds=PHASE_S.get(to, 0))).isoformat()
    rows = supabase.table("matches").update({"status": to, "phase_ends_at": ends}) \
        .eq("id", m["id"]).eq("status", m["status"]).execute().data
    return bool(rows)


def _players(match_id: str) -> list:
    return supabase.table("match_players").select("*").eq("match_id", match_id).order("seat").execute().data


def _speeches(match_id: str) -> dict:
    """{(player_id, round): speech row}"""
    links = supabase.table("match_speeches").select("*").eq("match_id", match_id).execute().data
    out = {}
    for link in links:
        out[(link["player_id"], link["round"])] = one("speeches", id=link["speech_id"])
    return out


async def tick() -> None:
    live = supabase.table("matches").select("*") \
        .in_("status", ["prep", "speaking", "rebuttal_listen", "rebuttal", "grading"]).execute().data
    for m in live:
        due = datetime.fromisoformat(m["phase_ends_at"])
        if m["status"] in UPLOAD_PHASES:
            due += timedelta(seconds=GRACE_S)
        if now() >= due:
            await advance(m)


async def advance(m: dict) -> None:
    status = m["status"]
    if status == "prep":
        nxt = "speaking"
    elif status == "speaking":
        _forfeit_missing(m, "opening")
        nxt = "rebuttal_listen" if m["rebuttal"] else "grading"
    elif status == "rebuttal_listen":
        nxt = "rebuttal"
    elif status == "rebuttal":
        nxt = "grading"
    else:                                   # grading deadline passed: judge with what is there
        await finalize(m["id"], force=True)
        return
    if _move(m, nxt):
        fresh = one("matches", id=m["id"])
        await broadcast(f"match:{m['id']}", "phase", {"status": nxt, "phase_ends_at": fresh["phase_ends_at"]})
        if nxt == "grading":
            await after_speech_graded(m["id"])     # everything may already be graded


def _forfeit_missing(m: dict, round_: str) -> None:
    have = {pid for (pid, r) in _speeches(m["id"]) if r == round_}
    for p in _players(m["id"]):
        if p["player_id"] not in have:
            supabase.table("match_players").update({"forfeited": True}) \
                .eq("match_id", m["id"]).eq("player_id", p["player_id"]).execute()


async def after_speech_graded(match_id: str) -> None:
    m = one("matches", id=match_id)
    if m["status"] != "grading":
        return
    rounds = ["opening", "rebuttal"] if m["rebuttal"] else ["opening"]
    speeches = _speeches(match_id)
    waiting = [(p["player_id"], r) for p in _players(match_id) if not p["forfeited"] for r in rounds
               if (p["player_id"], r) in speeches and not speeches[(p["player_id"], r)].get("report")]
    if not waiting:
        await finalize(match_id)


def build_entries(m: dict) -> list:
    speeches = _speeches(m["id"])
    entries = []
    for p in _players(m["id"]):
        reports = {}
        for r in (["opening", "rebuttal"] if m["rebuttal"] else ["opening"]):
            sp = speeches.get((p["player_id"], r))
            # Missing or still-ungraded speech counts as not gradable: score 0 for that round.
            reports[r] = sp["report"] if sp and sp.get("report") else {"gradable": False}
        entries.append(Entry(p["player_id"], p["side"], reports, p["team"]))
    return entries


async def finalize(match_id: str, force: bool = False) -> None:
    m = one("matches", id=match_id)
    if m["status"] != "grading":
        return
    ratings = {r["player_id"]: float(r["rating"]) for r in
               supabase.table("ratings").select("*").eq("mode", m["mode"]).execute().data}
    verdict = judge_match(m["mode"], build_entries(m), ratings, margin=settings.match_margin)
    claimed = supabase.table("matches").update({"status": "done", "standards_id": verdict["standards_id"]}) \
        .eq("id", match_id).eq("status", "grading").execute().data
    if not claimed:                                 # another runner judged it first
        return
    supabase.table("match_results").upsert({"match_id": match_id, "verdict": verdict,
                                            "judge_version": verdict["judge_version"]}).execute()
    if m["ranked"] and m["mode"] == "duel":
        await _ranked_duel_step(m, verdict)
    else:
        if m["ranked"]:
            _apply_ratings(m["mode"], verdict["rating_changes"])
        await broadcast(f"match:{match_id}", "verdict", verdict)


def _apply_ratings(mode: str, changes: dict) -> None:
    for player_id, delta in changes.items():
        row = one("ratings", player_id=player_id, mode=mode) or {"rating": 1200, "games": 0}
        supabase.table("ratings").upsert({"player_id": player_id, "mode": mode,
                                          "rating": float(row["rating"]) + delta,
                                          "games": row["games"] + 1}).execute()


async def _ranked_duel_step(m: dict, verdict: dict) -> None:
    if m["game"] == 1:                              # create game 2 with sides swapped
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))     # fresh code, never derived
        g2 = supabase.table("matches").insert({
            "code": code, "mode": "duel", "size": 2, "ranked": True, "rebuttal": m["rebuttal"],
            "motion_id": m["motion_id"], "series_id": m["id"], "game": 2, "created_by": m["created_by"],
            "status": "prep", "phase_ends_at": (now() + timedelta(seconds=PHASE_S["prep"])).isoformat(),
        }).execute().data[0]
        for p in _players(m["id"]):
            supabase.table("match_players").insert({"match_id": g2["id"], "player_id": p["player_id"],
                                                    "seat": p["seat"], "side": "no" if p["side"] == "yes" else "yes"}).execute()
        await broadcast(f"match:{m['id']}", "verdict", verdict)
        await broadcast(f"match:{m['id']}", "next_game", {"code": code})
        return
    first = one("match_results", match_id=m["series_id"])["verdict"]
    series = judge_series([first, verdict], margin=settings.match_margin)
    ratings = {r["player_id"]: float(r["rating"]) for r in
               supabase.table("ratings").select("*").eq("mode", "duel").execute().data}
    a, b = list(series["totals"])
    if series["winner"]:
        loser = b if series["winner"] == a else a
        placements = [(Unit(series["winner"], [series["winner"]], 0, {}), 1), (Unit(loser, [loser], 0, {}), 2)]
    else:
        placements = [(Unit(a, [a], 0, {}), 1), (Unit(b, [b], 0, {}), 1)]
    changes = elo_multi(placements, ratings)
    _apply_ratings("duel", changes)
    await broadcast(f"match:{m['id']}", "verdict", {**verdict, "series": series, "rating_changes": changes})


async def loop() -> None:
    while True:
        try:
            await tick()
        except Exception as exc:                    # keep the clock alive no matter what
            print("runner tick failed:", exc)
        await asyncio.sleep(1)
```

`match_state` (same file) builds the contract shape. The motion stays hidden until the match starts,
and opponents' recordings are only handed out during `rebuttal_listen`, to the other side:

```python
MATCH_FIELDS = ("id", "code", "mode", "ranked", "rebuttal", "size", "status", "phase_ends_at", "game", "series_id")


def match_state(match_id: str, player_id: str) -> dict:
    m = one("matches", id=match_id)
    if not m:
        raise HTTPException(404, "no such match")
    players = _players(match_id)
    me = next((p for p in players if p["player_id"] == player_id), None)
    if me is None:
        raise HTTPException(403, "not in this match")
    speeches = _speeches(match_id)
    names = {r["id"]: r["display_name"] for r in supabase.table("profiles").select("id,display_name")
             .in_("id", [p["player_id"] for p in players]).execute().data}
    out_players = [{
        **{k: p[k] for k in ("player_id", "team", "side", "seat", "forfeited")},
        "display_name": names.get(p["player_id"], "player"),
        "uploaded": [r for (pid, r) in speeches if pid == p["player_id"]],
        "graded": [r for (pid, r), sp in speeches.items() if pid == p["player_id"] and sp.get("report")],
    } for p in players]
    openings = []
    if m["status"] == "rebuttal_listen":
        for p in players:
            sp = speeches.get((p["player_id"], "opening"))
            if sp and p["side"] != me["side"]:
                openings.append({"player_id": p["player_id"], "audio_url": storage.signed_url(sp["audio_path"], 600)})
    motion = one("motions", id=m["motion_id"]) if m["motion_id"] else None
    result = one("match_results", match_id=match_id)
    return {**{k: m[k] for k in MATCH_FIELDS},
            "motion": {"id": motion["id"], "text": motion["text"]} if motion else None,
            "players": out_players, "opponent_openings": openings,
            "verdict": result["verdict"] if result else None}
```

In `server/run.py`, start the loop next to the servers:
`await asyncio.gather(api.serve(), engine.serve(...), match_runner.loop())`.

### 3. Runner tests with a fake clock

A finished test file is ready: copy `docs/implementation/tests/test_match_runner.py` (and
`conftest.py` from the same folder, if Phase 2 has not already added it) into `server/tests/`.
It swaps `supabase` for an in-memory fake, stubs `broadcast` and the clock, and attaches
`softskills-grader/sample_report.json` with chosen scores as each speech's report. It was run
against the code in this phase, assembled exactly as written here, and all 5 cases pass:

1. A 4-player free-for-all runs prep, speaking, done; sides are balanced 2 and 2; the player who
   never uploads is forfeited and placed 4th with score 0
2. Calling `advance` twice with a stale match row, and `finalize` twice, stores exactly one result
3. A rebuttal duel passes through `rebuttal_listen` (each player is handed only the other side's
   opening) and `rebuttal`; a skipped rebuttal scores 0 for that round
4. A ranked duel's game 1 creates game 2 with a fresh code and swapped sides; ratings change only
   after game 2, once, from the series result
5. A 2 v 2 team match assigns red and blue evenly and ranks by team mean

### 4. Bots for an end-to-end run

`server/scripts/simulate_match.py --api https://... --mode ffa --players 5`:

1. Create bot users with the Supabase admin API (`auth.admin.create_user` with email
   `bot{i}@networkmaxx.test`, a password and `email_confirm=True`), sign each in with
   `auth.sign_in_with_password` to get a token.
2. Bot 0 creates the lobby; the others join by code.
3. Generate distinct speeches with the grader's fixture at different tempos
   (`fixtures._timeline()`, scale timings by 0.85 to 1.15, `fixtures._synthesise`), convert to WebM
   with ffmpeg, and upload during `speaking`. Leave one bot silent with `--forfeit 1`.
4. Poll `GET /api/matches/{id}` until `done` and print the verdict explanation and rating changes.

Note: the fixture speeches all use the same words, so their content scores tie; the tempo spread is
what separates them, which also exercises the margin and tie-break rules.

### 5. Arena and match screens

`/arena`: three buttons (Duel, Team, Free-for-all) for quick match, a "Create lobby" form (mode,
size, ranked, rebuttal), and a join-by-code box.

`/match/[code]`: one component per status. Before anything else, call `syncClock()` and ask for the
microphone on a "Test your mic" button in the lobby, because recording later starts without a click.

| Status | Screen |
| --- | --- |
| `lobby` | `LobbyRoster` from presence on `match:{id}`; host sees Start; code shown large for sharing |
| `prep` | Motion, your side (and team), a 60 s `Countdown`, a notes box |
| `speaking` | Recording starts by itself, stops at the deadline, uploads; shows "uploaded" ticks per player |
| `rebuttal_listen` | Players for each opposing opening; the countdown keeps running |
| `rebuttal` | 30 s recording, same as speaking |
| `grading` | One row per player turning green on each `graded` event |
| `done` | `VerdictReveal` |

`VerdictReveal` animates placements from last to first, shows each score, the margin, the
`explanation` lines word for word (they already say "too close on overall" or "draw" when that is
what happened), a factor table (rows = factors, columns = players) and rating changes. For a ranked
duel after game 1, show the game result and a "Game 2 starts in 60 s, sides swapped" banner from
the `next_game` event.

## Pitfalls

- **Clock skew:** never use the browser clock for deadlines; use `serverNow()` from `lib/clock.ts`.
- **Mic permission:** browsers block `getUserMedia` without a user gesture. Get permission in the
  lobby, or the speaking phase silently records nothing.
- **Same rubric:** `judge_match` refuses reports graded with different rubric versions. If a deploy
  changes the YAML mid-match, re-grade that match's speeches (cheap: cached by audio hash) before
  finalizing.
- **Margin:** `settings.match_margin` defaults to 3.0, from the grader-noise test in the plan.
  Re-measure in Phase 9 with real voices and raise it if same-speaker re-recordings differ more.
- **Grading load:** an 8-player free-for-all with rebuttals off is 8 grades at once; the
  `GRADE_SLOTS` semaphore in `workers/debate.py` caps concurrency.
