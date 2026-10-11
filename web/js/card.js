import { api } from './api.js';
import { factorScores, renderRadar } from './radar.js';
import { metricRows, sectionScores, transcriptModel, evidenceForMetric, stanceEvidence, quoteTime, retryHref, safeAudioUrl } from './card-model.js';
import { addSoundToggle, sfx, wireSounds } from './sound.js';
import {mountCommunityShare} from './community-share.js';

const $ = id => document.getElementById(id);
const list = value => Array.isArray(value) ? value : [];
const fmt = value => typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value * 10) / 10) : '—';
const at = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2,'0')}`;
const node = (tag,text,className='') => { const element=document.createElement(tag); if(text!==undefined) element.textContent=String(text); if(className) element.className=className; return element; };
const speechId = new URLSearchParams(location.search).get('id');
let currentCard, audioAvailable=false, timer, pollingStarted=0, active=true, loading=false;
const sharing=mountCommunityShare({api});

function status(heading,message,{reload=false,practice=false,retry=false}={}) {
  $('status-panel').hidden=false;
  $('status-heading').textContent=heading;
  $('status').textContent=message;
  $('retry-saved').hidden=!retry;
  $('reload').hidden=!reload;
  $('status-practice').hidden=!practice;
  $('status-practice').href=retryHref(currentCard);
}

async function playAt(seconds) {
  if(!audioAvailable || typeof seconds!=='number' || !Number.isFinite(seconds) || seconds<0) return;
  const audio=$('audio');
  try {
    if(audio.readyState<1) {
      await new Promise((resolve,reject)=>{
        const cleanup=()=>{audio.removeEventListener('loadedmetadata',ready);audio.removeEventListener('error',failed);clearTimeout(timeout);};
        const ready=()=>{cleanup();resolve();};
        const failed=()=>{cleanup();reject(new Error('Recording could not load.'));};
        const timeout=setTimeout(failed,10000);
        audio.addEventListener('loadedmetadata',ready,{once:true}); audio.addEventListener('error',failed,{once:true}); audio.load();
      });
    }
    if(Number.isFinite(audio.duration) && seconds>audio.duration) throw new Error('This timestamp is outside the recording.');
    audio.currentTime=seconds;
    await audio.play();
    $('audio-status').textContent=`Playing from ${at(seconds)}.`;
  } catch(error) {
    $('audio-status').textContent=`Playback unavailable: ${error.message || 'use the recording controls to try again.'}`;
  }
}

function evidenceNode(evidence,report) {
  const wordTime=evidence.quote ? quoteTime(evidence.text,report.words) : null;
  const time=wordTime ?? evidence.start;
  const playable=audioAvailable && typeof time==='number' && Number.isFinite(time) && time>=0;
  const element=node(playable?'button':'span',evidence.text,playable?'evidence-play':'');
  if(playable) { element.type='button'; element.setAttribute('aria-label',`Play ${at(time)}: ${evidence.text}`); element.addEventListener('click',()=>playAt(time)); }
  const wrapper=node('span');
  if(evidence.label) wrapper.append(node('strong',`${evidence.label}: `));
  wrapper.append(element);
  if(playable) wrapper.append(node('small',` [${at(time)}]`));
  return wrapper;
}

function renderEvidence(target,evidence,report,empty='No verified evidence reported.') {
  target.replaceChildren();
  if(!evidence.length) { target.append(node('p',empty,'small-copy')); return; }
  const ul=node('ul',undefined,'evidence-list');
  for(const e of evidence) { const li=node('li');li.append(evidenceNode(e,report));ul.append(li); }
  target.append(ul);
}

function renderTranscript(report) {
  const model=transcriptModel(report), target=$('transcript'); target.replaceChildren();
  $('transcript-note').textContent=model.timed
    ? audioAvailable ? 'Select any timed word or evidence quote to play that moment. Highlights show the measured stretches.' : 'Word timings are available. Playback needs a recording URL.'
    : 'This report has no word timestamps. The transcript is shown as text; only independently timed evidence can play.';
  if(model.words.length && model.timed) {
    let pauseIndex=0;
    for(const segment of model.segments) {
      if(segment.word===null) { target.append(document.createTextNode(segment.text));continue; }
      const word=model.words[segment.word];
      while(pauseIndex<model.pauses.length && word.start!==null && model.pauses[pauseIndex].start<=word.start) {
        target.append(pauseNode(model.pauses[pauseIndex++])); target.append(document.createTextNode(' '));
      }
      const classes=['word',word.filler?'filler':'',word.monotone?'monotone':'',word.emphasis?'emphasis':''].filter(Boolean).join(' ');
      const playable=word.start!==null && audioAvailable;
      const element=node(playable?'button':'span',segment.text,classes);
      if(playable) { element.type='button';element.setAttribute('aria-label',`Play ${at(word.start)}: ${word.text}`);element.addEventListener('click',()=>playAt(word.start)); }
      target.append(element);
    }
    while(pauseIndex<model.pauses.length) target.append(pauseNode(model.pauses[pauseIndex++]));
  } else target.textContent=model.text || 'No transcript returned.';
  $('pause-markers').replaceChildren();
  if(!model.timed && model.pauses.length) {
    $('pause-markers').append(node('p','Timed pause evidence:'));
    for(const pause of model.pauses) $('pause-markers').append(pauseNode(pause),document.createTextNode(' '));
  }
}

function pauseNode(pause) {
  const element=node(audioAvailable?'button':'span',`[${pause.seconds} s pause · ${at(pause.start)}]`,'pause pause-button');
  if(audioAvailable) { element.type='button';element.addEventListener('click',()=>playAt(pause.start)); }
  return element;
}

function renderArgument(report) {
  const a=report.evidence?.argument ?? {}, target=$('argument');target.replaceChildren();
  const structure=node('div',undefined,'structure');
  for(const [label,key] of [['Thesis','thesis_found'],['Conclusion','conclusion_found'],['Counterargument','counterargument_found'],['Rebuttal','rebuttal_found']]) {
    structure.append(node('span',`${label}: ${a[key]===true?'found':a[key]===false?'not found':'not reported'}`));
  }
  target.append(structure,node('p',`Chosen side: ${a.side_chosen ?? currentCard.side ?? 'not reported'} · Argued side: ${a.stance_argued ?? 'not reported'}`));
  if(a.stance_verification) {
    target.append(node('p',a.stance_verification.message ?? 'Stance verification did not return a decision.'));
    const quotes=stanceEvidence(report);
    if(quotes.length) { const proof=node('div');renderEvidence(proof,quotes,report,'');target.append(proof); }
  }
  const claims=list(a.arguments);
  if(!claims.length) target.append(node('p','No verified claims reported.'));
  for(const claim of claims) {
    const item=node('article',undefined,'claim');
    const heading=node('p'); heading.append(node('strong','Claim: '),evidenceNode({text:claim.claim ?? '',quote:true},report));
    item.append(heading,node('p',`${claim.status ?? 'Status not reported'} · Support type: ${claim.support_type ?? 'none'}`,'small-copy'));
    if(claim.support) { const quote=node('blockquote');quote.append(evidenceNode({text:claim.support,quote:true},report));item.append(quote); }
    else item.append(node('p','No support quote reported.','small-copy'));
    target.append(item);
  }
  target.append(node('h3','FALLACIES','eyebrow'));
  const fallacies=node('div');renderEvidence(fallacies,list(a.fallacies).map(f=>({text:f.quote ?? f.text ?? '',label:f.type ?? f.name ?? 'Fallacy',quote:true})),report,'No verified fallacies found.');target.append(fallacies);
  // Do not display discarded Gemini quotes as evidence: verification explicitly rejected them.
  const discarded=list(a.discarded_from_gemini).length;
  if(discarded) target.append(node('p',`${discarded} model extraction${discarded===1?' was':'s were'} excluded because verification failed.`,'small-copy'));
}

// Gemini's one-line advice per metric, fetched after the card renders.
let coachingLines={};
async function loadCoaching(id) {
  try {
    const result=await api(`/api/speeches/${encodeURIComponent(id)}/coaching`);
    coachingLines=result.lines ?? {};
  } catch { coachingLines={}; }
  for(const line of document.querySelectorAll('[data-coach]')) {
    line.textContent=coachingLines[line.dataset.coach] ?? 'Open the numbers below to see what to work on.';
  }
}

function renderMetrics(report) {
  const target=$('metrics');target.replaceChildren();
  const rows=metricRows(report);
  for(const [key,factor] of Object.entries(report.factors ?? {})) {
    const details=node('details',undefined,'factor-details'); details.open=true;
    const delta=currentCard.deltas?.factors?.[key];
    details.append(node('summary',`${key.toUpperCase()} · ${fmt(factor.score)} / 100 · ${fmt(factor.weight_pct)}% OF OVERALL${typeof delta==='number'?` · ${delta>0?'+':''}${fmt(delta)} VS PREVIOUS BEST`:''}`));
    const body=node('div');
    for(const row of rows.filter(r=>r.factor===key)) {
      // Each metric is a closed box: its name and a score chip until it is opened.
      const item=node('details',undefined,'metric');item.dataset.metric=row.metric;
      const lost=row.kind==='metric' ? row.overallLost : row.points_lost;
      const chip=row.kind==='metric'
        ? (typeof row.score==='number' ? `${fmt(row.score)}/100` : 'NOT MEASURED')
        : (row.points_lost ? `-${fmt(row.points_lost)} PTS` : 'NO PENALTY');
      const summary=node('summary',undefined,'metric-summary');
      summary.append(node('span',row.label,'metric-name'),node('span',chip,`metric-chip${lost>0?' lost':''}`));
      item.append(summary);
      const coach=node('p',coachingLines[row.metric] ?? 'Writing your coaching…','metric-coach');coach.dataset.coach=row.metric;
      item.append(coach);
      const dl=node('dl');
      const entries=row.kind==='metric'
        ? [['Measured',`${fmt(row.value)} ${row.unit ?? ''}`],['Rubric bucket',row.bucket ?? 'Not reported'],['Metric score',`${fmt(row.score)} / 100`],['Weight within this part',fmt(row.weight)],['Overall points lost',fmt(row.overallLost)]]
        : [['Measured',`${fmt(row.value)} occurrences`],['Free allowance',fmt(row.free)],['Penalty per excess',`${fmt(row.points_each)} points`],['Penalty cap',`${fmt(row.max_points)} points`],['Part points deducted',fmt(row.points_lost)],['Overall points lost',fmt(row.overallLost)]];
      for(const [label,value] of entries) { const cell=node('div');cell.append(node('dt',label),node('dd',value));dl.append(cell); }
      item.append(dl);
      const evidence=evidenceForMetric(report,row.metric);
      if(evidence.length) {
        const expander=node('details',undefined,'metric-evidence body-copy');expander.append(node('summary',`Evidence (${evidence.length})`));
        const contents=node('div');renderEvidence(contents,evidence,report);expander.append(contents);item.append(expander);
      }
      body.append(item);
    }
    details.append(body);target.append(details);
  }
}

let fanfarePlayed=false;
/** The grade fanfare, once. Inside the stage page, that page plays it: it already has permission to make sound. */
function announceGrade(letter) {
  if(fanfarePlayed || !letter) return;
  fanfarePlayed=true;
  if(window.parent!==window) window.parent.postMessage({type:'arena:grade',letter},location.origin);
  else sfx.grade(letter);
}

function render(card) {
  const report=card.report;
  sharing.update({...card,id:card.id ?? speechId});
  $('status-panel').hidden=true;$('card').hidden=false;
  $('prompt').textContent=card.prompt?.text ?? report.inputs?.topic ?? 'Your speech';
  $('demo-banner').hidden=!card.demo;
  $('side').textContent=String(card.side ?? report.inputs?.side ?? 'SIDE NOT REPORTED').toUpperCase();
  $('score').textContent=fmt(report.overall?.score);$('letter').textContent=report.overall?.letter ?? '';
  announceGrade(report.overall?.letter);
  const cap=list(report.overall?.capped_by)[0];
  $('capped').hidden=!cap;
  $('capped').textContent=cap ? `Capped at ${fmt(cap.cap)}: ${cap.reason} Without this, your parts average ${fmt(report.overall.uncapped)}.` : '';
  $('retry').href=retryHref(card);
  const deltas=card.deltas;
  $('comparison').textContent=card.previous_best
    ? `Previous best on this prompt and side: ${fmt(card.previous_best.overall?.score ?? card.previous_best.overall)} / 100.${typeof deltas?.overall==='number' ? ` This take: ${deltas.overall>0?'+':''}${fmt(deltas.overall)} overall.`:''}`
    : 'Your first graded take on this prompt and side. Retry to see what moved.';
  $('sections').replaceChildren();
  for(const [key,score] of Object.entries(sectionScores(report,card.sections))) {
    const section=node('div',undefined,'section-score');section.append(node('h3',key.toUpperCase()),node('strong',fmt(score)));
    const delta=deltas?.[key];
    section.append(node('p',typeof delta==='number'?`${delta>0?'+':''}${fmt(delta)} vs previous best`:'No previous comparison',`delta ${delta>0?'positive':delta<0?'negative':''}`));
    section.append(node('p',key==='logos'?'Logic':key==='pathos'?'Emotion':'Pace · Diction · Tone','small-copy'));
    $('sections').append(section);
  }
  $('fixes').replaceChildren();
  for(const fix of list(card.fixes).slice(0,3)) {
    const item=node('li');item.append(node('strong',fix.label ?? fix.metric),node('p',fix.tip ?? ''),node('p',`${fmt(fix.lost_points)} rubric points lost · ${fix.factor}`,'small-copy'));$('fixes').append(item);
  }
  if(!$('fixes').children.length) $('fixes').append(node('li','No scored points lost. Keep this level on your next take.'));
  const series=[];
  if(card.previous_best?.factor_scores) series.push({name:'Previous best',kind:'area',values:card.previous_best.factor_scores});
  series.push({name:'This speech',kind:'outline',values:factorScores(report)});
  renderRadar($('radar'),{series});
  $('radar-note').textContent=card.previous_best?'Previous best is filled; this speech is outlined.':'This is your first take. The outline shows this speech.';
  const expired=card.audio_deleted_at || (card.audio_expires_at && Date.parse(card.audio_expires_at)<=Date.now());
  const audioUrl=expired ? null : safeAudioUrl(card.audio_url,globalThis.ARENA_CONFIG?.API_URL || location.href);
  audioAvailable=Boolean(audioUrl);
  $('audio').hidden=!audioAvailable;
  if(audioUrl) { $('audio').src=audioUrl;$('audio-status').textContent=card.demo?'Demo audio is a synthetic tone, not a recording of this transcript. Timed evidence controls demonstrate seeking only.':'Your recording. Use the controls or select a timed highlight. Community recordings are shared only when you choose to share them.'; }
  else { $('audio').removeAttribute('src');$('audio-status').textContent=expired ? 'This recording has expired. Your scores and evidence stay saved.' : 'Playback unavailable for this report. Your scores and evidence are still shown.'; }
  renderTranscript(report);renderArgument(report);
  renderEvidence($('appeals'),list(report.evidence?.argument?.emotional_appeals).map(e=>({text:e.quote ?? '',label:e.type ?? 'Appeal',quote:true})),report,'No verified emotional appeals found.');
  const emphasis=report.measurements?.loudness_variation_db;
  if(typeof emphasis==='number') $('audio-status').append(document.createTextNode(` Measured emphasis: ${fmt(emphasis)} dB loudness variation.`));
  renderMetrics(report);
  loadCoaching(card.id ?? speechId);
}

async function load() {
  if(!active || loading || !speechId) return;
  clearTimeout(timer);loading=true;$('reload').disabled=true;
  try {
    const card=await api(`/api/speeches/${encodeURIComponent(speechId)}`);
    if(!active) return;
    currentCard=card;
    if(['queued','grading'].includes(card.status)) {
      $('card').hidden=true;
      if(Date.now()-pollingStarted>=120000) { status('STILL GRADING','This speech is taking longer than usual. Check again to resume waiting.',{reload:true,practice:true}); return; }
      status(card.status==='queued'?'IN THE QUEUE':'GRADING YOUR SPEECH','Measuring delivery and verifying your argument. This page checks every 3 seconds.');
      timer=setTimeout(load,3000);return;
    }
    if(card.status==='failed') { $('card').hidden=true;status('GRADING DID NOT FINISH',card.error ?? card.reason ?? 'The grading service could not finish this take. Record again or check for an update.',{reload:!card.can_retry,practice:true,retry:card.can_retry===true});return; }
    if(card.report?.gradable===false || card.status==='ungradable') { $('card').hidden=true;status('NOT GRADABLE',card.report?.reason ?? card.reason ?? 'This recording did not contain enough clear speech to grade.',{practice:true});return; }
    if(!card.report?.gradable || !card.report?.factors) throw new Error('The speech report is not ready.');
    render(card);
  } catch(error) {
    if(active) { $('card').hidden=true;status('CARD UNAVAILABLE',error.message || 'Could not load this speech. Try again.',{reload:true,practice:true}); }
  } finally { loading=false;$('reload').disabled=false; }
}

$('audio').addEventListener('error',()=>{$('audio-status').textContent='The recording could not load. Check again to refresh its private playback link.';$('reload').hidden=false;$('status-panel').hidden=false;status('PLAYBACK UNAVAILABLE','The signed recording link may have expired. Refresh the card to get a new link.',{reload:true});});
// Grade the recording already saved on the server again: same speech, nothing re-recorded.
async function retrySaved() {
  if(loading || !speechId) return;
  const button=$('retry-saved');button.disabled=true;
  try {
    await api(`/api/speeches/${encodeURIComponent(speechId)}/retry`,{method:'POST'});
  } catch(error) {
    // 409: another tab or click already queued it, so fall through and show its progress.
    if(error.status!==409) { button.disabled=false;$('status').textContent=`Could not retry: ${error.message || 'try again.'}`;return; }
  }
  button.disabled=false;pollingStarted=Date.now();load();
}

$('retry-saved').addEventListener('click',retrySaved);
$('reload').addEventListener('click',()=>{pollingStarted=Date.now();load();});
const expiryClock=setInterval(()=>{
  if(!active || !currentCard?.audio_expires_at || Date.parse(currentCard.audio_expires_at)>Date.now() || !audioAvailable) return;
  audioAvailable=false;$('audio').pause();$('audio').removeAttribute('src');$('audio').load();$('audio').hidden=true;
  $('audio-status').textContent='This recording has expired. Your scores and evidence stay saved.';
},1000);
window.addEventListener('pagehide',()=>{active=false;clearTimeout(timer);clearInterval(expiryClock);$('audio').pause();});
wireSounds();addSoundToggle();
if(!speechId) status('RECORD YOUR FIRST SPEECH','A speech card opens after you finish a recording. Choose a prompt to begin.',{practice:true});
else { pollingStarted=Date.now();load(); }
