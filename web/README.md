# Debate Arena home screen

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
geometry crossing a coarse screen pixel grid. The camera stays fixed while the
head turns, breathing, blinking, and the **Argue** hop continue to animate.

`MAX_PIXEL_RATIO` caps the device pixel ratio at 2 to limit the rendering cost on
high-density screens. This uses a larger drawing buffer than the original
low-resolution renderer; check performance on your target devices.

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
