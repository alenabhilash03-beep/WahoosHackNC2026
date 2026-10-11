import { WALK_SECONDS, showHome, walkToStage } from "./js/arena.js";
import {requireAccount} from './js/auth.js';
import { addSoundToggle, sfx, startMusic, stopMusic, wireSounds } from "./js/sound.js";

// Lobby music starts with the first press anywhere (browsers block sound before that).
wireSounds();
addSoundToggle({ music: true });
startMusic();

// ARGUE: the speaker walks down the aisle and up the stage steps, then the
// practice screen opens on the same frame (see practice.html, data-arena="stage").
const argue = document.getElementById("argue");
let walking = false;

argue.addEventListener("click", async (event) => {
  // Let modified clicks (new tab, new window) behave like a normal link.
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (walking) return;
  try {
    if (!await requireAccount('practice.html')) return;
  } catch (error) {
    document.querySelector('.hint').textContent = error.message;
    return;
  }
  walking = true;
  document.body.classList.add("walking");
  sfx.confirm();
  stopMusic(1.5);
  // Animation frames pause in a hidden tab; the timer makes sure the page still moves on.
  const fallback = new Promise((resolve) => setTimeout(resolve, WALK_SECONDS * 1000 + 1500));
  await Promise.race([walkToStage(), fallback]);
  window.location.href = argue.href;
});

// Coming back with the browser's Back button restores this page from memory,
// mid-transition. Put the speaker back in the aisle.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  walking = false;
  document.body.classList.remove("walking");
  showHome();
  startMusic();
});
