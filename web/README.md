# SpeakUp home screen

A static Three.js page. No build step; Three.js loads from a CDN via an import map.

```bash
python3 -m http.server 5173 --directory web
```

Then open http://localhost:5173.

## How the pixel look works

The character keeps its voxel geometry, 16×16 pixel-art face, and tiny noisy
8×8 surface textures. These textures use nearest-neighbour filtering, so their
pixels stay crisp and attached to the model as it turns.

The canvas renders at the viewport size with antialiasing, rather than enlarging
a roughly 200-row image. This reduces the large outline jumps caused by moving
geometry crossing a coarse screen pixel grid. On the home screen the camera stays
fixed while head turns, breathing and blinking animate.

The scene lives in `js/arena.js`, shared by the home screen and `practice.html`.
**ARGUE** plays a 3-second walk (`WALK_SECONDS`): the speaker turns, walks down
the aisle with the camera following, and climbs the stage steps. The practice
screen then opens on the same frame: `<body data-arena="stage">` draws the speaker
frozen at the foot of the stage before the first paint, and the pages cross-fade.
The walk is a pure function of time, so `(await import("./js/arena.js")).poseAt(2.5)`
in the browser console holds any moment for tuning. Reduced-motion users skip
straight to the end pose.

`MAX_PIXEL_RATIO` caps the device pixel ratio at 2 to limit the rendering cost on
high-density screens. This uses a larger drawing buffer than the original
low-resolution renderer; check performance on your target devices.

## Solo practice and feedback

- **ARGUE** walks the speaker to the stage, then opens `practice.html`. The recorder requests microphone access, runs a 10-second countdown, records up to 60 seconds, and uploads to the authenticated solo API. A file upload is also available. Countdown cancellation releases the microphone; a failed upload keeps the recording in the current page for retry.
- `card.html?id=...` shows the complete grader report, verified evidence, timed transcript playback, top-three authored tips, prior-best deltas and a speech radar. Signed recording URLs are refreshed by reloading the card. Legacy reports without word timings remain readable.
- `config.js` contains only the public API URL and Supabase URL/anonymous key. Anonymous sign-in is shared across pages through the Supabase session. Never add service-role, ElevenLabs or Gemini keys to the browser. Same-origin FastAPI serving provides public configuration automatically; GitHub Pages needs those values configured in this file.
- Start the backend using `server/README.md`. The static HTTP server above still serves the home screen; the other screens need the API configured.

## Progress page

`progress.html` shows the speech radar from `js/radar.js`: one axis per graded part (logic,
emotion, tone, diction, pace), drawn as pixel art in the arena style: the first attempt as a dithered red area and the best on each part as a gold
outline, with a tooltip per part and a table view. It now calls authenticated `GET /api/me`, supports individual prompts or all prompts, names the graded attempt each best came from, and shows gains, best overall and the last ten scores. Failed and ungradable takes are excluded. Tests: `node --test web/test/*.test.mjs`.

The explicit local demo serves saved reports and matching synthetic buzz audio over the same HTTP routes; it does not grade real uploads. See [Phase 2 implementation](../docs/implementation/phase-2-solo.md) for acceptance coverage and remaining live-service checks.
