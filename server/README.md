# Solo API

Run from the repository root with Python 3.11+:

```sh
python -m venv .venv
python -m pip install -r server/requirements.txt -r softskills-grader/requirements.txt
python -m unittest discover -s server/tests
python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
```

Activate the virtual environment before running these commands. Install `ffmpeg` on PATH for browser WebM/Ogg/MP4 audio; WAV needs no external decoder. The server also serves `web/` at `/`, with public configuration at `/config.js`.

Copy `server/.env.example` to `server/.env`. Set the Supabase URL, service-role key, public anonymous key, ElevenLabs and Gemini keys. Apply `supabase/migrations/0001_init.sql`, `0002_prompts.sql`, then `0003_solo_feedback.sql` in order. Enable anonymous sign-ins in Supabase. The `audio` bucket stays private and all tables have RLS with no client policies. Run `python tools/checks/check_supabase.py` with real credentials to verify anonymous sessions and server token resolution. Credentials and infrastructure must be supplied by the deployment owner.

For a separate static host, set its exact origin in `CORS_ORIGINS`, and set `web/config.js` to the server API URL and public Supabase values. Same-origin serving fills public configuration from server environment automatically. Service-role and grading keys are never returned to the browser.

## Local saved-example viewer

Set `DEMO_MODE=true` and run on `127.0.0.1`. Open `http://localhost:8000/card.html?id=demo-best` or `http://localhost:8000/progress.html`. Fixture IDs are `demo-first`, `demo-best`, and `demo-ungradable`; prompt ID is `demo-ai-pacing`. The browser sends the explicit `local-demo` token. Fixtures persist in `server/data/demo.json` and contain the sample report, a lower-scoring synthetic report, and an ungradable example. Audio reuses the original synthetic buzz and canned word timeline from `softskills-grader/tests/fixtures.py`, **not real spoken audio**. Fixture words and their simulated timings are labeled synthetic so timed highlights can be tested without claiming real speech. Responses identify demo mode. Recordings are rejected with 503; this viewer does not claim to grade real speech. Host, origin, and client address must all be loopback. The grader's NumPy/SciPy dependencies are required for this fixture viewer as well as live grading.

## API contract

All live `/api/` routes require `Authorization: Bearer <Supabase access token>`. The server calls `auth.get_user(token)` and derives the owner from that result. It never accepts a client player ID. Responses use `Cache-Control: no-store`.

- `GET /api/prompts`: array of `{id,text,featured}`. `featured` matches `featured_on` to today's UTC date.
- `POST /api/speeches`: multipart `motion_id`, `side` (`yes` or `no`), `audio`, returns 202 `{speech_id}`. Maximum 15 MB; WAV, WebM, Ogg, MP4, MP3, FLAC. Uploads use owner-prefixed private storage paths. A background worker invokes the real grader and saves the unchanged report.
- `GET /api/speeches/{id}`: owner-only `{id,status,motion_id,side,prompt,report,audio_url,fixes,sections,deltas,previous_best,error}`. Other owners' IDs return 404 before signing any audio. Signed URLs expire after 300 seconds; reload the card to renew. `status` is `queued`, `grading`, `done`, `ungradable`, or `failed`. Ungradable keeps its explanation and transcript without contributing any zero grade.
- `GET /api/me`: `{id,display_name,prompts,speeches,bests,recent_overalls,season_tier:null,judge_xp:0}`. Speeches are in recording order, each with `{id,motion_id,side,created_at,status,overall,factor_scores}`. Failed/ungradable attempts have null overall/factors. Best per prompt uses highest graded overall, ties earliest. Recent overalls contains the last ten graded attempts.

Sections have `{score,factors,evidence}`. Logos uses `logic`, Pathos uses `emotion`, Delivery uses the normalized weighted average of `pace`, `diction`, and `tone`; it also includes each full factor separately. The report carries every metric, deduction, value, source, bucket and evidence without hiding zero deductions.

Fixes have `{factor,metric,label,kind,lost_points,tip}`. Metric loss is `weight_pct / 100 * metric.weight / sum(metric weights) * (100 - metric.score)`; deduction loss is `weight_pct / 100 * points_lost`. The metric denominator includes only measured/scored metrics, matching the grader's reweighting; unavailable metrics and unscored factors contribute no loss. Drop zeros, sort descending with stable factor/rubric order, take three. The sample sums to 4.275 lost points. These are rubric loss attributions before factor clipping: their sum may exceed `100 - overall` for clipped factors, and a single change is not a guaranteed score gain. Authored tips cover every current rubric metric and deduction.

`previous_best` is null or `{id,overall:{score,letter},factor_scores}`. Comparison candidates must be graded, on the same prompt and side, and strictly earlier than this recording. Highest overall wins, ties earliest. `deltas` is null or `{overall,logos,pathos,delivery,factors}` against that candidate. An unavailable or removed factor is omitted from numeric `factor_scores`, and its comparison delta is null if it was measurable on only one side. Delivery excludes unscored factors; section deltas are null when either section is unavailable. Later recordings cannot change an older card's baseline.

## Deployment and verification limits

Network-free route tests inject a repository and grader to exercise authentication failures, owner isolation, private URL generation, upload validation, grading success/failure, ungradable exclusion, calculations and explicit demo restrictions. `test_pipeline_integration.py` additionally uploads the same synthetic WAV twice through the production background grading path, replacing only external provider calls with canned responses. Real audio decoding, acoustics, feature extraction, scoring and persistence run unchanged; identical reports flow to full cards and ordered Me history. This is synthetic fixture validation, not a real speech or proof of deployed Supabase policies, provider availability or grading latency. Real recording and the plan's timing/quality calibration gate require configured live services and team recordings.

The current worker uses FastAPI background tasks on one machine, matching the plan. Keep `DATA_DIR` on persistent disk for grader cache repeatability. A machine restart can interrupt an in-flight task; queued/grading rows must be retried operationally. Durable job leasing and automatic restart recovery are future deployment hardening. Run one server worker so local cache writes share one process; do not scale horizontally without shared durable caches/jobs.
