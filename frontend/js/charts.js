
/**
 * Nagare — hand-built SVG charting layer.
 *
 * No library, no CDN, no canvas. Every mark is an SVG node created through
 * ui.js's `svg()` hyperscript, and every colour is a CSS custom property so a
 * theme flip repaints the whole dashboard without re-rendering a single chart.
 *
 * ---------------------------------------------------------------------------
 * Palette provenance (dataviz method — computed, never eyeballed)
 * ---------------------------------------------------------------------------
 * The design system supplies the parameters; this module only consumes them.
 * All values below were measured with the dataviz validator against the panel
 * surface each chart actually renders on (`--bg-raised`: #FFFFFF light,
 * #181C1F dark).
 *
 * ORDINAL ramp (CFD bands, wait/touch shades, histogram, throughput):
 *   `--flow-1..--flow-7` is a single-hue teal ramp, monotone in OKLCH L.
 *   Its extreme steps do NOT clear the 2:1 ordinal light-end floor against the
 *   panel (light `--flow-7` 1.36:1, dark `--flow-1` 1.39:1), so charts use a
 *   *window* of the ramp rather than its full span, with the anchor flipped per
 *   mode exactly as a sequential ramp should be:
 *       light  `--flow-1` (strong) -> `--flow-5` (weak)   weak end 2.65:1
 *       dark   `--flow-7` (strong) -> `--flow-2` (weak)   weak end 2.26:1
 *   metrics.css publishes that window as `--nv-ramp-a` / `--nv-ramp-b`;
 *   intermediate stops are interpolated with `color-mix(in oklab, …)`.
 *   Validated at 7 bands: monotone L PASS, adjacent dL PASS, light-end
 *   contrast PASS, single hue PASS — in both modes.
 *
 * CATEGORICAL palette (classes of service):
 *   `--sc-expedite / --sc-standard / --sc-fixed-date / --sc-intangible`, used
 *   in the SPEC's enum order. All 24 orderings were enumerated: this one is the
 *   measured optimum on the adjacent pairlist (worst CVD dE 12.5 light /
 *   14.4 dark against a target of 8).
 *   Two checks do not pass and cannot be fixed from this module — tokens.css is
 *   owned by another agent, and the same four hues carry service class on the
 *   board's card stripes, so colour must follow the entity product-wide:
 *     · chroma floor — `intangible` is a deliberately desaturated slate
 *       (C 0.022 light / 0.026 dark) and therefore reads as grey;
 *     · normal-vision floor — worst adjacent pair 12.8 light / 14.6 dark,
 *       under the 15 gate (intangible vs fixed-date / standard).
 *   The prescribed relief is therefore MANDATORY here and is implemented as a
 *   permanent second channel rather than an option: every chart that encodes
 *   service class also encodes it as SHAPE (circle / triangle / square /
 *   diamond), mirrors that shape in the legend, direct-labels the values, and
 *   ships the data-table twin. Colour never carries identity alone.
 *
 * STATUS colours (`--success/--warning/--danger`, `--wip-*`) are reserved for
 * state and are always paired with a label — never used as "series N".
 *
 * ---------------------------------------------------------------------------
 * Contract
 * ---------------------------------------------------------------------------
 * Every factory takes `(container, opts)` and returns a handle:
 *     { el, update(opts), destroy(), setBusy(bool) }
 * `update` re-renders in place; `destroy` releases the ResizeObserver and any
 * document-level listeners. Charts are responsive (viewBox re-measured on
 * resize), accessible (`role="img"` + `<title>` + `<desc>` + a data-table
 * twin), animated on first paint only, and degrade to a clean empty state.
 */

import { el, svg, clear, LOCALE } from "./ui.js";

/* ==========================================================================
   Geometry defaults
   ========================================================================== */

/** Plot padding. Left is generous because y tick labels live there. */
const PAD = Object.freeze({ top: 16, right: 18, bottom: 34, left: 52 });

const TICK_FONT = 11;           // px — matches --text-xs
const MAX_BAR = 24;             // px — mark spec: bars never fill their band
const GAP = 2;                  // px — the surface gap that separates marks
const DOT_R = 4.5;              // px — marker radius (>= 8px diameter)
const RING = 2;                 // px — surface ring on overlapping markers
const HIT = 24;                 // px — minimum pointer/focus target

/** Rough text width, good enough to decide whether a label fits. */
function textWidth(text, fontPx = TICK_FONT) {
  return String(text).length * fontPx * 0.56;
}

/* ==========================================================================
   Colour roles
   --------------------------------------------------------------------------
   Everything returned here is a CSS colour *expression*, never a resolved
   value: the browser re-evaluates it on a theme change for free.
   ========================================================================== */

/**
 * A paint value plus a plain-token fallback.
 * `applyPaint` writes the fallback first and the real value second; an engine
 * without `color-mix()` silently drops the second write and keeps the first.
 */
function paint(value, fallback) {
  return fallback ? { value, fallback } : { value, fallback: value };
}

/** Write a colour onto an SVG node with its no-`color-mix()` fallback. */
export function applyPaint(node, property, colour) {
  const c = typeof colour === "string" ? paint(colour) : colour;
  node.style[property] = c.fallback;
  node.style[property] = c.value;
  return node;
}

/**
 * Step `i` of `n` on the ordinal flow ramp.
 * Index 0 is the strong (most-advanced) end; index n-1 the weak end.
 */
export function rampColor(i, n) {
  if (n <= 1 || i <= 0) return paint("var(--nv-ramp-a)");
  if (i >= n - 1) return paint("var(--nv-ramp-b)");
  const share = Math.round(((n - 1 - i) / (n - 1)) * 1000) / 10;
  return paint(
    `color-mix(in oklab, var(--nv-ramp-a) ${share}%, var(--nv-ramp-b))`,
    // Fallback for engines without color-mix(): snap to the nearer anchor.
    share >= 50 ? "var(--nv-ramp-a)" : "var(--nv-ramp-b)"
  );
}

/** Class-of-service order — the measured optimum on the adjacent pairlist. */
export const SERVICE_CLASSES = Object.freeze(["expedite", "standard", "fixed_date", "intangible"]);

export const SERVICE_CLASS_LABEL = Object.freeze({
  expedite: "Expedite",
  standard: "Standard",
  fixed_date: "Fixed date",
  intangible: "Intangible",
});

const SC_TOKEN = Object.freeze({
  expedite: "var(--sc-expedite)",
  standard: "var(--sc-standard)",
  fixed_date: "var(--sc-fixed-date)",
  intangible: "var(--sc-intangible)",
});

/** Colour for a class of service. Unknown classes fall back to muted ink. */
export function serviceClassColor(key) {
  return paint(SC_TOKEN[key] || "var(--nv-muted)");
}

/**
 * Shape for a class of service — the mandatory second identity channel.
 * See the palette note at the top of this file for why this is not optional.
 */
export function serviceClassShape(key) {
  const index = SERVICE_CLASSES.indexOf(key);
  return ["circle", "triangle", "square", "diamond"][index] || "circle";
}

/**
 * Path data for a mark of `shape` centred on (cx, cy) with radius r.
 * Areas are equalised across shapes so no class looks heavier than another.
 */
export function shapePath(shape, cx, cy, r) {
  switch (shape) {
    case "square": {
      const a = r * 0.88;
      return `M${cx - a} ${cy - a}H${cx + a}V${cy + a}H${cx - a}Z`;
    }
    case "diamond": {
      const a = r * 1.24;
      return `M${cx} ${cy - a}L${cx + a} ${cy}L${cx} ${cy + a}L${cx - a} ${cy}Z`;
    }
    case "triangle": {
      const a = r * 1.28;
      const h = a * 0.866;
      return `M${cx} ${cy - a}L${cx + h} ${cy + a * 0.55}L${cx - h} ${cy + a * 0.55}Z`;
    }
    default: {
      // A circle as a path so every mark shares one element type.
      return `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${r * 2} 0a${r} ${r} 0 1 0 ${-r * 2} 0Z`;
    }
  }
}

/* ==========================================================================
   Formatting
   ========================================================================== */

/** Clean axis numbers: 1200 -> "1,200", 0.5 -> "0.5". */
export function formatTick(value) {
  if (!Number.isFinite(value)) return "";
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toLocaleString(LOCALE, { maximumFractionDigits: 0 });
  if (abs >= 10 || Number.isInteger(value)) return String(Math.round(value * 100) / 100);
  return String(Math.round(value * 100) / 100);
}

/** Days with the precision the number deserves: 0.8d, 4.2d, 27d. */
export function formatDays(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  if (Math.abs(n) >= 10) return `${Math.round(n)}d`;
  return `${(Math.round(n * 10) / 10).toFixed(1)}d`;
}

const MS_DAY = 86400000;

/** Parse an ISO-8601 string / epoch / Date into epoch ms, or null. */
export function toMs(value) {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value;
  const text = String(value);
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(text) || !text.includes("T") ? text : `${text}Z`;
  const ms = Date.parse(normalized);
  return Number.isNaN(ms) ? null : ms;
}

function fmtDate(ms, style) {
  const d = new Date(ms);
  if (style === "month") return d.toLocaleDateString(LOCALE, { month: "short", year: "2-digit" });
  if (style === "time") return d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(LOCALE, { day: "numeric", month: "short" });
}

/** Full date for tooltips and table cells. */
export function formatDateLong(ms) {
  if (ms === null || ms === undefined) return "";
  return new Date(ms).toLocaleDateString(LOCALE, { day: "numeric", month: "short", year: "numeric" });
}

/* ==========================================================================
   Scales
   ========================================================================== */

/**
 * Continuous linear scale.
 * @param {[number, number]} domain
 * @param {[number, number]} range Pixel range; y scales pass [height, 0].
 * @param {{clamp?: boolean}} [opts]
 */
export function linearScale(domain, range, opts = {}) {
  let [d0, d1] = domain;
  const [r0, r1] = range;
  if (!Number.isFinite(d0) || !Number.isFinite(d1)) { d0 = 0; d1 = 1; }
  if (d0 === d1) { d0 -= 0.5; d1 += 0.5; }

  const k = (r1 - r0) / (d1 - d0);
  const lo = Math.min(r0, r1);
  const hi = Math.max(r0, r1);

  const scale = (value) => {
    const p = r0 + (Number(value) - d0) * k;
    if (!Number.isFinite(p)) return r0;
    return opts.clamp ? Math.min(hi, Math.max(lo, p)) : p;
  };

  scale.invert = (px) => d0 + (px - r0) / k;
  scale.domain = [d0, d1];
  scale.range = [r0, r1];
  scale.ticks = (count = 5) => niceTicks(d0, d1, count).ticks;
  return scale;
}

/**
 * Time scale — a linear scale in epoch ms with calendar-aware ticks.
 * @param {[number, number]} domain epoch ms
 * @param {[number, number]} range
 */
export function timeScale(domain, range) {
  const scale = linearScale(domain, range);
  scale.ticks = (count = 6) => timeTicks(scale.domain[0], scale.domain[1], count);
  scale.tickFormat = () => {
    const span = scale.domain[1] - scale.domain[0];
    if (span <= MS_DAY * 3) return (ms) => fmtDate(ms, "time");
    if (span <= MS_DAY * 120) return (ms) => fmtDate(ms, "day");
    return (ms) => fmtDate(ms, "month");
  };
  return scale;
}

/**
 * Ordinal band scale, d3-compatible geometry.
 * @param {Array<string>} domain
 * @param {[number, number]} range
 * @param {{padding?: number, paddingOuter?: number}} [opts]
 */
export function bandScale(domain, range, opts = {}) {
  const inner = opts.padding ?? 0.24;
  const outer = opts.paddingOuter ?? inner / 2;
  const n = Math.max(1, domain.length);
  const [r0, r1] = range;
  const span = r1 - r0;

  const step = span / Math.max(1, n - inner + outer * 2);
  const bandwidth = Math.max(1, step * (1 - inner));
  const start = r0 + (span - (step * n - step * inner)) / 2;

  const index = new Map(domain.map((key, i) => [key, i]));

  const scale = (key) => {
    const i = index.get(key);
    return i === undefined ? NaN : start + step * i;
  };
  scale.bandwidth = () => bandwidth;
  scale.step = () => step;
  scale.center = (key) => scale(key) + bandwidth / 2;
  scale.domain = domain;
  scale.range = [r0, r1];
  /** Which band a pixel falls in — used by hit-testing. */
  scale.at = (px) => {
    const i = Math.floor((px - start) / step);
    return i >= 0 && i < n ? domain[i] : null;
  };
  return scale;
}

/* ==========================================================================
   Ticks
   ========================================================================== */

function niceNum(range, round) {
  const exponent = Math.floor(Math.log10(range || 1));
  const fraction = (range || 1) / 10 ** exponent;
  let nice;
  if (round) nice = fraction < 1.5 ? 1 : fraction < 3 ? 2 : fraction < 7 ? 5 : 10;
  else nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * 10 ** exponent;
}

/**
 * Round-number ticks covering [min, max].
 * @returns {{ticks: number[], min: number, max: number, step: number}}
 */
export function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { ticks: [0, 1], min: 0, max: 1, step: 1 };
  if (min === max) { min = Math.min(0, min); max = max === 0 ? 1 : max * 1.1; }

  const step = niceNum((max - min) / Math.max(1, count), true) || 1;
  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;

  const ticks = [];
  const total = Math.round((niceMax - niceMin) / step);
  for (let i = 0; i <= total; i += 1) {
    // Re-round each step: repeated addition drifts on values like 0.1.
    ticks.push(Math.round((niceMin + i * step) * 1e9) / 1e9);
  }
  return { ticks, min: niceMin, max: niceMax, step };
}

const TIME_STEPS = [
  { ms: 3600000, label: "hour" },
  { ms: 3600000 * 6, label: "6h" },
  { ms: MS_DAY, label: "day" },
  { ms: MS_DAY * 2, label: "2d" },
  { ms: MS_DAY * 7, label: "week" },
  { ms: MS_DAY * 14, label: "2w" },
  { ms: MS_DAY * 30, label: "month" },
  { ms: MS_DAY * 91, label: "quarter" },
  { ms: MS_DAY * 182, label: "half" },
  { ms: MS_DAY * 365, label: "year" },
];

/** Calendar-snapped time ticks (month/year steps land on the 1st). */
export function timeTicks(minMs, maxMs, count = 6) {
  const span = Math.max(1, maxMs - minMs);
  const target = span / Math.max(1, count);
  const step = TIME_STEPS.find((s) => s.ms >= target) || TIME_STEPS[TIME_STEPS.length - 1];

  const ticks = [];
  if (step.ms >= MS_DAY * 30) {
    const monthsPerStep = Math.max(1, Math.round(step.ms / (MS_DAY * 30)));
    const start = new Date(minMs);
    const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
    while (cursor.getTime() <= maxMs) {
      if (cursor.getTime() >= minMs) ticks.push(cursor.getTime());
      cursor.setUTCMonth(cursor.getUTCMonth() + monthsPerStep);
    }
  } else {
    const first = Math.ceil(minMs / step.ms) * step.ms;
    for (let t = first; t <= maxMs; t += step.ms) ticks.push(t);
  }

  if (!ticks.length) ticks.push(minMs, maxMs);
  return ticks;
}

/* ==========================================================================
   Shared tooltip
   --------------------------------------------------------------------------
   One node for the whole app. Follows the cursor, flips at the viewport edge,
   and is driven identically by pointer and by keyboard focus so the two paths
   can never drift apart. Labels are inserted as text nodes — series names come
   from the API and are untrusted.
   ========================================================================== */

let tipNode = null;

function tooltipNode() {
  if (tipNode && document.body.contains(tipNode)) return tipNode;
  tipNode = el("div", { className: "nv-tip", role: "tooltip", "aria-hidden": "true" });
  document.body.append(tipNode);
  return tipNode;
}

/**
 * Build the tooltip body.
 * @param {{title?: string, subtitle?: string, rows?: Array, footer?: string}} spec
 *   `rows` are `{label, value, color?, shape?, emphasis?}`. Value leads, label
 *   follows — the reader already knows the series and wants the number.
 */
function tooltipContent(spec) {
  const rows = (spec.rows || []).filter(Boolean);
  return el(
    "div",
    { className: "nv-tip-inner" },
    spec.title ? el("div", { className: "nv-tip-title" }, spec.title) : null,
    spec.subtitle ? el("div", { className: "nv-tip-sub" }, spec.subtitle) : null,
    rows.length
      ? el(
          "div",
          { className: "nv-tip-rows" },
          rows.map((row) =>
            el(
              "div",
              { className: row.emphasis ? "nv-tip-row is-emphasis" : "nv-tip-row" },
              row.color
                ? applyPaint(
                    el("span", { className: `nv-key nv-key-${row.shape || "line"}` }),
                    "backgroundColor",
                    row.color
                  )
                : el("span", { className: "nv-key nv-key-none" }),
              el("span", { className: "nv-tip-label" }, row.label),
              el("span", { className: "nv-tip-value" }, row.value)
            )
          )
        )
      : null,
    spec.footer ? el("div", { className: "nv-tip-foot" }, spec.footer) : null
  );
}

/** Show the shared tooltip near a point or an element rect. */
export function showTooltip(spec, position) {
  const node = tooltipNode();
  clear(node);
  node.append(tooltipContent(spec));
  node.setAttribute("aria-hidden", "false");
  node.classList.add("is-open");

  // Measure before positioning; the content just changed.
  const rect = node.getBoundingClientRect();
  const margin = 12;
  let left = (position.x ?? 0) + 14;
  let top = (position.y ?? 0) + 16;

  if (position.rect) {
    left = position.rect.left + position.rect.width / 2 - rect.width / 2;
    top = position.rect.top - rect.height - 10;
    if (top < margin) top = position.rect.bottom + 10;
  }

  left = Math.min(Math.max(margin, left), window.innerWidth - rect.width - margin);
  top = Math.min(Math.max(margin, top), window.innerHeight - rect.height - margin);

  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(top)}px`;
}

/** Hide the shared tooltip. */
export function hideTooltip() {
  if (!tipNode) return;
  tipNode.classList.remove("is-open");
  tipNode.setAttribute("aria-hidden", "true");
}

/* ==========================================================================
   Legend
   ========================================================================== */

/**
 * A legend row. Present whenever a chart carries two or more series — it is
 * the dependable identity channel; direct labels only ever supplement it.
 *
 * @param {Array<{key, label, color, shape?, value?}>} items
 * @param {{onHover?: (key|null) => void, dense?: boolean}} [opts]
 */
export function legend(items, opts = {}) {
  const list = (items || []).filter(Boolean);
  if (!list.length) return null;

  const node = el("ul", {
    className: opts.dense ? "nv-legend is-dense" : "nv-legend",
    role: "list",
  });

  for (const item of list) {
    const key = applyPaint(
      el("span", { className: `nv-key nv-key-${item.shape || "rect"}` }),
      "backgroundColor",
      item.color || "var(--nv-muted)"
    );

    const row = el(
      "li",
      { className: "nv-legend-item", dataset: { key: item.key ?? item.label } },
      key,
      el("span", { className: "nv-legend-label" }, item.label),
      item.value !== undefined && item.value !== null
        ? el("span", { className: "nv-legend-value" }, item.value)
        : null
    );

    if (opts.onHover) {
      row.addEventListener("pointerenter", () => opts.onHover(item.key ?? item.label));
      row.addEventListener("pointerleave", () => opts.onHover(null));
    }
    node.append(row);
  }

  return node;
}

/* ==========================================================================
   Chart host
   --------------------------------------------------------------------------
   Owns everything a chart needs that is not its marks: sizing, the accessible
   wrapper, the first-paint animation, the empty state, the data-table twin and
   the uniform pointer/keyboard interaction model.
   ========================================================================== */

const SVG_NS = "http://www.w3.org/2000/svg";
let uid = 0;

/**
 * @typedef {object} ChartSpec
 * @property {string} title      Accessible name; also the table caption.
 * @property {string} desc       One sentence describing the encoding.
 * @property {number} [height]   Plot height in px (the axis band is added).
 * @property {object} [pad]      Padding overrides.
 * @property {boolean} [isEmpty] Force the empty state.
 * @property {string} [emptyText]
 * @property {(ctx) => void} render   Draws into `ctx.root` (an SVG <g>).
 * @property {() => {columns: string[], rows: Array<Array<string>>}} [table]
 * @property {Array} [legend]    Legend items.
 */

/**
 * Create a chart. Returns `{el, update, destroy, setBusy}`.
 *
 * @param {HTMLElement} container Mount point; contents are replaced.
 * @param {ChartSpec} spec
 */
export function createChart(container, spec) {
  const id = `nv${(uid += 1)}`;
  const figure = el("figure", { className: "nv-chart" });
  const plotHost = el("div", { className: "nv-plot" });
  const legendHost = el("div", { className: "nv-legend-host" });
  const dataHost = el("div", { className: "nv-data is-hidden" });

  const dataToggle = el(
    "button",
    {
      type: "button",
      className: "nv-data-toggle",
      "aria-expanded": "false",
      onClick: () => {
        const open = dataHost.classList.toggle("is-hidden") === false;
        dataToggle.setAttribute("aria-expanded", String(open));
        dataToggle.textContent = open ? "Hide data table" : "Show data table";
      },
    },
    "Show data table"
  );

  figure.append(plotHost, legendHost, el("div", { className: "nv-chart-foot" }, dataToggle), dataHost);

  let current = spec;
  let painted = false;
  let width = 0;
  let destroyed = false;
  let active = null;      // active mark index
  let interaction = null; // set by the factory via ctx.interactive()

  /* ---- accessibility: the figure is the keyboard entry point -------------- */
  figure.tabIndex = 0;
  figure.setAttribute("role", "group");

  function refreshAria() {
    figure.setAttribute(
      "aria-label",
      `${current.title}. ${current.desc || ""} Interactive chart — press the arrow keys to read individual values, Escape to dismiss.`
    );
  }

  /* ---- interaction ------------------------------------------------------- */

  function activate(index, position) {
    if (!interaction || index === null || index === undefined || index < 0) return deactivate();
    active = index;
    if (interaction.onActive) interaction.onActive(index);

    const spec2 = interaction.tipFor(index);
    if (!spec2) return hideTooltip();

    if (position) showTooltip(spec2, position);
    else {
      const box = interaction.focusRect ? interaction.focusRect(index) : null;
      const svgRect = plotHost.getBoundingClientRect();
      showTooltip(spec2, box
        ? { rect: { left: svgRect.left + box.x, top: svgRect.top + box.y, width: box.w, height: box.h } }
        : { rect: svgRect });
    }
    return undefined;
  }

  function deactivate() {
    active = null;
    if (interaction && interaction.onActive) interaction.onActive(null);
    hideTooltip();
  }

  function onPointerMove(event) {
    if (!interaction) return;
    const rect = plotHost.getBoundingClientRect();
    if (!rect.width) return;
    // The SVG is 1 user unit per CSS px, so no scaling maths is needed.
    const index = interaction.hitTest(event.clientX - rect.left, event.clientY - rect.top);
    if (index === null || index === undefined) return deactivate();
    return activate(index, { x: event.clientX, y: event.clientY });
  }

  function onKeyDown(event) {
    if (!interaction) return;
    const count = interaction.count;
    if (event.key === "Escape") {
      if (active !== null) { event.stopPropagation(); deactivate(); }
      return;
    }
    let next = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = active === null ? 0 : active + 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = active === null ? count - 1 : active - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = count - 1;
    else return;

    event.preventDefault();
    activate(Math.min(count - 1, Math.max(0, next)));
  }

  plotHost.addEventListener("pointermove", onPointerMove);
  plotHost.addEventListener("pointerleave", deactivate);
  figure.addEventListener("keydown", onKeyDown);
  figure.addEventListener("blur", deactivate);

  /* ---- render ------------------------------------------------------------ */

  function draw() {
    if (destroyed) return;
    const box = plotHost.getBoundingClientRect();
    // A container with no width means the view is hidden; wait for the observer.
    if (!box.width) return;
    width = Math.max(200, Math.round(box.width));

    interaction = null;
    active = null;
    hideTooltip();
    clear(plotHost);
    clear(legendHost);
    clear(dataHost);
    refreshAria();

    if (current.isEmpty) {
      figure.classList.add("is-empty");
      figure.removeAttribute("tabindex");
      plotHost.append(
        el(
          "div",
          { className: "nv-empty", style: { minHeight: `${current.height || 220}px` } },
          el("div", { className: "nv-empty-mark", "aria-hidden": "true" }),
          el("p", { className: "nv-empty-text" }, current.emptyText || "No data in this range yet.")
        )
      );
      dataToggle.hidden = true;
      return;
    }

    figure.classList.remove("is-empty");
    figure.tabIndex = 0;
    dataToggle.hidden = false;

    const pad = { ...PAD, ...(current.pad || {}) };
    const height = (current.height || 240) + pad.top + pad.bottom;
    const inner = {
      w: Math.max(40, width - pad.left - pad.right),
      h: Math.max(40, height - pad.top - pad.bottom),
    };

    const node = svg("svg", {
      class: painted ? "nv-svg" : "nv-svg nv-animate",
      viewBox: `0 0 ${width} ${height}`,
      width: "100%",
      height: String(height),
      preserveAspectRatio: "xMidYMid meet",
      role: "img",
      "aria-labelledby": `${id}-t ${id}-d`,
      focusable: "false",
    });

    const titleNode = document.createElementNS(SVG_NS, "title");
    titleNode.id = `${id}-t`;
    titleNode.textContent = current.title;
    const descNode = document.createElementNS(SVG_NS, "desc");
    descNode.id = `${id}-d`;
    descNode.textContent = current.desc || "";
    node.append(titleNode, descNode);

    const root = svg("g", { transform: `translate(${pad.left} ${pad.top})` });
    node.append(root);
    plotHost.append(node);

    const ctx = {
      svg: node,
      root,
      width,
      height,
      pad,
      inner,
      painted,
      /** Register the pointer/keyboard model for this chart. */
      interactive(model) { interaction = model; },
      /** Add a layer group in draw order. */
      layer(name) {
        const g = svg("g", { class: `nv-layer nv-layer-${name}` });
        root.append(g);
        return g;
      },
    };

    current.render(ctx);

    if (current.legend && current.legend.length > 1) {
      const items = current.legend;
      const node2 = legend(items, {
        onHover: (key) => {
          if (!key) { root.removeAttribute("data-emphasis"); return; }
          root.setAttribute("data-emphasis", key);
        },
      });
      if (node2) legendHost.append(node2);
    }

    if (current.table) {
      const { columns, rows } = current.table();
      dataHost.append(buildTable(current.title, columns, rows));
    }

    painted = true;
  }

  /* ---- lifecycle --------------------------------------------------------- */

  const observer = new ResizeObserver(() => {
    const box = plotHost.getBoundingClientRect();
    // Ignore sub-pixel jitter; re-render only on a real size change.
    if (Math.abs(Math.round(box.width) - width) < 2) return;
    draw();
  });

  clear(container);
  container.append(figure);
  observer.observe(plotHost);
  draw();

  return {
    el: figure,
    /** Replace the spec and re-render. Pass a partial patch. */
    update(patch) {
      current = { ...current, ...patch };
      // A data change is a new story: let it animate again only if it was empty.
      if (current.isEmpty) painted = false;
      draw();
    },
    /** Hold the previous render at reduced opacity while data reloads. */
    setBusy(busy) {
      figure.classList.toggle("is-busy", Boolean(busy));
    },
    destroy() {
      destroyed = true;
      observer.disconnect();
      plotHost.removeEventListener("pointermove", onPointerMove);
      plotHost.removeEventListener("pointerleave", deactivate);
      figure.removeEventListener("keydown", onKeyDown);
      figure.removeEventListener("blur", deactivate);
      hideTooltip();
      figure.remove();
    },
  };
}

/** The WCAG-clean twin of every chart. Cells are inserted as text. */
function buildTable(caption, columns, rows) {
  return el(
    "table",
    { className: "table table-compact nv-datatable" },
    el("caption", null, `${caption} — data table`),
    el("thead", null, el("tr", null, columns.map((c, i) => el("th", { scope: "col", className: i ? "num" : null }, c)))),
    el(
      "tbody",
      null,
      rows.map((row) =>
        el("tr", null, row.map((cell, i) => (i ? el("td", { className: "num" }, cell) : el("th", { scope: "row" }, cell))))
      )
    )
  );
}

/* ==========================================================================
   Axis drawing
   ========================================================================== */

/** Horizontal gridlines + left tick labels. Hairline, solid, recessive. */
function drawAxisY(g, scale, opts = {}) {
  const { width, ticks, format = formatTick, label } = opts;
  for (const value of ticks) {
    const y = Math.round(scale(value)) + 0.5;
    g.append(svg("line", { class: "nv-grid", x1: 0, x2: width, y1: y, y2: y }));
    g.append(svg("text", { class: "nv-tick nv-tick-y", x: -8, y, dy: "0.32em" }, format(value)));
  }
  if (label) {
    g.append(
      svg(
        "text",
        { class: "nv-axis-label", x: 0, y: -8, "text-anchor": "start" },
        label
      )
    );
  }
}

/**
 * X tick labels along the baseline, thinned so they never collide.
 * @param {Array<{x: number, label: string}>} items
 */
function drawAxisX(g, items, opts = {}) {
  const { height, width } = opts;
  g.append(svg("line", { class: "nv-axis", x1: 0, x2: width, y1: height + 0.5, y2: height + 0.5 }));

  let lastRight = -Infinity;
  for (const item of items) {
    const half = textWidth(item.label) / 2;
    if (item.x - half < lastRight + 8) continue;
    lastRight = item.x + half;
    g.append(
      svg("text", { class: "nv-tick nv-tick-x", x: item.x, y: height + 16, "text-anchor": "middle" }, item.label)
    );
  }
}

/**
 * Lay out a set of reference labels so no two chips overlap.
 *
 * Percentiles are defined BY the data, so a p85 line almost always has points sitting
 * on it, and neighbouring percentiles (p50/p70) frequently land within a few pixels of
 * each other. Drawing each chip where its line falls therefore produces stacked,
 * half-hidden labels. This nudges chips apart along the label axis, preserving order,
 * and returns the adjusted position for each input.
 *
 * @param {number[]} positions raw positions along the label axis, any order
 * @param {number} minGap minimum separation in px
 * @param {[number, number]} bounds inclusive [min, max] the chips must stay within
 * @returns {number[]} adjusted positions, aligned to the input order
 */
function spreadLabels(positions, minGap, bounds) {
  const order = positions.map((value, index) => ({ value, index }))
    .sort((a, b) => a.value - b.value);
  const [lo, hi] = bounds;

  let previous = -Infinity;
  for (const item of order) {                       // forward pass: push down-list items apart
    item.value = Math.max(item.value, previous + minGap, lo);
    previous = item.value;
  }
  previous = Infinity;
  for (let i = order.length - 1; i >= 0; i -= 1) {   // backward pass: pull back inside bounds
    order[i].value = Math.min(order[i].value, previous - minGap, hi);
    previous = order[i].value;
  }

  const out = new Array(positions.length);
  for (const item of order) out[item.index] = item.value;
  return out;
}

/**
 * A labelled reference line — percentiles, SLE targets, "today".
 *
 * `opts.labelG` puts the chip in a different layer from the line. The line belongs
 * UNDER the data marks so it never hides a point; the chip belongs ABOVE them, or the
 * marks paint over the text and the label becomes unreadable — which defeats the whole
 * purpose of marking a percentile.
 */
function drawReference(g, opts) {
  const { orientation = "h", at, from, to, label, tone = "neutral", dashed = false } = opts;
  const labelG = opts.labelG || g;
  const labelAt = opts.labelAt ?? at;
  // Where the chip is anchored, independent of where the line ends — lets a caller park
  // the chip in a reserved margin so it never covers a data mark.
  const labelTo = opts.labelTo ?? to;
  const line = orientation === "h"
    ? { x1: from, x2: to, y1: Math.round(at) + 0.5, y2: Math.round(at) + 0.5 }
    : { x1: Math.round(at) + 0.5, x2: Math.round(at) + 0.5, y1: from, y2: to };

  g.append(svg("line", { class: `nv-ref nv-ref-${tone}${dashed ? " is-dashed" : ""}`, ...line }));

  if (!label) return;
  const chip = svg("g", { class: `nv-ref-label nv-ref-${tone}` });
  const w = textWidth(label, 10) + 10;
  if (orientation === "h") {
    chip.append(svg("rect", { class: "nv-ref-chip", x: labelTo - w, y: labelAt - 8, width: w, height: 16, rx: 3 }));
    chip.append(svg("text", { class: "nv-ref-text", x: labelTo - w / 2, y: labelAt, dy: "0.32em", "text-anchor": "middle" }, label));
  } else {
    chip.append(svg("rect", { class: "nv-ref-chip", x: labelAt - w / 2, y: from - 18, width: w, height: 16, rx: 3 }));
    chip.append(svg("text", { class: "nv-ref-text", x: labelAt, y: from - 10, dy: "0.32em", "text-anchor": "middle" }, label));
  }
  labelG.append(chip);
}

/** Rounded-end bar path: square at the baseline, 4px radius at the data end. */
function barPath(x, y, w, h, side = "top", radius = 4) {
  const r = Math.max(0, Math.min(radius, w / 2, h));
  if (h <= 0.5) return `M${x} ${y}h${w}`;
  switch (side) {
    case "right":
      return `M${x} ${y}h${w - r}a${r} ${r} 0 0 1 ${r} ${r}v${h - r * 2}a${r} ${r} 0 0 1 ${-r} ${r}H${x}Z`;
    case "left":
      return `M${x + w} ${y}H${x + r}a${r} ${r} 0 0 0 ${-r} ${r}v${h - r * 2}a${r} ${r} 0 0 0 ${r} ${r}H${x + w}Z`;
    default:
      return `M${x} ${y + h}V${y + r}a${r} ${r} 0 0 1 ${r} ${-r}h${w - r * 2}a${r} ${r} 0 0 1 ${r} ${r}v${h - r}Z`;
  }
}

/* ==========================================================================
   1. stackedArea — Cumulative Flow Diagram
   ========================================================================== */

/**
 * Stacked area chart.
 *
 * @param {HTMLElement} container
 * @param {object} opts
 * @param {Array<number|string|Date>} opts.x       n x-positions (dates)
 * @param {Array<{key, label, values: number[]}>} opts.series bottom band first
 * @param {string} [opts.yLabel]
 * @param {number} [opts.height]
 * @param {string} [opts.title] @param {string} [opts.desc]
 */
export function stackedArea(container, opts) {
  return createChart(container, buildStackedArea(opts));
}

function buildStackedArea(opts) {
  const xs = (opts.x || []).map(toMs).filter((v) => v !== null);
  const series = (opts.series || []).filter((s) => s && Array.isArray(s.values));
  const n = xs.length;
  const isEmpty = n < 2 || !series.length;

  return {
    title: opts.title || "Cumulative flow",
    desc: opts.desc || "Stacked bands show how many items had reached each stage on each day; band thickness is the work in progress in that stage.",
    height: opts.height || 260,
    isEmpty,
    emptyText: opts.emptyText || "No flow history in this range.",
    legend: series.map((s, i) => ({
      key: s.key ?? s.label,
      label: s.label,
      color: rampColor(i, series.length),
      shape: "rect",
    })),
    table: () => ({
      columns: ["Date", ...series.map((s) => s.label)],
      rows: xs.map((ms, i) => [formatDateLong(ms), ...series.map((s) => formatTick(s.values[i] ?? 0))]),
    }),
    render(ctx) {
      const { inner, root } = ctx;

      // Cumulative tops, bottom band first.
      const tops = [];
      let running = new Array(n).fill(0);
      for (const s of series) {
        running = running.map((v, i) => v + Math.max(0, Number(s.values[i]) || 0));
        tops.push(running.slice());
      }
      const maxY = Math.max(1, ...running);

      const x = timeScale([xs[0], xs[n - 1]], [0, inner.w]);
      const { ticks: yTicks, max } = niceTicks(0, maxY, Math.max(3, Math.round(inner.h / 56)));
      const y = linearScale([0, max], [inner.h, 0]);

      const grid = ctx.layer("grid");
      drawAxisY(grid, y, { width: inner.w, ticks: yTicks, label: opts.yLabel || "Items" });

      const areas = ctx.layer("areas");
      const edges = ctx.layer("edges");

      for (let s = series.length - 1; s >= 0; s -= 1) {
        const top = tops[s];
        const base = s === 0 ? new Array(n).fill(0) : tops[s - 1];

        let d = `M${x(xs[0]).toFixed(2)} ${y(top[0]).toFixed(2)}`;
        for (let i = 1; i < n; i += 1) d += `L${x(xs[i]).toFixed(2)} ${y(top[i]).toFixed(2)}`;
        for (let i = n - 1; i >= 0; i -= 1) d += `L${x(xs[i]).toFixed(2)} ${y(base[i]).toFixed(2)}`;
        d += "Z";

        const path = svg("path", { class: "nv-area", d, dataset: { key: series[s].key ?? series[s].label } });
        applyPaint(path, "fill", rampColor(s, series.length));
        areas.append(path);

        // The 2px surface gap that separates touching bands. It is the surface
        // colour doing the separating — never a border in a different hue.
        if (s < series.length - 1 || series.length > 1) {
          let line = `M${x(xs[0]).toFixed(2)} ${y(top[0]).toFixed(2)}`;
          for (let i = 1; i < n; i += 1) line += `L${x(xs[i]).toFixed(2)} ${y(top[i]).toFixed(2)}`;
          edges.append(svg("path", { class: "nv-area-gap", d: line }));
        }
      }

      const axis = ctx.layer("axis");
      const format = x.tickFormat();
      drawAxisX(axis, x.ticks(Math.max(3, Math.round(inner.w / 92))).map((ms) => ({ x: x(ms), label: format(ms) })), {
        height: inner.h,
        width: inner.w,
      });

      /* ---- crosshair ---------------------------------------------------- */
      const cursor = ctx.layer("cursor");
      const hair = svg("line", { class: "nv-crosshair", y1: 0, y2: inner.h, x1: 0, x2: 0, opacity: "0" });
      cursor.append(hair);
      const dots = svg("g", { class: "nv-crosshair-dots", opacity: "0" });
      cursor.append(dots);

      ctx.interactive({
        count: n,
        hitTest(px) {
          const local = px - ctx.pad.left;
          if (local < -HIT / 2 || local > inner.w + HIT / 2) return null;
          const ms = x.invert(Math.min(inner.w, Math.max(0, local)));
          // Nearest index — readers aim at a date, never at a 2px line.
          let best = 0;
          let bestDist = Infinity;
          for (let i = 0; i < n; i += 1) {
            const dist = Math.abs(xs[i] - ms);
            if (dist < bestDist) { bestDist = dist; best = i; }
          }
          return best;
        },
        onActive(index) {
          if (index === null) {
            hair.setAttribute("opacity", "0");
            dots.setAttribute("opacity", "0");
            return;
          }
          const px = x(xs[index]);
          hair.setAttribute("x1", px);
          hair.setAttribute("x2", px);
          hair.setAttribute("opacity", "1");
          clear(dots);
          for (let s = 0; s < series.length; s += 1) {
            const dot = svg("circle", { class: "nv-crosshair-dot", cx: px, cy: y(tops[s][index]), r: 3 });
            applyPaint(dot, "fill", rampColor(s, series.length));
            dots.append(dot);
          }
          dots.setAttribute("opacity", "1");
        },
        focusRect(index) {
          return { x: ctx.pad.left + x(xs[index]) - 1, y: ctx.pad.top, w: 2, h: inner.h };
        },
        tipFor(index) {
          const rows = [];
          for (let s = series.length - 1; s >= 0; s -= 1) {
            const value = Math.max(0, Number(series[s].values[index]) || 0);
            rows.push({
              label: series[s].label,
              value: formatTick(value),
              color: rampColor(s, series.length),
              shape: "rect",
            });
          }
          return {
            title: formatDateLong(xs[index]),
            subtitle: `${formatTick(running[index])} items in flow`,
            rows,
          };
        },
      });
    },
  };
}

/* ==========================================================================
   2. scatter — lead-time scatter plot
   ========================================================================== */

/**
 * Scatter plot with percentile reference lines.
 *
 * @param {HTMLElement} container
 * @param {object} opts
 * @param {Array<{x, y, group?, label?, sublabel?}>} opts.points
 * @param {Array<{key, label}>} [opts.groups]
 * @param {Array<{key, label, value}>} [opts.percentiles]
 */
export function scatter(container, opts) {
  return createChart(container, buildScatter(opts));
}

function buildScatter(opts) {
  const points = (opts.points || [])
    .map((p) => ({ ...p, ms: toMs(p.x), value: Number(p.y) }))
    .filter((p) => p.ms !== null && Number.isFinite(p.value))
    .sort((a, b) => a.ms - b.ms);

  const groups = opts.groups && opts.groups.length
    ? opts.groups
    : SERVICE_CLASSES.map((key) => ({ key, label: SERVICE_CLASS_LABEL[key] }));

  const percentiles = (opts.percentiles || []).filter((p) => Number.isFinite(Number(p.value)));
  const formatY = opts.formatY || formatDays;

  // Reserve a gutter wide enough for the widest percentile chip so the chips live in the
  // right margin instead of on top of the plot. Layering alone was not enough: putting
  // the chips above the marks stopped points hiding labels but started labels hiding
  // points (4 of 51 delivered items vanished under a chip). Neither the data nor its
  // annotation may be sacrificed, so they get separate real estate.
  const chipGutter = percentiles.length
    ? Math.ceil(Math.max(
      ...percentiles.map((p) => textWidth(`${p.label || p.key} ${formatY(Number(p.value))}`, 10) + 10)
    )) + 10
    : 0;

  return {
    title: opts.title || "Lead time",
    desc: opts.desc || "One mark per delivered item, positioned by delivery date and lead time; horizontal lines mark the percentiles of the distribution.",
    height: opts.height || 260,
    pad: { ...PAD, right: Math.max(PAD.right, chipGutter) },
    isEmpty: !points.length,
    emptyText: opts.emptyText || "Nothing delivered in this range.",
    legend: groups.map((g) => ({
      key: g.key,
      label: g.label,
      color: serviceClassColor(g.key),
      shape: serviceClassShape(g.key),
    })),
    table: () => ({
      columns: ["Item", "Delivered", "Lead time", "Class of service"],
      rows: points.map((p) => [
        p.label || "Item",
        formatDateLong(p.ms),
        formatY(p.value),
        SERVICE_CLASS_LABEL[p.group] || "—",
      ]),
    }),
    render(ctx) {
      const { inner } = ctx;
      const xsMin = points[0].ms;
      const xsMax = points[points.length - 1].ms;
      const maxY = Math.max(...points.map((p) => p.value), ...percentiles.map((p) => Number(p.value)));

      const x = timeScale([xsMin, xsMax === xsMin ? xsMin + MS_DAY : xsMax], [0, inner.w]);
      const { ticks: yTicks, max } = niceTicks(0, maxY, Math.max(3, Math.round(inner.h / 52)));
      const y = linearScale([0, max], [inner.h, 0]);

      const grid = ctx.layer("grid");
      drawAxisY(grid, y, { width: inner.w, ticks: yTicks, format: formatY, label: opts.yLabel || "Lead time" });

      // Percentile lines sit under the marks so no data point is hidden; their labels
      // sit above (see the refLabels layer created after marks) so no data point hides
      // THEM. Chip positions are spread so neighbouring percentiles stay readable.
      const refs = ctx.layer("refs");
      const marks = ctx.layer("marks");
      const refLabels = ctx.layer("ref-labels");
      const refAt = percentiles.map((p) => y(Number(p.value)));
      const labelAt = spreadLabels(refAt, 18, [8, inner.h - 8]);
      percentiles.forEach((p, i) => {
        const label = `${p.label || p.key} ${formatY(Number(p.value))}`;
        drawReference(refs, {
          orientation: "h",
          at: refAt[i],
          labelAt: labelAt[i],
          labelG: refLabels,
          from: 0,
          to: inner.w,
          // The line stops at the plot edge; the chip is anchored further right, parked
          // in the reserved gutter clear of every data mark.
          labelTo: inner.w + (chipGutter ? chipGutter - 6 : 0),
          label,
          tone: p.tone || "neutral",
        });
      });

      const positions = points.map((p) => ({ px: x(p.ms), py: y(p.value) }));

      points.forEach((p, i) => {
        const shape = serviceClassShape(p.group);
        const node = svg("path", {
          class: "nv-dot",
          d: shapePath(shape, positions[i].px, positions[i].py, DOT_R),
          dataset: { key: p.group || "standard", index: String(i) },
        });
        applyPaint(node, "fill", serviceClassColor(p.group));
        marks.append(node);
      });

      const axis = ctx.layer("axis");
      const format = x.tickFormat();
      drawAxisX(axis, x.ticks(Math.max(3, Math.round(inner.w / 92))).map((ms) => ({ x: x(ms), label: format(ms) })), {
        height: inner.h,
        width: inner.w,
      });

      const halo = svg("circle", { class: "nv-dot-halo", r: DOT_R + 5, opacity: "0", cx: 0, cy: 0 });
      ctx.layer("cursor").append(halo);

      ctx.interactive({
        count: points.length,
        // Nearest-point layer: the pointer only has to be closest, not centred.
        hitTest(px, py) {
          const lx = px - ctx.pad.left;
          const ly = py - ctx.pad.top;
          let best = null;
          let bestDist = HIT * HIT;
          for (let i = 0; i < positions.length; i += 1) {
            const dx = positions[i].px - lx;
            const dy = positions[i].py - ly;
            const dist = dx * dx + dy * dy;
            if (dist < bestDist) { bestDist = dist; best = i; }
          }
          return best;
        },
        onActive(index) {
          if (index === null) return halo.setAttribute("opacity", "0");
          halo.setAttribute("cx", positions[index].px);
          halo.setAttribute("cy", positions[index].py);
          applyPaint(halo, "stroke", serviceClassColor(points[index].group));
          halo.setAttribute("opacity", "1");
          return undefined;
        },
        focusRect(index) {
          return {
            x: ctx.pad.left + positions[index].px - HIT / 2,
            y: ctx.pad.top + positions[index].py - HIT / 2,
            w: HIT,
            h: HIT,
          };
        },
        tipFor(index) {
          const p = points[index];
          return {
            title: p.label || "Item",
            subtitle: p.sublabel || formatDateLong(p.ms),
            rows: [
              { label: "Lead time", value: formatY(p.value), emphasis: true },
              {
                label: SERVICE_CLASS_LABEL[p.group] || "Class of service",
                value: formatDateLong(p.ms),
                color: serviceClassColor(p.group),
                shape: serviceClassShape(p.group),
              },
            ],
          };
        },
      });
    },
  };
}

/* ==========================================================================
   3. bars — throughput columns with a rolling-average overlay
   ========================================================================== */

/**
 * Column chart, optionally with one line overlay on the SAME scale.
 * A second y-axis is never offered: two scales on one plot invent correlations.
 *
 * @param {object} opts
 * @param {Array<{label, value, sublabel?}>} opts.items
 * @param {{label: string, values: Array<number|null>}} [opts.overlay]
 */
export function bars(container, opts) {
  return createChart(container, buildBars(opts));
}

function buildBars(opts) {
  const items = (opts.items || []).filter(Boolean);
  const overlay = opts.overlay && Array.isArray(opts.overlay.values) ? opts.overlay : null;
  const formatValue = opts.formatValue || formatTick;
  const barColor = opts.color || "var(--nv-ramp-a)";

  return {
    title: opts.title || "Throughput",
    desc: opts.desc || "One column per period showing how many items were delivered; the line is the rolling average of the same measure.",
    height: opts.height || 220,
    isEmpty: !items.length,
    emptyText: opts.emptyText || "No completed items in this range.",
    legend: overlay
      ? [
          { key: "value", label: opts.valueLabel || "Delivered", color: barColor, shape: "rect" },
          { key: "overlay", label: overlay.label, color: "var(--nv-ink)", shape: "line" },
        ]
      : null,
    table: () => ({
      columns: ["Period", opts.valueLabel || "Delivered", ...(overlay ? [overlay.label] : [])],
      rows: items.map((item, i) => [
        item.sublabel || item.label,
        formatValue(item.value),
        ...(overlay ? [overlay.values[i] === null || overlay.values[i] === undefined ? "—" : formatValue(overlay.values[i])] : []),
      ]),
    }),
    render(ctx) {
      const { inner } = ctx;
      const values = items.map((d) => Number(d.value) || 0);
      const overlayValues = overlay ? overlay.values.map((v) => (v === null || v === undefined ? null : Number(v))) : [];
      const maxY = Math.max(1, ...values, ...overlayValues.filter((v) => v !== null));

      const x = bandScale(items.map((_, i) => String(i)), [0, inner.w], { padding: 0.34 });
      const { ticks: yTicks, max } = niceTicks(0, maxY, Math.max(3, Math.round(inner.h / 48)));
      const y = linearScale([0, max], [inner.h, 0]);

      const grid = ctx.layer("grid");
      drawAxisY(grid, y, { width: inner.w, ticks: yTicks, format: formatValue, label: opts.yLabel });

      const marks = ctx.layer("marks");
      const band = Math.min(MAX_BAR, x.bandwidth());
      const offset = (x.bandwidth() - band) / 2;
      const geometry = [];

      items.forEach((item, i) => {
        const value = values[i];
        const px = x(String(i)) + offset;
        const py = y(value);
        const h = inner.h - py;
        geometry.push({ x: px, y: py, w: band, h });
        const node = svg("path", {
          class: "nv-bar",
          d: barPath(px, py, band, h, "top"),
          dataset: { index: String(i) },
        });
        applyPaint(node, "fill", barColor);
        marks.append(node);
      });

      if (overlay) {
        const line = ctx.layer("overlay");
        let d = "";
        overlayValues.forEach((value, i) => {
          if (value === null) return;
          const px = x(String(i)) + x.bandwidth() / 2;
          d += `${d ? "L" : "M"}${px.toFixed(2)} ${y(value).toFixed(2)}`;
        });
        if (d) line.append(svg("path", { class: "nv-line nv-line-ink", d }));

        // Direct-label the endpoint only — a number on every point goes unread.
        const lastIndex = overlayValues.reduce((acc, v, i) => (v === null ? acc : i), -1);
        if (lastIndex >= 0) {
          const px = x(String(lastIndex)) + x.bandwidth() / 2;
          const py = y(overlayValues[lastIndex]);
          line.append(svg("circle", { class: "nv-line-end", cx: px, cy: py, r: 4 }));
          const label = formatValue(overlayValues[lastIndex]);
          const anchorRight = px + textWidth(label) + 14 > inner.w;
          line.append(
            svg(
              "text",
              {
                class: "nv-point-label",
                x: anchorRight ? px - 9 : px + 9,
                y: py - 8,
                "text-anchor": anchorRight ? "end" : "start",
              },
              label
            )
          );
        }
      }

      const axis = ctx.layer("axis");
      drawAxisX(
        axis,
        items.map((item, i) => ({ x: x(String(i)) + x.bandwidth() / 2, label: item.label })),
        { height: inner.h, width: inner.w }
      );

      const highlight = svg("rect", { class: "nv-bar-highlight", opacity: "0", x: 0, y: 0, width: 0, height: 0, rx: 4 });
      ctx.layer("cursor").append(highlight);

      ctx.interactive({
        count: items.length,
        hitTest(px) {
          const key = x.at(px - ctx.pad.left);
          return key === null ? null : Number(key);
        },
        onActive(index) {
          if (index === null) return highlight.setAttribute("opacity", "0");
          const g = geometry[index];
          highlight.setAttribute("x", x(String(index)));
          highlight.setAttribute("y", 0);
          highlight.setAttribute("width", x.bandwidth());
          highlight.setAttribute("height", inner.h);
          highlight.setAttribute("opacity", "1");
          return g;
        },
        focusRect(index) {
          const g = geometry[index];
          return { x: ctx.pad.left + g.x, y: ctx.pad.top + g.y, w: g.w, h: Math.max(g.h, 2) };
        },
        tipFor(index) {
          const item = items[index];
          const rows = [{ label: opts.valueLabel || "Delivered", value: formatValue(values[index]), color: barColor, shape: "rect" }];
          if (overlay && overlayValues[index] !== null && overlayValues[index] !== undefined) {
            rows.push({ label: overlay.label, value: formatValue(overlayValues[index]), color: "var(--nv-ink)", shape: "line" });
          }
          return { title: item.sublabel || item.label, rows };
        },
      });
    },
  };
}

/* ==========================================================================
   4. histogram — lead-time distribution
   ========================================================================== */

/**
 * @param {object} opts
 * @param {Array<{from: number, to: number, count: number}>} opts.bins
 * @param {Array<{key, label, value, tone?}>} [opts.markers] percentile markers
 */
export function histogram(container, opts) {
  return createChart(container, buildHistogram(opts));
}

function buildHistogram(opts) {
  const bins = (opts.bins || []).filter((b) => b && Number.isFinite(Number(b.count)));
  const markers = (opts.markers || []).filter((m) => Number.isFinite(Number(m.value)));
  const formatX = opts.formatX || formatDays;

  return {
    title: opts.title || "Lead-time distribution",
    desc: opts.desc || "How many delivered items fell into each lead-time bucket, with the distribution percentiles marked.",
    height: opts.height || 220,
    pad: { ...PAD, top: 26 },
    isEmpty: !bins.length,
    emptyText: opts.emptyText || "Not enough delivered items to build a distribution.",
    table: () => ({
      columns: ["Bucket", "Items"],
      rows: bins.map((b) => [`${formatX(b.from)} – ${formatX(b.to)}`, formatTick(b.count)]),
    }),
    render(ctx) {
      const { inner } = ctx;
      const lo = Math.min(...bins.map((b) => Number(b.from)));
      const hi = Math.max(...bins.map((b) => Number(b.to)));
      const maxCount = Math.max(1, ...bins.map((b) => Number(b.count)));

      const x = linearScale([lo, hi], [0, inner.w]);
      const { ticks: yTicks, max } = niceTicks(0, maxCount, Math.max(3, Math.round(inner.h / 48)));
      const y = linearScale([0, max], [inner.h, 0]);

      const grid = ctx.layer("grid");
      drawAxisY(grid, y, { width: inner.w, ticks: yTicks, label: opts.yLabel || "Items" });

      const marks = ctx.layer("marks");
      const geometry = [];

      bins.forEach((bin, i) => {
        const x0 = x(Number(bin.from));
        const x1 = x(Number(bin.to));
        const w = Math.max(1, x1 - x0 - GAP);
        const py = y(Number(bin.count));
        const h = inner.h - py;
        geometry.push({ x: x0, y: py, w, h });
        const node = svg("path", { class: "nv-bar", d: barPath(x0, py, w, h, "top"), dataset: { index: String(i) } });
        applyPaint(node, "fill", opts.color || "var(--nv-ramp-a)");
        marks.append(node);
      });

      // Percentile markers ride above the bars where they stay readable. Their chips
      // are spread horizontally: p50 and p70 often fall within a few pixels of each
      // other, and an overlapping chip's opaque background hides the neighbour's value
      // — which is how "p50 24d" ended up rendering as a bare "50".
      const refs = ctx.layer("refs");
      const markerLabels = markers.map(
        (m) => `${m.label || m.key} ${formatX(Number(m.value))}`
      );
      const halfWidest = markerLabels.reduce(
        (widest, label) => Math.max(widest, textWidth(label, 10) + 10), 0
      ) / 2;
      const markerAt = markers.map((m) => x(Number(m.value)));
      const markerLabelAt = spreadLabels(
        markerAt, halfWidest * 2 + 4, [halfWidest, inner.w - halfWidest]
      );
      markers.forEach((marker, i) => {
        drawReference(refs, {
          orientation: "v",
          at: markerAt[i],
          labelAt: markerLabelAt[i],
          from: 0,
          to: inner.h,
          label: markerLabels[i],
          tone: marker.tone || "neutral",
        });
      });

      const axis = ctx.layer("axis");
      const xTicks = niceTicks(lo, hi, Math.max(3, Math.round(inner.w / 80))).ticks.filter((t) => t >= lo && t <= hi);
      drawAxisX(axis, xTicks.map((t) => ({ x: x(t), label: formatX(t) })), { height: inner.h, width: inner.w });

      const highlight = svg("rect", { class: "nv-bar-highlight", opacity: "0", x: 0, y: 0, width: 0, height: 0, rx: 4 });
      ctx.layer("cursor").append(highlight);

      ctx.interactive({
        count: bins.length,
        hitTest(px) {
          const local = px - ctx.pad.left;
          const value = x.invert(local);
          const index = bins.findIndex((b) => value >= Number(b.from) && value <= Number(b.to));
          return index === -1 ? null : index;
        },
        onActive(index) {
          if (index === null) return highlight.setAttribute("opacity", "0");
          const g = geometry[index];
          highlight.setAttribute("x", g.x - GAP / 2);
          highlight.setAttribute("y", 0);
          highlight.setAttribute("width", g.w + GAP);
          highlight.setAttribute("height", inner.h);
          highlight.setAttribute("opacity", "1");
          return undefined;
        },
        focusRect(index) {
          const g = geometry[index];
          return { x: ctx.pad.left + g.x, y: ctx.pad.top + g.y, w: g.w, h: Math.max(g.h, 2) };
        },
        tipFor(index) {
          const bin = bins[index];
          const share = (Number(bin.count) / bins.reduce((sum, b) => sum + Number(b.count), 0)) * 100;
          return {
            title: `${formatX(bin.from)} – ${formatX(bin.to)}`,
            rows: [
              { label: "Items", value: formatTick(bin.count), emphasis: true },
              { label: "Share", value: `${share.toFixed(share < 10 ? 1 : 0)}%` },
            ],
          };
        },
      });
    },
  };
}

/* ==========================================================================
   5. dotPlot — aging work in progress
   ========================================================================== */

/**
 * One dot per in-flight card, grouped into columns on X, with percentile
 * bands painted behind as background regions.
 *
 * @param {object} opts
 * @param {Array<{key, label}>} opts.columns
 * @param {Array<{column, y, group?, label?, sublabel?}>} opts.points
 * @param {Array<{key, label, value, tone?}>} [opts.bands] percentile thresholds
 */
export function dotPlot(container, opts) {
  return createChart(container, buildDotPlot(opts));
}

function buildDotPlot(opts) {
  const columns = (opts.columns || []).filter(Boolean);
  const points = (opts.points || [])
    .map((p) => ({ ...p, value: Number(p.y) }))
    .filter((p) => Number.isFinite(p.value));
  const bands = (opts.bands || [])
    .filter((b) => Number.isFinite(Number(b.value)))
    .sort((a, b) => Number(a.value) - Number(b.value));
  const formatY = opts.formatY || formatDays;

  // Same reserved gutter as the scatter: band chips are right-anchored, and without
  // their own margin they simply trade "dots hide the label" for "the label hides dots".
  const chipGutter = bands.length
    ? Math.ceil(Math.max(
      ...bands.map((b) => textWidth(`${b.label || b.key} ${formatY(Number(b.value))}`, 10) + 10)
    )) + 10
    : 0;

  return {
    title: opts.title || "Aging work in progress",
    desc: opts.desc || "Every item currently in flight, placed in its column and plotted by how long it has been in progress; shaded bands are the historical lead-time percentiles.",
    height: opts.height || 260,
    pad: { ...PAD, right: Math.max(PAD.right, chipGutter) },
    isEmpty: !columns.length || !points.length,
    emptyText: opts.emptyText || "Nothing is in flight right now.",
    legend: SERVICE_CLASSES.map((key) => ({
      key,
      label: SERVICE_CLASS_LABEL[key],
      color: serviceClassColor(key),
      shape: serviceClassShape(key),
    })),
    table: () => ({
      columns: ["Item", "Column", "Age", "Class of service"],
      rows: points.map((p) => [
        p.label || "Item",
        columns.find((c) => c.key === p.column)?.label || "—",
        formatY(p.value),
        SERVICE_CLASS_LABEL[p.group] || "—",
      ]),
    }),
    render(ctx) {
      const { inner } = ctx;
      const maxY = Math.max(
        ...points.map((p) => p.value),
        ...bands.map((b) => Number(b.value)),
        1
      );

      const x = bandScale(columns.map((c) => c.key), [0, inner.w], { padding: 0.18 });
      const { ticks: yTicks, max } = niceTicks(0, maxY, Math.max(3, Math.round(inner.h / 52)));
      const y = linearScale([0, max], [inner.h, 0]);

      /* ---- percentile bands, painted behind everything ------------------ */
      const bg = ctx.layer("bands");
      const deferredBandLabels = [];
      let previous = 0;
      const tones = ["ok", "caution", "warn", "danger"];
      bands.forEach((band, i) => {
        const value = Number(band.value);
        const yTop = y(value);
        const yBottom = y(previous);
        bg.append(
          svg("rect", {
            class: `nv-band nv-band-${tones[Math.min(i, tones.length - 1)]}`,
            x: 0,
            y: yTop,
            width: inner.w,
            height: Math.max(0, yBottom - yTop),
          })
        );
        // The chip is deferred, not drawn here: this layer sits beneath the dots, and a
        // percentile label hidden by the very points that define it is useless. The line
        // stays down here (so it never covers a dot); the label is emitted into a layer
        // created after `marks` below.
        deferredBandLabels.push({
          at: yTop,
          label: `${band.label || band.key} ${formatY(value)}`,
          tone: band.tone || "neutral",
        });
        previous = value;
      });
      if (bands.length) {
        bg.append(
          svg("rect", {
            class: "nv-band nv-band-danger",
            x: 0,
            y: 0,
            width: inner.w,
            height: Math.max(0, y(previous)),
          })
        );
      }

      const grid = ctx.layer("grid");
      drawAxisY(grid, y, { width: inner.w, ticks: yTicks, format: formatY, label: opts.yLabel || "Age" });

      /* ---- dots, deterministically spread inside their column ----------- */
      const marks = ctx.layer("marks");

      // Created after `marks`, so band labels paint above the dots. Spread apart for the
      // same reason as the scatter's: adjacent percentiles often land within a few px.
      if (deferredBandLabels.length) {
        const bandLabels = ctx.layer("band-labels");
        const labelAt = spreadLabels(
          deferredBandLabels.map((b) => b.at), 18, [8, inner.h - 8]
        );
        deferredBandLabels.forEach((band, i) => {
          drawReference(bg, {
            orientation: "h",
            at: band.at,
            labelAt: labelAt[i],
            labelG: bandLabels,
            from: 0,
            to: inner.w,
            labelTo: inner.w + (chipGutter ? chipGutter - 6 : 0),
            label: band.label,
            tone: band.tone,
          });
        });
      }

      const byColumn = new Map();
      for (const p of points) {
        if (!byColumn.has(p.column)) byColumn.set(p.column, []);
        byColumn.get(p.column).push(p);
      }

      const positions = new Array(points.length);
      const ordered = [];
      for (const column of columns) {
        const list = (byColumn.get(column.key) || []).slice().sort((a, b) => b.value - a.value);
        const centre = x.center(column.key);
        const half = Math.max(6, x.bandwidth() / 2 - DOT_R - 2);
        list.forEach((p, i) => {
          // Fan out symmetrically around the column centre so overlapping ages
          // stay countable: 0, +1, -1, +2, -2 …
          const rank = Math.ceil(i / 2) * (i % 2 === 0 ? 1 : -1);
          const spread = Math.min(half, (DOT_R * 2 + 1) * Math.abs(rank));
          const px = centre + Math.sign(rank) * spread;
          const index = ordered.length;
          ordered.push(p);
          positions[index] = { px, py: y(p.value) };

          const node = svg("path", {
            class: "nv-dot nv-dot-ring",
            d: shapePath(serviceClassShape(p.group), px, y(p.value), DOT_R),
            dataset: { key: p.group || "standard" },
          });
          applyPaint(node, "fill", serviceClassColor(p.group));
          marks.append(node);
        });
      }

      const axis = ctx.layer("axis");
      drawAxisX(axis, columns.map((c) => ({ x: x.center(c.key), label: c.label })), {
        height: inner.h,
        width: inner.w,
      });

      const halo = svg("circle", { class: "nv-dot-halo", r: DOT_R + 5, opacity: "0", cx: 0, cy: 0 });
      ctx.layer("cursor").append(halo);

      ctx.interactive({
        count: ordered.length,
        hitTest(px, py) {
          const lx = px - ctx.pad.left;
          const ly = py - ctx.pad.top;
          let best = null;
          let bestDist = HIT * HIT;
          for (let i = 0; i < positions.length; i += 1) {
            const dx = positions[i].px - lx;
            const dy = positions[i].py - ly;
            const dist = dx * dx + dy * dy;
            if (dist < bestDist) { bestDist = dist; best = i; }
          }
          return best;
        },
        onActive(index) {
          if (index === null) return halo.setAttribute("opacity", "0");
          halo.setAttribute("cx", positions[index].px);
          halo.setAttribute("cy", positions[index].py);
          applyPaint(halo, "stroke", serviceClassColor(ordered[index].group));
          halo.setAttribute("opacity", "1");
          return undefined;
        },
        focusRect(index) {
          return {
            x: ctx.pad.left + positions[index].px - HIT / 2,
            y: ctx.pad.top + positions[index].py - HIT / 2,
            w: HIT,
            h: HIT,
          };
        },
        tipFor(index) {
          const p = ordered[index];
          const column = columns.find((c) => c.key === p.column);
          return {
            title: p.label || "Item",
            subtitle: p.sublabel || column?.label || "",
            rows: [
              { label: "Age", value: formatY(p.value), emphasis: true },
              {
                label: SERVICE_CLASS_LABEL[p.group] || "Class of service",
                value: column?.label || "",
                color: serviceClassColor(p.group),
                shape: serviceClassShape(p.group),
              },
            ],
          };
        },
      });
    },
  };
}

/* ==========================================================================
   6. hbar — horizontal bars, optionally stacked
   ========================================================================== */

/**
 * Horizontal bar chart. Used for the blocker pareto (sorted descending, with
 * the running share as a direct label — never as a second y-axis) and for the
 * per-column wait/touch split (two stacked segments).
 *
 * @param {object} opts
 * @param {Array<{label, sublabel?, segments: Array<{key, value}>, note?}>} opts.rows
 * @param {Array<{key, label, color}>} opts.series
 */
export function hbar(container, opts) {
  return createChart(container, buildHbar(opts));
}

function buildHbar(opts) {
  const rows = (opts.rows || []).filter(Boolean);
  const series = (opts.series || []).filter(Boolean);
  const formatValue = opts.formatValue || formatTick;
  const rowHeight = opts.rowHeight || 30;

  const totals = rows.map((r) => (r.segments || []).reduce((sum, s) => sum + (Number(s.value) || 0), 0));

  return {
    title: opts.title || "Breakdown",
    desc: opts.desc || "Horizontal bars comparing each category; longer is more.",
    height: Math.max(90, rows.length * rowHeight),
    pad: { top: 8, right: opts.noteWidth || 56, bottom: 26, left: opts.labelWidth || 132 },
    isEmpty: !rows.length || totals.every((t) => t === 0),
    emptyText: opts.emptyText || "Nothing recorded in this range.",
    legend: series.length > 1 ? series.map((s) => ({ key: s.key, label: s.label, color: s.color, shape: "rect" })) : null,
    table: () => ({
      columns: [opts.categoryLabel || "Category", ...series.map((s) => s.label), "Total"],
      rows: rows.map((row, i) => [
        row.label,
        ...series.map((s) => formatValue(Number((row.segments || []).find((seg) => seg.key === s.key)?.value) || 0)),
        formatValue(totals[i]),
      ]),
    }),
    render(ctx) {
      const { inner } = ctx;
      const maxTotal = Math.max(1, ...totals);
      const x = linearScale([0, maxTotal], [0, inner.w]);
      const y = bandScale(rows.map((_, i) => String(i)), [0, inner.h], { padding: 0.32 });

      const grid = ctx.layer("grid");
      const { ticks: xTicks } = niceTicks(0, maxTotal, Math.max(2, Math.round(inner.w / 100)));
      xTicks.forEach((tick, i) => {
        const px = Math.round(x(tick)) + 0.5;
        grid.append(svg("line", { class: "nv-grid", x1: px, x2: px, y1: 0, y2: inner.h }));
        // Anchor the outermost ticks inward. Centre-anchoring the last one pushes half
        // its label past the plot edge into the note column, where it was overlapped and
        // read as a truncated number ("300d" appearing as "30").
        const anchor = i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle";
        grid.append(
          svg("text", { class: "nv-tick nv-tick-x", x: px, y: inner.h + 16, "text-anchor": anchor }, formatValue(tick))
        );
      });
      grid.append(svg("line", { class: "nv-axis", x1: 0.5, x2: 0.5, y1: 0, y2: inner.h }));

      const marks = ctx.layer("marks");
      const labels = ctx.layer("labels");
      const band = Math.min(MAX_BAR, y.bandwidth());
      const geometry = [];

      rows.forEach((row, i) => {
        const py = y(String(i)) + (y.bandwidth() - band) / 2;
        let cursor = 0;
        const segments = row.segments || [];

        segments.forEach((segment, s) => {
          const value = Number(segment.value) || 0;
          if (value <= 0) return;
          const w = x(value);
          const isLast = s === segments.length - 1;
          // The 2px surface gap separates touching segments.
          const drawWidth = Math.max(1, isLast ? w : w - GAP);
          const node = svg("path", {
            class: "nv-bar",
            d: barPath(cursor, py, drawWidth, band, isLast ? "right" : "square"),
            dataset: { key: segment.key },
          });
          applyPaint(node, "fill", series.find((sr) => sr.key === segment.key)?.color || "var(--nv-ramp-a)");
          marks.append(node);
          cursor += w;
        });

        geometry.push({ y: py, h: band, w: x(totals[i]) });

        // Category label, left of the axis; truncated by the padding, not clipped.
        labels.append(
          svg(
            "text",
            { class: "nv-row-label", x: -10, y: py + band / 2, dy: "0.32em", "text-anchor": "end" },
            row.label
          )
        );

        // The value rides outside the bar end where it always fits.
        labels.append(
          svg(
            "text",
            { class: "nv-point-label", x: x(totals[i]) + 8, y: py + band / 2, dy: "0.32em", "text-anchor": "start" },
            row.note || formatValue(totals[i])
          )
        );
      });

      const highlight = svg("rect", { class: "nv-bar-highlight", opacity: "0", x: 0, y: 0, width: 0, height: 0, rx: 4 });
      ctx.layer("cursor").append(highlight);

      ctx.interactive({
        count: rows.length,
        hitTest(px, py) {
          const key = y.at(py - ctx.pad.top);
          if (key === null) return null;
          if (px < ctx.pad.left - (opts.labelWidth || 132)) return null;
          return Number(key);
        },
        onActive(index) {
          if (index === null) return highlight.setAttribute("opacity", "0");
          highlight.setAttribute("x", -6);
          highlight.setAttribute("y", y(String(index)));
          highlight.setAttribute("width", inner.w + 12);
          highlight.setAttribute("height", y.bandwidth());
          highlight.setAttribute("opacity", "1");
          return undefined;
        },
        focusRect(index) {
          const g = geometry[index];
          return { x: ctx.pad.left, y: ctx.pad.top + g.y, w: Math.max(g.w, 2), h: g.h };
        },
        tipFor(index) {
          const row = rows[index];
          const tipRows = series
            .map((s) => {
              const value = Number((row.segments || []).find((seg) => seg.key === s.key)?.value) || 0;
              if (!value && series.length > 1) return null;
              return { label: s.label, value: formatValue(value), color: s.color, shape: "rect" };
            })
            .filter(Boolean);
          if (series.length > 1) {
            tipRows.push({ label: "Total", value: formatValue(totals[index]), emphasis: true });
          }
          return { title: row.label, subtitle: row.sublabel || "", rows: tipRows, footer: row.footer || null };
        },
      });
    },
  };
}

/* ==========================================================================
   7. donut — class-of-service mix
   ========================================================================== */

/**
 * Part-to-whole at a glance, capped at 6 slices.
 *
 * @param {object} opts
 * @param {Array<{key, label, value}>} opts.slices
 * @param {string} [opts.centerLabel] @param {string} [opts.centerValue]
 */
export function donut(container, opts) {
  return createChart(container, buildDonut(opts));
}

function buildDonut(opts) {
  const slices = (opts.slices || []).filter((s) => Number(s.value) > 0);
  const total = slices.reduce((sum, s) => sum + Number(s.value), 0);
  const colorFor = opts.colorFor || ((s) => serviceClassColor(s.key));
  const shapeFor = opts.shapeFor || ((s) => serviceClassShape(s.key));
  const formatValue = opts.formatValue || formatTick;

  return {
    title: opts.title || "Mix",
    desc: opts.desc || "Share of the total taken by each category; the ring is read as parts of a whole, not as precise values.",
    height: opts.height || 200,
    pad: { top: 8, right: 8, bottom: 8, left: 8 },
    isEmpty: !slices.length || total <= 0,
    emptyText: opts.emptyText || "No demand in this range.",
    legend: slices.map((s) => ({
      key: s.key,
      label: s.label,
      color: colorFor(s),
      shape: shapeFor(s),
      value: `${formatValue(s.value)} · ${((Number(s.value) / total) * 100).toFixed(0)}%`,
    })),
    table: () => ({
      columns: [opts.categoryLabel || "Category", "Items", "Share"],
      rows: slices.map((s) => [
        s.label,
        formatValue(s.value),
        `${((Number(s.value) / total) * 100).toFixed(1)}%`,
      ]),
    }),
    render(ctx) {
      const { inner } = ctx;
      const size = Math.min(inner.w, inner.h);
      const cx = inner.w / 2;
      const cy = inner.h / 2;
      const outer = size / 2 - 2;
      const innerR = outer * 0.62;
      const mid = (outer + innerR) / 2;
      // A 2px surface gap expressed as an angle at the ring's mid-radius.
      const padAngle = Math.min(0.09, GAP / Math.max(1, mid));

      const marks = ctx.layer("marks");
      const geometry = [];
      let angle = -Math.PI / 2;

      slices.forEach((slice, i) => {
        const share = Number(slice.value) / total;
        const sweep = share * Math.PI * 2;
        const a0 = angle + (slices.length > 1 ? padAngle / 2 : 0);
        const a1 = angle + sweep - (slices.length > 1 ? padAngle / 2 : 0);
        angle += sweep;
        if (a1 <= a0) return;

        const large = a1 - a0 > Math.PI ? 1 : 0;
        const d = [
          `M${(cx + Math.cos(a0) * outer).toFixed(2)} ${(cy + Math.sin(a0) * outer).toFixed(2)}`,
          `A${outer} ${outer} 0 ${large} 1 ${(cx + Math.cos(a1) * outer).toFixed(2)} ${(cy + Math.sin(a1) * outer).toFixed(2)}`,
          `L${(cx + Math.cos(a1) * innerR).toFixed(2)} ${(cy + Math.sin(a1) * innerR).toFixed(2)}`,
          `A${innerR} ${innerR} 0 ${large} 0 ${(cx + Math.cos(a0) * innerR).toFixed(2)} ${(cy + Math.sin(a0) * innerR).toFixed(2)}`,
          "Z",
        ].join("");

        const node = svg("path", { class: "nv-arc", d, dataset: { key: slice.key, index: String(i) } });
        applyPaint(node, "fill", colorFor(slice));
        marks.append(node);
        geometry.push({ a0, a1, mid: (a0 + a1) / 2 });
      });

      const centre = ctx.layer("centre");
      if (opts.centerValue) {
        centre.append(
          svg("text", { class: "nv-donut-value", x: cx, y: cy - 2, "text-anchor": "middle" }, opts.centerValue)
        );
      }
      if (opts.centerLabel) {
        centre.append(
          svg("text", { class: "nv-donut-label", x: cx, y: cy + (opts.centerValue ? 16 : 4), "text-anchor": "middle" }, opts.centerLabel)
        );
      }

      const halo = ctx.layer("cursor");
      const ring = svg("circle", { class: "nv-arc-halo", cx, cy, r: outer + 2, opacity: "0" });
      halo.append(ring);

      ctx.interactive({
        count: geometry.length,
        hitTest(px, py) {
          const dx = px - ctx.pad.left - cx;
          const dy = py - ctx.pad.top - cy;
          const dist = Math.hypot(dx, dy);
          if (dist < innerR - 6 || dist > outer + 6) return null;
          let a = Math.atan2(dy, dx);
          // Normalise into the slice space, which starts at -PI/2.
          while (a < -Math.PI / 2) a += Math.PI * 2;
          while (a > Math.PI * 1.5) a -= Math.PI * 2;
          const index = geometry.findIndex((g) => a >= g.a0 - padAngle && a <= g.a1 + padAngle);
          return index === -1 ? null : index;
        },
        onActive(index) {
          const nodes = marks.querySelectorAll(".nv-arc");
          nodes.forEach((node, i) => node.classList.toggle("is-active", i === index));
          ring.setAttribute("opacity", "0");
        },
        focusRect(index) {
          const g = geometry[index];
          return {
            x: ctx.pad.left + cx + Math.cos(g.mid) * mid - 12,
            y: ctx.pad.top + cy + Math.sin(g.mid) * mid - 12,
            w: 24,
            h: 24,
          };
        },
        tipFor(index) {
          const slice = slices[index];
          return {
            title: slice.label,
            rows: [
              { label: "Items", value: formatValue(slice.value), color: colorFor(slice), shape: shapeFor(slice) },
              { label: "Share", value: `${((Number(slice.value) / total) * 100).toFixed(1)}%`, emphasis: true },
            ],
          };
        },
      });
    },
  };
}

/* ==========================================================================
   8. sparkline — the trend inside a stat tile
   ========================================================================== */

/**
 * A 12-ish point trend line for a KPI tile. Returns a bare `<svg>`: it is
 * decoration on a number, not a chart, so it carries no axes, legend or
 * tooltip — the tile's value and delta already say everything.
 *
 * @param {Array<number>} values
 * @param {{width?: number, height?: number, tone?: string, label?: string}} [opts]
 */
export function sparkline(values, opts = {}) {
  const width = opts.width || 96;
  const height = opts.height || 26;
  const series = (values || []).map(Number).filter(Number.isFinite);

  const node = svg("svg", {
    class: "nv-spark",
    viewBox: `0 0 ${width} ${height}`,
    width: String(width),
    height: String(height),
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": opts.label || `Trend over the last ${series.length} periods`,
    focusable: "false",
  });

  if (series.length < 2) return node;

  const min = Math.min(...series);
  const max = Math.max(...series);
  const x = linearScale([0, series.length - 1], [1, width - 1]);
  const y = linearScale([min === max ? min - 1 : min, max === min ? max + 1 : max], [height - 3, 3]);

  let d = "";
  series.forEach((value, i) => { d += `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(value).toFixed(1)}`; });

  const area = svg("path", {
    class: "nv-spark-area",
    d: `${d}L${x(series.length - 1).toFixed(1)} ${height}L${x(0).toFixed(1)} ${height}Z`,
  });
  const line = svg("path", { class: "nv-spark-line", d });
  const end = svg("circle", { class: "nv-spark-end", cx: x(series.length - 1), cy: y(series[series.length - 1]), r: 2.5 });

  if (opts.tone) {
    applyPaint(area, "fill", opts.tone);
    applyPaint(line, "stroke", opts.tone);
    applyPaint(end, "fill", opts.tone);
  }

  node.append(area, line, end);
  return node;
}

/* ==========================================================================
   9. ring & gauge
   ========================================================================== */

/**
 * A readiness ring — used once per KMM level.
 * Returns a bare `<svg>`; the percentage is drawn inside so the ring never
 * depends on colour alone.
 *
 * @param {number} pct 0-100
 * @param {{size?: number, tone?: string, label?: string, state?: string}} [opts]
 */
export function ring(pct, opts = {}) {
  const size = opts.size || 48;
  const stroke = opts.stroke || 4;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const value = Math.max(0, Math.min(100, Number(pct) || 0));

  const node = svg("svg", {
    class: `nv-ring${opts.state ? ` is-${opts.state}` : ""}`,
    viewBox: `0 0 ${size} ${size}`,
    width: String(size),
    height: String(size),
    role: "img",
    "aria-label": opts.label || `${Math.round(value)}% ready`,
    focusable: "false",
  });

  node.append(
    svg("circle", { class: "nv-ring-track", cx: size / 2, cy: size / 2, r, "stroke-width": stroke }),
    svg("circle", {
      class: "nv-ring-value",
      cx: size / 2,
      cy: size / 2,
      r,
      "stroke-width": stroke,
      "stroke-dasharray": `${((value / 100) * c).toFixed(2)} ${c.toFixed(2)}`,
      transform: `rotate(-90 ${size / 2} ${size / 2})`,
    }),
    svg("text", { class: "nv-ring-text", x: size / 2, y: size / 2, dy: "0.34em", "text-anchor": "middle" }, `${Math.round(value)}`)
  );

  if (opts.tone) applyPaint(node.querySelector(".nv-ring-value"), "stroke", opts.tone);
  return node;
}

/**
 * A half-circle gauge with an optional target marker. Used for Little's Law
 * coherence, where 1.0 is the meaningful target and both sides are meaningful.
 *
 * @param {object} opts
 * @param {number} opts.value @param {number} opts.min @param {number} opts.max
 * @param {number} [opts.target] @param {string} [opts.valueLabel]
 */
export function gauge(container, opts) {
  return createChart(container, buildGauge(opts));
}

function buildGauge(opts) {
  const min = Number(opts.min ?? 0);
  const max = Number(opts.max ?? 2);
  const value = Number(opts.value);
  const target = opts.target === undefined ? null : Number(opts.target);
  const valid = Number.isFinite(value);

  return {
    title: opts.title || "Coherence",
    desc: opts.desc || "Where the measured ratio sits between its bounds; the notch is the value the system should hold.",
    height: opts.height || 128,
    pad: { top: 10, right: 12, bottom: 8, left: 12 },
    isEmpty: !valid,
    emptyText: opts.emptyText || "Not enough history to compute this.",
    table: () => ({
      columns: ["Measure", "Value"],
      rows: [
        [opts.title || "Coherence", opts.valueLabel || formatTick(value)],
        ...(target !== null ? [["Target", formatTick(target)]] : []),
      ],
    }),
    render(ctx) {
      const { inner } = ctx;
      const cx = inner.w / 2;
      const r = Math.min(inner.w / 2 - 8, inner.h - 14);
      const cy = inner.h - 4;
      const stroke = Math.max(8, Math.min(14, r * 0.16));

      const angleFor = (v) => Math.PI + ((Math.min(max, Math.max(min, v)) - min) / (max - min)) * Math.PI;
      const point = (angle, radius) => [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius];

      const arc = (a0, a1, className, paintValue) => {
        const [x0, y0] = point(a0, r);
        const [x1, y1] = point(a1, r);
        const large = a1 - a0 > Math.PI ? 1 : 0;
        const node = svg("path", {
          class: className,
          d: `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`,
          "stroke-width": stroke,
          "stroke-linecap": "round",
        });
        if (paintValue) applyPaint(node, "stroke", paintValue);
        return node;
      };

      const marks = ctx.layer("marks");
      marks.append(arc(Math.PI, Math.PI * 2, "nv-gauge-track"));
      marks.append(arc(Math.PI, angleFor(value), "nv-gauge-value", opts.tone || "var(--nv-ramp-a)"));

      if (target !== null) {
        const a = angleFor(target);
        const [tx0, ty0] = point(a, r - stroke / 2 - 3);
        const [tx1, ty1] = point(a, r + stroke / 2 + 3);
        marks.append(svg("line", { class: "nv-gauge-target", x1: tx0, y1: ty0, x2: tx1, y2: ty1 }));
        marks.append(
          svg(
            "text",
            { class: "nv-tick", x: cx, y: cy - r - stroke / 2 - 8, "text-anchor": "middle" },
            `target ${formatTick(target)}`
          )
        );
      }

      marks.append(
        svg("text", { class: "nv-gauge-readout", x: cx, y: cy - 6, "text-anchor": "middle" }, opts.valueLabel || formatTick(value))
      );
      marks.append(svg("text", { class: "nv-tick", x: cx - r, y: cy + 16, "text-anchor": "middle" }, formatTick(min)));
      marks.append(svg("text", { class: "nv-tick", x: cx + r, y: cy + 16, "text-anchor": "middle" }, formatTick(max)));

      ctx.interactive({
        count: 1,
        hitTest: () => 0,
        focusRect: () => ({ x: ctx.pad.left + cx - 40, y: ctx.pad.top + cy - r, w: 80, h: r }),
        tipFor: () => ({
          title: opts.title || "Coherence",
          rows: [
            { label: opts.measureLabel || "Measured", value: opts.valueLabel || formatTick(value), emphasis: true },
            ...(target !== null ? [{ label: "Target", value: formatTick(target) }] : []),
          ],
          footer: opts.footer || null,
        }),
      });
    },
  };
}

export default {
  createChart,
  stackedArea,
  scatter,
  bars,
  histogram,
  dotPlot,
  hbar,
  donut,
  sparkline,
  ring,
  gauge,
  legend,
  linearScale,
  timeScale,
  bandScale,
  niceTicks,
  timeTicks,
  rampColor,
  serviceClassColor,
  serviceClassShape,
  formatDays,
  formatTick,
  formatDateLong,
  showTooltip,
  hideTooltip,
  SERVICE_CLASSES,
  SERVICE_CLASS_LABEL,
};
