// The practice round, on stage:
//   setup     pick a difficulty and a category (or type your own)
//   walk      the speaker walks to the podium while the question is written; pick a side
//   countdown 5 seconds through the speaker's eyes, gazing across the audience
//   speaking  up to 60 seconds, with a time bar that turns from white to red
//   applause  applause while the speech uploads; the speech card fades in as it dies away
import {api} from './api.js';
import {createRecording} from './recorder.js';
import {addSoundToggle, sfx, unlock, wireSounds} from './sound.js';
import {pickCategories} from './topics.js';
import {cheer, pauseRendering, startPov, walkToPodium} from './arena.js';
import {communityEnabled, roundState, closingLine, RETENTION_NOTICE} from './community-model.js';

const COUNTDOWN_SECONDS = 5;
const SPEECH_SECONDS = 60;
const APPLAUSE_SECONDS = 3.2;        // full for a moment, then fading as the stats come up
const STATS_AFTER_SECONDS = 1.2;     // when the stats screen starts to fade in over the stage
const STATS_FADE_SECONDS = 1.6;
const OWN = '__own';
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const dailyMode = params.get('mode') === 'daily';
// With community rounds on, the Daily Prompt is the community round: the same question, and
// the recording can be uploaded to the community board from its card. Otherwise it is private.
let communityMode = params.get('mode') === 'community' || (dailyMode && communityEnabled());
let communityRound=null;
$('community-practice').hidden=!communityEnabled();
if (communityMode) {
  document.title='Community Prompt · SpeakUp';
  $('setup-title').textContent='CURRENT COMMUNITY PROMPT';
  document.querySelector('.lede').textContent='Choose your side and speak for up to 60 seconds. Sharing is your choice after grading.';
  $('levels').hidden=true;
  $('round-leave').href='practice.html?mode=community';
}
if (dailyMode) {
  document.title = 'Daily Prompt · SpeakUp';
  $('setup-title').textContent = 'DAILY PROMPT';
  document.querySelector('.lede').textContent = communityMode
    ? 'The question of the day. Choose your side and make your case in 60 seconds. Afterwards you can upload it to the community board.'
    : 'The question of the day. Choose your side, then make your case in 60 seconds.';
  $('levels').hidden = true;
  $('round-leave').href = 'practice.html?mode=daily';
}

const round = {difficulty: null, motion: null, side: null, atPodium: false, started: false, recorded: null, speechStart: 0};

function phase(name) { document.body.dataset.phase = name; }
function say(text) { $('round-status').textContent = text; }

// ------------------------------------------------------------------ setup
async function checkService() {
  $('reload').hidden = true;
  $('status').textContent = 'Checking the grading service…';
  try {
    if(communityMode) {
      if(!communityEnabled()) throw new Error('Community recording is not available yet. Choose private practice in the arena.');
      const data=await api('/api/community/round');
      communityRound=data.round;
      if(roundState(communityRound)==='active') {
        if(!communityRound.prompt?.id || !communityRound.prompt?.text) throw new Error('The community prompt is unavailable. Retry.');
        // Each account gets a few recordings per round. Say so before a minute is spent speaking.
        const attempts=data.attempts;
        if(attempts && attempts.used>=attempts.limit) {
          $('status').textContent=`You have used all ${attempts.limit} recordings for today's prompt. Upload one from YOUR PROGRESS, or come back for the next prompt.`;
          return;
        }
        $('status').textContent=attempts
          ? `Recording ${attempts.used+1} of ${attempts.limit} for today's prompt · private until you upload it.`
          : 'Community prompt · private until you explicitly share.';
        $('community-notice').hidden=false;
        $('community-retention').textContent=RETENTION_NOTICE;
        $('community-round-closing').textContent=closingLine(communityRound);
        begin(communityRound.prompt,null);return;
      }
      if(!dailyMode) { $('status').textContent=closingLine(communityRound);$('reload').hidden=false;return; }
      // No round is open: the Daily Prompt still works as private practice.
      communityMode=false;
    }
    const prompts = await api('/api/prompts');
    if (globalThis.ARENA_CONFIG?.DEMO_MODE) {
      $('status').textContent = 'LOCAL PREVIEW · Saved examples only. Run the server in local mode to play.';
      $('preview').hidden = false;
      return;
    }
    // Only local mode keeps everything on this computer; the deployed server stores each
    // recording privately for its player.
    $('status').textContent = globalThis.ARENA_CONFIG?.LOCAL_MODE
      ? 'Private practice. Your recording and report stay on this computer.'
      : 'Private practice. Your recording and report are saved to your account and visible only to you.';
    // "Retry this prompt" from a speech card: skip the picker, same question and side.
    const retry = prompts.find(p => p.id === params.get('motion_id'));
    if (retry) {
      begin({id: retry.id, text: retry.text}, ['yes', 'no'].includes(params.get('side')) ? params.get('side') : null);
    } else if (dailyMode) {
      const daily = prompts.find(p => p.featured);
      if (daily) begin(daily, null);
      else {
        $('status').textContent = 'No daily prompt has been published yet. Try again later, or choose Solo Speak in the arena.';
        $('reload').hidden = false;
      }
    } else $('levels').disabled = false;
  } catch (error) {
    $('status').textContent = error.message;
    $('reload').hidden = false;
  }
}

function fillCategories() {
  const select = $('category');
  select.replaceChildren(new Option('Choose a category…', ''));
  for (const name of pickCategories(5)) select.add(new Option(name, name));
  select.add(new Option('Your own…', OWN));
  select.value = '';
  $('own').hidden = true;
}

for (const button of document.querySelectorAll('.level')) {
  button.addEventListener('click', () => {
    round.difficulty = button.dataset.level;
    for (const other of document.querySelectorAll('.level')) other.setAttribute('aria-pressed', String(other === button));
    if ($('category-step').hidden) { fillCategories(); $('category-step').hidden = false; }
    $('category').focus();
  });
}

$('shuffle').addEventListener('click', fillCategories);
$('category').addEventListener('change', () => {
  const value = $('category').value;
  $('own').hidden = value !== OWN;
  if (value === OWN) $('own-text').focus();
  else if (value) begin(null, null, value);
});
$('own-go').addEventListener('click', () => {
  const text = $('own-text').value.trim();
  if (text.length < 2) { $('own-text').focus(); return; }
  begin(null, null, text);
});
$('own-text').addEventListener('keydown', event => { if (event.key === 'Enter') $('own-go').click(); });

// ------------------------------------------------------------------ the round
/** Start the round with a known motion (retry) or a category to write a question for. */
function begin(motion, side, category) {
  if (round.started) return;
  round.started = true;
  phase('walk');
  $('round').hidden = false;
  $('question-meta').textContent = category ? `${round.difficulty.toUpperCase()} · ${category.toUpperCase()}` : dailyMode ? 'DAILY PROMPT' : communityMode ? 'COMMUNITY PROMPT' : 'THE QUESTION';
  // The speaker walks on while the question is written; both take about 2 seconds.
  walkToPodium().then(() => { round.atPodium = true; maybeStartCountdown(); });
  if (motion) showQuestion(motion, side);
  else writeQuestion(category);
}

async function writeQuestion(category) {
  $('question').textContent = 'Writing your question…';
  $('round-retry').hidden = true;
  say('');
  try {
    const motion = await api('/api/questions', {method: 'POST', body: {category, difficulty: round.difficulty}});
    showQuestion(motion, null);
  } catch (error) {
    $('question').textContent = 'No question yet';
    say(error.message);
    $('round-leave').hidden = false;
    $('round-retry').hidden = false;
    $('round-retry').onclick = () => writeQuestion(category);
  }
}

function showQuestion(motion, side) {
  round.motion = motion;
  $('question').textContent = motion.text;
  $('question-card').classList.add('revealed');
  sfx.reveal();
  if (side) chooseSide(side);
  else { $('side-pick').hidden = false; say('Choose the side you will argue.'); }
}

function chooseSide(side) {
  unlock();
  round.side = side;
  sfx.confirm();
  $('side-pick').hidden = true;
  $('side-chosen').hidden = false;
  $('side-chosen').textContent = `YOU ARGUE ${side.toUpperCase()}`;
  maybeStartCountdown();
}
for (const button of document.querySelectorAll('.side')) button.addEventListener('click', () => chooseSide(button.dataset.side));

function maybeStartCountdown() {
  if (!round.atPodium || !round.motion || !round.side || recorder.state !== 'idle' || round.recorded) return;
  if(communityMode && roundState(communityRound)!=='active') {say('This round has closed. Start again with the next community prompt.');$('round-leave').hidden=false;return;}
  say('Allow the microphone to begin.');
  recorder.start();
}

// ------------------------------------------------------------------ countdown and speech
let barFrame = 0;
const recorder = createRecording({
  countdownMs: COUNTDOWN_SECONDS * 1000,
  durationMs: SPEECH_SECONDS * 1000,
  onUpdate({state, seconds, error}) {
    if (error) {
      phase('walk');
      $('countdown').hidden = true;
      say(error);
      $('round-retry').hidden = false;
      $('round-retry').onclick = () => { $('round-retry').hidden = true; maybeStartCountdown(); };
      return;
    }
    if (state === 'countdown') {
      if (document.body.dataset.phase !== 'countdown') { phase('countdown'); startPov(COUNTDOWN_SECONDS); say(''); }
      $('countdown').hidden = false;
      const number = String(Math.max(1, seconds));
      if ($('countdown').textContent !== number) { $('countdown').textContent = number; sfx.tick(); }
    } else if (state === 'recording') {
      if (document.body.dataset.phase !== 'speaking') {
        phase('speaking');
        $('countdown').hidden = true;
        $('speaking').hidden = false;
        $('timebar').hidden = false;
        round.speechStart = performance.now();
        sfx.go();
        barFrame = requestAnimationFrame(drawBar);
      }
      $('clock').textContent = String(seconds);
    }
  },
  onCaptured(blob) {
    cancelAnimationFrame(barFrame);
    round.recorded = blob;
    finishSpeech();
  },
});

/** The time bar fills over the speech and turns from white to red as time runs out. */
function drawBar() {
  const u = Math.min(1, (performance.now() - round.speechStart) / (SPEECH_SECONDS * 1000));
  const red = u * u;                         // stays light for most of the minute
  const fill = $('timebar-fill');
  fill.style.width = `${u * 100}%`;
  fill.style.background = `rgb(${Math.round(255 - 55 * red)}, ${Math.round(255 - 205 * red)}, ${Math.round(255 - 205 * red)})`;
  if (u < 1) barFrame = requestAnimationFrame(drawBar);
}

$('finish').addEventListener('click', () => recorder.stop());

// ------------------------------------------------------------------ applause and upload
async function finishSpeech() {
  phase('applause');
  $('speaking').hidden = true;
  say('');
  round.applauseStart = performance.now();
  sfx.applause(APPLAUSE_SECONDS);
  cheer(APPLAUSE_SECONDS);
  const settle = new Promise(resolve => setTimeout(resolve, STATS_AFTER_SECONDS * 1000));
  try {
    const [result] = await Promise.all([upload(), settle]);
    showStats(`card.html?id=${encodeURIComponent(result.speech_id)}`);
  } catch (error) {
    await settle;
    phase('walk');
    say(`${error.message} Your speech is kept on this page.`);
    $('round-retry').hidden = false;
    $('round-retry').onclick = () => { $('round-retry').hidden = true; finishSpeech(); };
  }
}

/** Fade the speech card in over the stage while the applause dies away, so the
 *  sound carries across. Once it is fully shown, the address becomes the card's. */
function showStats(url) {
  const frame = document.createElement('iframe');
  frame.className = 'stats-frame';
  frame.title = 'Your speech card';
  frame.src = url;
  frame.style.setProperty('--fade', `${STATS_FADE_SECONDS}s`);
  frame.addEventListener('load', () => {
    requestAnimationFrame(() => frame.classList.add('shown'));
    setTimeout(() => {
      pauseRendering();
      recorder.destroy();
      history.replaceState(null, '', url);
      document.title = frame.contentDocument?.title || document.title;
      frame.focus();
    }, STATS_FADE_SECONDS * 1000);
  }, {once: true});
  document.body.append(frame);
}

async function upload() {
  if(communityMode && roundState(communityRound)!=='active') throw new Error('This community round has closed. Start a new recording for the next prompt.');
  const blob = round.recorded;
  if (blob.size > 15 * 1024 * 1024) throw new Error('The recording exceeds 15 MB.');
  const name = blob.type.includes('mp4') ? 'recording.mp4' : blob.type.includes('ogg') ? 'recording.ogg' : 'recording.webm';
  const form = new FormData();
  form.append('motion_id', round.motion.id);
  form.append('side', round.side);
  if(communityMode) form.append('community_round_id',communityRound.id);
  form.append('audio', blob, name);
  const result = await api('/api/speeches', {method: 'POST', body: form});
  if (!result.speech_id) throw new Error('The server returned no speech id.');
  return result;
}

wireSounds();
addSoundToggle();
// The speech card fades in inside this page; it asks us to play the grade fanfare,
// because this page already has permission to make sound.
// It waits for the applause to die down, even when a cached grade comes back at once.
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.data?.type !== 'arena:grade') return;
  const wait = round.applauseStart ? Math.max(0, round.applauseStart + APPLAUSE_SECONDS * 900 - performance.now()) : 0;
  setTimeout(() => sfx.grade(event.data.letter), wait);
});
$('reload').addEventListener('click', checkService);
const communityClock=communityMode ? setInterval(()=>{
  if(!communityRound || document.hidden) return;
  $('community-round-closing').textContent=closingLine(communityRound);
  if(roundState(communityRound)!=='active') {
    for(const button of document.querySelectorAll('.side')) button.disabled=true;
    say('This community round has closed. Start again with the next prompt.');$('round-leave').hidden=false;
  }
},1000) : null;
window.addEventListener('pagehide', () => {recorder.destroy();clearInterval(communityClock);});
checkService();
