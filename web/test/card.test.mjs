import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { metricRows, sectionScores, transcriptModel, evidenceForMetric, stanceEvidence, quoteTime, retryHref, safeAudioUrl } from '../js/card-model.js';

const sample = JSON.parse(readFileSync(new URL('../../softskills-grader/sample_report.json', import.meta.url)));

test('stance evidence displays only grounded quotes and preserves the decision explanation', () => {
  const check={quote_verified:true,evidence_quote:'Homework should not be banned.'};
  const report={evidence:{argument:{side_chosen:'no',stance_argued:'no',stance_verification:{
    message:'Your speech matches the chosen side.',primary:check,confirmation:{...check},
  }}}};
  assert.equal(stanceEvidence(report).length,1);
  assert.equal(stanceEvidence(report)[0].text,check.evidence_quote);
  assert.ok(evidenceForMetric(report,'stance_mismatch').some(e=>e.text===check.evidence_quote && e.quote));
  report.evidence.argument.stance_verification.primary.quote_verified=false;
  report.evidence.argument.stance_verification.confirmation=null;
  assert.deepEqual(stanceEvidence(report),[]);
  assert.deepEqual(stanceEvidence({}),[]);
});

test('the card keeps every metric and deduction, including zero penalties', () => {
  const rows = metricRows(sample);
  const expected = Object.values(sample.factors).reduce((n, f) => n + f.metrics.length + f.deductions.length, 0);
  assert.equal(rows.length, expected);
  assert.equal(rows.filter(r => r.kind === 'deduction' && r.points_lost === 0).length, 9);
  assert.ok(Math.abs(rows.reduce((n, r) => n + r.overallLost, 0) - 4.275) < 0.01);
  for (const row of rows) assert.ok(row.label && row.factor && row.metric);
});

test('unmeasurable metrics keep their row but are excluded from loss attribution weights', () => {
  const report={factors:{tone:{weight_pct:20,metrics:[
    {metric:'unknown',label:'Not measurable',weight:3,score:null,value:null},
    {metric:'known',label:'Measured',weight:1,score:80,value:3},
  ],deductions:[]}}};
  const rows=metricRows(report);
  assert.equal(rows.length,2);
  assert.equal(rows[0].overallLost,null);
  assert.equal(rows[0].score,null);
  assert.equal(rows[1].overallLost,4);
});

test('delivery uses factor weights; API section scores are authoritative', () => {
  assert.equal(sectionScores(sample).logos,100);
  assert.equal(sectionScores(sample).pathos,94);
  assert.ok(Math.abs(sectionScores(sample).delivery - (100 + 100 + 83.1) / 3) < 1e-10);
  assert.deepEqual(sectionScores(sample, {logos: 12, pathos: 13, delivery: 14}), {logos: 12, pathos: 13, delivery: 14});
});

test('server sections include score and evidence objects', () => {
  assert.deepEqual(sectionScores(sample, {logos:{score:11},pathos:{score:22},delivery:{score:33}}), {logos:11,pathos:22,delivery:33});
});

test('filler phrases, pause times and monotone spans match timed words', () => {
  const r = {words: [{text:'You',start:0,end:0.2},{text:'know,',start:0.3,end:0.5},{text:'knowledge',start:0.6,end:1},{text:'um',start:4,end:4.3}], evidence:{diction:{fillers:['you know','um']},pace:{long_pauses:[{at_seconds:1,seconds:3}]},tone:{monotone_ranges:[{start:0.5,end:1}],emphasis_ranges:[{start:0,end:0.2}]}}};
  const model = transcriptModel(r);
  assert.deepEqual(model.words.map(w => w.filler), [true,true,false,true]);
  assert.equal(model.words[2].monotone, true);
  assert.equal(model.words[0].emphasis, true);
  assert.equal(model.pauses[0].start,1);
  assert.equal(quoteTime('you know', r.words), 0);
  assert.equal(quoteTime('know knowledge', r.words), 0.3);
  assert.equal(quoteTime('invented quote', r.words), null);
});

test('legacy transcript preserves literal malicious text and does not invent timings', () => {
  const transcript = '<img src=x onerror=alert(1)> you know';
  const model = transcriptModel({transcript, evidence:sample.evidence});
  assert.equal(model.text, transcript);
  assert.equal(model.timed, false);
  assert.deepEqual(model.words, []);
  assert.equal(model.pauses[0].start,18.21);
});

test('timed transcript keeps punctuation and unmatched words from the full original text', () => {
  const transcript='Hello, untimed <img src=x> world!';
  const model=transcriptModel({transcript,words:[{text:'Hello,',start:0,end:1},{text:'world!',start:2,end:3}]});
  assert.equal(model.segments.map(s=>s.text).join(''),transcript);
  assert.equal(model.segments.filter(s=>s.word!==null).length,2);
  assert.ok(model.segments.some(s=>s.word===null && s.text.includes('<img src=x>')));
});

test('invalid word timings cannot seek; structure booleans never become quotes', () => {
  const model = transcriptModel({words:[{text:'hi',start:-1,end:2},{text:'there',start:2,end:1}],transcript:'hi there'});
  assert.ok(model.words.every(w => w.start === null));
  assert.equal(quoteTime('hi', model.words), null);
  const evidence = evidenceForMetric(sample, 'structure_points');
  assert.deepEqual(evidence.map(e=>e.text), ['Thesis: found','Conclusion: found']);
  assert.ok(evidence.every(e=>!e.quote));
  assert.equal(evidenceForMetric(sample,'supported_arguments').length,4);
  assert.equal(evidenceForMetric(sample,'emotional_appeals').length,3);
});

test('retry preserves prompt and side; playback only accepts HTTP(S) signed URLs', () => {
  assert.equal(retryHref({motion_id:'a & b',side:'yes'}), 'practice.html?motion_id=a+%26+b&side=yes');
  assert.equal(safeAudioUrl('javascript:alert(1)'),null);
  assert.equal(safeAudioUrl('data:audio/wav;base64,x'),null);
  assert.equal(safeAudioUrl('https://example.com/signed?token=x'),'https://example.com/signed?token=x');
  assert.equal(safeAudioUrl('/api/demo/audio','http://localhost:8000/card.html'),'http://localhost:8000/api/demo/audio');
  assert.equal(safeAudioUrl('//elsewhere.test/audio','http://localhost:8000'),null);
});
