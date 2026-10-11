# Database foundation

SpeakUp uses Supabase Postgres and a private `audio` Storage bucket. All application
tables have RLS enabled with no browser policies. FastAPI uses the server key and
checks each caller's account and speech ownership; signed audio URLs last 300 seconds.
GitHub accounts are configured separately in [the account guide](../docs/implementation/social-accounts.md).

## Migrations

Apply in numeric order; do not rewrite already-applied migrations.

| Migration | Adds |
| --- | --- |
| 0001 | Profiles, speeches, prompts and reserved conversation/community/duel tables; RLS; private audio bucket |
| 0002 | Featured date and ranked flag |
| 0003 | Solo grading state, owner history indexes, three practice prompts |
| 0004 | Generated prompt category/difficulty |
| 0005 | Case-insensitive unique display names; name validation for new writes; repair audio bucket privacy |

Unnamed profiles may all use `player`. Named profiles have 3–20 ASCII letters,
digits or underscores. The server translates concurrent unique-name conflicts to
HTTP 409. Legacy invalid names are preserved using a `NOT VALID` check constraint;
new/updated rows must meet the rule. Existing case-insensitive duplicates prevent
0005 from applying. Inspect them before an upgrade:

```sql
select lower(display_name) as name, count(*)
from public.profiles where display_name <> 'player'
group by lower(display_name) having count(*) > 1;
```

Choose replacement names with the affected players; the migration never renames
them or deletes speech data automatically. After correcting legacy invalid names,
validate the constraint with `ALTER TABLE public.profiles VALIDATE CONSTRAINT
profiles_display_name_check`.

## Local Supabase

Install Docker and [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started).
From the repository root:

```sh
supabase start
pnpm --dir tools/database install --frozen-lockfile
DATABASE_TEST_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres pnpm --dir tools/database test
```

In PowerShell set `$env:DATABASE_TEST_URL` before the test command. This URL must
point to a disposable loopback database. Most fixtures roll back; the concurrency
test creates two temporary accounts and removes them in `finally`.
`supabase start` applies migrations on first creation. For later changes run
`supabase migration up --local`. `supabase db reset --local` rebuilds and erases
only your local development data; use it only on a disposable local project.

For database checks without Docker, omit `DATABASE_TEST_URL` and run the same test
command. It applies the migrations to PGlite's embedded PostgreSQL with minimal
Auth/Storage schema fixtures. It skips only the pgcrypto extension installation
(UUID generation is built into PostgreSQL) and the multi-connection race test.
This checks SQL, RLS and persistence, but does not replace Supabase integration.
GitHub Actions starts actual Supabase and runs the race and live API checks too.

## Existing hosted project

Use the current project's normal migration workflow. For CLI-managed projects,
link the intended project, inspect `supabase migration list`, then review
`supabase db push --dry-run` before applying pending migrations with `supabase db push`.
Never reset a hosted database. If migrations were pasted into the SQL editor,
reconcile migration history before using CLI push, so old migrations are not replayed.
No hosted migration or deployment is performed by the tests or CI.

Fill the ignored `server/.env` from `server/.env.example`, then run:

```sh
python tools/checks/check_supabase.py
```

This read-only smoke check verifies current columns, active prompts, public-key
isolation and bucket privacy. Optionally supply `SUPABASE_CHECK_ACCESS_TOKEN` in
the environment using an existing GitHub account's Supabase access token to check
authenticated table isolation. It never creates users or writes records and does
not print keys, tokens or row data. SQL tests prove constraints/RLS even when the
database is empty; the smoke check alone cannot prove those on empty tables.

Live GitHub consent, microphone recording, Storage upload/playback and provider
grading still require the separate app acceptance checks. Community reactions and
round retention remain in their [separate phase plan](../docs/plans/2026-10-11-community-reactions.md).
