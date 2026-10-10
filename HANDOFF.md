# Handoff: Debate Arena

State of the repo on Saturday, October 10, 2026. Read this first, then the
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
| `softskills-grader/` | Rates a speech; retries busy APIs and falls back to another Gemini model | `python softskills-grader/tests/test_pipeline.py`: 26/26 |
| `Arena/arena_ratings.py` | Crowd scores from head-to-head votes | `python Arena/test_arena_ratings.py`: 13/13 |
| `Arena/match_judge.py` | Duel verdicts (parked feature) | `python Arena/test_match_judge.py`: 27/27 |
| `supabase/migrations/` | `0001_init.sql` + `0002_prompts.sql` (prompt of the day, ranked attempts) | both parse; not applied to a project |
| `web/` | Pixel-art home screen with an ARGUE button | - |

**Not started:** the Supabase project, `server/`, every screen beyond home.

## What to do next

Follow the schedule in the Build Plan and the checklist in
[docs/implementation/README.md](docs/implementation/README.md): Phase 0 foundations, Phase 1 record
and grade, Phase 2 make solo good with calibration in parallel, then the gate, then Phase 3 prompt
of the day and Phase 4 community rating.

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
- The repo is public: never commit `.env`; add `.gitignore` in Phase 0.

## Run all tests

```bash
python softskills-grader/tests/test_pipeline.py && python Arena/test_arena_ratings.py \
  && python Arena/test_match_judge.py
```
