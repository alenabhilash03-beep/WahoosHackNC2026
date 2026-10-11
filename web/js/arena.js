// The 3D arena shared by the home screen and the practice screen.
//   home  : the speaker idles in the aisle; walkToStage() plays the ~3 s walk.
//   stage : the speaker stands frozen at the foot of the stage, where the walk ends.
import * as THREE from "three";

// ---------------------------------------------------------------------------
// Render silhouettes at screen resolution so animation does not jump between
// magnified screen pixels. Pixel art lives in the nearest-filtered textures.
// ---------------------------------------------------------------------------
const MAX_PIXEL_RATIO = 2;

const canvas = document.getElementById("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.BasicShadowMap;

const scene = new THREE.Scene();
const BG = new THREE.Color("#0d0b14");
scene.background = BG;
scene.fog = new THREE.Fog(BG, 12, 30);

const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
const CAMERA_POS = new THREE.Vector3(0, 3.1, 6.0);
const CAMERA_LOOK = new THREE.Vector3(0, 1.05, -6.6);
camera.position.copy(CAMERA_POS);
camera.lookAt(CAMERA_LOOK);

function resize() {
  const w = window.innerWidth;
  const h = window.innerHeight;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));
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

const HIP_Y = 0.94;

function buildSpeaker() {
  const root = new THREE.Group();
  const body = new THREE.Group(); // everything above the feet, for breathing/bob
  root.add(body);

  // Legs: a hip joint for the thigh and a knee joint for the shin and shoe,
  // so the speaker can stride and climb stairs.
  const legs = [];
  for (const s of [-1, 1]) {
    const hip = new THREE.Group();
    hip.position.set(0.19 * s, HIP_Y, 0);
    box(hip, 0.3, 0.42, 0.32, 0, -0.2, 0, TROUSER);
    box(hip, 0.04, 0.32, 0.02, 0, -0.2, 0.165, "#1d2749", { shadow: false }); // crease
    const knee = new THREE.Group();
    knee.position.y = -0.4;
    box(knee, 0.3, 0.38, 0.32, 0, -0.19, 0, TROUSER);
    box(knee, 0.04, 0.28, 0.02, 0, -0.17, 0.165, "#1d2749", { shadow: false }); // crease
    box(knee, 0.32, 0.16, 0.5, 0.01 * s, -0.46, 0.06, SHOE);
    box(knee, 0.3, 0.04, 0.12, 0.01 * s, -0.37, 0.25, "#4a3022"); // toe shine
    hip.add(knee);
    body.add(hip);
    legs.push({ hip, knee });
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

  return { root, body, head, arms, legs, faceMat };
}

const speaker = buildSpeaker();
const HOME_SPOT = new THREE.Vector3(0, 0, -2.4); // in the aisle, just in front of the first row
const HOME_TURN = -0.3; // three-quarter view, like a character select screen
speaker.root.position.copy(HOME_SPOT);
speaker.root.scale.setScalar(1.15);
speaker.root.rotation.y = HOME_TURN;
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
const ROW_Z = [-3.2, -4.2, -5.2, -6.2, -7.2];
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
  // Steps up from the aisle: the speaker climbs these on the way to the podium.
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
// The walk to the stage. walkPose(t) is a pure function of time, so the last
// frame of the walk on the home screen and the frozen frame on the practice
// screen are exactly the same picture.
// ---------------------------------------------------------------------------
// Timeline (seconds): turn and walk the aisle, climb two stairs, then lift a
// foot toward the stage and freeze there.
const AISLE_END = 2.15;
const STAIR_SECONDS = 0.5;                 // one stair: lift, plant, rise
const FINAL_LIFT = 0.32;                   // how far into the third stride the speaker freezes
export const WALK_SECONDS = AISLE_END + 2 * STAIR_SECONDS + FINAL_LIFT * STAIR_SECONDS;
const TURN_END = 0.45;                     // turned to face the stage
const FACE_STAGE = -Math.PI;               // back to the camera, facing the stage
const STEP_RISE = STAGE_H / 3;
const STEP_DEPTH = 0.35;
const FIRST_STEP_Z = STAGE_Z + 1.05;       // centre of the lowest step
const AISLE_END_Z = FIRST_STEP_Z + 0.5;
const STEPS = [
  { z: FIRST_STEP_Z, y: STEP_RISE },
  { z: FIRST_STEP_Z - STEP_DEPTH, y: 2 * STEP_RISE }, // the walk ends here, one stride below the stage
];
const STRIDE = 1.2;                        // world units per step: about 2.3 steps a second
const SWING = 0.5;                         // hip swing in the aisle, radians
const LEG = HIP_Y;                         // hip to sole, in the speaker's own units

const clamp01 = (u) => Math.min(1, Math.max(0, u));
const smooth = (u) => u * u * (3 - 2 * u);
const easeInOut = (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2);
/** Accelerate for the first a of the way, then cruise at constant speed. */
const launch = (u, a = 0.18) => (u < a ? (u * u) / (2 * a) : u - a / 2) / (1 - a / 2);
const mix = (a, b, u) => a + (b - a) * u;
/** Piecewise-smooth interpolation through [v, value] keys. */
function keyed(keys, v) {
  for (let i = 1; i < keys.length; i++) {
    if (v <= keys[i][0]) {
      const [v0, a] = keys[i - 1];
      const [v1, b] = keys[i];
      return mix(a, b, smooth(clamp01((v - v0) / (v1 - v0))));
    }
  }
  return keys[keys.length - 1][1];
}

function stageCamera() {
  // Wide screens put the panel on the left, so frame the speaker right of centre.
  return camera.aspect >= 0.9
    ? { pos: new THREE.Vector3(-1.3, 5.6, 0.4), look: new THREE.Vector3(-1.7, 1.2, -9.6) }
    : { pos: new THREE.Vector3(0, 4.6, 0.6), look: new THREE.Vector3(0, 0.8, -9.4) };
}

// Legs are [hip, knee] in radians: hip > 0 swings the thigh forward, knee > 0
// bends the shin back.
// One stair, for the leading leg: lift high, plant on the next stair, then
// straighten as the body rises over it.
const LEAD_HIP = [[0, -0.3], [0.35, 1.3], [0.5, 1.15], [1, 0]];
const LEAD_KNEE = [[0, 0.45], [0.35, 1.6], [0.5, 1.15], [1, 0]];
// The trailing leg holds, then pushes off and lifts its heel, ready to lead next.
const TRAIL_HIP = [[0, 0], [0.5, 0], [0.85, -0.25], [1, -0.3]];
const TRAIL_KNEE = [[0, 0], [0.5, 0], [0.85, 0.2], [1, 0.45]];

function aislePose(t) {
  const u = clamp01((t - 0.05) / (AISLE_END - 0.05));
  const z = HOME_SPOT.z + (AISLE_END_Z - HOME_SPOT.z) * launch(u);
  const phase = ((HOME_SPOT.z - z) / STRIDE) * Math.PI;
  const amp = SWING * smooth(clamp01(t / 0.35));
  const hip = amp * Math.sin(phase);
  // Each knee bends while its leg swings forward, and is straight while it carries weight.
  const kneeA = 0.75 * amp / SWING * Math.max(0, Math.cos(phase));
  const kneeB = 0.75 * amp / SWING * Math.max(0, -Math.cos(phase));
  return {
    z, y: 0,
    legs: [[hip, kneeA], [-hip, kneeB]],
    // The hips dip as the legs spread, so the planted foot stays on the floor.
    bob: -LEG * (1 - Math.cos(hip)),
    lean: 0.06 * amp / SWING,
  };
}

function walkPose(t) {
  const pose = { x: 0, turn: HOME_TURN, look: 0, ...aislePose(Math.min(t, AISLE_END)) };
  pose.turn = HOME_TURN + (FACE_STAGE - HOME_TURN) * smooth(clamp01(t / TURN_END));
  if (t <= AISLE_END) return pose;

  // Up the stairs, one stair every STAIR_SECONDS, alternating the leading leg.
  const climb = (t - AISLE_END) / STAIR_SECONDS;
  const k = Math.min(2, Math.floor(climb));
  const v = k === 2 ? Math.min(climb - 2, FINAL_LIFT) : clamp01(climb - k);
  const from = k === 0 ? { z: AISLE_END_Z, y: 0 } : STEPS[k - 1];
  const to = STEPS[k] ?? from;               // the third stride only lifts the foot
  // Weight moves forward a little while the foot lifts, the rest while rising.
  pose.z = mix(from.z, to.z, v < 0.5 ? 0.25 * smooth(v / 0.5) : 0.25 + 0.75 * smooth((v - 0.5) / 0.5));
  pose.y = mix(from.y, to.y, smooth(clamp01((v - 0.45) / 0.5)));
  pose.bob = 0;
  pose.lean = 0.1 + 0.08 * Math.sin(Math.PI * clamp01((v - 0.4) / 0.6));
  pose.look = -0.15 * smooth(clamp01(climb / 2));

  const lead = [keyed(LEAD_HIP, v), keyed(LEAD_KNEE, v)];
  const trail = [keyed(TRAIL_HIP, v), keyed(TRAIL_KNEE, v)];
  pose.legs = k % 2 === 0 ? [lead, trail] : [trail, lead];

  // Ease out of the aisle stride into the first stair.
  if (k === 0 && v < 0.3) {
    const walkEnd = aislePose(AISLE_END);
    const w = smooth(v / 0.3);
    pose.legs = pose.legs.map((leg, i) => leg.map((angle, j) => mix(walkEnd.legs[i][j], angle, w)));
    pose.bob = mix(walkEnd.bob, 0, w);
  }
  return pose;
}

function applyPose(pose) {
  speaker.root.position.set(pose.x, pose.y, pose.z);
  speaker.root.rotation.y = pose.turn;
  speaker.body.position.y = pose.bob;
  speaker.body.scale.y = 1;
  speaker.body.rotation.x = pose.lean;
  pose.legs.forEach(([hip, knee], i) => {
    speaker.legs[i].hip.rotation.x = -hip;
    speaker.legs[i].knee.rotation.x = knee;
  });
  // Arms swing against the legs.
  speaker.arms[0].rotation.set(-pose.legs[1][0] * 0.7, 0, -0.05);
  speaker.arms[1].rotation.set(-pose.legs[0][0] * 0.7, 0, 0.05);
  speaker.head.rotation.set(pose.look, 0, 0);
  // The key and rim lights travel with the speaker so they stay lit on the way.
  key.position.set(pose.x - 3.5, pose.y + 6, pose.z + 6);
  rim.position.set(pose.x + 1.6, pose.y + 3, pose.z - 1.8);
}

function applyCamera(t) {
  const end = stageCamera();
  const u = easeInOut(clamp01(t / WALK_SECONDS));
  camera.position.lerpVectors(CAMERA_POS, end.pos, u);
  camera.lookAt(new THREE.Vector3().lerpVectors(CAMERA_LOOK, end.look, u));
}

// ---------------------------------------------------------------------------
// Modes and the render loop.
// ---------------------------------------------------------------------------
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const clock = new THREE.Clock();
let mode = "idle"; // idle | walking | stage | paused
let walkStart = 0;
let walkDone = null;
let nextBlink = 2;
let blinkUntil = 0;

/** Home screen: the speaker idles in the aisle. */
export function showHome() {
  mode = "idle";
  applyPose(walkPose(0));
  applyCamera(0);
}

/** Practice screen: the speaker stands frozen at the foot of the stage. */
export function showStage() {
  mode = "stage";
  applyPose(walkPose(WALK_SECONDS));
  applyCamera(WALK_SECONDS);
  setFace(faceOpen);
  renderer.render(scene, camera);
}

/** Hold the walk at t seconds. For tuning the transition from the browser console:
 *    (await import("./js/arena.js")).poseAt(2.6) */
export function poseAt(t) {
  mode = "paused";
  applyPose(walkPose(t));
  applyCamera(t);
}

/** Play the walk to the stage. Resolves when the speaker reaches the last step. */
export function walkToStage() {
  if (reduceMotion) {
    showStage();
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    mode = "walking";
    walkStart = clock.getElapsedTime();
    walkDone = resolve;
  });
}

function setFace(map) {
  if (speaker.faceMat.map !== map) {
    speaker.faceMat.map = map;
    speaker.faceMat.needsUpdate = true;
  }
}

function idle(t) {
  const m = reduceMotion ? 0 : 1;
  // Breathing and a gentle bob.
  speaker.body.position.y = Math.sin(t * 2) * 0.015 * m;
  speaker.body.scale.y = 1 + Math.sin(t * 2) * 0.006 * m;
  speaker.arms[0].rotation.x = Math.sin(t * 2 + 0.4) * 0.04 * m;
  speaker.arms[1].rotation.x = Math.sin(t * 2 + 0.9) * 0.04 * m;

  // Occasional glance back at the stage.
  const glance = Math.max(0, Math.sin(t * 0.35) - 0.75) * 4;
  speaker.head.rotation.y = (-0.6 * glance + Math.sin(t * 0.7) * 0.05) * m;
  speaker.head.rotation.x = Math.sin(t * 1.1) * 0.025 * m;

  if (t > nextBlink) {
    blinkUntil = t + 0.12;
    nextBlink = t + 2.5 + rand() * 3;
  }
  setFace(t < blinkUntil ? faceBlink : faceOpen);
}

function animate() {
  const t = clock.getElapsedTime();

  if (mode === "idle") {
    idle(t);
  } else if (mode === "walking") {
    const w = Math.min(t - walkStart, WALK_SECONDS);
    applyPose(walkPose(w));
    applyCamera(w);
    setFace(faceOpen);
    if (w >= WALK_SECONDS) {
      mode = "stage";
      walkDone?.();
    }
  }

  // The audience keeps fidgeting in every mode.
  const m = reduceMotion ? 0 : 1;
  for (const a of audience) {
    a.headG.rotation.z = (a.lean + Math.sin(t * a.speed + a.phase) * 0.06) * m;
    a.headG.rotation.y = Math.sin(t * a.speed * 0.5 + a.phase) * 0.15 * m;
  }

  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

window.addEventListener("resize", () => {
  if (mode === "stage") showStage();
});

// The browser may skip the cross-page fade (for example in a background tab); that is fine.
window.addEventListener("pageswap", (event) => event.viewTransition?.ready.catch(() => {}));
window.addEventListener("pagereveal", (event) => event.viewTransition?.ready.catch(() => {}));

// A page can ask for the stage view before its first frame: <body data-arena="stage">.
if (document.body.dataset.arena === "stage") showStage();
else showHome();
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
