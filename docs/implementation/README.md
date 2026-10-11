# SpeakUp implementation guide

Database setup and regression checks are in the [database guide](../../supabase/README.md).
The separate positive-reactions plan's phase 3 UI is covered by the [community frontend guide](community-frontend.md); its feature flag stays off until the community backend and cleanup are ready.
The [Build Plan](../../Build%20Plan.md) is the source of truth: both stages, scoring, schema,
routes, screens and the schedule. This page is the short checklist for starting the build.

## Phases

| Phase | Builds | Lane | Hours | Detailed steps |
| --- | --- | --- | --- | --- |
| 0. Foundations | Supabase, server scaffold, deploy, sign-in on the static page | B + A | 0-3 | archive/phase-0, steps 1, 3, 4, 5, 7, 8, 9 |
| 1. Record and grade | Prompts, practice screen, upload, grading worker, basic card | A + B | 3-9 | archive/phase-5, steps 2, 3, 7 |
| 2. Make solo good | Full card, "fix these first", deltas, Me page | A + B | 9-16 | [Implementation and checks](phase-2-solo.md) |
| 2c. Calibrate | Team recordings, tune `standards.yaml`, tips | C | 4-16 | Build Plan, "What good means" |
| 3. Prompt of the day | Featured prompt, publish, ranked attempts, grade leaderboard, tiers | B + A | 16-22 | archive/phase-5 step 4; Build Plan, "Stage 2" |
| 4. Community rating | Judging flow, aggregation job, crowd scores and board | B + A + C | 20-29 | archive/phase-7, all steps |
| 5. Demo | Seeding, rehearsal, backup video | All | 29-36 | Build Plan, "Demo script" |

**Gate:** Phases 3 and 4 start only after every row of the Build Plan's "What good means before
stage 2 starts" passes. The Build Plan's "Which archived steps to take" table lists what to skip in
each archived doc (Next.js, Speech Engine, voice mask, AI opponent, conversation XP).

## Layout

```
WahoosHackNC2026/
  softskills-grader/            exists - rates one speech
  Arena/arena_ratings.py        exists - crowd scores (stage 2)
  Arena/match_judge.py          exists - parked two-player duel
  supabase/migrations/          0001 to 0005, applied to the live project
  tools/checks/                 browser checks of sign-in and the solo round
  web/                          exists - static site served by the server: home, practice, card, progress, leaderboard, login
    js/radar.js                 exists - speech radar (first vs best per part); progress.html shows it
  server/                       exists - FastAPI on Fly.io
    app/main.py                 every route, the token check and background grading
    app/repository.py local.py demo.py   Supabase, local-files and saved-example storage
    app/feedback.py tips.py coaching.py questions.py leaderboard.py settings.py
    tests/
  not built (stage 2)           safety check, publishing, judgments, aggregation job, seeding scripts
```

## Run all tests before every merge

```bash
python softskills-grader/tests/test_pipeline.py && python Arena/test_arena_ratings.py \
  && python Arena/test_match_judge.py && node --test web/test/*.test.mjs \n  && python -m pytest server/tests softskills-grader/tests/test_stance.py softskills-grader/tests/test_timing.py -q
```

## Environment variables

| Name | Where | Used for |
| --- | --- | --- |
| `ELEVENLABS_API_KEY` | server | Scribe transcription (in the grader) |
| `GEMINI_API_KEY` | server | Argument extraction, safety check |
| `SUPABASE_URL` | server, web | Project URL |
| `GEMINI_API_KEYS` | server | Optional extra Gemini keys, comma separated, each from a different Google project |
| `SUPABASE_SERVICE_ROLE_KEY` | server only | Database and storage |
| `SUPABASE_ANON_KEY` | server, which passes it to the web page in `/config.js` | Public anon/publishable key for GitHub sign-in; RLS blocks every table |
| `API_URL` | web (`web/config.js`), only on a separate static host | The FastAPI base URL; empty when the server serves the site |
| `SIGNING_SECRET` | server | Matchup tokens (stage 2) |

## Archive

`archive/` holds the NetworkMaxx phase docs, their ready-made tests and the old build plan. Phases
0, 5, 7 and part of 8 are reused as described above; the rest describe cut features (personas,
rapport, Speech Engine, online matches).
