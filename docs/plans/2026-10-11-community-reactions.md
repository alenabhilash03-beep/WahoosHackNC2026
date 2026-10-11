# Community speech reactions: implementation plan

Status: phases 1, 2 and 4 (schema, backend, review, rotation and cleanup) are implemented and tested against real Postgres, Storage and Auth; see the [backend guide](../implementation/community-backend.md). Phase 3 (frontend) was already implemented behind the feature flag; see [frontend implementation and API contract](../implementation/community-frontend.md). Still pending from phase 5: the deployed two-account browser pass, automatic content review, an external scheduler and a storage budget. The flag stays off until the operator turns it on.
Date: 2026-10-11
Repository: alenabhilash03-beep/WahoosHackNC2026
Code baseline reviewed: `a0b198c31533eca714f07f024147309cb28f4aac` on `main`.

## Goal and first-release behavior

Let signed-in users listen to voluntarily shared speeches for the current community prompt and react with positive emoji. Show two rankings over the same set of eligible shared speeches:

- **Highest graded:** AI overall score descending.
- **Community favorites:** number of distinct reacting accounts descending.
- Reactions: Love (heart), Like (thumbs up), Fire, Amazing (star).
- Each account has one reaction per speech. Changing the emoji changes its category, not its voting weight. Clicking the selected emoji removes it.
- Every reaction contributes one vote; users cannot react to their own speech.
- One shared speech per speaker per round. Once an entry is created, its speech cannot be replaced in that round; withdrawal and re-sharing of the same speech are allowed.
- A speaker explicitly chooses to share after grading. Choosing a display name does not grant permission to share audio.
- Community recordings expire shortly after the round ends; grades and private progress remain available.

Assumption: “refresh” means rotation of the community prompt, not browser reload or the leaderboard's 30-second refresh. Use one daily round, midnight to midnight UTC, matching the existing featured-prompt convention. Show the closing time in the viewer's local time. Round start/end timestamps are authoritative, so expired content closes even if a scheduled job is delayed.

This plan implements the requested lightweight reactions feature. The archived head-to-head judging plan, persuasion scores, Elo, voice masking, comments, and notifications are outside this release. Its `judgments` and `arena_scores` tables are not needed for reactions.

## Existing foundation and integration boundaries

- `web/js/auth.js` and `web/js/api.js`: account sessions and authenticated requests already exist; anonymous sessions are rejected.
- `server/app/main.py`: FastAPI routes, uploads, background grading, and owner-only speech cards.
- `server/app/repository.py`: server-side Supabase operations, private audio bucket, and five-minute signed playback URLs.
- `server/app/leaderboard.py`: existing global best-score-per-speaker board. Preserve this behavior; add a separate prompt-scoped community section.
- `web/js/leaderboard.js`: existing refresh every 30 seconds. Reuse polling for community counts.
- `supabase/migrations/0001_init.sql`: speeches already have `published` and `moderation_status`; all application tables use RLS with no browser access policies. Writes go through the service-role backend.
- `supabase/migrations/0002_prompts.sql`: `motions.featured_on` exists, but an operational daily rotation and reaction feature still need implementation.
- Existing recordings have no automatic retention cleanup in the reviewed upload flow.

Keep owner-only speech/report endpoints private. Add a narrow community playback endpoint; do not relax their ownership checks. Use existing audio objects rather than duplicating recordings.

## Phase overview

Implement in order. Each phase has an independently checkable completion gate; phases 1-4 can be tested locally without publishing the feature to users. Public availability requires phase 5 and a working cleanup schedule.

| Phase | Deliverable | Estimated focused time | Dependency |
| --- | --- | --- | --- |
| 1 | Round, entry, reaction, and expiry schema | 1-2 hours | Existing solo upload/grading works |
| 2 | Sharing, playback, reaction, and ranking APIs | 2-3 hours | Phase 1 |
| 3 | Sharing controls and community leaderboard UI | 2-3 hours | Phase 2 |
| 4 | Daily rotation and recording cleanup | 1-2 hours | Phases 1-2 |
| 5 | Integration checks and deployment | 2-4 hours | Phases 1-4 |
| **Total** | **Tested first release** | **8-14 hours** | |

Estimate assumes an experienced developer, working Supabase/OAuth/deployment access, and reuse of the current stack. A basic happy-path demo can take 3-5 hours, but it does not satisfy the full release gates. Allow additional time for unfamiliarity, provider setup, or existing infrastructure issues.

## Phase 1: Data model and round eligibility

Files: new `supabase/migrations/0005_community_reactions.sql` (use the next available number if another migration lands first); new `server/tests/test_community_db.py` for real database constraints where existing mocks cannot verify concurrency.

- [ ] Create `community_rounds`: UUID `id`, `motion_id`, `starts_at`, `ends_at`, `created_at`. Require start before end and prevent overlapping round windows, including concurrent creation. Use an exclusion constraint or an equivalent database-enforced rule.
- [ ] Add nullable `community_round_id` and `audio_expires_at` to `speeches`; make `audio_path` nullable and add `audio_deleted_at`. Existing speeches remain untagged and unchanged.
- [ ] Create `community_entries`: UUID `id`, `round_id`, `speech_id`, `player_id`, `shared_at`, nullable `withdrawn_at`. Require unique `(round_id, player_id)` and unique `speech_id`. Enforce that entry owner and round match the referenced speech, using composite foreign keys or a database trigger.
- [ ] Create `speech_reactions`: `speech_id`, `user_id`, `reaction`, `created_at`, `updated_at`. Primary key `(speech_id, user_id)`; allow only `love`, `like`, `fire`, `amazing`. Reference the entry's unique speech ID; cascade reaction deletion if the entry is deleted.
- [ ] Add indexes for round entries, reaction aggregation by speech, and outstanding audio expiry. Enable RLS on every new table with no direct browser policies, matching the current backend architecture.
- [ ] Add a server-only SQL function/RPC for paginated community rankings. Filter the active round and eligible entries before aggregation; return totals, each emoji count, and the requesting user's reaction. Do not send every reaction or full report to Python.

**Done when:** the migration applies to a test project; duplicate entries/reactions and invalid emoji fail at the database layer; empty reaction sets produce zero counts; existing speech/progress/global-leaderboard reads still work with nullable audio paths.

## Phase 2: Backend sharing, listening, and reactions

Files: new `server/app/community.py`; extend `server/app/main.py`, `server/app/repository.py`, `server/app/settings.py`; new `server/tests/test_community.py`; extend `server/tests/test_repository.py` and API fixtures as needed.

### API contract

All routes require the existing account bearer token. Derive the caller from that token; ignore client-supplied owner IDs, scores, totals, timestamps, and expiry values. Use POST for mutations to fit existing CORS configuration.

| Route | Request | Result |
| --- | --- | --- |
| `GET /api/community/round` | None | Current round, prompt, opening/closing times, and caller's entry; `round: null` if none exists |
| `POST /api/speeches` | Existing upload fields plus optional `community_round_id` form field | Existing speech ID response; server validates round and sets retention metadata |
| `POST /api/community/entries` | `{speech_id}` | Share an eligible owned speech; retrying the same share is idempotent |
| `POST /api/community/entries/{entry_id}/visibility` | `{shared: false}` or `{shared: true}` | Withdraw or re-share the same entry |
| `GET /api/community/leaderboard` | `sort=grade\|favorites`, bounded `limit`, `offset`, optional pinned `round_id` | Round metadata, paginated entries, reaction breakdown, `my_reaction`, `is_owner` |
| `GET /api/community/speeches/{speech_id}/audio` | None | Short-lived signed audio URL and expiry |
| `POST /api/community/speeches/{speech_id}/reaction` | `{reaction: "fire"}` or `{reaction: null}` | Authoritative updated counts and caller's selection |

- [ ] Community upload requires an active round whose motion matches the upload. The server records the round ID and sets `audio_expires_at = round.ends_at + 5 minutes`. Non-community practice uploads follow their current flow.
- [ ] Only a completed, gradable speech recorded for that round can be shared. Require an available recording, a valid non-default display name, and an explicit share request from its owner.
- [ ] Existing `moderation_status` defaults to `pending`: implement an actual approval/rejection path before public playback. Reuse an existing suitable content-safety result only after verifying its fields and policy; otherwise add an account-authorized server CLI for manual review in the first release. A good grade alone is not approval. Pending/rejected entries stay off public rankings and playback.
- [ ] Use `community_entries` as the round publication record; synchronize `speeches.published` for compatibility. Both rankings and playback require an active round, shared entry, approved moderation, completed grade, and non-expired audio.
- [ ] Implement same-reaction upsert, emoji replacement, and removal as database operations. Never increment totals from client requests. Serialize entry visibility checks and reaction mutations in a transaction/RPC to prevent races with withdrawal or round closure.
- [ ] Reject self-reactions with 403, invalid emoji with 422, a different already-shared speech with 409, and closed-round writes with 409. Return 404 for audio that is not eligible to be shared. Apply a server/database-backed per-account mutation limit (initially 60 reaction requests/minute, including removals), returning 429 with retry guidance.
- [ ] Grade sort: overall descending, shared time ascending, entry ID ascending. Favorites sort: total reactions descending, overall descending, shared time ascending, entry ID ascending. Use identical visibility filters for both.
- [ ] Return only entry/speech IDs, display name, side, grade/letter, counts, and display metadata. Sign audio on play, not for every board row. Do not expose full reports, private transcripts, voter identities, or storage paths.
- [ ] Bound signed URL lifetime to the shorter of five minutes and remaining round time. Withdrawal stops new playback links immediately; previously issued links can remain usable until their expiry.
- [ ] Guard the new community routes in local/demo modes with a clear unavailable response, or add explicit sample fixtures. Preserve existing local solo behavior and loopback restrictions.

**Done when:** two test accounts can share an approved speech and react; repeat/concurrent requests from one account still count once; changing emoji preserves the total; removal decreases it; private/unapproved/withdrawn/expired speech audio is inaccessible through community routes; round reuse does not include earlier submissions.

## Phase 3: Frontend sharing and leaderboard

Frontend behavior is implemented and covered by model and two-session mocked browser tests. The live **Done when** gate below remains pending on phases 1, 2 and 4; this is not a production-ready community release.

Files: extend `web/practice.html`, `web/js/practice.js`, `web/card.html`, `web/js/card.js`, `web/leaderboard.html`, `web/js/leaderboard.js`, and relevant CSS; new `web/js/community-model.js`, `web/test/community.test.mjs`; extend `web/test/api.test.mjs` as needed.

- [x] Offer an explicit “Current community prompt” practice action. Show prompt, countdown to closing, and the retention notice before recording: “This round's recording will be deleted shortly after the prompt changes. Your grade stays saved.” Include the round ID in that upload.
- [x] On an eligible completed card, offer “Share with the community,” a display-name setup link if needed, and a separate withdrawal control. Show pending-review/rejected states; do not automatically publish when grading finishes.
- [x] Update the existing promise that other users never see recordings to explain opt-in sharing accurately. Regular private practice remains private.
- [x] Add a community section with “Highest graded” and “Community favorites” tabs, prompt text, closing time, rank, speaker, side, grade, and play button. Keep the existing global board available separately.
- [x] Render four accessible emoji buttons with labels/counts, selected state, and a disabled owner state. Clicking another selects it; clicking the selected emoji sends `null`.
- [x] Disable controls while a mutation is pending, then render the server result. On failure restore the previous state and show a retry message. Prevent stale polling responses from overwriting a newer reaction response.
- [x] Fetch a signed URL only on play and use `preload="none"`. Preserve the active audio element during count refreshes; do not restart playback or autoplay a speech. Handle expired links by requesting a fresh eligible link.
- [x] Poll every 30 seconds only while the page is visible. On a new round, clear the old entries and selection and stop old playback. Show explicit empty-board, no-current-round, closed-round, and network-error states.
- [x] Mark expired owner recordings as unavailable while leaving grades/progress usable. Support keyboard controls and a narrow mobile viewport.

**Done when:** two browser sessions complete recording, sharing, review approval, listening, reaction change/removal, and withdrawal; both tabs agree on counts; polling does not interrupt playback; reload preserves reactions; prompt rotation clears the old community board.

## Phase 4: Prompt rotation and reliable audio cleanup

Files: new `server/scripts/manage_community_rounds.py`, new `server/scripts/cleanup_community_audio.py`, new `server/tests/test_community_cleanup.py`; document scheduler configuration in `server/README.md`.

- [ ] Implement an idempotent CLI to create today's and tomorrow's rounds from explicitly selected motion IDs. Start with scheduled, curated prompts; arbitrary generated practice questions do not each create a community round. Pre-seed several days so rotation works without an interactive operator.
- [ ] Use round windows as the source of truth, and keep `motions.featured_on` aligned for the current daily prompt UI. Reusing the same motion in a later round must get a new round ID.
- [ ] Implement cleanup with `--dry-run`, bounded batches, and safe retry behavior. Select only speeches explicitly tagged for community retention whose `audio_expires_at` has passed. Leave historical and ordinary private-practice recordings untouched.
- [ ] Delete files through `db.remove_audio()`/the Supabase Storage API. After confirmed removal (or confirmed absence), clear `audio_path`, set `audio_deleted_at`, and clear `published`. Keep speech IDs, reports, overall grades, and progress history intact.
- [ ] If storage deletion fails, retain the path for retry and record a useful failure count. If deletion succeeds but the database update fails, the next run must tolerate the missing object and complete the update. Do not delete grading temporary files; ensure later grading completion cannot restore an expired audio path or re-publish a speech.
- [ ] Delete expired reaction rows and entries after audio cleanup completes; keep small round metadata. Public eligibility expires at round close even while cleanup is retrying.
- [ ] Schedule rotation daily and cleanup every 5-15 minutes using the deployment's scheduler or Supabase Cron invoking a protected backend/Edge Function. Keep credentials server-side and authenticate any HTTP job entrypoint. Do not rely on an open browser tab or a single in-process timer.
- [ ] Record last successful cleanup time, failed removals, expired files pending deletion, and bytes pending cleanup without logging credentials or signed URLs. Alert operationally if cleanup repeatedly fails; disable new community uploads if storage headroom becomes unsafe.

**Done when:** a short test round closes automatically; its audio is removed, its board disappears, and owner grades remain readable; a storage outage retries successfully; a second cleanup run is harmless; reused prompts have independent rankings; unrelated recordings remain available.

## Phase 5: Verification and release

Files: new `tools/checks/check_community_browser.cjs` following existing browser checks; update `tools/checks/package.json`, `server/README.md`, `docs/implementation/README.md`, and relevant user-facing copy during implementation.

- [ ] Use real test-project database checks for unique constraints and concurrent writes; mocked Python query traces alone cannot prove vote deduplication or transaction behavior.
- [ ] Run focused backend and frontend checks, then the repository's documented required pre-merge checks. Add the new browser acceptance to `tools/checks/package.json` and run it against two authenticated mocked sessions; perform a real deployed two-account smoke check afterward.
- [ ] Test unowned IDs, anonymous accounts, pending/rejected moderation, self-reactions, duplicate clicks, simultaneous emoji changes, removal, withdrawal/re-sharing, round boundaries, repeated prompt text, empty favorites, equal-score ordering, and pagination.
- [ ] Test that private reports/transcripts never appear in community responses; confirm the existing global board and owner progress endpoints retain their behavior.
- [ ] Apply the reviewed migration, deploy the backend, enable/verify the scheduler, seed rounds, and verify cleanup before exposing frontend controls. Gate with a server setting such as `COMMUNITY_ENABLED=false` until checks pass.
- [ ] Measure real 60-second recording sizes in supported browsers and sample playback usage. Prefer compressed audio; request approximately 64-128 kbps when supported, while preserving MIME compatibility with the current grader. Validate actual size rather than assuming the browser honors a requested bitrate.
- [ ] Set an initial community recording cap (for example 2 MB after measuring samples), a per-account round-attempt cap (for example 5), and a project budget for active community audio (for example 250 MB, adjusted for actual remaining storage). Enforce these server-side with reservation/concurrency protection so parallel uploads cannot bypass them; release failed-upload reservations.
- [ ] Rollback: disable community routes/controls and uploads while leaving cleanup scheduled and existing solo functionality available. Additive tables can remain; avoid deleting existing practice data.

**Release gate:** the complete two-account flow passes on the deployed app; no duplicate votes are possible through concurrent requests; no private audio is exposed; daily rotation and actual storage deletion have been observed; storage and playback usage are within the project's remaining quota.

## Capacity and cost assumptions

As checked on 2026-10-11, Supabase Free includes 500 MB of database size, 1 GB of file storage, 5 GB of egress, and a separate 5 GB of cached egress. These are plan allowances, not this project's verified remaining capacity.

| Item | Planning estimate |
| --- | --- |
| 10,000 reaction rows with indexes | Roughly 2-10 MB of database space; measure after implementation |
| One 60-second recording at 64-128 kbps | Roughly 0.5-1 MB, plus small container overhead |
| 100 retained compressed recordings | Roughly 50-100 MB |
| 500 retained compressed recordings | Roughly 250-500 MB |
| 1,000 complete listens to a 1 MB recording | Roughly 1 GB of transfer, before retries/other traffic |

Reactions and metadata consume database space; recordings consume file storage. Signing a URL or hiding a row does not delete the file. Deleting files frees storage but does not refund already-used transfer. Cached and uncached egress are separate allowances; do not assume they form a universally interchangeable 10 GB pool.

The current browser recorder uses defaults, and uploads can currently be as large as 15 MB. The estimates above apply to compressed recordings after measuring/enforcing community limits. Expiring community audio bounds only that feature's retention; existing private practice uploads still contribute to project usage. Inspect the Supabase usage dashboard before choosing production caps. A small daily community should fit the Free tier if sufficient headroom remains; no paid plan is assumed in this implementation estimate.

## References

- [Supabase pricing](https://supabase.com/pricing)
- [Delete Storage objects through the API](https://supabase.com/docs/guides/storage/management/delete-objects)
- [Private Storage and signed URLs](https://supabase.com/docs/guides/storage/serving/downloads)
- [Supabase Cron](https://supabase.com/docs/guides/cron)
- [Existing implementation guide](../implementation/README.md)
- [Archived community judging plan](../implementation/archive/phase-7-community-judging.md)

The existing implementation guide has a stage-2 readiness gate. During feature implementation, verify that gate against current code; this document does not assert it has passed. This plan is the scoped specification for simple positive reactions, while the archived judging system remains optional future work.
