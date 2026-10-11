import {test} from 'node:test';
import assert from 'node:assert/strict';
import {introPose, micDropPose, MIC_DROP_SECONDS} from '../js/intro-motion.js';

test('the intro visits three on-stage viewpoints and loops without a cut', () => {
  const views = [0, 12, 24].map(t => introPose(t));
  assert.equal(new Set(views.map(p => p.position[0])).size, 3);
  for (const {position, look} of views) {
    assert.ok(position[1] >= 3.4 && position[1] <= 3.8, 'speaker eye height');
    assert.ok(position[2] < -9.4, 'camera stays on stage');
    assert.ok(look[2] > position[2], 'camera faces audience');
  }
  assert.deepEqual(introPose(36), introPose(0));
  for (const boundary of [12, 24, 36]) {
    const before = introPose(boundary - 0.001), after = introPose(boundary + 0.001);
    for (const key of ['position', 'look']) {
      assert.ok(Math.hypot(...before[key].map((v, i) => v - after[key][i])) < 0.001);
    }
  }
});

test('reduced motion holds one viewpoint for the entire intro', () => {
  for (const t of [0, 5, 12, 24, 36, 100]) assert.deepEqual(introPose(t, true), introPose(0, true));
});

test('mic lifts into the centre, falls below the frame, and completes on time', () => {
  const held = micDropPose(0), lifted = micDropPose(0.42), fallen = micDropPose(1.57);
  assert.ok(lifted.y > held.y && lifted.z > held.z);
  assert.equal(lifted.x, 0);
  assert.ok(fallen.y < -2.5);
  assert.ok(Math.abs(fallen.rotation) > Math.PI);
  assert.equal(micDropPose(MIC_DROP_SECONDS - 0.01).done, false);
  assert.equal(micDropPose(MIC_DROP_SECONDS).done, true);
});
