// Every sound in the game, synthesised with Web Audio: no files to ship or license.
//   music        a chiptune loop for the lobby
//   sfx.*        clicks, hovers, chimes, countdown beeps, applause, grade fanfares
//   wireSounds   click and hover sounds for every button, link and menu on a page
//   addSoundToggle  the ♪ ON / OFF button; the choice is remembered
// Browsers only allow sound after the player presses something on the page, so
// unlock() runs on the first press and everything before that is silent.
const STORE_KEY = 'arena:sound';
let context = null;
let master = null;
let musicBus = null;
let musicTimer = 0;
let musicWanted = false;

export function soundOn() {
  try { return localStorage.getItem(STORE_KEY) !== 'off'; } catch { return true; }
}

function audio() {
  if (!context) {
    context = new AudioContext();
    master = context.createGain();
    master.gain.value = soundOn() ? 1 : 0;
    master.connect(context.destination);
  }
  if (context.state === 'suspended') context.resume();
  return context;
}

/** Call from a press: lets the page make sound from now on. */
export function unlock() {
  try { audio(); } catch { /* no Web Audio: the game stays silent */ }
  if (musicWanted && soundOn()) startMusic();
}

const ready = () => context && context.state === 'running' && soundOn();
const hz = (midi) => 440 * 2 ** ((midi - 69) / 12);

/** One note: oscillator through an envelope into a bus. */
function tone({ freq, type = 'square', at = 0, dur = 0.1, vol = 0.1, slide, vibrato = 0, bus = master, filter }) {
  const start = context.currentTime + at;
  const osc = context.createOscillator();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  if (slide) osc.frequency.exponentialRampToValueAtTime(slide, start + dur);
  if (vibrato) {
    const lfo = context.createOscillator();
    const depth = context.createGain();
    lfo.frequency.value = 5.5;
    depth.gain.value = vibrato;
    lfo.connect(depth).connect(osc.frequency);
    lfo.start(start);
    lfo.stop(start + dur + 0.05);
  }
  const env = context.createGain();
  env.gain.setValueAtTime(0, start);
  env.gain.linearRampToValueAtTime(vol, start + 0.008);
  env.gain.setValueAtTime(vol, start + dur * 0.7);
  env.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  let node = osc.connect(env);
  if (filter) {
    const lowpass = context.createBiquadFilter();
    lowpass.type = 'lowpass';
    lowpass.frequency.value = filter;
    node = env.connect(lowpass);
  }
  node.connect(bus);
  osc.start(start);
  osc.stop(start + dur + 0.02);
}

let noiseBuffer = null;
function noise({ at = 0, dur = 0.05, vol = 0.1, band = 4000, q = 0.8, bus = master }) {
  if (!noiseBuffer) {
    noiseBuffer = context.createBuffer(1, Math.floor(context.sampleRate * 0.5), context.sampleRate);
    const data = noiseBuffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  }
  const start = context.currentTime + at;
  const source = context.createBufferSource();
  source.buffer = noiseBuffer;
  const filter = context.createBiquadFilter();
  filter.type = 'bandpass';
  filter.frequency.value = band;
  filter.Q.value = q;
  const env = context.createGain();
  env.gain.setValueAtTime(0, start);
  env.gain.linearRampToValueAtTime(vol, start + 0.003);
  env.gain.exponentialRampToValueAtTime(0.0001, start + dur);
  source.connect(filter).connect(env).connect(bus);
  source.start(start);
  source.stop(start + dur + 0.02);
}

// ------------------------------------------------------------------ effects
let lastHover = 0;
export const sfx = {
  click() { if (!ready()) return; tone({ freq: 880, dur: 0.05, vol: 0.06 }); tone({ freq: 1320, at: 0.03, dur: 0.05, vol: 0.04 }); },
  hover() {
    if (!ready() || context.currentTime - lastHover < 0.06) return;
    lastHover = context.currentTime;
    tone({ freq: 1500, type: 'triangle', dur: 0.03, vol: 0.025 });
  },
  select() { if (!ready()) return; tone({ freq: 660, dur: 0.06, vol: 0.06 }); tone({ freq: 990, at: 0.06, dur: 0.08, vol: 0.06 }); },
  confirm() { if (!ready()) return; [72, 76, 79, 84].forEach((n, i) => tone({ freq: hz(n), at: i * 0.06, dur: 0.12, vol: 0.06 })); },
  reveal() {
    if (!ready()) return;
    tone({ freq: hz(84), type: 'triangle', dur: 0.7, vol: 0.12 });
    tone({ freq: hz(91), type: 'triangle', at: 0.12, dur: 0.8, vol: 0.1 });
  },
  tick() { if (!ready()) return; tone({ freq: hz(74), dur: 0.1, vol: 0.08 }); },
  go() { if (!ready()) return; tone({ freq: hz(86), dur: 0.35, vol: 0.09 }); tone({ freq: hz(93), type: 'triangle', dur: 0.4, vol: 0.07 }); },
  micDrop() {
    if (!ready()) return;
    noise({at: 0.42, dur: 0.3, vol: 0.05, band: 2400, q: 0.4});
    tone({freq: 160, type: 'sine', at: 1.35, dur: 0.2, vol: 0.15, slide: 45});
    noise({at: 1.35, dur: 0.12, vol: 0.12, band: 900, q: 0.6});
  },

  /** A crowd of virtual clappers, full for 40% of the time, then fading out. */
  applause(seconds = 3) {
    if (!ready()) return;
    const bus = context.createGain();
    const now = context.currentTime + 0.03;
    bus.gain.setValueAtTime(0.6, now);
    bus.gain.setValueAtTime(0.6, now + seconds * 0.4);
    bus.gain.exponentialRampToValueAtTime(0.0001, now + seconds);
    bus.connect(master);
    for (let clapper = 0; clapper < 18; clapper++) {
      const rate = 3.5 + Math.random() * 1.8;
      const band = 900 + Math.random() * 1500;
      const vol = 0.25 + Math.random() * 0.35;
      for (let at = Math.random() / rate; at < seconds; at += (0.85 + Math.random() * 0.3) / rate) {
        noise({ at, dur: 0.06, vol, band, q: 1.1, bus });
      }
    }
  },

  /** A: big victory fanfare. B: a smaller one. C: a neutral jingle. D/F: the trumpet of defeat. */
  grade(letter) {
    if (!ready()) return;
    const tier = String(letter || '').charAt(0);
    if (tier === 'A' || tier === 'B') {
      const loud = tier === 'A';
      const vol = loud ? 0.16 : 0.07;
      // Da-da-da DAAA: G, G, G, then a held C major chord.
      [67, 67, 67].forEach((n, i) => tone({ freq: hz(n + 5), at: i * 0.13, dur: 0.11, vol }));
      for (const n of loud ? [72, 76, 79, 84] : [72, 76, 79]) tone({ freq: hz(n), at: 0.4, dur: loud ? 1.4 : 0.9, vol: vol * 0.8, vibrato: 3 });
      tone({ freq: hz(48), type: 'triangle', at: 0.4, dur: loud ? 1.4 : 0.9, vol: vol * 1.2 });
      if (loud) {
        noise({ at: 0.4, dur: 1.2, vol: 0.12, band: 7000, q: 0.5 });             // cymbal
        [84, 88, 91, 96].forEach((n, i) => tone({ freq: hz(n), type: 'triangle', at: 0.45 + i * 0.07, dur: 0.25, vol: 0.05 }));
      }
    } else if (tier === 'C') {
      [72, 76, 79].forEach((n, i) => tone({ freq: hz(n), type: 'triangle', at: i * 0.12, dur: 0.2, vol: 0.07 }));
    } else {
      // Wah, wah, wah, waaah: a muted trombone sliding down.
      [[58, 0.35], [57, 0.35], [56, 0.35], [55, 1.2]].reduce((at, [n, dur]) => {
        tone({ freq: hz(n), type: 'sawtooth', at, dur, vol: 0.12, filter: 900, vibrato: dur > 1 ? 6 : 0,
               slide: dur > 1 ? hz(n - 0.6) : undefined });
        return at + dur + 0.05;
      }, 0);
    }
  },
};

// ------------------------------------------------------------------ lobby music
// 120 BPM in sixteenth notes; four bars of C, Am, F, G, looping every 8 seconds.
const STEP = 0.125;
const MELODY = [
  72, 0, 76, 0, 79, 0, 76, 0, 77, 0, 76, 0, 74, 0, 72, 0,
  69, 0, 72, 0, 76, 0, 72, 0, 74, 0, 72, 0, 71, 0, 69, 0,
  65, 0, 69, 0, 72, 0, 69, 0, 72, 0, 74, 0, 76, 0, 77, 0,
  79, 0, 0, 0, 77, 0, 76, 0, 74, 0, 0, 0, 71, 0, 0, 0,
];
const ROOTS = [36, 45, 41, 43];
let musicStep = 0;
let musicClock = 0;

function scheduleMusic() {
  while (musicClock < context.currentTime + 0.25) {
    const at = musicClock - context.currentTime;
    const step = musicStep % 64;
    if (MELODY[step]) tone({ freq: hz(MELODY[step]), at: Math.max(0, at), dur: 0.2, vol: 0.05, bus: musicBus });
    if (step % 2 === 0) {
      const root = ROOTS[Math.floor(step / 16)] + (step % 4 === 2 ? 12 : 0);
      tone({ freq: hz(root), type: 'triangle', at: Math.max(0, at), dur: 0.22, vol: 0.12, bus: musicBus });
    }
    if (step % 8 === 0) tone({ freq: 110, type: 'sine', at: Math.max(0, at), dur: 0.15, vol: 0.18, slide: 45, bus: musicBus });
    if (step % 4 === 2) noise({ at: Math.max(0, at), dur: 0.04, vol: 0.05, band: 8000, bus: musicBus });
    musicStep++;
    musicClock += STEP;
  }
}

/** Start the lobby loop (waits for the first press if sound is still locked). */
export function startMusic() {
  musicWanted = true;
  if (!context || context.state !== 'running' || !soundOn() || musicTimer) return;
  musicBus = context.createGain();
  musicBus.gain.setValueAtTime(0.0001, context.currentTime);
  musicBus.gain.exponentialRampToValueAtTime(0.7, context.currentTime + 1);
  musicBus.connect(master);
  musicStep = 0;
  musicClock = context.currentTime + 0.05;
  scheduleMusic();
  musicTimer = setInterval(scheduleMusic, 60);
}

/** Fade the lobby loop out over `fade` seconds. */
export function stopMusic(fade = 1) {
  musicWanted = false;
  if (!musicTimer) return;
  clearInterval(musicTimer);
  musicTimer = 0;
  const bus = musicBus;
  bus.gain.cancelScheduledValues(context.currentTime);
  bus.gain.setValueAtTime(Math.max(bus.gain.value, 0.0001), context.currentTime);
  bus.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + fade);
  setTimeout(() => bus.disconnect(), (fade + 0.3) * 1000);
}

// ------------------------------------------------------------------ page wiring
const PRESSABLE = 'button, a[href], select, summary, input[type=radio], input[type=checkbox]';

/** Click and hover sounds for every control on the page. Mark an element data-silent to skip it. */
export function wireSounds(root = document) {
  for (const type of ['pointerdown', 'keydown']) root.addEventListener(type, unlock, { capture: true });
  root.addEventListener('click', (event) => {
    const control = event.target.closest?.(PRESSABLE);
    if (control && !control.closest('[data-silent]') && control.tagName !== 'SELECT') sfx.click();
  }, { capture: true });
  root.addEventListener('change', (event) => { if (event.target.tagName === 'SELECT') sfx.select(); }, { capture: true });
  root.addEventListener('pointerover', (event) => {
    const control = event.target.closest?.(PRESSABLE);
    if (control && !control.contains(event.relatedTarget) && !control.closest('[data-silent]')) sfx.hover();
  });
}

/** The ♪ ON / OFF switch in the corner. With music: true it also controls the lobby loop. */
export function addSoundToggle({ music = false } = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'sound-toggle';
  button.dataset.silent = '';
  const show = () => {
    button.textContent = soundOn() ? '♪ ON' : '♪ OFF';
    button.setAttribute('aria-label', soundOn() ? 'Sound on. Turn sound off' : 'Sound off. Turn sound on');
  };
  button.addEventListener('click', () => {
    const on = !soundOn();
    try { localStorage.setItem(STORE_KEY, on ? 'on' : 'off'); } catch { /* not remembered */ }
    unlock();
    if (master) master.gain.setTargetAtTime(on ? 1 : 0, context.currentTime, 0.05);
    if (music) { if (on) startMusic(); else stopMusic(0.3); }
    if (on) sfx.click();
    show();
  });
  show();
  const style = document.createElement('style');
  style.textContent = `.sound-toggle { position: fixed; top: 10px; right: 10px; z-index: 5; font: 9px/1 "Press Start 2P", monospace;
    color: #f4ead2; background: rgba(28, 15, 23, 0.85); border: 0; padding: 9px 10px; cursor: pointer;
    box-shadow: 0 0 0 2px #000, 0 3px 0 #000; } .sound-toggle:hover { color: #f2b84b; }
    .sound-toggle:focus-visible { outline: 3px solid #f2b84b; outline-offset: 3px; }`;
  document.head.append(style);
  // Join the account strip when there is one, so the two never overlap.
  const strip = document.querySelector('.account-nav');
  (strip && !strip.hidden ? strip : document.body).append(button);
  return button;
}
