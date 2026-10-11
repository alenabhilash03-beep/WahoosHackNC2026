# Social login implementation

Goal: let users access their saved speeches with Google or GitHub.

Architecture: browser redirects through Supabase OAuth and reuses its existing
account session. The login page reads public provider availability; provider secrets
remain exclusively in Supabase. Stack: vanilla JavaScript, Supabase JS, FastAPI.

```text
Protected page -> login.html?next=progress.html
                    |
                    +-> GET Supabase /auth/v1/settings -> enabled buttons
                    +-> disabled / network error -> setup or retry message
                    |
                    +-> sessionStorage safe next -> signInWithOAuth
                                                     |
                                              Google / GitHub
                                                     |
                                          Supabase /auth/v1/callback
                                                     |
                                         app login.html + SDK session
                                                     |
                                            original protected page
Failed callback -> clear callback details -> retry buttons
```

- [x] Add auth unit tests for `signInWithProvider('google')` and `github`, a fixed
  callback, rejected unsupported providers, and public provider discovery. Run
  `node --test web/test/auth.test.mjs`; expect missing function failures.
- [x] Add `signInWithProvider: provider => call('signInWithOAuth',
  {provider, options: {redirectTo: callback.href}})` with a Google/GitHub allow list.
  Add `getLoginProviders` using `/auth/v1/settings` with only the publishable apikey.
  Run the same tests; expect all passing.
- [x] Replace browser acceptance email cases with OAuth redirects, stored return
  paths, cancelled callbacks, provider/network errors, disabled providers, logout,
  guest rejection, and mobile layout. Run `npm run test:auth --prefix tools/checks`;
  expect missing provider-button failure.
- [x] Replace `web/login.html` and `web/js/login.js` with social controls and callback
  handling; add provider-button styles to `web/auth.css`. Run acceptance plus
  `npm test --prefix web`; expect passing checks and no browser page errors.
- [x] Update account setup docs and prepare an ignored `server/.env.oauth` for
  provider client IDs and secrets. Check configured-provider status using the
  already approved saved Supabase credential; never print credentials.
- [ ] Run `git diff --check`, inspect the diff for secrets and ownership regressions,
  commit and push to the existing draft PR. Update PR title/body to match social
  accounts and explicitly state unconfigured providers and deployment status.
