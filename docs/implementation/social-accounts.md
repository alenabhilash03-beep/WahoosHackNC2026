# Google and GitHub accounts

Speak Up uses Supabase OAuth for Google and GitHub. Users can create an account and
sign in with the same button. No SMTP, sender domain, or app password is needed.
The browser shares one Supabase client with API requests. FastAPI verifies the
Supabase bearer token and rejects anonymous users before loading private data.

## Register the OAuth apps

Both providers redirect to **Supabase**, which then redirects to the arena. Use this
exact provider callback URL:

```text
https://tzwfuwrppoqhfnznaxsu.supabase.co/auth/v1/callback
```

### Google

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select
   a project named Speak Up. Go to **Google Auth Platform** and choose **Get started**.
2. Set the app name to Speak Up and choose your own support/contact email. Choose
   **External** for the audience so people outside your organization can sign in.
3. Under **Data Access**, use only `openid`, `userinfo.email`, and `userinfo.profile`.
   No Gmail, Drive, or other account access is needed.
4. Under **Clients**, create an OAuth client with type **Web application**.
   Add this **Authorized JavaScript origin**:

   ```text
   https://speak-up.fly.dev
   ```

   Add the Supabase callback above under **Authorized redirect URIs**. Do not use
   the Fly login page as Google's provider callback.
5. Save the client ID and secret in the ignored `server/.env.oauth` file under
   `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, or directly in Supabase's Google
   provider settings. These are application credentials, not your Google password.
6. In **Audience**, publish the app for public access. While the app stays in Testing,
   add your testers to its test-user list. Keep optional branding/verification fields
   within Google's requirements; a custom domain is not required for this basic flow.

Reference: [Supabase Google setup](https://supabase.com/docs/guides/auth/social-login/auth-google).

### GitHub

1. Open [Register a new OAuth application](https://github.com/settings/applications/new).
   This is an **OAuth App**, not a repository access token or a GitHub App.
2. Enter:

   | Field | Value |
   | --- | --- |
   | Application name | Speak Up |
   | Homepage URL | `https://speak-up.fly.dev` |
   | Authorization callback URL | `https://tzwfuwrppoqhfnznaxsu.supabase.co/auth/v1/callback` |

3. Register the app and generate a client secret.
4. Save the client ID and secret in the ignored `server/.env.oauth` file under
   `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, or directly in Supabase's GitHub
   provider settings. No repository access scopes are requested by our frontend.

Reference: [Supabase GitHub setup](https://supabase.com/docs/guides/auth/social-login/auth-github).

## Supabase configuration

Open [Speak Up authentication settings](https://supabase.com/dashboard/project/tzwfuwrppoqhfnznaxsu/auth/providers).
Enable Google and GitHub and enter each provider's client ID and client secret.
Keep the secrets out of Git, browser configuration, chat, and the Fly image.

Under **URL Configuration**, set Site URL to `https://speak-up.fly.dev` and allow
the app callback:

```text
https://speak-up.fly.dev/login.html
```

For local development or a separate static deployment, allow these exact callbacks:

```text
http://localhost:8000/login.html
http://127.0.0.1:8000/login.html
http://localhost:5173/login.html
http://127.0.0.1:5173/login.html
https://alenabhilash03-beep.github.io/WahoosHackNC2026/login.html
```

The page saves a validated return path in sessionStorage before OAuth, so the callback
stays exactly `login.html` with no wildcard redirects. Supabase JS consumes the
implicit-flow session before the page redirects back to practice or progress.
Failed or cancelled callbacks show a retry message and clear provider error details.

Disable anonymous sign-ins **after** deploying the new account client. The old client
still needs anonymous sign-in until then. Allow new user signups. With social-only
accounts, Email can be disabled after checking that no existing password accounts
need it; leave email confirmation enabled if Email remains enabled. Email/password
signup, magic links, and reset emails still need SMTP if added later.

The login page reads Supabase's public Auth settings and only enables configured
Google/GitHub buttons. It does not expose OAuth secrets. Once the providers are
enabled, the page uses them without a new frontend build.

## Server and deployment

Keep `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` in
`server/.env` or Fly secrets. Only the project URL and anon/publishable key go to
the browser. FastAPI generates `/config.js` when serving the frontend. A separate
static frontend needs its deployed API URL and an exact server CORS origin.
Do not enable `LOCAL_MODE` or `DEMO_MODE` on the deployed server.

No new migration is required when migrations 0001 through 0004 are already applied.
Tables retain RLS without browser policies; the server enforces speech ownership
and creates private audio URLs. Google/GitHub users receive ordinary Supabase IDs.
Existing anonymous recordings remain owned by their guest IDs and are not merged
into new accounts.

## Verify

```sh
npm test --prefix web
python -m unittest discover -s server/tests
npm run test:auth --prefix tools/checks
```

The browser test uses mock providers and an isolated loopback server. It checks both
provider redirects, session restoration, private return paths, cancelled callbacks,
disabled providers, retries, sign-out, cross-tab sign-out, anonymous rejection,
unsafe return links, local mode, and mobile layout. It uses no live accounts.

After provider setup and deployment, test each provider in a real browser. Sign out,
then sign back in and verify your progress. Use a different account to confirm it
cannot load another user's speech card. OAuth authorization requires the user to
sign in and consent on the provider's own page; our app never sees their password.

Deployment and real provider consent remain separate from mocked acceptance.
