# Backend integration fixes and deployment

Reviewed after PR #26 and PR #27 against main commit `42f3ccb`. GitHub Auth is already implemented;
this change reuses `web/js/auth.js`, `web/js/login.js`, `web/js/api.js`, and
`SupabaseRepository.resolve_token()`. It keeps the same Supabase project, private bucket and
existing account IDs, and adds no table or migration.

## Findings and fixes

- The checked-in static `web/config.js` had an empty API URL, so the GitHub Pages copy sent its API
  requests to GitHub Pages. It now points to `https://speak-up.fly.dev`. FastAPI still answers
  `/config.js` itself with a same-origin API URL when it serves the site.
- The deployed server answered the Pages-origin preflight with 405 and no allowed-origin header.
  `fly.toml` now sets the exact Pages origin in `CORS_ORIGINS`. A Fly secret with the same name
  takes precedence over `fly.toml`, so set it explicitly when deploying (below).
- Every backend auth exception was treated as an expired session, so an Auth outage signed players
  out. Now only a refusal from Auth (a 4xx other than 429, or an anonymous user) is
  `InvalidSession` and returns 401; rate limits, 5xx and network failures return a generic 503.
  The frontend refresh path does the same: an outage while refreshing keeps the session and
  reports "temporarily unavailable" instead of redirecting to login.
- Generated-question insertion reuses the existing motion after a concurrent unique-text conflict
  (Postgres `23505`), the same handling display names already had.
- Failed speeches can be retried through the authenticated, owner-only
  `POST /api/speeches/{id}/retry`. One conditional `UPDATE` claims the row only if it is the
  caller's and still `failed`; the background task downloads the private recording and runs the
  existing grader with its cache. Speech ID, timestamp, recording and history stay intact. Cards
  expose `can_retry` and show **RETRY SAVED RECORDING**.
- `server/scripts/recover_grading.py` is a dry-run/manual recovery for gradings interrupted by a
  restart. It marks `queued`/`grading` rows `failed` so their owners can retry. It never grades
  anything itself and never changes a finished report.
- Repository initialization is synchronized, and `GET /api/health` reports process liveness. Fly
  checks this route. It does not claim the database or the providers are ready.

## Actions for the existing deployed app

Merge this change first. Keep the existing Supabase Auth provider settings and the existing
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `ELEVENLABS_API_KEY` and
`GEMINI_API_KEY` (or `GEMINI_API_KEYS`) secrets. Do not create another project or make the
application tables directly readable from the browser.

From the repository root:

```sh
fly secrets list --app speak-up
fly secrets set --app speak-up CORS_ORIGINS=https://alenabhilash03-beep.github.io LOCAL_MODE=false DEMO_MODE=false
fly deploy --app speak-up
```

The first command shows names only. If a required secret is absent, set its value privately
through Fly; never paste server keys into chat or `web/config.js`. `fly deploy` rebuilds the
Python code. GitHub Pages publishes `web/` from `main` on merge.

Sign-in from the Pages copy also needs its callback,
`https://alenabhilash03-beep.github.io/WahoosHackNC2026/web/login.html`, in the Supabase Auth
redirect allow list (see `social-accounts.md`). Without it, Auth returns the player to the site URL.

Apply only pending migrations; this change adds none beyond PR #26's 0005. Do not replay
0001 to 0004 or reset the hosted database. Existing duplicate display names must be resolved
before 0005 can apply. See `supabase/README.md`.

Check the deployed Pages preflight in PowerShell:

```powershell
$response = Invoke-WebRequest -Method Options -Uri 'https://speak-up.fly.dev/api/me' -Headers @{Origin='https://alenabhilash03-beep.github.io';'Access-Control-Request-Method'='GET';'Access-Control-Request-Headers'='authorization'}
$response.StatusCode
$response.Headers['Access-Control-Allow-Origin']
```

Expected: `200` and `https://alenabhilash03-beep.github.io`. Then open the app in a browser, sign
in with GitHub, record a speech, check its card, audio and progress, sign out and in again, and
confirm the progress is still there. With a different account, opening that speech's card must
return 404. If a speech fails grading, its card offers **RETRY SAVED RECORDING**.

## Optional recovery of already-stuck grading

Stop the server first (it is the only grading worker), and put the project URL and server key in
the git-ignored `server/.env`. From the repository root:

```sh
python -m server.scripts.recover_grading
python -m server.scripts.recover_grading --apply --worker-stopped
```

The first command only counts pending rows. The second marks them failed without deleting audio
or reports. Restart the server; owners can then retry on their cards. Do not run the apply command
beside a running server: it cannot tell a grading in progress from an interrupted one. This is
controlled recovery, not a durable distributed queue.

## Tests

`server/tests/test_supabase_integration.py` uses the real SDK against a disposable local stack:
account-token verification, PostgREST persistence, private Storage links, ownership, name
conflicts, the concurrent retry claim, concurrent identical questions and recovery. Only provider
grading is injected. It creates two temporary accounts and removes them with their audio and
speeches, refuses any non-loopback URL, and runs in the disposable-Supabase CI job.

To run it locally with Docker and the Supabase CLI:

```powershell
supabase start
python -m pip install -r server/requirements-dev.txt -r softskills-grader/requirements.txt
$local = supabase status -o json | ConvertFrom-Json
$env:SUPABASE_URL = $local.API_URL
$env:SUPABASE_ANON_KEY = $local.ANON_KEY
$env:SUPABASE_SERVICE_ROLE_KEY = $local.SERVICE_ROLE_KEY
$env:SUPABASE_INTEGRATION_TEST = 'true'
python -m pytest server/tests/test_supabase_integration.py -q
Remove-Item Env:SUPABASE_INTEGRATION_TEST
```

The regular suite (`server/tests/test_api.py`, `test_retry.py`, `test_repository.py`,
`web/test/api.test.mjs`) covers the same failure paths without live services, and
`tools/checks/check_auth_browser.cjs` drives the retry button in a real browser with mocked
accounts and API responses. That script turns off cross-page view transitions in its own static
server only, because it navigates faster than a person can.
