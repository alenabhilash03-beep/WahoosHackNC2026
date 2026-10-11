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

- **ARGUE** walks the speaker to the stage, then opens `practice.html`, the round:
  1. Pick a difficulty (beginner, intermediate, expert) and a category: five random well-known ones (`js/topics.js`) or your own.
  2. The speaker climbs onto the stage and walks to the podium (~2 s) while the server writes a yes-or-no question (`POST /api/questions`). Choose YES or NO.
  3. A 5-second countdown through the speaker's eyes, gazing left to right across the audience and the stage lights.
  4. Speak for up to 60 seconds. The time bar along the bottom turns from white to red; FINISH stops early.
  5. Applause while the speech uploads, with the audience clapping; the speech card fades in as it dies away, then plays a fanfare for the grade (A loud, B softer, C a jingle, D/F a sad trombone). The card has RETURN TO LOBBY.

## Sound

All audio is synthesised in `js/sound.js` (Web Audio, no files): the lobby's chiptune loop, click and
hover sounds on every control (`wireSounds`), the question chime, countdown beeps, applause and grade
fanfares. The ♪ ON / OFF button (`addSoundToggle`) is remembered in `localStorage`. Browsers allow
sound only after a press on the page, so the music starts with the first click. Inside the stage page,
the speech card asks that page (`postMessage`) to play the fanfare, since it already has permission.

## Audience

Everyone in the hall comes from `createCrowd` in `js/arena.js`: one `InstancedMesh` per body part. Each
person has a mood from neutral to beaming (mouth, smile corners, eyebrows), blinks on their own clock,
and glances and nods; during applause the mood lifts and box hands clap under the chin. The front rows
are always shown; the rest of the house and the balcony only from the podium.

  Real grading needs the server; see "Local mode" in `server/README.md`. On GitHub Pages the page explains that.
- `card.html?id=...` shows the complete grader report, verified evidence, timed transcript playback, top-three authored tips, prior-best deltas and a speech radar. Signed recording URLs are refreshed by reloading the card. Legacy reports without word timings remain readable.
- `login.html` supports Continue with GitHub, with no SMTP required. The button is enabled when GitHub is configured in Supabase. Account controls let users sign out from any screen. Signed-out and anonymous visitors are redirected to login before protected API requests. See the [social accounts guide](../docs/implementation/social-accounts.md) for OAuth app registration and Supabase settings.
- `config.js` contains only the public API URL, Supabase URL, and anon/publishable key. The account session is shared across pages and refreshed by Supabase. Never add service-role, ElevenLabs or Gemini keys to the browser. Same-origin FastAPI serving provides public configuration automatically; GitHub Pages needs those values configured in this file and a deployed API URL.
- Start the backend using `server/README.md`. The static HTTP server above still serves the home screen; the other screens need the API configured.

## Progress page

`progress.html` shows the speech radar from `js/radar.js`: one axis per graded part (logic,
emotion, tone, diction, pace), drawn as pixel art in the arena style: the first attempt as a dithered red area and the best on each part as a gold
outline, with a tooltip per part and a table view. It now calls authenticated `GET /api/me`, supports individual prompts or all prompts, names the graded attempt each best came from, and shows gains, best overall and the last ten scores. Failed and ungradable takes are excluded. Tests: `node --test web/test/*.test.mjs`.

The explicit local demo serves saved reports and matching synthetic buzz audio over the same HTTP routes; it does not grade real uploads. See [Phase 2 implementation](../docs/implementation/phase-2-solo.md) for acceptance coverage and remaining live-service checks.
