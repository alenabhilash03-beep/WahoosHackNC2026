import * as THREE from "three";

// ---------------------------------------------------------------------------
// Renderer: draw at low resolution, let CSS upscale with nearest-neighbour.
// TARGET_ROWS is roughly how many "pixels" tall the screen is.
// ---------------------------------------------------------------------------
const TARGET_ROWS = 200;

const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
renderer.setPixelRatio(1);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.BasicShadowMap;

const scene = new THREE.Scene();
const BG = new THREE.Color("#0d0b14");
scene.background = BG;
scene.fog = new THREE.Fog(BG, 12, 30);

const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
const CAMERA_POS = new THREE.Vector3(0, 3.1, 9.6);
const CAMERA_LOOK = new THREE.Vector3(0, 1.35, -3);
camera.position.copy(CAMERA_POS);
camera.lookAt(CAMERA_LOOK);

function resize() {
  const scale = Math.max(2, Math.round(window.innerHeight / TARGET_ROWS));
  const w = Math.ceil(window.innerWidth / scale);
  const h = Math.ceil(window.innerHeight / scale);
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  // Keep the character framed on narrow (portrait) screens.
  camera.fov = camera.aspect < 0.9 ? 62 : 40;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

// ---------------------------------------------------------------------------
// Materials: every colour gets a tiny noisy texture so surfaces read as
// hand-placed pixels rather than flat plastic.
// ---------------------------------------------------------------------------
const rand = mulberry32(7);
const matCache = new Map();

function noiseTexture(hex, size = 8, amount = 0.07) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const base = new THREE.Color(hex);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const k = 1 + (rand() - 0.5) * 2 * amount;
      const col = base.clone().multiplyScalar(k);
      ctx.fillStyle = `#${col.getHexString()}`;
      ctx.fillRect(x, y, 1, 1);
    }
  }
  return pixelTexture(c);
}

function pixelTexture(canvasEl) {
  const t = new THREE.CanvasTexture(canvasEl);
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function mat(hex, { noise = 0.07, emissive } = {}) {
  const key = `${hex}|${noise}|${emissive ?? ""}`;
  if (!matCache.has(key)) {
    const m = new THREE.MeshLambertMaterial({ map: noiseTexture(hex, 8, noise) });
    if (emissive) m.emissive = new THREE.Color(emissive);
    matCache.set(key, m);
  }
  return matCache.get(key);
}

const boxGeo = new THREE.BoxGeometry(1, 1, 1);

/** Add a box of size (w,h,d) centred at (x,y,z) to parent. */
function box(parent, w, h, d, x, y, z, material, { shadow = true } = {}) {
  const m = new THREE.Mesh(boxGeo, typeof material === "string" ? mat(material) : material);
  m.scale.set(w, h, d);
  m.position.set(x, y, z);
  m.castShadow = shadow;
  m.receiveShadow = true;
  parent.add(m);
  return m;
}

// ---------------------------------------------------------------------------
// The player: a chunky voxel speaker in a navy suit.
// ---------------------------------------------------------------------------
const SKIN = "#e8b48a";
const SKIN_SHADE = "#c98f66";
const HAIR = "#6b4423";
const HAIR_LIGHT = "#8a5a30";
const SUIT = "#2b3a67";
const SUIT_DARK = "#1c2648";
const TROUSER = "#253159";
const SHIRT = "#f1ede4";
const TIE = "#c8423a";
const SHOE = "#2a1a12";

/** 16x16 pixel-art face for the front of the head. */
function faceTexture(blink) {
  const c = document.createElement("canvas");
  c.width = c.height = 16;
  const ctx = c.getContext("2d");
  const px = (x, y, col, w = 1, h = 1) => {
    ctx.fillStyle = col;
    ctx.fillRect(x, y, w, h);
  };
  px(0, 0, SKIN, 16, 16);
  // Fringe with an uneven edge.
  px(0, 0, HAIR, 16, 3);
  [0, 1, 2, 5, 6, 9, 13, 14, 15].forEach((x) => px(x, 3, HAIR));
  [0, 1, 14, 15].forEach((x) => px(x, 4, HAIR));
  [3, 7, 11].forEach((x) => px(x, 1, HAIR_LIGHT));
  // Eyebrows.
  px(3, 5, "#4a2e17", 3, 1);
  px(10, 5, "#4a2e17", 3, 1);
  if (blink) {
    px(3, 9, "#2a1a10", 3, 1);
    px(10, 9, "#2a1a10", 3, 1);
  } else {
    px(3, 7, "#ffffff", 3, 3);
    px(10, 7, "#ffffff", 3, 3);
    px(4, 7, "#2a1a10", 2, 3);
    px(10, 7, "#2a1a10", 2, 3);
    px(5, 7, "#ffffff");
    px(11, 7, "#ffffff");
  }
  // Nose shadow, cheeks, confident half-smile.
  px(7, 10, SKIN_SHADE, 2, 2);
  px(2, 11, "#e59a82", 2, 1);
  px(12, 11, "#e59a82", 2, 1);
  px(6, 13, "#6e2a24", 4, 1);
  px(10, 12, "#6e2a24");
  // Jaw shading.
  px(0, 15, SKIN_SHADE, 16, 1);
  return pixelTexture(c);
}

const faceOpen = faceTexture(false);
const faceBlink = faceTexture(true);

function buildSpeaker() {
  const root = new THREE.Group();
  const body = new THREE.Group(); // everything above the feet, for breathing/bob
  root.add(body);

  // Shoes.
  for (const s of [-1, 1]) {
    box(root, 0.32, 0.16, 0.5, 0.2 * s, 0.08, 0.06, SHOE);
    box(root, 0.3, 0.04, 0.12, 0.2 * s, 0.17, 0.25, "#4a3022"); // toe shine
  }
  // Legs.
  for (const s of [-1, 1]) {
    box(body, 0.3, 0.78, 0.32, 0.19 * s, 0.55, 0, TROUSER);
    box(body, 0.04, 0.6, 0.02, 0.19 * s, 0.55, 0.165, "#1d2749", { shadow: false }); // crease
  }

  // Torso / jacket.
  box(body, 0.88, 0.86, 0.48, 0, 1.33, 0, SUIT);
  box(body, 0.92, 0.12, 0.5, 0, 0.96, 0, SUIT_DARK); // hem
  box(body, 0.28, 0.44, 0.02, 0, 1.53, 0.245, SHIRT, { shadow: false });
  box(body, 0.1, 0.42, 0.02, 0, 1.46, 0.258, TIE, { shadow: false });
  box(body, 0.14, 0.08, 0.03, 0, 1.71, 0.258, "#a3322b", { shadow: false }); // knot
  for (const s of [-1, 1]) {
    const lapel = box(body, 0.1, 0.46, 0.03, 0.15 * s, 1.5, 0.252, SUIT_DARK, { shadow: false });
    lapel.rotation.z = 0.22 * s;
  }
  box(body, 0.06, 0.06, 0.02, 0, 1.19, 0.25, "#d9a640", { shadow: false }); // button
  box(body, 0.06, 0.06, 0.02, 0, 1.05, 0.25, "#d9a640", { shadow: false });
  box(body, 0.14, 0.06, 0.02, 0.27, 1.58, 0.25, SHIRT, { shadow: false }); // pocket square
  box(body, 0.84, 0.08, 0.44, 0, 1.79, 0, SHIRT); // collar
  box(body, 0.26, 0.1, 0.26, 0, 1.85, 0, SKIN); // neck

  // Arms hang from shoulder pivots so they can swing.
  const arms = [];
  for (const s of [-1, 1]) {
    const pivot = new THREE.Group();
    pivot.position.set(0.58 * s, 1.72, 0);
    box(pivot, 0.28, 0.76, 0.32, 0, -0.38, 0, SUIT);
    box(pivot, 0.3, 0.1, 0.34, 0, -0.03, 0, SUIT_DARK); // shoulder pad line
    box(pivot, 0.26, 0.07, 0.3, 0, -0.79, 0, SHIRT); // cuff
    box(pivot, 0.22, 0.2, 0.24, 0, -0.92, 0.01, SKIN);
    body.add(pivot);
    arms.push(pivot);
  }

  // Head with a pixel-art face on the front (+z) side only.
  const head = new THREE.Group();
  head.position.set(0, 1.9, 0);
  const skinMat = mat(SKIN, { noise: 0.04 });
  const faceMat = new THREE.MeshLambertMaterial({ map: faceOpen });
  const headMesh = new THREE.Mesh(boxGeo, [skinMat, skinMat, skinMat, skinMat, faceMat, skinMat]);
  headMesh.scale.set(0.84, 0.84, 0.84);
  headMesh.position.y = 0.42;
  headMesh.castShadow = true;
  head.add(headMesh);
  // Hair: top, back, sides, plus a swoop on top.
  box(head, 0.92, 0.22, 0.92, 0, 0.9, -0.02, HAIR);
  box(head, 0.5, 0.12, 0.6, -0.12, 1.05, 0.06, HAIR_LIGHT);
  box(head, 0.92, 0.66, 0.14, 0, 0.55, -0.42, HAIR);
  for (const s of [-1, 1]) {
    box(head, 0.1, 0.44, 0.7, 0.45 * s, 0.68, -0.07, HAIR);
    box(head, 0.08, 0.16, 0.12, 0.45 * s, 0.4, 0.04, SKIN_SHADE); // ear
  }
  body.add(head);

  return { root, body, head, arms, faceMat };
}

const speaker = buildSpeaker();
speaker.root.position.set(0, 0, 1.2);
speaker.root.scale.setScalar(1.15);
speaker.root.rotation.y = -0.3; // three-quarter view, like a character select screen
scene.add(speaker.root);

// ---------------------------------------------------------------------------
// The hall: floor, centre aisle, audience, stage, podium, microphone.
// ---------------------------------------------------------------------------
const floor = box(scene, 30, 0.2, 40, 0, -0.1, -6, mat("#3a2233", { noise: 0.12 }), { shadow: false });
floor.receiveShadow = true;
box(scene, 1.6, 0.02, 14, 0, 0.01, -2.5, mat("#7a2a2e", { noise: 0.1 }), { shadow: false }); // runner
for (const s of [-1, 1]) {
  box(scene, 0.12, 0.03, 14, 0.8 * s, 0.015, -2.5, "#d9a640", { shadow: false }); // runner trim
}

// Audience, seated in rows facing the stage (-z), backs to the camera.
const HAIRS = ["#1c1410", "#3b2416", "#6b4423", "#b68a4c", "#d8c08a", "#8d8d8d", "#e3e3e3", "#8e3b1e", "#2a2a3a"];
const SKINS = ["#f1c7a0", "#e8b48a", "#c98f66", "#a86f48", "#7a4c2f", "#5a3824"];
const SHIRTS = ["#3c6e71", "#8a4f7d", "#c97b2a", "#4b5d9b", "#5f7f3a", "#9b3b3b", "#d4c9a8", "#2f3b4a", "#6f5a9b", "#2c6e8f"];
const pick = (arr) => arr[Math.floor(rand() * arr.length)];

const audience = [];
const ROW_Z = [-2.6, -3.75, -4.9, -6.05, -7.2];
for (let r = 0; r < ROW_Z.length; r++) {
  const z = ROW_Z[r];
  for (const side of [-1, 1]) {
    for (let i = 0; i < 6; i++) {
      const x = side * (1.35 + i * 0.86);
      // Seat: back faces the camera.
      box(scene, 0.74, 0.92, 0.12, x, 0.46, z + 0.34, "#5e1b22");
      box(scene, 0.74, 0.12, 0.6, x, 0.42, z, "#4a141a");
      box(scene, 0.78, 0.08, 0.16, x, 0.95, z + 0.34, "#7a262d"); // seat top roll
      if (rand() < 0.1) continue; // a few empty seats

      const person = new THREE.Group();
      person.position.set(x, 0, z - 0.04);
      const shirt = pick(SHIRTS);
      const skin = pick(SKINS);
      const hair = pick(HAIRS);
      const torsoH = 0.6 + rand() * 0.14;
      box(person, 0.56, torsoH, 0.34, 0, 0.5 + torsoH / 2, 0, shirt);
      box(person, 0.2, 0.08, 0.2, 0, 0.54 + torsoH, 0, skin); // neck
      const headG = new THREE.Group();
      headG.position.y = 0.58 + torsoH;
      box(headG, 0.42, 0.42, 0.42, 0, 0.21, 0, skin);
      // Hair covers the back and top; length varies.
      const hairLen = 0.18 + rand() * 0.34;
      box(headG, 0.46, 0.14, 0.46, 0, 0.42, 0, hair);
      box(headG, 0.46, hairLen, 0.08, 0, 0.42 - hairLen / 2, 0.2, hair);
      for (const s of [-1, 1]) box(headG, 0.06, hairLen * 0.8, 0.3, 0.22 * s, 0.42 - hairLen * 0.4, 0.06, hair);
      if (rand() < 0.12) box(headG, 0.22, 0.2, 0.22, 0, 0.56, 0.06, hair); // bun
      person.add(headG);
      scene.add(person);
      audience.push({ headG, phase: rand() * Math.PI * 2, speed: 0.4 + rand() * 0.6, lean: (rand() - 0.5) * 0.25 });
    }
  }
}

// Stage.
const STAGE_Z = -9.4; // front edge
const STAGE_H = 1.0;
const PODIUM_X = 2.3; // off-centre so it reads over the player's shoulder
box(scene, 14, STAGE_H, 5, 0, STAGE_H / 2, STAGE_Z - 2.5, mat("#6e4127", { noise: 0.14 }));
box(scene, 14, 0.08, 5, 0, STAGE_H + 0.04, STAGE_Z - 2.5, mat("#8b5a33", { noise: 0.14 }), { shadow: false });
box(scene, 14.2, 0.14, 0.16, 0, STAGE_H, STAGE_Z + 0.02, "#d9a640"); // lip trim
for (let i = 0; i < 3; i++) {
  // Steps up from the aisle, for the future walk to the podium.
  const h = (STAGE_H / 3) * (i + 1);
  box(scene, 1.5, h, 0.35, 0, h / 2, STAGE_Z + 1.05 - i * 0.35, "#7a4a2a");
}

// Back curtain with folds, a valance, and drawn side curtains.
for (let i = -16; i <= 16; i++) {
  const shade = i % 2 === 0 ? "#8e1f2a" : "#6e1620";
  box(scene, 0.45, 7, 0.3 + (i % 2) * 0.15, i * 0.44, 3.5, STAGE_Z - 5, shade, { shadow: false });
}
box(scene, 15, 0.9, 0.6, 0, 6.3, STAGE_Z - 0.4, "#7a1a24", { shadow: false });
box(scene, 15, 0.12, 0.62, 0, 5.82, STAGE_Z - 0.4, "#d9a640", { shadow: false });
for (const s of [-1, 1]) {
  for (let i = 0; i < 4; i++) {
    box(scene, 0.5, 6, 0.4, s * (5.4 + i * 0.45), 3, STAGE_Z - 0.6 - (i % 2) * 0.1, i % 2 ? "#6e1620" : "#8e1f2a", { shadow: false });
  }
  box(scene, 2.2, 0.16, 0.5, s * 6.1, 2.2, STAGE_Z - 0.3, "#d9a640", { shadow: false }); // tie-back
}

// Podium.
const podium = new THREE.Group();
podium.position.set(PODIUM_X, STAGE_H, STAGE_Z - 1.6);
box(podium, 1.2, 0.12, 0.75, 0, 0.06, 0, "#3d2414");
box(podium, 1.0, 1.25, 0.6, 0, 0.74, 0, "#5b3620");
box(podium, 0.84, 1.0, 0.04, 0, 0.74, 0.31, "#6e4428");
box(podium, 1.2, 0.1, 0.8, 0, 1.4, 0.02, "#3d2414");
// Gold seal on the podium front.
box(podium, 0.34, 0.34, 0.03, 0, 0.85, 0.335, mat("#d9a640", { emissive: "#3a2400" }), { shadow: false });
box(podium, 0.18, 0.18, 0.04, 0, 0.85, 0.34, "#8e1f2a", { shadow: false });
// Microphone on a gooseneck.
const neckMat = mat("#2e2e33", { noise: 0.03 });
box(podium, 0.14, 0.06, 0.14, 0.18, 1.48, 0.1, neckMat);
const neck1 = box(podium, 0.05, 0.36, 0.05, 0.18, 1.66, 0.13, neckMat);
neck1.rotation.x = 0.25;
const neck2 = box(podium, 0.05, 0.3, 0.05, 0.18, 1.9, 0.27, neckMat);
neck2.rotation.x = 0.9;
const micHead = box(podium, 0.13, 0.13, 0.22, 0.18, 2.0, 0.42, mat("#4b4b52", { noise: 0.2 }));
micHead.rotation.x = 0.5;
scene.add(podium);

// Faint light shaft onto the empty podium.
const beam = new THREE.Mesh(
  new THREE.CylinderGeometry(0.35, 1.3, 6, 12, 1, true),
  new THREE.MeshBasicMaterial({ color: "#fff1c4", transparent: true, opacity: 0.07, depthWrite: false, blending: THREE.AdditiveBlending, fog: false })
);
beam.position.set(PODIUM_X, STAGE_H + 3, STAGE_Z - 1.6);
scene.add(beam);
const pool = new THREE.Mesh(
  new THREE.CircleGeometry(1.35, 12),
  new THREE.MeshBasicMaterial({ color: "#fff1c4", transparent: true, opacity: 0.12, depthWrite: false, blending: THREE.AdditiveBlending })
);
pool.rotation.x = -Math.PI / 2;
pool.position.set(PODIUM_X, STAGE_H + 0.09, STAGE_Z - 1.6);
scene.add(pool);

// ---------------------------------------------------------------------------
// Lighting: the hall is dim, the stage is lit, the player is lit best.
// ---------------------------------------------------------------------------
scene.add(new THREE.HemisphereLight("#8a7fb0", "#2a1620", 0.55));

const key = new THREE.DirectionalLight("#fff0d8", 2.4);
key.position.set(-3.5, 6, 6);
key.target = speaker.root;
key.castShadow = true;
key.shadow.mapSize.set(512, 512);
Object.assign(key.shadow.camera, { left: -3, right: 3, top: 4, bottom: -1, near: 1, far: 20 });
scene.add(key);

const rim = new THREE.PointLight("#ffb46b", 6, 6, 1.5);
rim.position.set(1.6, 3, -0.6);
scene.add(rim);

const stageSpot = new THREE.SpotLight("#ffe6b0", 60, 14, 0.35, 0.5, 1.4);
stageSpot.position.set(PODIUM_X, STAGE_H + 7, STAGE_Z + 1.5);
stageSpot.target = podium;
stageSpot.castShadow = true;
stageSpot.shadow.mapSize.set(256, 256);
scene.add(stageSpot);

const houseFill = new THREE.PointLight("#c24a5a", 8, 12, 1.6);
houseFill.position.set(0, 4.5, STAGE_Z - 2);
scene.add(houseFill);

// ---------------------------------------------------------------------------
// Animation: idle breathing, arm sway, blinking, glances; audience fidgets.
// ---------------------------------------------------------------------------
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clock = new THREE.Clock();
let nextBlink = 2;
let blinkUntil = 0;
let hopStart = -10;

document.getElementById("argue").addEventListener("click", () => {
  // Placeholder until the walk-to-podium sequence exists.
  hopStart = clock.getElapsedTime();
  window.dispatchEvent(new CustomEvent("argue"));
});

function animate() {
  const t = clock.getElapsedTime();
  const m = reduceMotion ? 0 : 1;

  // Breathing and a gentle bob.
  speaker.body.position.y = Math.sin(t * 2) * 0.015 * m;
  speaker.body.scale.y = 1 + Math.sin(t * 2) * 0.006 * m;
  speaker.arms[0].rotation.x = Math.sin(t * 2 + 0.4) * 0.04 * m;
  speaker.arms[1].rotation.x = Math.sin(t * 2 + 0.9) * 0.04 * m;
  speaker.arms[0].rotation.z = -0.05;
  speaker.arms[1].rotation.z = 0.05;

  // Occasional glance back at the stage.
  const glance = Math.max(0, Math.sin(t * 0.35) - 0.75) * 4;
  speaker.head.rotation.y = (-0.6 * glance + Math.sin(t * 0.7) * 0.05) * m;
  speaker.head.rotation.x = Math.sin(t * 1.1) * 0.025 * m;

  // Blink.
  if (t > nextBlink) {
    blinkUntil = t + 0.12;
    nextBlink = t + 2.5 + rand() * 3;
  }
  const map = t < blinkUntil ? faceBlink : faceOpen;
  if (speaker.faceMat.map !== map) {
    speaker.faceMat.map = map;
    speaker.faceMat.needsUpdate = true;
  }

  // "Ready!" hop when Argue is pressed.
  const hp = t - hopStart;
  speaker.root.position.y = hp >= 0 && hp < 0.35 ? Math.sin((hp / 0.35) * Math.PI) * 0.35 : 0;

  for (const a of audience) {
    a.headG.rotation.z = (a.lean + Math.sin(t * a.speed + a.phase) * 0.06) * m;
    a.headG.rotation.y = Math.sin(t * a.speed * 0.5 + a.phase) * 0.15 * m;
  }

  // Slow handheld drift keeps the scene alive without pulling focus.
  camera.position.x = CAMERA_POS.x + Math.sin(t * 0.25) * 0.12 * m;
  camera.position.y = CAMERA_POS.y + Math.sin(t * 0.33) * 0.05 * m;
  camera.lookAt(CAMERA_LOOK);

  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}
animate();

function mulberry32(seed) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let r = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}
