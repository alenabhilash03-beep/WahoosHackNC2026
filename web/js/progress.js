import {api} from './api.js';
import {AXES,renderRadar} from './radar.js';
import {progressFor,sparklinePoints} from './progress-model.js';

const $=id=>document.getElementById(id);
let me;
function draw() {
  if (!me) return;
  const {summary,best,gains,recent}=progressFor(me,$('prompt').value);
  $('gains').replaceChildren(); $('radar').replaceChildren();
  $('meta').textContent=summary ? `${summary.attempts} graded attempt${summary.attempts===1?'':'s'}` : 'No graded speeches on this prompt yet. Record your first speech.';
  $('best').replaceChildren();
  if (best) {
    const link=document.createElement('a'); link.href=`card.html?id=${encodeURIComponent(best.id)}`;
    link.textContent=`Best overall: ${best.overall.score} / 100 (${best.overall.letter})`;
    $('best').append(link);
  }
  if (summary) {
    renderRadar($('radar'),{series:[{name:'First attempt',kind:'area',values:summary.first},{name:'Best per part',kind:'outline',values:summary.best}],
      notes:Object.fromEntries(AXES.map(a=>[a.key,`Best from graded attempt ${summary.bestFrom[a.key] ?? '—'}`]))});
    for (const gain of gains) {
      const li=document.createElement('li'),label=document.createElement('span'),score=document.createElement('b');
      label.textContent=gain.label.toUpperCase();score.textContent=gain.gain==null?'—':`${gain.gain>0?'+':''}${Math.round(gain.gain)}`;
      if (!gain.gain) score.className='flat'; li.append(label,score);$('gains').append(li);
    }
  }
  $('history').replaceChildren(); $('sparkline').replaceChildren();
  if (!recent.length) { $('history').textContent='No scores yet.'; return; }
  const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox','0 0 360 90');
  svg.setAttribute('role','img');svg.setAttribute('aria-label',`Last ${recent.length} overall scores: ${recent.map(s=>s.overall.score).join(', ')}`);
  const points=sparklinePoints(recent.map(s=>s.overall.score));
  const line=document.createElementNS(ns,'polyline');line.setAttribute('points',points.map(p=>p.join(',')).join(' '));svg.append(line);
  for (const [x,y] of points) { const c=document.createElementNS(ns,'circle'); c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r',3);svg.append(c); }
  $('sparkline').append(svg);
  for (const s of recent) {
    const li=document.createElement('li'),link=document.createElement('a'),label=document.createElement('span'),score=document.createElement('span');
    link.href=`card.html?id=${encodeURIComponent(s.id)}`;
    const date=new Date(s.created_at);
    label.textContent=`${Number.isFinite(date.getTime())?date.toLocaleString():s.created_at} · ${s.side==='no'?'NO':'YES'}`;
    score.textContent=`${s.overall.score} / 100 · ${s.overall.letter}`;link.append(label,score);li.append(link);$('history').append(li);
  }
}
async function load() {
  $('reload').hidden=true;$('content').hidden=true;$('status').textContent='Loading your speeches…';
  try {
    me=await api('/api/me');$('prompt').replaceChildren(new Option('All prompts',''));
    for (const p of me.prompts ?? []) $('prompt').add(new Option(p.text,p.id));
    $('status').textContent=me.demo ? 'LOCAL PREVIEW · Synthetic audio and sample reports. Connect Supabase to see your own speeches.' : `Your speeches · ${me.display_name ?? 'player'}`;
    $('status').classList.toggle('demo-notice',Boolean(me.demo));
    $('content').hidden=false;draw();
  } catch(error) { me=null;$('status').textContent=error.message;$('reload').hidden=false; }
}
$('prompt').addEventListener('change',draw);$('reload').addEventListener('click',load);
document.fonts?.ready.then(draw);
load();
