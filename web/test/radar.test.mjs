// node --test web/test/*.test.mjs   (no dependencies)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { AXES, factorScores, firstAndBest, pixelFill, pixelLine, polarPoint } from "../js/radar.js";

const sample = JSON.parse(readFileSync(new URL("../../softskills-grader/sample_report.json", import.meta.url)));
const report = (logic, emotion, tone, diction, pace) => ({
  gradable: true,
  factors: { logic: { score: logic }, emotion: { score: emotion }, tone: { score: tone },
             diction: { score: diction }, pace: { score: pace } },
});

test("reads the five factor scores from a real grader report", () => {
  const s = factorScores(sample);
  assert.deepEqual(Object.keys(s).sort(), AXES.map((a) => a.key).sort());
  for (const a of AXES) assert.equal(s[a.key], sample.factors[a.key].score);
});

test("an ungradable report gives null", () => {
  assert.equal(factorScores({ gradable: false, reason: "Under 40 words" }), null);
  assert.equal(factorScores(null), null);
});

test("first is the earliest gradable attempt; best is the max per part with its attempt", () => {
  const r = firstAndBest([
    { gradable: false },
    report(60, 50, 55, 70, 80),
    report(90, 40, 60, 75, 70),
    report(70, 65, 58, 72, 85),
  ]);
  assert.equal(r.attempts, 3);
  assert.deepEqual(r.first, { logic: 60, emotion: 50, tone: 55, diction: 70, pace: 80 });
  assert.deepEqual(r.best, { logic: 90, emotion: 65, tone: 60, diction: 75, pace: 85 });
  assert.deepEqual(r.bestFrom, { logic: 2, emotion: 3, tone: 2, diction: 2, pace: 3 });
});

test("a tie keeps the earlier attempt, and a missing factor stays null", () => {
  const partial = report(80, 60, 60, 60, 60);
  delete partial.factors.emotion;
  const r = firstAndBest([report(80, 50, 60, 60, 60), partial]);
  assert.equal(r.bestFrom.logic, 1);
  assert.equal(r.best.emotion, 50);
  assert.equal(r.bestFrom.emotion, 1);
  assert.equal(firstAndBest([partial]).best.emotion, null);
});

test("no graded attempts gives null", () => {
  assert.equal(firstAndBest([]), null);
  assert.equal(firstAndBest([{ gradable: false }]), null);
});

test("geometry: 100 on axis 0 is straight up, 0 is the centre, values are clamped", () => {
  const close = ([x, y], [ex, ey]) => assert.ok(Math.abs(x - ex) < 1e-9 && Math.abs(y - ey) < 1e-9, `${x},${y}`);
  close(polarPoint(0, 5, 100, 100, 100, 50), [100, 50]);
  close(polarPoint(3, 5, 0, 100, 100, 50), [100, 100]);
  close(polarPoint(0, 5, 150, 100, 100, 50), [100, 50]);
  close(polarPoint(0, 4, 50, 0, 0, 10), [0, -5]);
  close(polarPoint(1, 4, 100, 0, 0, 10), [10, 0]);
});

test("pixel lines hit both ends and step one pixel at a time", () => {
  const line = pixelLine(0, 0, 5, 2);
  assert.deepEqual(line[0], [0, 0]);
  assert.deepEqual(line.at(-1), [5, 2]);
  for (let i = 1; i < line.length; i++) {
    assert.ok(Math.abs(line[i][0] - line[i - 1][0]) <= 1 && Math.abs(line[i][1] - line[i - 1][1]) <= 1);
  }
  assert.deepEqual(pixelLine(3, 3, 3, 3), [[3, 3]]);
  assert.equal(pixelLine(0, 4, 0, 0).length, 5);
});

test("pixel fill covers the inside of a square and nothing outside it", () => {
  const filled = pixelFill([[0, 0], [4, 0], [4, 4], [0, 4]]);
  assert.equal(filled.length, 16);
  for (const [x, y] of filled) assert.ok(x >= 0 && x < 4 && y >= 0 && y < 4);
});
