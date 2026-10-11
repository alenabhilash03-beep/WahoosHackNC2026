# Handoff: SpeakUp

State on Sunday, October 11, 2026, with `main` deployed. Read this first, then the
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

Stack: static `web/` served by FastAPI on Fly.io from one origin, Supabase (GitHub sign-in,
Postgres, Storage), ElevenLabs Scribe and Gemini inside the grader.

## Where things stand

Live at https://speak-up.fly.dev: Fly app `speak-up`, one machine in `iad`, volume `arena_data`.
The Supabase project has migrations 0001 to 0005 applied. Sign-in is GitHub only
([setup](docs/implementation/social-accounts.md)): anonymous sign-ins are switched off in Supabase
and the server rejects anonymous tokens. Speeches recorded by guests before that change stay under
their guest ids and are not merged into accounts. The GitHub Pages copy
(`https://alenabhilash03-beep.github.io/WahoosHackNC2026/web/`) calls the same API: `web/config.js`
names the live server and `fly.toml` allows the Pages origin
([backend integration guide](docs/implementation/backend-integration.md)).

| Path | What it is | Tests |
| --- | --- | --- |
| `softskills-grader/` | Rates a speech; retries busy APIs; verifies which side the speaker argued before any wrong-side penalty | `tests/test_pipeline.py`, `test_stance.py`, `test_timing.py` |
| `Arena/arena_ratings.py` | Crowd scores from head-to-head votes; not used by the app yet | `Arena/test_arena_ratings.py` |
| `Arena/match_judge.py` | Duel verdicts (parked feature) | `Arena/test_match_judge.py` |
| `supabase/migrations/` | 0001 to 0005, applied to the live project | `tools/database/`; `server/tests/test_supabase_integration.py` in CI |
| `web/` | Home (Solo Speak, Daily Prompt), the practice round, speech card, progress, leaderboard, GitHub login | `web/test/*.test.mjs`; browser checks in `tools/checks/` |
| `server/` | Token check, private upload and grading, card with tips and deltas, coaching lines, Me, question writer, leaderboard, local and demo modes | `server/tests/` |

**Checked on the live site:** a spoken 50-second WAV came back as a card in 8.3 s, the same file
uploaded again gave the identical report, and another account got 404 on that speech (first
deployment, `server/README.md`). GitHub sign-in, session restore, sign-out and the stance decision
on a card were checked after PR #21. After PR #29, two temporary accounts were run against the live
API from the GitHub Pages origin: upload, grading, the card, private playback, progress, a second
sign-in, and 404 for the other account on the speech, its retry and its coaching.

**Not checked, or not built:**

- **Phase 2c calibration.** No results are recorded for strong against weak takes, agreement with
  listeners, whether the top tip raises the score, or the off-side check. The 20-second bar has
  single runs behind it, not 9 out of 10. Stance checking is tested with mocked model answers only.
- **Stage 2.** Only the grade leaderboard exists (`leaderboard.html`, `GET /api/leaderboard`): pick
  a name, unlimited tries, best score per player across all prompts. Home offers Daily Prompt, which
  plays the prompt whose `featured_on` is today, but nothing sets `featured_on`. Publishing, ranked
  attempts, season tiers and all of Phase 4 are not implemented. A lighter alternative, emoji
  reactions on shared speeches, is planned in
  [`docs/plans/2026-10-11-community-reactions.md`](docs/plans/2026-10-11-community-reactions.md).
  Its browser side is in `web/` (PR #28,
  [notes](docs/implementation/community-frontend.md)) and stays hidden while `COMMUNITY_ENABLED`
  is false.
- **Phase 5.** No backup video, saved card or rehearsal notes are in the repository.

## What to do next

1. Run Phase 2c with team recordings and tune `softskills-grader/config/standards.yaml` until every
   row of the Build Plan's "What good means" passes.
2. Decide what stage 2 is: the Build Plan's publishing and head-to-head rating, or the reactions
   plan. Either way Daily Prompt needs a prompt featured each day.
3. Phase 5: backup video, a saved card that renders from disk, two rehearsals.

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

- Every round calls Gemini several times: the question, the argument extraction, the stance check
  and the coaching lines on the card. Free keys have a daily limit; `GEMINI_API_KEYS` holds extras.
- Uploads and question requests have no per-account limit.
- Grading uses in-process background tasks on one machine. A restart loses a grading in progress
  and its row stays `queued` or `grading` until `server/scripts/recover_grading.py` marks it failed;
  the owner can then retry from the card. See `server/README.md`.
- Every generated question adds a row to `motions`, and `SupabaseRepository.prompts()` reads them in
  one request without paging (PostgREST returns at most 1000 rows by default).
- The crowd needs volume (about 12 listener-minutes per speech for 10 judgments); plan to seed.
- CI covers the database (`.github/workflows/database.yml`, including the real Supabase round trip)
  the frontend tests (`community-frontend.yml`) and the Python API, grader and Arena suites
  (`checks.yml`), on pull requests and on pushes to `main`. A push straight to `main` is only
  checked after it lands, and nothing deploys automatically: run `fly deploy` after merging.
- `.gitignore` excludes credentials, recordings, caches and local environments; never commit `.env`.

## Run all tests

```bash
python softskills-grader/tests/test_pipeline.py && python Arena/test_arena_ratings.py \
  && python Arena/test_match_judge.py && node --test web/test/*.test.mjs \
  && python -m pytest server/tests softskills-grader/tests/test_stance.py softskills-grader/tests/test_timing.py -q
```
