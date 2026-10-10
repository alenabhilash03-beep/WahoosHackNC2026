# Handoff: NetworkMaxx

State of the repo on Saturday, October 10, 2026, with `main` at `664de68`. Read this first, then
the [implementation guide](docs/implementation/README.md).

## What we are building

NetworkMaxx is a voice game for soft skills. You talk to an AI persona (ElevenLabs voice, Gemini
brain) that has a hidden goal, a chess-style rapport bar moves after every turn, and you finish with
a player card. Every number on the card is either measured from your audio by our code or labeled
by Gemini with a quoted line as evidence; code, not the model, turns labels into scores. A second
loop, the Debate Arena, grades 60-second arguments and lets the community judge them.

Stack: Next.js (web), FastAPI (game server and audio analysis), Supabase (database, storage,
realtime), ElevenLabs Speech Engine and Scribe, Gemini.

## Where things stand

**Built and tested (all offline, no live API calls):**

| Path | What it is | Tests on `main` |
| --- | --- | --- |
| `softskills-grader/` | Grades a one-minute spoken argument: pace, diction, tone, logic, emotion | `python softskills-grader/tests/test_pipeline.py`: 23/23 |
| `conversation/netmaxx_scoring.py` | Measures a conversation: pace, delivery, confidence, listening, flow | `python conversation/test_netmaxx_scoring.py`: 16/16 |
| `Arena/arena_ratings.py` | Turns community votes into logos, pathos, ethos, persuasion and Elo | `python Arena/test_arena_ratings.py`: 13/13 |
| `Arena/match_judge.py` | Picks winners of duels, team matches and free-for-alls from grader reports | `python Arena/test_match_judge.py`: 27/27 |
| `supabase/migrations/0001_init.sql` | All 16 tables for every phase, RLS on, private `audio` bucket | parses; not yet applied to a project |
| `docs/implementation/` | The plan as 10 executable phases (files, code, commands, "Done when" checklists) | guide's server code assembled and imported; its tests pass |
| `docs/implementation/tests/` | Ready-made tests: rapport (5), match runner (5), persona timeline (7, vitest) | pass against the guide's code |
| `tools/checks/` | First-hour checks for Supabase and ElevenLabs ([README](tools/checks/README.md)) | run against local stand-ins only |

**Not started:** the app itself. There is no `server/` or `web/` folder yet, no Supabase project
with the schema applied, no Speech Engine created, nothing deployed, and no CI workflow (Phase 0
step 9 adds it). Nothing in this repo has been run against the live ElevenLabs, Gemini or Supabase
services.

**Open pull request:** [#4 Retry busy API responses and fall back to another Gemini model](https://github.com/alenabhilash03-beep/WahoosHackNC2026/pull/4)
(grader only). It merges cleanly onto current `main`, and with it merged all four suites pass
(grader 26/26, conversation 16, arena ratings 13, match judge 27). Its fallback models have not
been called live. Recommendation: merge it.

**Paused: the Arena.** Live matches (Phase 6), community judging (Phase 7) and the `Arena/` modules
are on hold by decision. Phase 5 (solo debate) is the entry point to the Arena, so treat it as
paused too unless the team decides otherwise. The code and docs stay in the repo untouched; don't
build on them until the pause is lifted.

## What to do next, in order

Hours follow the guide's 36-hour schedule. Lanes: A frontend, B live loop, C grading.

1. **Merge PR #4.**
2. **Phase 0, foundations (hours 0 to 2, everyone).** [phase-0-foundations.md](docs/implementation/phase-0-foundations.md)
   Create the Supabase project, turn on anonymous sign-ins, raise their rate limit (default 30 per
   hour per IP; the venue shares one IP), apply the schema, scaffold `server/` and `web/`, deploy.
   Then run `python tools/checks/check_supabase.py` and open `tools/checks/broadcast.html`.
   Both must pass before anything else is built on Realtime or auth.
3. **Phase 1, live voice loop (hours 2 to 6, lanes B + A).** [phase-1-voice-loop.md](docs/implementation/phase-1-voice-loop.md)
   Create the Speech Engine with `server/scripts/create_engine.py`, run
   `python tools/checks/check_engine.py`, build the call screen, then run the `/dev/voice-check`
   page and paste its summary into `server/app/speech_engine.py`.
4. **Phase 2, rapport meter (hours 6 to 12).** [phase-2-rapport.md](docs/implementation/phase-2-rapport.md)
5. **Phase 3, merged grader (hours 2 to 12, lane C, in parallel with 1 and 2).** [phase-3-merged-grader.md](docs/implementation/phase-3-merged-grader.md)
6. **Phase 4, card and replay (hours 12 to 20).** [phase-4-card-and-replay.md](docs/implementation/phase-4-card-and-replay.md)
7. **Phase 8, game layer, without the Arena parts.** Its tiers, XP, rematch, daily challenge and
   share image only need Phase 4. Skip the `ratings` rows on `/me`, which come from Phase 6.
8. **Phase 9, calibrate and demo.** Never cut it. Skip the match-margin calibration while the Arena
   is paused.

If the schedule slips, the guide's cut order is Phase 8 first. Feature freeze is hour 33.

## Decisions already made (don't relitigate without a reason)

- **Scores come from code.** Gemini extracts and labels with verbatim quotes (majority of 3 runs);
  Python turns labels into numbers. No route asks a model for a score.
- **One recording, one grade.** Grades are cached by audio hash and stored with `standards_id`.
- **The browser never holds an API key.** It gets the Supabase public key and short-lived tokens.
  Every API call carries the player's Supabase access token; the server resolves it with
  `auth.get_user` and never trusts a player id sent in a body.
- **Live updates go through Supabase Realtime broadcast** on public channels: the server sends to
  the bare topic (`session:<id>`), the browser listens with `supabase.channel("session:<id>")`.
- **One server machine.** Rapport state lives in memory (`fly scale count 1`).
- **Headphones required.** The persona's voice in the mic track breaks every timing metric.

## Facts confirmed from the SDK source (so you don't rediscover them)

Read from `@elevenlabs/react` 1.17.0, `@elevenlabs/client` 1.27.0, `@supabase/supabase-js`
2.117.3, `supabase` 2.32.0 and `elevenlabs` 2.71.0. Full list in
[tools/checks/README.md](tools/checks/README.md).

- `useConversation` throws unless rendered inside `<ConversationProvider>`.
- `startSession()` returns nothing. The conversation id comes from `onConnect({ conversationId })`
  (or `getId()` once connected); `/bind` happens there.
- `onMessage` gives `{ message, role: "agent" | "user", event_id }`. `source` is deprecated.
- Persona speaking times come from `onAudio` (every 25 ms the persona is audible), not
  `onModeChange`, whose "listening" can lag on WebRTC sessions.
- The Speech Engine's `client_events` list decides which events reach the browser; a missing one
  fails silently. `check_engine.py --fix` repairs it. The first-message override can only be set
  when the engine is created.
- Supabase's new keys (`sb_publishable_`, `sb_secret_`) are not JWTs: the server sends its key on
  the `apikey` header only. Secret keys are refused from browsers.
- Leave Realtime's "Allow public access" on; every channel in the guide is public.

## Known gaps and risks

- **Nothing has been run live.** The first-hour checks exist for exactly this; run them in the
  order above.
- **The live persona and judge have no retry or fallback.** PR #4 adds retries and fallback models
  to the grader only. A Gemini 503 during the demo would still stall the call. When building
  Phases 1 and 2, give the persona and rapport judge the same treatment.
- **The repo is public.** Never commit `server/.env`, `web/.env.local` or any key. There is no
  `.gitignore` yet; add one in Phase 0 (`.env`, `.env.local`, `__pycache__/`, `node_modules/`).
  A scan of the history found no committed keys.
- **`NetworkMaxx Build Plan.md` is an older export.** It predates the Arena matches section and the
  pitch-tracker update after PR #2 (it still says to switch to Praat, which is no longer needed).
  Where it disagrees with `docs/implementation/`, the implementation guide is current.
- **Small doc drift:** `softskills-grader/README.md` says 22 checks; there are 23 on `main` (26 with
  PR #4).

## Run all tests

```bash
python softskills-grader/tests/test_pipeline.py && python conversation/test_netmaxx_scoring.py \
  && python Arena/test_arena_ratings.py && python Arena/test_match_judge.py
```

Expected: 23/23 (26/26 after PR #4), 16 PASS, 13 PASS, 27/27. Once `server/` exists, add
`pytest server/tests -q`, as the guide's "Conventions" section says.

## History so far

All on October 10, 2026:

- Initial commit, then the spoken-argument grader (PR #1).
- Upload of the build plan, then grading standards grounded in published research (PR #2).
- Debate Arena rating aggregator, then the m4a decoding fix (PR #3).
- Match judge, conversation scorer, schema and implementation guide.
- SDK event and broadcast confirmation plus the first-hour checks.
- PR #4 (retries and fallback) is open.

## Links

- [Implementation guide](docs/implementation/README.md): phases, conventions, environment
  variables, first-hour checks
- [First-hour checks](tools/checks/README.md)
- [Grader README](softskills-grader/README.md), including the research behind the standards
- [Open PR #4](https://github.com/alenabhilash03-beep/WahoosHackNC2026/pull/4)
- Live build plan (Claude Doc, private to its owner unless shared):
  https://claude.ai/code/artifact/ffc757fc-df04-4049-a416-22978126ba01
