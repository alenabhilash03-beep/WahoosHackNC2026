# Debate Arena implementation guide

The [Build Plan](../../Build%20Plan.md) is the source of truth: match format, scoring, routes, a
server sketch, screens and the schedule. This page is the short checklist for building it.

## Phases

| Phase | Builds | Lane | Hours | Needs |
| --- | --- | --- | --- | --- |
| 0. Foundations | `server/` FastAPI scaffold, `.env`, `.gitignore`, ffmpeg | B | 0-2 | - |
| 1. Rate one speech | Recorder, `POST /api/speeches`, polling, speech card | A + B | 2-8 | 0 |
| 2. Two-player match | Setup, turns, handoffs, prep with claims, reply toggle, `POST /api/verdict` | A + B | 8-16 | 1 |
| 3. Verdict and rematch | Verdict screen, card detail, swapped rematch, `POST /api/series` | A | 16-24 | 2 |
| 4. Calibrate | Team recordings, tune `standards.yaml`, check clash | C | 8-28 | 1 |
| 5. Demo | Podium animation, rehearsal, backup video | All | 28-36 | all |

Lanes: **A** frontend, **B** server and judge, **C** grader and calibration. Feature freeze at
hour 33. Each phase's "Done when" list is in the Build Plan's schedule table.

## Target layout

```
WahoosHackNC2026/
  softskills-grader/      exists - rates one speech
  Arena/match_judge.py    exists - match and series verdicts
  web/                    exists - static home screen; add game.js, screens, recorder
  server/                 Phase 0
    app/main.py           routes from the Build Plan
    uploads/              recordings (git-ignored)
    tests/test_verdict.py stubbed grader, no network
    requirements.txt      fastapi, uvicorn, python-multipart + the grader's requirements
```

## Phase 0 steps

1. Merge PR #4 (grader retries and fallback models).
2. Add `.gitignore`: `.env`, `__pycache__/`, `server/uploads/`, `softskills-grader/cache/`.
3. `cp softskills-grader/.env.example softskills-grader/.env` and add `ELEVENLABS_API_KEY` and
   `GEMINI_API_KEY`. Never commit it; the repo is public.
4. Install ffmpeg (the grader needs it to decode browser webm).
5. Create `server/app/main.py` from the Build Plan sketch. Run with the grader and judge on the path:

   ```bash
   PYTHONPATH=softskills-grader:Arena uvicorn server.app.main:app --port 8000
   python3 -m http.server 5173 --directory web
   ```

6. Grade one real 30-second webm through `POST /api/speeches` with live keys.

## Run all tests before every merge

```bash
python softskills-grader/tests/test_pipeline.py && python Arena/test_match_judge.py \
  && pytest server/tests -q
```

## Environment variables

| Name | Where | Used for |
| --- | --- | --- |
| `ELEVENLABS_API_KEY` | server (`softskills-grader/.env`) | Scribe transcription |
| `GEMINI_API_KEY` | server (`softskills-grader/.env`) | Argument extraction |

## Archive

`archive/` holds the NetworkMaxx phase docs, their ready-made tests and the old build plan. They
describe cut features (personas, rapport, Speech Engine, online matches, community judging) and are
kept for reference only.
