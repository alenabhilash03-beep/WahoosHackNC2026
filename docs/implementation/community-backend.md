# Community reactions backend (plan phases 1, 2 and 4)

Implements the schema, API, review path, rotation and cleanup of the
[community reactions plan](../plans/2026-10-11-community-reactions.md), to the response contract
the [frontend](community-frontend.md) already uses. It is off unless the server sets
`COMMUNITY_ENABLED=true`.

## What it does

- One **round** is open at a time: a prompt for one UTC day. The round's window decides what is
  open; no job has to run for a round to close.
- A speaker records the round's prompt (`community_round_id` on the normal upload), then
  **explicitly shares** that speech. One shared speech per speaker per round; it can be withdrawn
  and shared again, never swapped for another.
- A shared speech is hidden until **review** approves it.
- Listeners with an account play it through a short private link and leave **one reaction**:
  love, like, fire or amazing. Choosing another changes it; choosing the same one removes it.
  Nobody can react to their own speech.
- Two rankings over the same speeches: **highest graded** and **community favorites** (number of
  accounts that reacted).
- Recordings of a round are **deleted** five minutes after it ends. Grades, reports and progress stay.

The three difficulty leaderboards (`GET /api/leaderboard`) are separate and unchanged. A community
prompt usually has no difficulty, so its speeches are not ranked there.

## Where the rules live

`supabase/migrations/0006_community_reactions.sql` adds `community_rounds`, `community_entries`,
`speech_reactions` and `community_reaction_limits`, and three nullable columns on `speeches`
(`community_round_id`, `audio_expires_at`, `audio_deleted_at`; `audio_path` becomes nullable).
Existing speeches are untouched.

| Rule | Enforced by |
| --- | --- |
| Two rounds never overlap, even if created at once | Exclusion constraint on the round window |
| One entry per speaker per round; a speech is entered once | Unique constraints |
| An entry is its own speaker's speech from its own round | Composite foreign key to `speeches` |
| One reaction per account per speech; only the four emoji | Primary key and check constraint |
| What the community may see, hear and react to | `community_eligible_entries()`, the single definition used by the board, playback and reactions: round open, entry shared, review approved, graded, recording present |
| A reaction cannot race a withdrawal, a round closing or a second click | `community_react()` locks the entry row, checks, writes and counts in one transaction |
| 60 reaction requests per account per minute | Counted in `community_reaction_limits` inside the same transaction |
| The browser cannot reach any of it | RLS with no policies on the tables; `EXECUTE` on the functions revoked from `public`, `anon` and `authenticated` |

The functions take the caller's id as an argument, which is why only the server (service role) may
call them. The server always passes the id it resolved from the bearer token.

## API

All routes need the account bearer token. With community off, or in local/demo mode, they
return 503. Ids that are not UUIDs are 404.

| Route | Result |
| --- | --- |
| `GET /api/community/round` | `{round, entry, display_name}`; `round` is null when none is open |
| `POST /api/speeches` + `community_round_id` | Tags the speech and sets its audio expiry. 404 unknown round, 409 closed, 422 other prompt, 413 over 4 MB, 429 after 5 recordings in the round |
| `POST /api/community/entries` `{speech_id}` | 201 `{entry}`; 200 when repeated. 404 not yours, 409 private practice, closed round, another speech already shared, not graded, recording expired, rejected, or no display name |
| `POST /api/community/entries/{id}/visibility` `{shared}` | `{entry}`. Withdrawing always works; sharing again needs the round open |
| `GET /api/community/leaderboard?sort=grade\|favorites&limit=&offset=&round_id=` | `{round, entries, has_more}`; ranks are absolute across pages; a closed round has no entries |
| `GET /api/community/speeches/{id}/audio` | `{audio_url, expires_at}`; the link lasts five minutes or until the round ends, whichever is sooner. 404 unless eligible |
| `POST /api/community/speeches/{id}/reaction` `{reaction}` | `{counts, total_reactions, my_reaction}`. 403 own speech, 404 not eligible, 409 round closed, 422 unknown emoji, 429 with `Retry-After` |

The owner's `GET /api/speeches/{id}` also returns `community_round_id`, `moderation_status`,
`audio_expires_at` and `audio_deleted_at` (null for private practice), and `audio_url` is null once
the recording has expired. Board rows carry ids, display name, side, grade, letter and counts only.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `COMMUNITY_ENABLED` | `false` | Turns the routes on and tells the page (`/config.js`) to show the feature |
| `COMMUNITY_AUTO_APPROVE` | `false` | Approve at share time instead of waiting for review. Only for a demo among people who trust each other; it never overrides a rejection |
| `COMMUNITY_MAINTENANCE_SECONDS` | `600` | How often the running server creates missing rounds and deletes expired recordings; `0` turns that off |

The GitHub Pages copy reads the static `web/config.js`, where `COMMUNITY_ENABLED` stays `false`
until it is changed there too.

## Operating it

```sh
python -m server.scripts.manage_community_rounds --days 7 --apply   # choose rounds ahead; add --motion <id> to pick prompts
python -m server.scripts.review_community                           # list shared speeches waiting, with a private listen link
python -m server.scripts.review_community --approve <speech id>
python -m server.scripts.review_community --reject <speech id>
python -m server.scripts.cleanup_community_audio --apply            # delete expired recordings now
```

Without `--apply` the rounds and cleanup scripts only report. A day without a round gets that
day's Daily Prompt if one is set, else the next hand-written prompt (a motion with no category) in
rotation; today's round also becomes today's Daily Prompt, so `motions.featured_on` stays aligned.

While the server runs with community on, it does the same for today and tomorrow and runs the
cleanup every ten minutes. That timer only keeps prompts flowing and storage tidy: visibility is
decided by round windows and expiry times, so a late or stopped timer never exposes a closed
round. A failed file deletion keeps the path and is retried on the next pass; a second run of
anything is harmless. On a host that stops the machine, schedule the two scripts instead.

To turn the feature off again, unset `COMMUNITY_ENABLED`. The tables can stay.

## Checked

- `server/tests/test_community.py`: every route, refusal and script against an in-memory store.
- `server/tests/test_community_db.py` (real Postgres, Storage and Auth, in the database CI job):
  eight simultaneous round creations make one round; twelve simultaneous reactions from one
  account count once; changing keeps the total and removing lowers it; the rate limit; the public
  and account keys are refused by every function and table; a real signed link returns the
  recording; withdrawal, rejection and round close each hide the speech; cleanup removes the
  files from Storage, the entries and the reactions, keeps the grades, and leaves private practice
  alone; rotation is idempotent.
- `tools/database/foundation.test.mjs`: the same constraints and functions in SQL, on embedded and
  real Postgres.

## Not built

- Automatic content review. Approval is a person with the review script, or the auto-approve
  switch.
- A scheduler outside the server process, and alerts when cleanup keeps failing (it logs counts).
- A project-wide storage budget. The per-recording size cap and the per-account round cap exist.
- The plan's deployed two-account browser pass with real microphones.
