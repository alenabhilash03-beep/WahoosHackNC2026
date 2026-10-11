import test from 'node:test';
import assert from 'node:assert/strict';
import { LEVELS, boardFor, nameProblem, scoreLine, youLine } from '../js/leaderboard-model.js';

test('each difficulty has its own board', () => {
  const ada = {place: 1, display_name: 'Ada', score: 90, letter: 'A-', attempts: 1, you: true};
  const result = {display_name: 'Ada', boards: {beginner: {rows: [], you: null}, intermediate: {rows: [], you: null}, expert: {rows: [ada], you: ada}}};
  assert.deepEqual(LEVELS, ['beginner', 'intermediate', 'expert']);
  assert.deepEqual(boardFor(result, 'expert'), {display_name: 'Ada', demo: false, rows: [ada], you: ada});
  assert.deepEqual(boardFor(result, 'beginner'), {display_name: 'Ada', demo: false, rows: [], you: null});
  assert.deepEqual(boardFor({display_name: null}, 'expert'), {display_name: null, demo: false, rows: [], you: null});
});
test('names follow the server rule', () => {
  for (const good of ['Ada', 'ada_lovelace_1815', ' Grace ', 'x'.repeat(20)]) assert.equal(nameProblem(good), '', good);
  for (const bad of ['', 'ab', 'x'.repeat(21), 'two words', 'né', '<b>', 'player', 'PLAYER', null]) assert.notEqual(nameProblem(bad), '', String(bad));
});
test('score line counts speeches and tolerates a missing letter', () => {
  assert.equal(scoreLine({score:82.4,letter:'B',attempts:3}), '82.4 / 100 · B · 3 speeches');
  assert.equal(scoreLine({score:70,letter:null,attempts:1}), '70 / 100 · 1 speech');
});
test('your place covers not joined, joined without a grade, and ranked', () => {
  assert.match(youLine({display_name:null,you:null}), /Pick a name/);
  assert.match(youLine({display_name:'Ada',you:null}), /You are Ada\. Record a graded speech/);
  assert.equal(youLine({display_name:'Ada',you:{place:2,score:91,letter:'A-',attempts:4}}), 'You are Ada: place 2, best 91 / 100 · A- · 4 speeches.');
});
