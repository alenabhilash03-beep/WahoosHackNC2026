import {addSoundToggle, sfx, unlock, wireSounds} from './js/sound.js';

wireSounds();
const soundButton = addSoundToggle();
let arena;
let entering = false;
let applauseTimer;
const status = document.getElementById('intro-status');
const loading = document.getElementById('intro-loading');

// Keep the two real links usable even when WebGL or the Three.js CDN fails.
const loadingTimeout = setTimeout(() => {
  loading.hidden = true;
  status.textContent = 'The stage is taking a moment. You can still choose a mode.';
}, 12000);
import('./js/arena.js').then(module => {
  arena = module;
  document.body.classList.add('intro-ready');
  status.textContent = 'The floor is yours. Choose a mode to begin.';
}).catch(() => {
  status.textContent = 'The 3D stage is unavailable. Choose a mode to continue.';
}).finally(() => {
  clearTimeout(loadingTimeout);
  loading.hidden = true;
});

// Browser audio starts only after a press; the audience already cheers visually.
function welcomeApplause() {
  if (entering || document.hidden) return;
  sfx.applause(4);
}
function startApplause() {
  clearInterval(applauseTimer);
  welcomeApplause();
  applauseTimer = setInterval(welcomeApplause, 7000);
}
for (const type of ['pointerdown', 'keydown']) {
  document.addEventListener(type, startApplause, {once: true});
}
soundButton.addEventListener('click', welcomeApplause);

for (const link of document.querySelectorAll('[data-mode]')) {
  link.addEventListener('click', async event => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (entering) return;
    entering = true;
    unlock();
    clearInterval(applauseTimer);
    document.body.classList.add('dropping');
    document.querySelector('.menu').inert = true;
    status.textContent = `Opening ${link.dataset.mode === 'daily' ? 'Daily Prompt' : 'Solo Speak'}…`;
    sfx.confirm();
    sfx.micDrop();
    // The timeout also navigates if animation frames stop in a hidden tab.
    if (arena) await Promise.race([
      arena.dropMic(),
      new Promise(resolve => setTimeout(resolve, 2300)),
    ]);
    window.location.assign(link.href);
  });
}

window.addEventListener('pagehide', () => clearInterval(applauseTimer));
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  entering = false;
  document.body.classList.remove('dropping');
  document.querySelector('.menu').inert = false;
  arena?.showIntro();
  status.textContent = 'The floor is yours. Choose a mode to begin.';
});
