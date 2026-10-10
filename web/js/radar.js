// Speech radar: each part of a speech graded 0-100, first attempt against best.
//
// Data comes straight from softskills-grader reports: report.factors[name].score.
// The pure helpers (factorScores, firstAndBest, polarPoint) have no DOM and are
// tested in web/test/radar.test.mjs; renderRadar draws an SVG into an element.

export const AXES = [
  { key: "logic", label: "Logic", sub: "logos" },
  { key: "emotion", label: "Emotion", sub: "pathos" },
  { key: "tone", label: "Tone", sub: "delivery" },
  { key: "diction", label: "Diction", sub: "delivery" },
  { key: "pace", label: "Pace", sub: "delivery" },
];

const RINGS = [25, 50, 75, 100];
const SVG_NS = "http://www.w3.org/2000/svg";

/** {logic: 92, emotion: 70, ...} from one grader report, or null if it was not gradable. */
export function factorScores(report, axes = AXES) {
  if (!report || !report.gradable || !report.factors) return null;
  const out = {};
  for (const { key } of axes) {
    const score = report.factors[key]?.score;
    out[key] = typeof score === "number" ? score : null;
  }
  return out;
}

/**
 * First and best attempt on each part, from reports in the order they were recorded.
 * "best" is per part (the highest score each part ever reached), so its shape can
 * combine several attempts; bestFrom says which attempt (1-based) each part came from.
 */
export function firstAndBest(reports, axes = AXES) {
  const graded = reports.map((r) => factorScores(r, axes)).filter(Boolean);
  if (!graded.length) return null;
  const first = graded[0];
  const best = {};
  const bestFrom = {};
  for (const { key } of axes) {
    best[key] = null;
    bestFrom[key] = null;
    graded.forEach((scores, i) => {
      const v = scores[key];
      if (v != null && (best[key] == null || v > best[key])) {
        best[key] = v;
        bestFrom[key] = i + 1;
      }
    });
  }
  return { first, best, bestFrom, attempts: graded.length };
}

/** Point for `value` (0-100) on axis `i` of `n`; axis 0 points straight up. */
export function polarPoint(i, n, value, cx, cy, radius) {
  const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
  const r = (radius * Math.max(0, Math.min(100, value ?? 0))) / 100;
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function fmt(v) {
  return v == null ? "–" : Math.round(v).toString();
}

/**
 * Draw the radar into `container`.
 * series: [{ name, kind: "area" | "outline", values: {key: score} }]; area first, outline on top.
 * Adds a legend, a per-part hover tooltip and a table view toggle.
 */
export function renderRadar(container, { series, axes = AXES, notes = {} }) {
  container.innerHTML = "";
  container.classList.add("radar");

  const W = 380, H = 330, cx = W / 2, cy = 168, R = 112, n = axes.length;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Speech parts radar: ${series.map((s) => s.name).join(" and ")}` });

  // Grid: rings and spokes, recessive.
  const grid = el("g", { class: "radar-grid" }, svg);
  for (const ring of RINGS) {
    const pts = axes.map((_, i) => polarPoint(i, n, ring, cx, cy, R).join(",")).join(" ");
    el("polygon", { points: pts }, grid);
  }
  axes.forEach((_, i) => {
    const [x, y] = polarPoint(i, n, 100, cx, cy, R);
    el("line", { x1: cx, y1: cy, x2: x, y2: y }, grid);
  });
  const ringLabels = el("g", { class: "radar-ring-label" }, svg);
  for (const ring of [50, 100]) {
    const t = el("text", { x: cx + 4, y: cy - (R * ring) / 100 + 11 }, ringLabels);
    t.textContent = ring;
  }

  // Series: area underneath, outline on top, each with a 2px surface ring on its dots.
  for (const s of series) {
    const pts = axes.map((a, i) => polarPoint(i, n, s.values[a.key], cx, cy, R));
    el("polygon", { class: `radar-${s.kind}`, points: pts.map((p) => p.join(",")).join(" ") }, svg);
    for (const [x, y] of pts) el("circle", { class: `radar-dot radar-dot-${s.kind}`, cx: x, cy: y, r: s.kind === "outline" ? 4.5 : 3.5 }, svg);
  }

  // Axis labels with the outline series' score (the headline number per part).
  const headline = series.find((s) => s.kind === "outline") ?? series[series.length - 1];
  axes.forEach((a, i) => {
    const [x, y] = polarPoint(i, n, 100, cx, cy, R + 24);
    const anchor = Math.abs(x - cx) < 6 ? "middle" : x > cx ? "start" : "end";
    const t = el("text", { class: "radar-axis-label", x, y: y + (y < cy - 10 ? -6 : y > cy + 10 ? 12 : 4), "text-anchor": anchor }, svg);
    const name = el("tspan", {}, t);
    name.textContent = a.label + " ";
    const val = el("tspan", { class: "radar-axis-value" }, t);
    val.textContent = fmt(headline.values[a.key]);
  });

  // Hover: one wedge per part as the hit target, bigger than any mark.
  const tip = document.createElement("div");
  tip.className = "radar-tip";
  tip.hidden = true;
  const hits = el("g", { class: "radar-hits" }, svg);
  const spokes = grid.querySelectorAll("line");
  axes.forEach((a, i) => {
    const left = polarPoint(i - 0.5, n, 100, cx, cy, R + 30);
    const right = polarPoint(i + 0.5, n, 100, cx, cy, R + 30);
    const wedge = el("polygon", { points: [[cx, cy], left, right].map((p) => p.join(",")).join(" "), tabindex: 0 }, hits);
    const show = () => {
      const rows = series.map((s) => `<div><span class="radar-swatch radar-swatch-${s.kind}"></span>${s.name} <b>${fmt(s.values[a.key])}</b></div>`).join("");
      tip.innerHTML = `<div class="radar-tip-title">${a.label} <span>${a.sub}</span></div>${rows}${notes[a.key] ? `<div class="radar-tip-note">${notes[a.key]}</div>` : ""}`;
      // Anchor at the axis tip, outside the shapes, so the tooltip never covers the data.
      const [x, y] = polarPoint(i, n, 100, cx, cy, R);
      tip.style.left = `${(x / W) * 100}%`;
      tip.style.top = `${(y / H) * 100}%`;
      tip.classList.toggle("below", y > cy);
      tip.hidden = false;
      spokes[i].classList.add("active");
    };
    const hide = () => { tip.hidden = true; spokes[i].classList.remove("active"); };
    wedge.addEventListener("pointerenter", show);
    wedge.addEventListener("pointerleave", hide);
    wedge.addEventListener("focus", show);
    wedge.addEventListener("blur", hide);
  });

  const plot = document.createElement("div");
  plot.className = "radar-plot";
  plot.append(svg, tip);

  // Table view: the same numbers, for screen readers and anyone who prefers rows.
  const table = document.createElement("table");
  table.className = "radar-table";
  table.hidden = true;
  table.innerHTML = `<thead><tr><th>Part</th>${series.map((s) => `<th>${s.name}</th>`).join("")}</tr></thead><tbody>${
    axes.map((a) => `<tr><td>${a.label} <span>${a.sub}</span></td>${series.map((s) => `<td>${fmt(s.values[a.key])}</td>`).join("")}</tr>`).join("")
  }</tbody>`;

  const legend = document.createElement("div");
  legend.className = "radar-legend";
  legend.innerHTML = series.map((s) => `<span><i class="radar-swatch radar-swatch-${s.kind}"></i>${s.name}</span>`).join("");

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "radar-toggle";
  toggle.textContent = "Table view";
  toggle.addEventListener("click", () => {
    const showTable = table.hidden;
    table.hidden = !showTable;
    plot.hidden = showTable;
    toggle.textContent = showTable ? "Chart view" : "Table view";
  });

  container.append(legend, plot, table, toggle);
}
