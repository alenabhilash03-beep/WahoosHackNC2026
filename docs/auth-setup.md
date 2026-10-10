# Account setup

The static frontend now supports email/password registration, email confirmation,
login, session restoration/refresh, and logout through Supabase Auth. ARGUE prompts
signed-out visitors to log in and retains the existing scene animation for signed-in
players. Recording and posting are still future features. `progress.html` still uses
example data and is not an authenticated API.

## Supabase

1. Create a Supabase project. Enable Email in Authentication → Providers, allow
   new signups, and keep email confirmation enabled. Set a minimum password length
   of at least 8. Configure production SMTP before relying on public signup emails.
2. In Authentication → URL Configuration, set Site URL to the deployed frontend.
   Add exact redirect URLs for `http://localhost:5173/index.html` and the deployment,
   e.g. `https://alenabhilash03-beep.github.io/WahoosHackNC2026/index.html`.
   The app resolves this relative to its directory, preserving GitHub Pages subpaths.
3. Apply migrations 0001, 0002, then 0003 using the SQL editor or your Supabase
   migration workflow. 0003 creates profiles for new and existing auth users.
   It does not grant browser access to profiles, recordings, or other tables.
4. Copy the Project URL and **publishable** key from the project Connect/API Keys
   screen. Secret/service-role keys must stay exclusively on a backend.

## Local configuration (Node 22+)

From the repository root:

```powershell
Copy-Item .env.example .env
# Edit .env with your project URL and publishable key.
node tools/configure-web.mjs
python -m http.server 5173 --directory web
```

Open http://localhost:5173. The generator emits only the two explicitly selected
public values to `web/config.js`. Both `.env` and generated config are ignored by
Git. Never serve the repository root: serve `web/` so local environment files stay
outside the document root. Browser configuration is necessarily public; no server
secrets belong in it. Deployment must run the same generator with those environment
variables and upload the resulting `web/` directory including `config.js`.
GitHub Pages needs a build/deploy workflow or another publishing step that generates
this file; simply publishing the branch without it leaves accounts unavailable.

The SDK is pinned to Supabase JS 2.49.8 via esm.sh. The Three.js and font CDN behavior
is unchanged. Without configuration or with an unreachable SDK CDN, the account
dialog displays an unavailable message and the scene remains usable.

## Verification

```powershell
node --test web/test/*.test.mjs
```

Automated tests mock Auth; they do not certify a real project or migration execution.
Before deployment, check these against your configured project:

- Sign up with a fresh email; see the confirmation notice. Check `auth.users` and
  the matching `public.profiles` row in the dashboard.
- Follow the confirmation link; verify the welcome name and ARGUE animation.
- Refresh and verify the session persists. Log out; ARGUE must show the login dialog.
- Log in again; test an incorrect password, duplicate email, and network failure.
- Test a confirmation link that has expired, keyboard navigation, and a mobile screen.

The `arena:auth` browser event reports the current user to future frontend features.
It is not authorization. Future FastAPI endpoints must validate bearer tokens with
Supabase (e.g. `get_user`) and derive ownership from the verified user ID. Never
trust a browser-supplied user ID or user metadata for permission decisions.

Official references: [Password Auth](https://supabase.com/docs/guides/auth/passwords),
[API keys](https://supabase.com/docs/guides/getting-started/api-keys).
