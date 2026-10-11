# Checks

## Current: browser checks

Both drive a real browser against a local server with mocked sign-in; no live account or provider is
used. Set-up and what each one covers: [Phase 2](../../docs/implementation/phase-2-solo.md#checks)
and the [accounts guide](../../docs/implementation/social-accounts.md#verify).

```sh
npm install --prefix tools/checks
npm --prefix tools/checks exec -- playwright install chromium
npm run test:auth --prefix tools/checks                       # GitHub sign-in, sessions, sign-out
node tools/checks/check_solo_browser.cjs http://127.0.0.1:8000  # solo round, card, progress (DEMO_MODE server)
```

## From the archived plan

`check_engine.py` and `voice-check/` were written for the NetworkMaxx plan in
`docs/implementation/archive/` (the cut Speech Engine) and no longer match the app. They are kept
for reference only. `check_supabase.py` is not one of them: it was rewritten for SpeakUp.

For the current SpeakUp app, run `python tools/checks/check_supabase.py` for a
read-only schema, prompt, public-key isolation and private-bucket check. Use the
[database guide](../../supabase/README.md) for migrations and constraint/RLS tests.
An existing GitHub account token can optionally verify authenticated isolation;
anonymous login and Realtime are not required by the current app.

The conversation-engine/broadcast checks below belong to archived NetworkMaxx.

Three small tools that confirm the live services behave the way the implementation guide assumes.
Each takes a few minutes. Run them in this order.

| When | Tool | Proves |
| --- | --- | --- |
| Phase 0, step 7 | open `tools/checks/broadcast.html` in a browser | The same broadcast reaches a browser using `supabase-js` |
| Phase 1, step 2 (engine created) | `python tools/checks/check_engine.py` (`--fix` to repair) | The Speech Engine forwards every event the call screen listens for, the first-message override is on, and `ws_url` points at your server |
| Phase 1, step 9 (call screen works) | `/dev/voice-check` page (copy `voice-check/page.tsx` to `web/app/dev/voice-check/page.tsx`) | The conversation id, event types, payload keys and timing your account actually produces |

The two Python checks need the server's virtualenv (Phase 0, step 4) and read keys from the
environment, then `server/.env` and `web/.env.local`.
Keys never go in the HTML page except the public one (anon or `sb_publishable_`).

## What is already confirmed from the SDK source

Read from the published packages (`@elevenlabs/react` 1.17.0, `@elevenlabs/client` 1.27.0,
`@supabase/supabase-js` 2.117.3, `supabase` 2.32.0, `elevenlabs` 2.71.0), so the checks above
only have to confirm your account's settings:

- `useConversation` throws unless it is inside `<ConversationProvider>`.
- `startSession(...)` returns nothing. The conversation id arrives in `onConnect({ conversationId })`;
  `getId()` returns the same id once connected. With a `conversationToken` the session is WebRTC.
- `onMessage` payload: `{ message, role: "agent" | "user", event_id, response_id? }`. `source`
  (`"ai"` / `"user"`) still exists but is deprecated. Agent messages come from `agent_response`
  events, user messages from `user_transcript` (final transcripts only).
- `onModeChange` payload: `{ mode: "speaking" | "listening" }`. On WebRTC, "listening" comes from
  LiveKit's server-side active-speaker updates, so it can trail the end of the persona's audio.
- `onAudio(base64)` fires for every 25 ms chunk of persona audio louder than 0.01, measured in the
  browser on the incoming track. The call screen uses it to time the persona's turns.
- `onInterruption({ event_id })` and `onAgentResponseCorrection({ original_agent_response,
  corrected_agent_response, event_id, response_id? })` exist with those fields.
- The Speech Engine's `conversation.client_events` list decides which of these events ElevenLabs
  sends. `speech_engine.create` and `speech_engine.update` both accept it.
- `supabase.channel("x")` joins socket topic `realtime:x`; the REST broadcast topic is the bare
  `"x"`. Channels are public unless `{ config: { private: true } }` is passed; REST messages are
  public unless `"private": true` is set. A public broadcast only reaches public channels.
- New `sb_secret_` / `sb_publishable_` keys are not JWTs: send them on the `apikey` header only.
  Secret keys are also refused from browsers.
