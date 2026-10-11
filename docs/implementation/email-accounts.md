# Email accounts

Debate Arena uses Supabase email/password accounts. The browser shares one Supabase
client between the account controls and API requests. FastAPI verifies each bearer
token with `auth.get_user` and rejects anonymous users before loading private data.

```text
Sign up -> confirmation email -> login.html -> practice
Sign in -> saved session -> bearer token -> FastAPI -> owner's speeches
Forgot password -> reset email -> login.html?mode=recovery -> new password
No session / anonymous / expired token -> login.html -> original app page
Sign out -> local browser session cleared -> arena
```

## Project configuration

1. In Supabase Authentication -> Sign In / Providers, enable Email and new-user
   signups, keep Confirm email enabled, and disable anonymous sign-ins.
2. Under Authentication -> URL Configuration, set Site URL to the deployed app's
   homepage. Add both `login.html` and `login.html?mode=recovery` for each frontend:

   ```text
   http://localhost:8000/login.html
   http://localhost:8000/login.html?mode=recovery
   http://127.0.0.1:8000/login.html
   http://127.0.0.1:8000/login.html?mode=recovery
   http://localhost:5173/login.html
   http://localhost:5173/login.html?mode=recovery
   https://alenabhilash03-beep.github.io/WahoosHackNC2026/login.html
   https://alenabhilash03-beep.github.io/WahoosHackNC2026/login.html?mode=recovery
   ```

   Also add the equivalent two URLs for your deployed API host if it serves the
   frontend. The callback is relative to the app directory, so GitHub Pages works
   without routing rewrites or a separate auth callback endpoint.
3. Configure custom SMTP under Authentication email settings: host, port, username,
   password, sender email, and sender name. Use a verified sender domain. The default
   Supabase mail service is limited to project-team addresses and two messages an
   hour. Keep the default confirmation/reset templates using `{{ .ConfirmationURL }}`
   for this browser-only implicit flow.
4. For a fresh database, apply `supabase/migrations/0001_init.sql` through
   `0004_generated_prompts.sql` in order. Existing projects that have all four applied
   need no new migration for accounts. Tables remain protected by RLS without client
   policies; the server enforces ownership and generates private audio URLs.

References: [password auth](https://supabase.com/docs/guides/auth/passwords),
[redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls),
[SMTP](https://supabase.com/docs/guides/auth/auth-smtp).

## Run or deploy

Set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` in
`server/.env` or your hosting secrets. The public-key variable accepts a
`sb_publishable_` key; the server-key variable accepts a `sb_secret_` key. Never put
the server key in `web/config.js`. Leave `DEMO_MODE` and `LOCAL_MODE` false for accounts.

FastAPI serves the UI and generates `/config.js` from those environment values.
For a separate static host, put the public project values and the deployed API URL
in `web/config.js`, then add the frontend's exact origin to server `CORS_ORIGINS`.
GitHub Pages hosts only the frontend; it still needs a running Python API.

Local account-mode server, from the repository root:

```sh
python -m pip install -r server/requirements.txt -r softskills-grader/requirements.txt
python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000/login.html`. Local/demo modes still work without accounts
on loopback and hide account controls. Signing out clears the session in this browser;
it does not sign the user out of other devices.

Old anonymous progress stays with its original guest ID. Creating a new account
does not merge that progress. No existing accounts or recordings are deleted.

## Verify

```sh
node --test web/test/*.test.mjs
python -m unittest discover -s server/tests
cd tools/checks
npm install
npx playwright install chromium
npm run test:auth
```

The auth browser test uses an isolated loopback server and mocked provider responses;
it sends no email and needs no credentials. It checks signup confirmation, invalid
login, recovery, sign-out, anonymous rejection, safe return URLs, and mobile layout.
The older `check_supabase.py` checks the archived anonymous/realtime flow and is not
an acceptance test for email accounts.

For live acceptance, create and confirm an account, record a speech, and sign into
the same account in another browser. Verify the same progress appears, and that a
different account cannot retrieve the first account's speech card. Verify reset
emails arrive, the reset link lets you save a new password, and the old password no
longer signs in. Invalid/expired links must offer another reset request.
