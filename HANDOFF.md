# Handoff: Debate Arena

State after the Phase 2 solo implementation on Saturday, October 10, 2026. Read this first, then the
[Build Plan](Build%20Plan.md).

## What we are building

A game that rates how well you argue out loud, in two stages:

1. **Single player (build first, get it good).** Pick a prompt and a side, speak for 60 seconds,
   get a speech card in under 20 seconds: overall and letter, Logos / Pathos / Delivery, every metric
   with its evidence, and the three changes worth the most points. Retry and see the deltas.
2. **Community (after stage 1 passes its bar).** A prompt of the day; everyone's answers are graded
   and ranked on a leaderboard; community members rate speeches by hearing two answers from opposite
   sides and picking the better one, which produces crowd scores and a crowd leaderboard.

The two-player duel is parked. The networking game (personas, rapport, live voice) is cut and
archived.

Stack: static `web/` on GitHub Pages, FastAPI on Fly.io, Supabase (anonymous sign-in, Postgres,
Storage), ElevenLabs Scribe and Gemini inside the grader.

## Where things stand

| Path | What it is | Tests on `main` |
| --- | --- | --- |
| `softskills-grader/` | Rates a speech; retries busy APIs, word timings and measured monotone ranges for replay | 26 pipeline + 7 timing tests |
| `Arena/arena_ratings.py` | Crowd scores from head-to-head votes | `python Arena/test_arena_ratings.py`: 13/13 |
| `Arena/match_judge.py` | Duel verdicts (parked feature) | `python Arena/test_match_judge.py`: 27/27 |
| `supabase/migrations/` | 0001 + 0002 + 0003 (solo status, history indexes, practice prompts) | not applied to a live project |
| `web/` | Home, recording, full speech card, HTTP-backed Me radar and history | 33 frontend tests; desktop/mobile browser checks |
| `server/` | Supabase auth, private upload/grading, card/tips/deltas, Me API, local saved-example viewer | See [Phase 2 verification](docs/implementation/phase-2-solo.md) |

**Implemented:** Phase 2 code and its recording/auth/API prerequisites. Full card, replay, coaching tips and progress work in the local HTTP preview. Demo audio is explicitly synthetic. Supabase, ElevenLabs, Gemini and deployed real-speech acceptance have not been verified.

## What to do next

Follow [Phase 2 setup and acceptance](docs/implementation/phase-2-solo.md) and `server/README.md`: configure live services, apply the migrations, verify a real recording and retry, then complete Phase 2c calibration. Keep the community gate closed until the Build Plan's quality checks pass. From Phase 3 only the grade leaderboard exists (`leaderboard.html`, `GET /api/leaderboard`): pick a name, unlimited tries, best score per player across all prompts. The prompt of the day, publishing, ranked attempts, season tiers and all of Phase 4 are not implemented.

## Decisions

- **Solo first, with a bar.** Stage 2 starts only when "What good means" in the Build Plan passes.
- **Prompts are motions with sides.** The grader's stance check and the crowd math both need sides.
- **Grade and crowd are separate leaderboards.** Their gap is coaching, not noise to average away.
- **Head-to-head picks, not star ratings,** for community rating; the math is already built.
- **3 ranked attempts per prompt;** practice is unlimited and never ranked.
- **Scores come from code.** Gemini extracts with verbatim quotes; Python turns them into numbers.
- **One recording, one grade.** The grader caches by audio hash.
- **Keys stay on the server.** The browser holds only the Supabase public key and its own session.

## Known gaps

- Nothing has run against live ElevenLabs, Gemini or Supabase yet.
- The crowd needs volume (about 12 listener-minutes per speech for 10 judgments); plan to seed.
- `.gitignore` excludes credentials, recordings, caches and local environments; never commit `.env`.
- Grading uses in-process background tasks on one machine. Keep the cache on persistent disk; restart recovery is documented in `server/README.md`.

## Run all tests

```bash
python softskills-grader/tests/test_pipeline.py && python Arena/test_arena_ratings.py \
  && python Arena/test_match_judge.py && node --test web/test/*.test.mjs \
  && python -m pytest server/tests softskills-grader/tests/test_timing.py -q
```
