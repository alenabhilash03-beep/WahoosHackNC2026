// Speech radar: each part of a speech graded 0-100, first attempt against best.
//
// Data comes straight from softskills-grader reports: report.factors[name].score.
// The pure helpers (factorScores, firstAndBest, polarPoint, pixelLine, pixelFill) have no DOM and are
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

// ------------------------------------------------------------- pixel raster
// The shapes are drawn pixel by pixel on a small canvas that CSS upscales with hard edges,
// like the arena scene, so no line is ever anti-aliased.

/** Pixels on the line from (x0, y0) to (x1, y1), integer coordinates (Bresenham). */
export function pixelLine(x0, y0, x1, y1) {
  const out = [];
  let dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) {
    out.push([x0, y0]);
    if (x0 === x1 && y0 === y1) return out;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

/** Pixels whose centres fall inside the polygon (even-odd rule). */
export function pixelFill(points) {
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const out = [];
  for (let y = Math.floor(Math.min(...ys)); y <= Math.ceil(Math.max(...ys)); y++) {
    for (let x = Math.floor(Math.min(...xs)); x <= Math.ceil(Math.max(...xs)); x++) {
      const px = x + 0.5, py = y + 0.5;
      let inside = false;
      for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const [xi, yi] = points[i], [xj, yj] = points[j];
        if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
      }
      if (inside) out.push([x, y]);
    }
  }
  return out;
}

function cssVar(node, name, fallback) {
  return getComputedStyle(node).getPropertyValue(name).trim() || fallback;
}

/**
 * Draw the radar into `container`.
 * series: [{ name, kind: "area" | "outline", values: {key: score} }]; area first, outline on top.
 * Adds a legend, a per-part hover tooltip and a table view toggle.
 */
export function renderRadar(container, { series, axes = AXES, notes = {} }) {
  container.innerHTML = "";
  container.classList.add("radar");

  // Layout in view units; the canvas has one pixel per PX view units.
  const W = 384, H = 348, PX = 3, cx = W / 2, cy = 180, R = 120, n = axes.length;
  const pix = ([x, y]) => [Math.round(x / PX), Math.round(y / PX)];

  const canvas = document.createElement("canvas");
  canvas.width = W / PX;
  canvas.height = H / PX;
  canvas.className = "radar-canvas";
  canvas.setAttribute("aria-hidden", "true");

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Speech parts radar: ${series.map((s) => s.name).join(" and ")}. Table view has the numbers.` });

  const plot = document.createElement("div");
  plot.className = "radar-plot";
  container.append(plot);
  const colors = {
    grid: cssVar(plot, "--radar-grid", "#55303f"),
    hot: cssVar(plot, "--radar-hot", "#f4ead2"),
    area: cssVar(plot, "--series-first", "#c8423a"),
    outline: cssVar(plot, "--series-best", "#c98500"),
    ink: cssVar(plot, "--radar-shadow", "#000"),
  };

  const ctx = canvas.getContext("2d");
  const put = (pixels, color, keep = () => true) => {
    ctx.fillStyle = color;
    for (const [x, y] of pixels) if (keep(x, y)) ctx.fillRect(x, y, 1, 1);
  };
  const ring = (value) => axes.map((_, i) => pix(polarPoint(i, n, value, cx, cy, R)));
  const outline = (pts) => pts.flatMap((p, i) => pixelLine(...p, ...pts[(i + 1) % pts.length]));
  const centre = pix([cx, cy]);

  function draw(hot = -1) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // Grid: solid rings, dotted spokes; the hovered spoke lights up.
    for (const v of RINGS) put(outline(ring(v)), colors.grid);
    ring(100).forEach((tip, i) => {
      put(pixelLine(...centre, ...tip), i === hot ? colors.hot : colors.grid, (x, y) => (x + y) % 2 === 0);
    });
    for (const s of series) {
      const pts = axes.map((a, i) => pix(polarPoint(i, n, s.values[a.key], cx, cy, R)));
      const color = colors[s.kind];
      if (s.kind === "area") {
        put(pixelFill(pts), color, (x, y) => (x + y) % 2 === 0);   // checker dither = translucent fill
        put(outline(pts), color);
        for (const [x, y] of pts) { ctx.fillStyle = color; ctx.fillRect(x - 1, y - 1, 3, 3); }
      } else {
        // A 2-pixel brush so diagonals read as solid steps, over a one-pixel black drop shadow.
        const line = outline(pts);
        const brush = line.flatMap(([x, y]) => [[x, y], [x + 1, y]]);
        put(brush.map(([x, y]) => [x, y + 1]), colors.ink);
        put(brush, color);
        for (const [x, y] of pts) {
          ctx.fillStyle = colors.ink; ctx.fillRect(x - 2, y - 2, 5, 5);
          ctx.fillStyle = color; ctx.fillRect(x - 1, y - 1, 3, 3);
        }
      }
    }
  }
  draw();

  // Axis labels in the pixel font: the part's name, and under it the outline series' score.
  const headline = series.find((s) => s.kind === "outline") ?? series[series.length - 1];
  axes.forEach((a, i) => {
    const [x, y] = polarPoint(i, n, 100, cx, cy, R + 16);
    const anchor = Math.abs(x - cx) < 6 ? "middle" : x > cx ? "start" : "end";
    const top = y < cy - 10 ? y - 22 : y > cy + 10 ? y + 8 : y - 8;
    const t = el("text", { class: "radar-axis-label", x, y: top, "text-anchor": anchor }, svg);
    el("tspan", { x, dy: 0 }, t).textContent = a.label.toUpperCase();
    el("tspan", { class: "radar-axis-value", x, dy: 16 }, t).textContent = fmt(headline.values[a.key]);
  });

  // Hover: one wedge per part as the hit target, bigger than any mark.
  const tip = document.createElement("div");
  tip.className = "radar-tip";
  tip.hidden = true;
  const hits = el("g", { class: "radar-hits" }, svg);
  axes.forEach((a, i) => {
    const left = polarPoint(i - 0.5, n, 100, cx, cy, R + 30);
    const right = polarPoint(i + 0.5, n, 100, cx, cy, R + 30);
    const wedge = el("polygon", { points: [[cx, cy], left, right].map((p) => p.join(",")).join(" "), tabindex: 0,
      "aria-label": `${a.label}: ${series.map((s) => `${s.name} ${fmt(s.values[a.key])}`).join(", ")}` }, hits);
    const show = () => {
      const rows = series.map((s) => `<div><i class="radar-swatch radar-swatch-${s.kind}"></i>${s.name}<b>${fmt(s.values[a.key])}</b></div>`).join("");
      tip.innerHTML = `<div class="radar-tip-title">${a.label.toUpperCase()} <span>${a.sub}</span></div>${rows}${notes[a.key] ? `<div class="radar-tip-note">${notes[a.key]}</div>` : ""}`;
      // Anchor at the axis tip, outside the shapes, so the tooltip never covers the data.
      const [x, y] = polarPoint(i, n, 100, cx, cy, R);
      tip.style.left = `${(x / W) * 100}%`;
      tip.style.top = `${(y / H) * 100}%`;
      tip.classList.toggle("below", y > cy);
      tip.hidden = false;
      draw(i);
    };
    const hide = () => { tip.hidden = true; draw(); };
    wedge.addEventListener("pointerenter", show);
    wedge.addEventListener("pointerleave", hide);
    wedge.addEventListener("focus", show);
    wedge.addEventListener("blur", hide);
  });

  plot.append(canvas, svg, tip);

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
  toggle.textContent = "TABLE";
  toggle.addEventListener("click", () => {
    const showTable = table.hidden;
    table.hidden = !showTable;
    plot.hidden = showTable;
    toggle.textContent = showTable ? "CHART" : "TABLE";
  });

  container.prepend(legend);
  container.append(table, toggle);
}
