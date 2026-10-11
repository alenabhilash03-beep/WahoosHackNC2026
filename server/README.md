# Solo API

Run from the repository root with Python 3.11+:

```sh
python -m venv .venv
python -m pip install -r server/requirements.txt -r softskills-grader/requirements.txt
python -m unittest discover -s server/tests
python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
```

Activate the virtual environment before running these commands. Install `ffmpeg` on PATH for browser WebM/Ogg/MP4 audio; WAV needs no external decoder. The server also serves `web/` at `/`, with public configuration at `/config.js`.

Copy `server/.env.example` to `server/.env`. Set the Supabase URL, server-only service-role/secret key, public anon/publishable key, ElevenLabs and Gemini keys. Apply all four `supabase/migrations/` files in order. Enable email authentication and disable anonymous sign-ins. Configure confirmation/reset redirects and SMTP using the [email accounts guide](../docs/implementation/email-accounts.md). The `audio` bucket stays private and all tables have RLS with no client policies. Credentials and infrastructure must be supplied by the deployment owner.

For a separate static host, set its exact origin in `CORS_ORIGINS`, and set `web/config.js` to the server API URL and public Supabase values. Same-origin serving fills public configuration from server environment automatically. Service-role and grading keys are never returned to the browser.

## Local mode: play and grade on this computer, no accounts

The quickest way to play with real grading. Speeches, prompts and recordings are stored in
`server/data/local/`; ElevenLabs and Gemini keys come from `softskills-grader/.env`. No Supabase.

```sh
LOCAL_MODE=true python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000` and press ARGUE. The browser sends the fixed `local-player` token, and
the server only answers requests from this computer (host, origin and client must be loopback). Static
files are served with `Cache-Control: no-cache`, so edits show up on reload.

Accounts mode later: leave `LOCAL_MODE` unset and configure Supabase as above, then apply
`supabase/migrations/0004_generated_prompts.sql`. `LocalRepository` (`server/app/local.py`) and
`SupabaseRepository` have the same methods, so routes and the web page are unchanged.

## Local saved-example viewer

Set `DEMO_MODE=true` and run on `127.0.0.1`. Open `http://localhost:8000/card.html?id=demo-best` or `http://localhost:8000/progress.html`. Fixture IDs are `demo-first`, `demo-best`, and `demo-ungradable`; prompt ID is `demo-ai-pacing`. The browser sends the explicit `local-demo` token. Fixtures persist in `server/data/demo.json` and contain the sample report, a lower-scoring synthetic report, and an ungradable example. Audio reuses the original synthetic buzz and canned word timeline from `softskills-grader/tests/fixtures.py`, **not real spoken audio**. Fixture words and their simulated timings are labeled synthetic so timed highlights can be tested without claiming real speech. Responses identify demo mode. Recordings are rejected with 503; this viewer does not claim to grade real speech. Host, origin, and client address must all be loopback. The grader's NumPy/SciPy dependencies are required for this fixture viewer as well as live grading.

## API contract

All live `/api/` routes require `Authorization: Bearer <Supabase access token>`. The server calls `auth.get_user(token)` and derives the owner from that result. It never accepts a client player ID. Responses use `Cache-Control: no-store`.

- `GET /api/prompts`: array of `{id,text,featured}`. `featured` matches `featured_on` to today's UTC date.
- `POST /api/questions`: JSON `{category, difficulty}` (`beginner`, `intermediate` or `expert`). Gemini writes a yes-or-no question for the category; it is saved as a prompt and returned as `{id,text,category,difficulty}`. Categories are cleaned to 40 characters; a category Gemini judges unsuitable returns 422 with a reason. If Gemini is unavailable, a template question is used so play continues.
- `POST /api/speeches`: multipart `motion_id`, `side` (`yes` or `no`), `audio`, returns 202 `{speech_id}`. Maximum 15 MB; WAV, WebM, Ogg, MP4, MP3, FLAC. Uploads use owner-prefixed private storage paths. A background worker invokes the real grader and saves the unchanged report.
- `GET /api/speeches/{id}`: owner-only `{id,status,motion_id,side,prompt,report,audio_url,fixes,sections,deltas,previous_best,error}`. Other owners' IDs return 404 before signing any audio. Signed URLs expire after 300 seconds; reload the card to renew. `status` is `queued`, `grading`, `done`, `ungradable`, or `failed`. Ungradable keeps its explanation and transcript without contributing any zero grade.
- `GET /api/me`: `{id,display_name,prompts,speeches,bests,recent_overalls,season_tier:null,judge_xp:0}`. Speeches are in recording order, each with `{id,motion_id,side,created_at,status,overall,factor_scores}`. Failed/ungradable attempts have null overall/factors. Best per prompt uses highest graded overall, ties earliest. Recent overalls contains the last ten graded attempts.

Sections have `{score,factors,evidence}`. Logos uses `logic`, Pathos uses `emotion`, Delivery uses the normalized weighted average of `pace`, `diction`, and `tone`; it also includes each full factor separately. The report carries every metric, deduction, value, source, bucket and evidence without hiding zero deductions.

Fixes have `{factor,metric,label,kind,lost_points,tip}`. Metric loss is `weight_pct / 100 * metric.weight / sum(metric weights) * (100 - metric.score)`; deduction loss is `weight_pct / 100 * points_lost`. The metric denominator includes only measured/scored metrics, matching the grader's reweighting; unavailable metrics and unscored factors contribute no loss. Drop zeros, sort descending with stable factor/rubric order, take three. The sample sums to 4.275 lost points. These are rubric loss attributions before factor clipping: their sum may exceed `100 - overall` for clipped factors, and a single change is not a guaranteed score gain. Authored tips cover every current rubric metric and deduction.

`previous_best` is null or `{id,overall:{score,letter},factor_scores}`. Comparison candidates must be graded, on the same prompt and side, and strictly earlier than this recording. Highest overall wins, ties earliest. `deltas` is null or `{overall,logos,pathos,delivery,factors}` against that candidate. An unavailable or removed factor is omitted from numeric `factor_scores`, and its comparison delta is null if it was measurable on only one side. Delivery excludes unscored factors; section deltas are null when either section is unavailable. Later recordings cannot change an older card's baseline.

## Deploy to Fly.io

`Dockerfile`, `.dockerignore` and `fly.toml` at the repository root build one container that serves the API and `web/` from the same origin, so `/config.js` is filled from the server environment and `web/config.js` and `CORS_ORIGINS` can stay empty. The image installs FFmpeg, runs a single Uvicorn worker, and keeps `DATA_DIR` on a volume mounted at `/data`.

Before deploying: create the Supabase project, configure email accounts using the [account guide](../docs/implementation/email-accounts.md), apply migrations 0001 to 0004, and have the ElevenLabs and Gemini keys ready. Then, from the repository root:

```sh
fly launch --no-deploy --copy-config        # keep fly.toml; choose the app name
fly volumes create arena_data --size 1 --region iad
fly secrets set SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... SUPABASE_ANON_KEY=... ELEVENLABS_API_KEY=... GEMINI_API_KEY=...
fly deploy
```

Open `https://<app>.fly.dev/practice.html`. Keep one machine: `fly.toml` disables auto-stop and scaling out is unsupported (see below). To also serve the GitHub Pages copy, set `API_URL`, `SUPABASE_URL` and `SUPABASE_ANON_KEY` in `web/config.js` and add the Pages origin (`https://alenabhilash03-beep.github.io`) to `CORS_ORIGINS` with `fly secrets set`. Never set `DEMO_MODE` on the deployed server.

The image has not been built end to end or deployed yet: no hosting account or live credentials were available.

## Deployment and verification limits

Network-free route tests inject a repository and grader to exercise authentication failures, owner isolation, private URL generation, upload validation, grading success/failure, ungradable exclusion, calculations and explicit demo restrictions. `test_pipeline_integration.py` additionally uploads the same synthetic WAV twice through the production background grading path, replacing only external provider calls with canned responses. Real audio decoding, acoustics, feature extraction, scoring and persistence run unchanged; identical reports flow to full cards and ordered Me history. This is synthetic fixture validation, not a real speech or proof of deployed Supabase policies, provider availability or grading latency. Real recording and the plan's timing/quality calibration gate require configured live services and team recordings.

The current worker uses FastAPI background tasks on one machine, matching the plan. Keep `DATA_DIR` on persistent disk for grader cache repeatability. A machine restart can interrupt an in-flight task; queued/grading rows must be retried operationally. Durable job leasing and automatic restart recovery are future deployment hardening. Run one server worker so local cache writes share one process; do not scale horizontally without shared durable caches/jobs.
