# SpeakUp: map for agents

Speak for 60 s on a yes/no question and get a graded speech card. Live: https://speak-up.fly.dev.
Status and phases: `HANDOFF.md` and `Build Plan.md`. Read those before changing scope.

## Where things are
| Path | What |
|---|---|
| `server/app/` | FastAPI app. `main.py` routes, `repository.py` Supabase storage, `local.py` the same interface on disk (`LOCAL_MODE`), `community.py` reactions, `leaderboard.py`, `questions.py` (Gemini questions), `feedback.py`/`coaching.py` card fixes and tips |
| `softskills-grader/` | The grader: ElevenLabs transcript + Gemini argument map in, scores from our own rubric out (`grader/pipeline.py`, `scoring.py`; rubric in `config/*.yaml`) |
| `web/` | Static front end, no build step. Pages are `*.html`; logic in `web/js/` (`*-model.js` files are pure and unit-tested) |
| `supabase/migrations/` | Numbered SQL migrations. Add the next number; never edit an applied one |
| `Arena/`, `conversation/` | Standalone scoring experiments with their own tests |
| `tools/checks/` | Browser and live checks (Playwright) |
| `docs/implementation/` | Per-feature guides; `server/README.md` is the API contract |

## Run and test
- Server, no accounts: `LOCAL_MODE=true python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000` (keys from `softskills-grader/.env`).
- Python tests (what CI runs): `python -m pytest server/tests softskills-grader/tests -q`, plus `python Arena/test_arena_ratings.py` and `python Arena/test_match_judge.py`.
- Web tests: `npm test --prefix web`.

## Rules
- Team repo: work on a branch and open a PR; teammates also push to `main`, so `git fetch` before starting and before finishing.
- Never commit `.env` files or keys. Service-role and grading keys stay server-side.
- The Gemini keys are rate-limited; don't spend grading calls casually in tests.
- The hosted database holds real users' speeches: never bulk-delete rows.
