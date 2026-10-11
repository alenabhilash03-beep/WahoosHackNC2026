import {api} from './api.js';
import {LEVELS,boardFor,nameProblem,scoreLine,youLine} from './leaderboard-model.js';
import {mountCommunityBoard} from './community-board.js';

const $=id=>document.getElementById(id);
const REFRESH_MS=30000;
let saving=false,latest=null;
const LEVEL_KEY='arena:board-level';
let level=(()=>{ try { const saved=localStorage.getItem(LEVEL_KEY); return LEVELS.includes(saved) ? saved : 'beginner'; } catch { return 'beginner'; } })();
function showTabs() {
  for (const tab of document.querySelectorAll('[data-level]')) {
    const selected=tab.dataset.level===level;
    tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected ? 0 : -1;
  }
  $('board-title').textContent=`TOP SPEAKERS · ${level.toUpperCase()}`;
}
function choose(next) {
  level=next;
  try { localStorage.setItem(LEVEL_KEY,level); } catch { /* not remembered */ }
  showTabs();
  if (latest) draw(boardFor(latest,level));
}
for (const tab of document.querySelectorAll('[data-level]')) {
  tab.addEventListener('click',()=>choose(tab.dataset.level));
  // Arrow keys move between the tabs, as in any tab list.
  tab.addEventListener('keydown',event=>{
    const step=event.key==='ArrowRight' ? 1 : event.key==='ArrowLeft' ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next=LEVELS[(LEVELS.indexOf(level)+step+LEVELS.length)%LEVELS.length];
    choose(next);document.querySelector(`[data-level="${next}"]`).focus();
  });
}
showTabs();
mountCommunityBoard({api});
function draw(board) {
  $('you').textContent=board.demo ? 'LOCAL PREVIEW · Sample scores. Connect Supabase to join the board.' : youLine(board);
  $('you').classList.toggle('demo-notice',Boolean(board.demo));
  $('name-form').hidden=Boolean(board.demo);
  $('name-label').textContent=board.display_name ? 'CHANGE YOUR NAME' : 'NAME ON THE BOARD';
  $('save').textContent=board.display_name ? 'CHANGE NAME' : 'JOIN THE BOARD';
  // Don't overwrite a name the player is typing when the board refreshes.
  if (document.activeElement!==$('name') && !saving) $('name').value=board.display_name ?? '';
  $('board').replaceChildren();
  if (!board.rows.length) { $('board').textContent=`Nobody is on the ${level} board yet. Be the first.`; return; }
  const rows=board.you && !board.rows.some(row=>row.you) ? [...board.rows,board.you] : board.rows;
  for (const row of rows) {
    const li=document.createElement('li'),place=document.createElement('span'),name=document.createElement('span'),score=document.createElement('span');
    place.className='place';place.textContent=`#${row.place}`;
    name.className='who';name.textContent=row.you ? `${row.display_name} (you)` : row.display_name;
    score.textContent=scoreLine(row);
    if (row.you) li.className='you';
    li.append(place,name,score);$('board').append(li);
  }
}
async function load(quiet=false) {
  if (!quiet) { $('reload').hidden=true;$('content').hidden=true;$('status').textContent='Loading the board…'; }
  try {
    latest=await api('/api/leaderboard');
    $('status').textContent='Best score per speaker at each difficulty · updates every 30 seconds';
    $('reload').hidden=true;$('content').hidden=false;draw(boardFor(latest,level));
  } catch(error) {
    if (quiet) return;               // keep showing the last board; the next refresh retries
    $('status').textContent=error.message;$('reload').hidden=false;
  }
}
$('name-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if (saving) return;
  const problem=nameProblem($('name').value);
  $('name-error').textContent=problem;
  if (problem) { $('name').focus();return; }
  saving=true;$('save').disabled=true;
  try {
    await api('/api/me/name',{method:'POST',body:{display_name:$('name').value.trim()}});
    saving=false;await load(true);
  } catch(error) { $('name-error').textContent=error.message; }
  finally { saving=false;$('save').disabled=false; }
});
$('reload').addEventListener('click',()=>load());
setInterval(()=>{ if (!document.hidden) load(true); },REFRESH_MS);
load();
