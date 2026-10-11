# Community reactions frontend (plan phase 3)

Implemented against the [community reactions plan](../plans/2026-10-11-community-reactions.md). The schema and backend it talks to are described in the [backend guide](community-backend.md); when this page was written they did not exist yet. The database foundation in PR #26 does **not** implement community rounds or reactions. This change does not claim the live two-account completion gate has passed.

## Availability

`COMMUNITY_ENABLED` must be the boolean `true` in the public `ARENA_CONFIG` to show the community actions. It defaults to `false` in the static config, and absence in the current server's generated config also disables it. Local/demo modes always disable community access. Regular private practice, progress and the global best-score board keep working without community API requests.

Keep the flag off until plan phases 1, 2 and 4 are implemented, moderation works, cleanup has been observed and the deployed integration checks pass. The backend must independently authorize every request; the UI flag is only a release control. The future backend should expose the flag through its generated config as well as enforce its own availability setting.

## User flow

- Practice and leaderboard offer **Current community prompt**, opening `practice.html?mode=community`. Before recording, it shows the prompt, closing time/countdown, private-until-shared explanation and retention notice. Only this mode adds `community_round_id` to the existing multipart upload. Daily and ordinary practice remain private flows.
- An eligible completed card offers **Share with the community**. A missing/invalid name links to the leaderboard name form. No automatic publication occurs. The card supports pending review, rejected, shared, withdrawn and expired states. An existing entry for a different speech prevents replacing it in that round.
- The community leaderboard is separate from the global board. Highest graded and Community favorites use server rankings and pagination, not client sorting. Emoji selection/change/removal uses authoritative server counts. Owners cannot react to their own speeches.
- Playback URLs are requested on demand, with `preload="none"`; loading a recording does not autoplay it. Refreshing counts retains the audio node and current playback. Withdrawal, round closure and rotation remove old playback. A failed audio link offers a fresh-link request.
- Reads poll every 30 seconds only while visible. Mutations invalidate older reads. Failed mutations retain the prior selection and then reconcile with the server because a lost response might have committed. Keyboard tab navigation, labeled buttons, minimum 44px targets and a 390px viewport are covered.

## Backend response contract used by this frontend

All calls go through the existing bearer-token API client. The server derives identity and enforces all eligibility, timing, one-entry, self-vote and deduplication rules from phase 2.

`GET /api/community/round`:

```json
{
  "round": {
    "id": "round-uuid",
    "starts_at": "2026-10-11T00:00:00Z",
    "ends_at": "2026-10-12T00:00:00Z",
    "prompt": {"id": "motion-uuid", "text": "Should schools require uniforms?"}
  },
  "entry": {"id": "entry-uuid", "speech_id": "speech-uuid", "withdrawn_at": null},
  "display_name": "Ada"
}
```

Return `round: null` when unavailable, `entry: null` before sharing, and the caller's entry even when withdrawn, pending or rejected. Use timestamps with timezone offsets. The prompt ID is the `motion_id` for uploads. Display name is the caller's current name.

The owner-only `GET /api/speeches/{id}` keeps its existing report fields and additionally returns `community_round_id`, `moderation_status` (`pending`, `approved`, `rejected`), `audio_expires_at`, and `audio_deleted_at`; untagged speech fields are null. `status: "done"`, `report.gradable: true` and an available `audio_url` are needed before sharing. Checking sharing status refreshes these owner metadata fields as well as the round. Expired recordings stay unavailable while their grades remain readable.

`GET /api/community/leaderboard?sort=grade|favorites&limit=20&offset=0&round_id=...`:

```json
{
  "round": {"id": "round-uuid", "starts_at": "...", "ends_at": "...", "prompt": {"id": "motion-uuid", "text": "..."}},
  "entries": [{
    "id": "entry-uuid", "speech_id": "speech-uuid", "rank": 1,
    "display_name": "Ada", "side": "yes", "overall": 95.7, "letter": "A",
    "counts": {"love": 0, "like": 0, "fire": 1, "amazing": 0},
    "total_reactions": 1, "my_reaction": "fire", "is_owner": false
  }],
  "has_more": false
}
```

Ranks are absolute across pages. Both sorts use the same server eligibility filter; return no private report, transcript, storage path or voter identities. The client checks the current round before each page request and discards a response for a different round.

Mutation calls:

| Route | JSON body | Frontend handling |
| --- | --- | --- |
| `POST /api/community/entries` | `{"speech_id":"..."}` | Re-read round/entry after success; same share must be idempotent |
| `POST /api/community/entries/{id}/visibility` | `{"shared":false}` or `{"shared":true}` | Withdraw or re-share original entry; re-read round/entry |
| `POST /api/community/speeches/{id}/reaction` | `{"reaction":"fire"}` or `{"reaction":null}` | Return `counts`, `total_reactions`, `my_reaction` directly (same fields as an entry) |
| `GET /api/community/speeches/{id}/audio` | None | Return `{"audio_url":"https://...","expires_at":"..."}`; server bounds lifetime to round close |

Errors use the existing HTTP status plus `{"detail":"Readable error"}`. The frontend displays 403/404/409/422/429/503 messages without assuming the write succeeded. New routes are absent from current `main`; mock tests verify the integration contract, not production authorization or storage deletion.

## Validation

```bash
node --test web/test/*.test.mjs
npm ci --prefix tools/checks
npx --prefix tools/checks playwright install --with-deps chromium
npm --prefix tools/checks run test:community
```

The browser check hosts the static files locally, supplies two authenticated mock sessions and a shared in-memory API, and uses a browser fake microphone to exercise actual multipart uploads. It checks review visibility, sharing/withdrawal, both sorts, reaction replacement/removal/failure/reload, delayed polling, preserved playback, signed-link refresh, visible-only polling, mobile/keyboard behavior, closed/no rounds, repeated prompt rotation and the disabled flag. Screenshots are saved under `work/browser-qa/`. It never contacts the live Supabase project or uploads real recordings. CI runs both frontend suites and retains screenshots.

Before release, repeat the plan's complete two-account flow against the actual API, observe real moderation and cleanup, and verify authorization/concurrent mutations in the database. The frontend alone cannot pass those gates.
