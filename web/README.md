# Debate Arena home screen

A static Three.js page. No build step; Three.js loads from a CDN via an import map.

```bash
python3 -m http.server 5173 --directory web
```

Then open http://localhost:5173.

## How the pixel look works

`main.js` renders the scene at roughly 200 rows tall (`TARGET_ROWS`), and CSS
(`image-rendering: pixelated`) upscales the canvas without smoothing. Lower
`TARGET_ROWS` for chunkier pixels. Every surface gets a tiny noisy 8×8 texture
so blocks read as hand-placed pixels.

## Hooks for what comes next

- The **Argue** button fires a `window` `argue` event and plays a placeholder hop.
  The walk-to-podium animation should replace that.
- The centre aisle and stage steps lead to the podium (`PODIUM_X`, `STAGE_Z`),
  so the walk path is already clear.

## Progress page

`progress.html` shows the speech radar from `js/radar.js`: one axis per graded part (logic,
emotion, tone, diction, pace), drawn as pixel art in the arena style: the first attempt as a dithered red area and the best on each part as a gold
outline, with a tooltip per part and a table view. It uses example data until `GET /api/me`
exists. Open http://localhost:5173/progress.html; tests: `node --test web/test/*.test.mjs`.
