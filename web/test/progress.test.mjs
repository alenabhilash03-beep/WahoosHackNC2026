import test from 'node:test';
import assert from 'node:assert/strict';
import { progressFor, sparklinePoints } from '../js/progress-model.js';

const speech = (id, motion_id, created_at, logic, score = logic) => ({
  id, motion_id, created_at, status: 'done', overall: {score, letter:'B'},
  factor_scores: {logic, emotion:logic, tone:logic, diction:logic, pace:logic},
});
test('all prompts keep recording order even when the API is unordered', () => {
  const result = progressFor({speeches:[speech('later','b','2026-10-10T12:00:00Z',90), speech('first','a','2026-10-09T12:00:00Z',50)]});
  assert.equal(result.summary.first.logic,50);
  assert.equal(result.summary.best.logic,90);
  assert.equal(result.summary.bestFrom.logic,2);
});
test('prompt filter excludes pending and ungradable records and preserves per-part provenance', () => {
  const me = {speeches:[speech('a','p','2026-10-09',60), {...speech('b','p','2026-10-10',85),factor_scores:null}, {...speech('c','p','2026-10-11',95),status:'grading'}, speech('d','other','2026-10-12',100), speech('e','p','2026-10-13',80)]};
  const result=progressFor(me,'p');
  assert.equal(result.summary.attempts,2);
  assert.equal(result.best.id,'e');
  assert.deepEqual(result.recent.map(s=>s.id),['a','e']);
  assert.equal(result.gains[0].gain,20);
});
test('no usable speeches means an empty summary; perfect zero is valid', () => {
  assert.equal(progressFor({speeches:[]}).summary,null);
  assert.equal(progressFor({speeches:[speech('zero','p','2026-10-10',0)]}).summary.first.logic,0);
});
test('recent history is the last ten and a score tie keeps the first best', () => {
  const me={speeches:Array.from({length:12},(_,i)=>speech(String(i),'p',`2026-10-${String(i+1).padStart(2,'0')}`,50))};
  assert.equal(progressFor(me).recent.length,10);
  assert.equal(progressFor(me).best.id,'0');
});
test('sparkline uses fixed 0-100 scale and handles single/empty/nonfinite values', () => {
  assert.deepEqual(sparklinePoints([],100,50),[]);
  assert.deepEqual(sparklinePoints([50],100,50),[[50,25]]);
  assert.deepEqual(sparklinePoints([0,100],100,50),[[4,46],[96,4]]);
  assert.deepEqual(sparklinePoints([NaN,Infinity],100,50),[]);
});
