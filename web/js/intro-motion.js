export const MIC_DROP_SECONDS = 1.8;
const mix = (a, b, u) => a + (b - a) * u;
const smooth = u => u * u * (3 - 2 * u);
const clamp = u => Math.max(0, Math.min(1, u));

// All viewpoints stay on stage, at the speaker's eye height. Each sweep takes
// twelve seconds; the final point joins the first without a camera cut.
const VIEWS = [
  {position: [2.3, 3.75, -11.45], look: [7, 2.4, 0]},
  {position: [0.2, 3.65, -10.7], look: [0, 2.6, 2]},
  {position: [-2.3, 3.75, -11.1], look: [-7, 2.5, 0]},
];
export function introPose(seconds, reduced = false) {
  if (reduced) return {position: [...VIEWS[1].position], look: [...VIEWS[1].look]};
  const phase = ((seconds % 36) + 36) % 36 / 12;
  const index = Math.floor(phase);
  const u = smooth(phase - index);
  const a = VIEWS[index], b = VIEWS[(index + 1) % VIEWS.length];
  return {
    position: a.position.map((v, i) => mix(v, b.position[i], u)),
    look: a.look.map((v, i) => mix(v, b.look[i], u)),
  };
}

export function micDropPose(seconds) {
  const lift = smooth(clamp(seconds / 0.42));
  const fall = clamp((seconds - 0.42) / 1.15);
  return {
    lift,
    x: mix(0.55, 0, lift) - fall * 0.16,
    y: mix(-0.65, -0.1, lift) - 2.7 * fall * fall,
    z: mix(-1.9, -1.35, lift) - fall * 0.35,
    rotation: -0.18 - fall * Math.PI * 1.7,
    done: seconds >= MIC_DROP_SECONDS,
  };
}
