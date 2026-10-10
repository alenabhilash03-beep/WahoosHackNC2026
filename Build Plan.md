# Debate Arena Build Plan

Oct 10, 2026 · replaces the NetworkMaxx plan (archived in `docs/implementation/archive/`)

## Summary

Debate Arena is a speech and rhetoric game for two players on one laptop. Player 1 argues one side
of a motion; Player 2 goes after and argues the other side, answering what Player 1 said. Every
speech gets a rating, and the game names a winner. Every number is measured from the audio by our
code or labeled by Gemini with a verified quote as evidence; code, not the model, turns labels into
scores.

The core loop:

1. Enter two names and pick a motion. A coin flip assigns sides; Player 1 always argues first.
2. **Player 1 speaks** for 60 seconds.
3. **Player 2 goes after**: 30 seconds to prepare, then 60 seconds that must argue their side and
   answer Player 1.
4. **Player 1 replies** for 30 seconds (on by default, can be switched off). This offsets Player 2's
   advantage of speaking with Player 1's case already heard.
5. **Verdict.** Both speech cards are revealed side by side with the winner, the margin and why.
6. **Rematch** with roles swapped, so each player goes first once. The two games add up to a series.

### What changed from the old plan

| Cut | Why |
| --- | --- |
| Networking scenarios, AI personas, hidden goals, rapport bar, move badges | Not speech and rhetoric |
| ElevenLabs Speech Engine (live voice calls) | No AI on the other end any more |
| Conversation scorer (`conversation/`) | It scores dialogue: latency, interruptions, follow-ups |
| Online lobbies, Supabase auth and Realtime, community judging, Elo ladder | Both players share one laptop; no accounts |

| Kept | Role now |
| --- | --- |
| `softskills-grader/` | Rates every speech: pace, diction, tone, logic, emotion. Unchanged |
| `Arena/match_judge.py` | Picks the winner from the two players' reports. Unchanged |
| `web/` Debate Arena home screen | The game's front door; the ARGUE button starts a match |
| ElevenLabs Scribe and Gemini | Used inside the grader only (transcript with word times; argument extraction) |

## Match format

| Step | Who | Time | Screen | Background work |
| --- | --- | --- | --- | --- |
| Setup | both | - | Names, motion, coin flip for sides | - |
| Opening | Player 1 | 60 s | Countdown, live mic level, "stop early" | - |
| Handoff | - | until tapped | "Pass to Player 2" | Player 1's opening uploads and starts grading |
| Prep | Player 2 | 30 s | Motion, their side, and Player 1's claims once grading has extracted them | Grading finishes (about 15 s) |
| Response | Player 2 | 60 s | Countdown | - |
| Handoff | - | until tapped | "Pass to Player 1" | Player 2's speech grades |
| Reply (optional) | Player 1 | 30 s | Countdown, Player 2's claims on screen | - |
| Verdict | both | - | Two cards, winner, explanation | Reply grades, then the judge runs (pure function, instant) |

Scores stay hidden until the verdict, so Player 1's number does not rattle Player 2 mid-match.
Player 2 hears Player 1 live in the room; a "replay opening" button covers a noisy venue.

## How a speech is rated

Each speech goes through `softskills-grader` exactly as it is today: Scribe transcribes with word
timestamps, Gemini extracts the thesis, claims, support, counterargument and fallacies as verified
quotes, and the rubric in `config/standards.yaml` turns measurements into a 0 to 100 score with a
letter grade. The card groups the grader's five factors into rhetoric terms:

| Card section | Grader factor | What it measures |
| --- | --- | --- |
| **Logos** | logic | Thesis, claims with support, counterargument and rebuttal, fallacies, stance matches the assigned side |
| **Pathos** | emotion | Appeals to emotion, grounded in the speech's own quotes |
| **Delivery** | pace, diction, tone | Words per minute, pauses, fillers, stumbles, pitch variation, monotone stretches, emphasis |
| **Clash** (Player 2's response and Player 1's reply) | from `match_judge.clash` | Share of the opponent's claims your speech engages (2+ shared content words per claim) |

Ethos is not on the card: nothing in the audio measures credibility honestly, and the old plan's
crowd-voted ethos needed community judging, which is cut.

Every number keeps its evidence: the card lists each metric with its bucket and each deduction with
the quote or timestamp behind it, which is what makes "why did I lose?" answerable.

## How the winner is picked

`Arena/match_judge.judge_match("duel", ...)` already scores an opening plus a rebuttal plus clash,
weighted 0.5 / 0.3 / 0.2. The turn order maps onto it with no code changes:

| Player | `reports` passed to the judge | Match score |
| --- | --- | --- |
| Player 1, with reply | `{"opening": opening, "rebuttal": reply}` | 0.5 opening + 0.3 reply + 0.2 clash (reply against Player 2's speech) |
| Player 1, reply off | `{"opening": opening}` | opening |
| Player 2 | `{"opening": response, "rebuttal": response}` | 0.8 response + 0.2 clash (response against Player 1's opening) |

So going second carries a duty: Player 2's clash is measured against Player 1's claims, and ignoring
them costs up to 20 points. This mapping was run against the current judge: a 78-point response
with full clash scores 82.4, and Player 1's breakdown shows opening, reply and clash separately.

Close calls are handled by the judge's existing rules: a gap under 3.0 points is too close to call
on score, so the player who leads by 5+ points on more factors wins, and an even split is a draw.
The explanation line ("ben wins by 4.2 points (82.4 to 78.2)") prints on the verdict screen.

**Series.** A rematch swaps who goes first. `judge_series` sums both games and widens the margin by
the square root of the number of games, so the series winner is fair to turn order. In the verdict
JSON, Player 2's single speech appears as both `opening` and `rebuttal`; the UI labels it
"Response".

## Architecture

One laptop runs everything: a static web page and a small FastAPI server on localhost. The server
exists only to keep the ElevenLabs and Gemini keys out of the browser and to run the Python grader.
No database, no accounts, no deploy.

```
browser (web/, static)  --POST audio-->  FastAPI (server/, localhost:8000)  -->  softskills-grader  -->  Scribe, Gemini
        |                                        |
        | match state in memory + localStorage   | reports in memory, grader's own disk cache by audio hash
        '--POST verdict request-->               '--> Arena/match_judge.py
```

| Layer | Choice | Why |
| --- | --- | --- |
| Frontend | The existing static `web/` (Three.js from a CDN, plain JS modules) | Already built; no build step to break during the demo |
| Recording | `MediaRecorder`, `audio/webm;codecs=opus`, AGC off | The grader decodes webm through ffmpeg; AGC off keeps loudness metrics honest |
| Server | FastAPI, grader called in a thread pool | The grader is synchronous Python |
| Storage | Uploads in `server/uploads/`, reports in memory, grader cache on disk | Same recording, same report; restarts lose only in-progress matches |
| Win/loss record | `localStorage` in the browser, per player name | Enough for a head-to-head tally |

### Routes

| Route | Body | Returns |
| --- | --- | --- |
| `GET /api/motions` | - | `[{"id", "text"}]` |
| `POST /api/speeches` | form: `audio`, `motion_id`, `side` (`yes`/`no`), `role` (`opening`/`response`/`reply`) | `{"speech_id"}` at once; grading runs in the background |
| `GET /api/speeches/{id}` | - | `{"status": "grading" \| "done" \| "failed", "claims": [...], "report"?}` |
| `POST /api/verdict` | `{"p1": {"name", "opening", "reply"?}, "p2": {"name", "response"}}` (speech ids) | the `judge_match` verdict plus both reports |
| `POST /api/series` | `{"verdicts": [v1, v2]}` | `judge_series` output |

`claims` comes from the report's `evidence.argument.arguments[].claim`, and is what Player 2 sees
during prep. The server refuses to judge until every speech is `done`, and refuses speeches from
different motions (the judge's `check_comparable` raises on that).

### Server sketch

```python
# server/app/main.py
import asyncio, uuid
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from grader import grade                        # softskills-grader on PYTHONPATH
from match_judge import Entry, judge_match      # Arena/ on PYTHONPATH

app = FastAPI()
UPLOADS = Path(__file__).resolve().parents[1] / "uploads"
UPLOADS.mkdir(exist_ok=True)
MOTIONS = {m["id"]: m for m in [
    {"id": "ai-pace", "text": "Should the frontier of AI be paced?"},       # the grader's own test topic
    {"id": "free-college", "text": "Should college be free at public universities?"},
    {"id": "homework", "text": "Should homework be banned in elementary school?"},
    {"id": "age-check", "text": "Should social media require age verification?"},
    {"id": "remote", "text": "Should remote work be the default for office jobs?"},
]}
SPEECHES: dict[str, dict] = {}
GRADE_SLOTS = asyncio.Semaphore(3)


async def run_grade(sid: str):
    sp = SPEECHES[sid]
    try:
        async with GRADE_SLOTS:
            report = await asyncio.to_thread(grade, sp["path"], MOTIONS[sp["motion_id"]]["text"], sp["side"])
        sp.update(status="done", report=report)
    except Exception as e:                       # surface, never hang the match
        sp.update(status="failed", error=str(e))


@app.post("/api/speeches")
async def upload(audio: UploadFile = File(...), motion_id: str = Form(...),
                 side: str = Form(...), role: str = Form(...)):
    if motion_id not in MOTIONS or side not in ("yes", "no") or role not in ("opening", "response", "reply"):
        raise HTTPException(422, "bad motion, side or role")
    sid = uuid.uuid4().hex
    path = UPLOADS / f"{sid}.webm"
    path.write_bytes(await audio.read())
    SPEECHES[sid] = {"status": "grading", "path": str(path), "motion_id": motion_id, "side": side, "role": role}
    asyncio.create_task(run_grade(sid))
    return {"speech_id": sid}


class Side(BaseModel):
    name: str
    opening: str | None = None
    response: str | None = None
    reply: str | None = None


@app.post("/api/verdict")
def verdict(p1: Side, p2: Side):
    ids = [p1.opening, p2.response] + ([p1.reply] if p1.reply else [])
    if None in ids or any(SPEECHES.get(i, {}).get("status") != "done" for i in ids):
        raise HTTPException(409, "speeches still grading")
    r = {i: SPEECHES[i]["report"] for i in ids}
    p1_reports = {"opening": r[p1.opening]} | ({"rebuttal": r[p1.reply]} if p1.reply else {})
    p2_reports = {"opening": r[p2.response], "rebuttal": r[p2.response]}
    v = judge_match("duel", [Entry(p1.name, SPEECHES[p1.opening]["side"], p1_reports),
                             Entry(p2.name, SPEECHES[p2.response]["side"], p2_reports)])
    return {"verdict": v, "reports": r}
```

The two names must differ (the judge keys players by name); the setup screen enforces it.

## Screens

All in `web/`, one `index.html` with screens swapped by a small state machine in `web/game.js`.

| Screen | Contents |
| --- | --- |
| Home (exists) | The pixel-art arena; ARGUE starts setup. The walk-to-podium animation plays on each turn start |
| Setup | Two name fields, motion picker, reply round toggle, coin flip animation for sides |
| Turn | Speaker's name and side, motion, big countdown, mic level meter, "stop early" |
| Handoff | Full-screen "Pass to Player 2"; tap when ready. Prevents the next timer starting on the wrong person |
| Prep | 30 s countdown, the opponent's claims as they arrive ("Claims to answer") |
| Verdict | Two cards side by side: overall and letter, Logos / Pathos / Delivery / Clash, top 3 deductions with quotes; winner banner and the judge's explanation line |
| Card detail | Every metric, bucket and deduction; the transcript with fillers and pauses highlighted |
| Rematch | Same motion, roles swapped; after game 2 the series result |

## Build schedule

Lanes: **A** frontend, **B** server and judge, **C** grader and calibration. Feature freeze at hour 33.

| Phase | Builds | Lane | Hours | Done when |
| --- | --- | --- | --- | --- |
| 0. Foundations | `server/` FastAPI scaffold, `.env`, `.gitignore`, grader and judge importable, ffmpeg installed | B | 0-2 | `python softskills-grader/demo.py` and all test suites pass; a real 30 s webm grades with live keys |
| 1. Rate one speech | Recorder, `POST /api/speeches`, polling, one speech card | A + B | 2-8 | Recording on the page shows a card in under 20 s; the same file twice gives the identical report |
| 2. Two-player match | Setup, turns, handoffs, prep with claims, reply toggle, `POST /api/verdict` | A + B | 8-16 | Two people play a full match in under 5 minutes with no page reload; reply off works |
| 3. Verdict and rematch | Verdict screen, card detail, roles-swapped rematch, `POST /api/series`, localStorage tally | A | 16-24 | A draw, a factor tie-break and a clear win each render with the right banner and explanation |
| 4. Calibrate | 10+ team recordings: deliberately strong, flat, rambling, off-topic; tune `standards.yaml`; check clash on real transcripts | C | 8-28 | A strong and a deliberately weak speech land 15+ points apart; clash credits a real rebuttal and not an unrelated speech |
| 5. Demo | Podium walk animation, two rehearsed demo speeches, backup video, saved verdict JSON | All | 28-36 | Rehearsed twice under 3 minutes; the backup plays from disk |

If hour 8 slips: cut the reply round toggle (keep reply on), then the series, then card detail.
Never cut calibration or the backup video.

Tests to add in Phase 2: `server/tests/test_verdict.py` with stubbed `grade` (no network):
turn-order mapping, reply on and off, 409 while grading, mismatched motions refused, failed grade
surfaces as `failed`.

## Demo script (3 minutes)

1. **0:00 to 0:20, hook.** Everyone has an opinion and nobody gets scored on how they argue it.
   Two people, one motion, one laptop, and every point is measured.
2. **0:20 to 1:50, live match.** Two teammates on "Should homework be banned in elementary school?"
   Player 1 opens; Player 2 prepares with Player 1's claims on screen, then answers them.
3. **1:50 to 2:30, verdict.** Reveal both cards, read the explanation line, open one deduction and
   play the filler or monotone stretch it quotes. Point at Clash: Player 2 answered 3 of 4 claims.
4. **2:30 to 3:00, credibility.** Grade the same recording twice: identical report (cache by audio
   hash). Flash the research table from the grader README.

## Risks and fallbacks

| Risk | Mitigation |
| --- | --- |
| Venue Wi-Fi or an API fails mid-demo | Backup video; a saved verdict and both reports render from JSON on disk |
| Gemini or Scribe busy (503) | Merge PR #4 (retries and fallback models in the grader) before Phase 1 |
| Player 2's voice or room noise in Player 1's recording | Each turn records only during its countdown; quiet corner; test the mic on the setup screen |
| Grading slower than the 30 s prep | Prep shows claims when ready; the verdict screen waits on a spinner; at most 3 grades at once |
| Second-mover advantage | Player 1's reply round, the clash duty on Player 2, and a roles-swapped series |
| Clash heuristic too loose (shared words without real engagement) | Calibrate in Phase 4; upgrade to a Gemini label with a verified quote per claim if needed |
| Judges ask about bias | Voice metrics are relative to the speaker's own baseline; accent, natural pitch and loudness are not scored |
| Recordings | Stay on the laptop in `server/uploads/`; delete after the event |

## What stays in the repo but is not built on

`conversation/`, `Arena/arena_ratings.py`, `supabase/migrations/`, `tools/checks/` (Supabase and
Speech Engine checks) and the archived phase docs belong to the cut features. They are kept for
reference; nothing in this plan imports them.
