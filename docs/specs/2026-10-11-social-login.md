# Google and GitHub accounts

The approved login design uses Continue with Google and Continue with GitHub.
Supabase handles the provider redirect and stores the same account session already
used by FastAPI. The login page matches the arena's existing gold and velvet style.
Email signup and reset controls are removed because SMTP is unavailable.

The page discovers enabled providers from Supabase's public Auth settings. Disabled
providers stay disabled with a setup message. A failed or cancelled callback leaves
the user on the login page with a retry message. Successful callbacks return to the
requested known app page, saved in sessionStorage before leaving for the provider.
The callback URL itself stays exactly login.html, matching the existing allow list.

OAuth client IDs and secrets belong in the Supabase provider configuration, never
in browser configuration or Git. Google and GitHub OAuth apps must be registered
under the user's accounts with the project's Supabase callback URL. No SMTP,
database migration, or new API token format is needed.

Provider setup and deployment remain incomplete until the OAuth apps are registered
and production deployment is approved. Anonymous sign-in stays enabled on the old
deployment until the account version is deployed.
