# NetworkMaxx implementation guide

This folder turns the [NetworkMaxx Build Plan](../../NetworkMaxx%20Build%20Plan.md) into work a
team can execute phase by phase. Each phase file lists the files to create, the exact contracts
between pieces, the commands to run, and an acceptance checklist. A phase is done when every box in
its **Done when** list is ticked, not when the code is written.

## What already exists in this repo

| Path | What it is | Tests |
| --- | --- | --- |
| `softskills-grader/` | Grades a one-minute spoken argument: `grade(audio, topic, side) -> report` | `python softskills-grader/tests/test_pipeline.py` (23) |
| `conversation/netmaxx_scoring.py` | Measures a conversation: pace, delivery, confidence, listening, flow | `python conversation/test_netmaxx_scoring.py` (16) |
| `Arena/arena_ratings.py` | Turns community votes into logos, pathos, ethos, persuasion and Elo | `python Arena/test_arena_ratings.py` (13) |
| `Arena/match_judge.py` | Picks winners of duels, team matches and free-for-alls from grader reports | `python Arena/test_match_judge.py` (27) |
| `supabase/migrations/0001_init.sql` | Every table for every phase | parsed with the Postgres parser |
| `docs/implementation/tests/` | Ready-made server tests for Phases 2 and 6; copy into `server/tests/` | 5 + 5, run against this guide's code |

**How this guide was checked.** Every Python block in it parses. The server files it defines were
assembled from the blocks exactly as written: all 23 modules import against the real libraries, and
the Phase 2 rapport tests and Phase 6 match-runner tests in `tests/` pass. What could not be
checked offline is listed under "Things to confirm in the first hour".

## What the phases build

| Phase | File | Builds | Lane | Hours | Needs |
| --- | --- | --- | --- | --- | --- |
| 0 | [phase-0-foundations.md](phase-0-foundations.md) | Accounts, keys, repo layout, database, deploys, auth | All | 0-2 | - |
| 1 | [phase-1-voice-loop.md](phase-1-voice-loop.md) | Talk to a Gemini persona through ElevenLabs; record your mic | A + B | 2-6 | 0 |
| 2 | [phase-2-rapport.md](phase-2-rapport.md) | Scenarios, rapport judge, eval bar, move badges, win and lose | B + A | 6-12 | 1 |
| 3 | [phase-3-merged-grader.md](phase-3-merged-grader.md) | Conversation metrics inside the grader, one rubric per mode | C | 2-12 | 0 |
| 4 | [phase-4-card-and-replay.md](phase-4-card-and-replay.md) | Post-call grading, player card, replay timeline | C + A | 12-20 | 2, 3 |
| 5 | [phase-5-debate-solo.md](phase-5-debate-solo.md) | Motions, 60 s recording, debate grade, publishing | D | 12-18 | 0 |
| 6 | [phase-6-arena-matches.md](phase-6-arena-matches.md) | Live duels, team matches, free-for-all, machine verdict, ratings | B + A | 18-26 | 5 |
| 7 | [phase-7-community-judging.md](phase-7-community-judging.md) | Head-to-head judging, crowd scores, gold pairs | D + C | 20-28 | 5 |
| 8 | [phase-8-game-layer.md](phase-8-game-layer.md) | Ranks, XP, rematch deltas, daily challenges, share image | A | 26-31 | 4, 6 |
| 9 | [phase-9-calibrate-and-demo.md](phase-9-calibrate-and-demo.md) | Calibration, seeding, rehearsal, backup video, submission | All | 28-36 | all |

Lanes: **A** frontend, **B** live loop and matches, **C** grading, **D** debate, community and demo.
Feature freeze at hour 33. If hour 6 slips, cut in this order: Phase 8, then team mode in Phase 6,
then the rebuttal round. Never cut Phase 9.

## Target layout

```
WahoosHackNC2026/
  softskills-grader/            exists - speech grader package (Phase 3 extends it)
  conversation/                 exists - conversation scorer (Phase 3 ports it into the grader)
  Arena/                        exists - crowd aggregation + match judge
  supabase/migrations/          exists - schema
  server/                       Phase 0 - FastAPI game server (Python 3.11+)
    app/
      main.py                   FastAPI app, routers, CORS, health
      settings.py               environment variables
      auth.py                   verifies the Supabase access token on every request
      db.py                     Supabase client with the service-role key
      realtime.py               broadcast(topic, event, payload) over the REST API
      storage.py                upload/download/sign recordings
      grading.py                imports softskills-grader and Arena modules
      speech_engine.py          Phase 1 - ElevenLabs Speech Engine handlers
      persona.py                Phase 1-2 - system prompt per rapport band
      rapport.py                Phase 2 - judge, nudges, badges
      scenarios.py              Phase 2 - YAML loader
      routes/                   sessions.py, speeches.py, matches.py, judgments.py, me.py
      workers/                  postcall.py, debate.py, match_runner.py, aggregate.py
    scenarios/*.yaml
    tests/
    requirements.txt
    run.py                      starts FastAPI and the Speech Engine together
    Dockerfile
  web/                          Phase 0 - Next.js app (TypeScript, Tailwind)
    app/                        page.tsx, play/[scenario]/page.tsx, card/[id]/page.tsx,
                                debate/page.tsx, arena/page.tsx, match/[code]/page.tsx,
                                judge/[motion]/page.tsx, me/page.tsx
    components/                 EvalBar, MoveBadge, PlayerCard, ReplayTimeline, Recorder,
                                Countdown, LobbyRoster, VerdictReveal, JudgeMatchup
    lib/                        api.ts, supabase.ts, recorder.ts, clock.ts
  docs/implementation/          this guide
```

## Conventions every phase follows

- **The browser never holds an API key.** ElevenLabs, Gemini and the Supabase service-role key live
  only on the server. The browser gets a Supabase anon key (for sign-in and broadcast listening) and
  short-lived tokens.
- **Every API call carries the player's Supabase access token** as `Authorization: Bearer <token>`.
  `server/app/auth.py` resolves it to a `player_id`; no route trusts a player id sent in a body.
- **Times come from the server.** Phase deadlines are absolute UTC timestamps (`phase_ends_at`);
  clients count down to them and never decide when a phase ends.
- **Scores come from code.** Gemini labels and extracts; Python code in `softskills-grader`,
  `conversation` and `Arena` turns those into numbers. No route asks a model for a score.
- **One recording, one grade.** Grader results are cached by audio hash, so re-grading costs nothing
  and returns the identical report. Store `standards_id` with every grade.
- **Run all four test suites before every merge to main:**

  ```bash
  python softskills-grader/tests/test_pipeline.py && python conversation/test_netmaxx_scoring.py \
    && python Arena/test_arena_ratings.py && python Arena/test_match_judge.py && pytest server/tests -q
  ```

## Environment variables

| Name | Where | Used for |
| --- | --- | --- |
| `ELEVENLABS_API_KEY` | server | Speech Engine, Scribe, Voice Changer |
| `SPEECH_ENGINE_ID` | server | id printed by `server/scripts/create_engine.py` (Phase 1) |
| `GEMINI_API_KEY` | server | persona, rapport judge, labeling, safety check |
| `SUPABASE_URL` | server, web | project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | server only | database, storage, broadcast |
| `NEXT_PUBLIC_SUPABASE_URL` | web | same as `SUPABASE_URL` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web | anonymous sign-in, listening to broadcasts |
| `NEXT_PUBLIC_API_URL` | web | the FastAPI base URL |
| `PUBLIC_WS_URL` | server | public `wss://.../ws` address ElevenLabs connects to |

## Checked against the installed libraries

The server code in this guide was checked against `elevenlabs` 2.71.0, `google-genai` 2.29.0,
`supabase` 2.32.0 and `fastapi` 0.143.0:

- `AsyncElevenLabs().speech_engine` has `create`, `get` and `update`; only `create` accepts
  `overrides`. `engine.serve(port, path, on_init, on_transcript, on_close, on_error)` wires those
  handler names with exactly the signatures used in Phase 1, and transcript messages have `.role`
  and `.content`.
- `conversational_ai.conversations.get_webrtc_token`, `speech_to_speech.convert` and
  `text_to_speech.convert` exist.
- `types.ThinkingConfig(thinking_level="low")` is accepted; `aio.models.generate_content_stream`
  and `aio.models.embed_content` exist.
- `create_client(...).auth.get_user(token)` exists.

## Things to confirm in the first hour

These depend on the live services or the browser SDK and could not be checked offline:

1. The `@elevenlabs/react` event names for mode changes and messages, and how the browser reads the
   conversation id (`startSession` return value or `conversation.getId()`). Phase 1, step 8.
2. That a broadcast sent through the REST endpoint reaches a `supabase.channel(topic)` listener
   with the same topic name. Phase 0, step 7.
3. That `auth.get_user(token)` accepts an anonymous user's access token. Phase 0, step 5.
