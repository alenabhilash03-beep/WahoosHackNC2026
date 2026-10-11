# Set up signup on the live website

The existing URL is https://alenabhilash03-beep.github.io/WahoosHackNC2026/web/index.html.
The Pages pipeline preserves `/web/index.html`, practice, cards and progress under
`/web/`, with a shortcut redirect from the repository site's root.

## Why signup did not work

At inspection, the deployed home page had no account form, and deployed
`web/config.js` had empty API/Supabase values. The account implementation was on
`feat/supabase-auth`, not `main`. No workflow generated the ignored local public
configuration and included it in the published artifact. Copying a local `.env`
does not configure GitHub Pages: it serves static files and cannot read runtime
environment variables. A Supabase project is required independently of GitHub.

## One-time owner setup

1. Create or locate the team's project at https://supabase.com/dashboard. Keep
   credentials in the dashboard or GitHub settings, never an issue or commit.
2. Under Authentication → Providers, enable Email and new-user signup, keep email
   confirmation enabled, and set a minimum password length of at least 8. Configure
   SMTP/email delivery for public users. Guest practice also needs anonymous sign-in
   enabled; email accounts themselves do not.
3. In the SQL Editor, review and apply missing migrations in order: `0001_init.sql`,
   `0002_prompts.sql`, `0003_solo_feedback.sql`, `0004_account_profiles.sql`.
   Do not rerun already-applied migrations blindly. The profile trigger is included
   in 0004. Keep table RLS and the private audio bucket enabled.
4. Authentication → URL Configuration:
   - Site URL: `https://alenabhilash03-beep.github.io/WahoosHackNC2026/web/index.html`
   - Allowed redirect: the same exact URL.
   - For local tests, also allow `http://localhost:5173/index.html`.
5. Repository Settings → Secrets and variables → Actions → **Variables**: add
   `SUPABASE_URL` (the project's HTTPS URL) and `SUPABASE_PUBLISHABLE_KEY` (the
   browser publishable key from Connect/API Keys). Both are public browser values.
   Add `API_URL` only when a working HTTPS FastAPI server is deployed. Signup works
   without API_URL; recording, grading and progress require that backend.
   Service-role, ElevenLabs and Gemini keys must never be supplied to this pipeline.
6. Repository Settings → Pages → Build and deployment → Source: **GitHub Actions**.
   This owner/admin setting cannot be set by the current connector. Configure the
   `github-pages` environment to allow deployment from `main` if it is restricted.
7. Merge the reviewed account/deployment branch into `main`. In Actions, the
   **Deploy website** workflow runs automatically, or choose Run workflow on main.
   A missing project URL or public key makes the build fail with a setup message;
   it will not publish an unconfigured replacement.

## What the pipeline does

`.github/workflows/checks.yml` runs frontend and backend tests for pull requests,
main and auth-branch pushes without any production secrets. `pages.yml` reruns
these checks, then `tools/build-pages.mjs` packages only browser assets with a
generated public `web/config.local.js`. GitHub's Pages artifact/deployment actions
publish that artifact. No repository root, server code, `.env`, tests or credential
files are uploaded. New main commits repeat this process automatically.

The pipeline deploys the frontend. It does not create a Supabase project, send test
signup email, apply database migrations, deploy the grader, or configure SMTP.
Those steps require the team's project access and are deliberately separate from
the public Pages build. Backend deployment remains in `server/README.md`; set
`CORS_ORIGINS=https://alenabhilash03-beep.github.io` on that backend. For a Fly
same-origin site, the server supplies public config automatically instead.

## Check the result

After a successful deployment, open the existing URL and hard refresh. Confirm the
LOG IN / SIGN UP button appears and `web/config.local.js` has the public project
settings. Register with a new accessible email, follow the confirmation link,
refresh, log out and log back in. In Supabase, verify the matching auth user and
profile. Never share passwords or access tokens in a GitHub issue.

If the dialog says accounts are unavailable, check that the account code and generated
config actually deployed and that the SDK CDN is reachable. If Supabase returns an
error, check Authentication logs for disabled signup, email/SMTP/rate-limit problems,
or profile-trigger/migration errors. Missing API_URL only affects speech APIs; it
does not prevent Supabase signup.

References: [GitHub Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages),
[Supabase Auth configuration](https://supabase.com/docs/guides/auth/general-configuration).
