/**
 * Nagare — Metrics dashboard (SPEC 9.3, Requirement 4).
 *
 * A teaching instrument as much as a dashboard: every chart panel carries a
 * one-line "how to read this / what healthy looks like" note, every KPI tile
 * shows its sample size and flags low confidence, and the Kanban Maturity
 * Model section explains exactly which metrics unlock the next level.
 *
 * Data flow
 * ---------
 * One `reload()` call fetches the board, the project list (for the filter),
 * and every `/boards/{id}/metrics/*` endpoint in parallel via
 * `Promise.allSettled` — a single chart failing to load never takes the rest
 * of the dashboard down with it. `metrics_advanced`-only endpoints (CFD,
 * aging, flow-efficiency, Little's Law) are not even requested when the flag
 * is off; `kmm` likewise only fires under `kmm_dashboard`.
 *
 * Rendering is a full rebuild on every filter change (preset, project,
 * service class, segment): the static shell (header, KPI grid, panel scaffolds)
 * is built and painted into the DOM *first*, and only then are the charts.js
 * factories invoked against the now-attached body hosts — those factories size
 * themselves from `getBoundingClientRect()` via a `ResizeObserver`, which reads
 * zero on a detached node. Building on the live DOM is what makes first paint
 * correct without relying on a resize event to "fix" it later.
 *
 * Charts are hand-built SVG from `charts.js` (CHARTS' own primitives) — this
 * module never draws a mark directly, only shapes API responses into the
 * factories' input contracts.
 */

import api from "./api.js";
import store, { hasFlag, setSegment, can } from "./store.js";
import {
  el, icon, render, skeleton, emptyState, errorState, upsellState,
  formatNumber, formatPercent,
} from "./ui.js";
import {
  stackedArea, scatter, bars, histogram, dotPlot, hbar, donut, ring, gauge,
  formatDays, formatDateLong, toMs,
} from "./charts.js";

/* ==========================================================================
   Constants
   ========================================================================== */

const SERVICE_CLASS_OPTIONS = [
  ["expedite", "Expedite"],
  ["standard", "Standard"],
  ["fixed_date", "Fixed date"],
  ["intangible", "Intangible"],
];

const DATE_PRESETS = [
  ["30d", "30d", 30],
  ["90d", "90d", 90],
  ["182d", "6m", 182],
  ["365d", "12m", 365],
  ["all", "All", null],
];

const ENDPOINT_PATH = {
  summary: "summary",
  cfd: "cfd",
  scatter: "scatter",
  throughput: "throughput",
  aging: "aging",
  flowEfficiency: "flow-efficiency",
  distribution: "distribution",
  blockers: "blockers",
  demand: "demand",
  littlesLaw: "littles-law",
  kmm: "kmm",
};

/** Endpoints that only exist behind `metrics_advanced` — never even requested otherwise. */
const ADVANCED_ONLY = new Set(["cfd", "aging", "flowEfficiency", "littlesLaw"]);

const REF_TONE = { p50: "info", p70: "success", p85: "warning", p95: "danger" };

/**
 * KPI tiles in the order Requirement 4 lists them. `primary: true` marks the
 * Little's Law triad — lead time, throughput, WIP — the three numbers a
 * Kanban lead actually manages flow by (WIP = throughput x lead time); those
 * three get a stronger visual treatment so the row reads as a hierarchy
 * instead of nine identical boxes.
 */
const KPI_DEFS = [
  { key: "p50", label: "Lead time (p50)", advanced: false, pick: pickP50, primary: true },
  { key: "customer_lead_time_p85", label: "Lead time (p85)", advanced: false },
  { key: "system_lead_time", label: "Cycle time", advanced: false },
  { key: "throughput", label: "Throughput", advanced: false, primary: true },
  { key: "wip", label: "WIP", advanced: false, primary: true },
  { key: "flow_efficiency", label: "Flow efficiency", advanced: true },
  { key: "due_date_performance", label: "Due-date performance", advanced: false },
  { key: "sle_attainment", label: "SLE attainment", advanced: true },
  { key: "failure_demand", label: "Failure demand", advanced: false },
];

/* ==========================================================================
   Module state — reset on every mount, so a stale response can never land.
   ========================================================================== */

let root = null;
let generation = 0;
let unsubscribeSegment = null;
let currentBoardId = null;
let boardCache = null;       // { id, data }
let projectsCache = null;    // Array<project>
let activeCharts = [];       // chart handles from the current render pass
let expandedKmmLevel = null;
let filters = defaultFilters();

function defaultFilters() {
  return { preset: "90d", from: null, to: null, projectId: "", serviceClass: "" };
}

/* ==========================================================================
   Lifecycle
   ========================================================================== */

/**
 * Router entry point.
 * @param {HTMLElement} container
 * @param {{boardId?: string}} params
 */
export async function mount(container, params = {}) {
  root = container;
  currentBoardId = params.boardId;
  boardCache = null;
  filters = defaultFilters();
  expandedKmmLevel = null;

  if (unsubscribeSegment) unsubscribeSegment();
  // The segment filter also lives in the shell's topbar (Req. 1); either
  // control moving it must refetch every chart on this board.
  unsubscribeSegment = store.subscribe((state, changed) => {
    if (changed.has("segment") && root) reload();
  });

  await reload(true);
}

/** Router teardown. */
export function unmount() {
  generation += 1;
  destroyCharts();
  if (unsubscribeSegment) {
    unsubscribeSegment();
    unsubscribeSegment = null;
  }
  root = null;
}

function stale(token) {
  return token !== generation || !root;
}

function paint(token, ...children) {
  if (stale(token)) return;
  render(root, ...children);
}

function destroyCharts() {
  for (const handle of activeCharts) {
    try {
      handle.destroy();
    } catch {
      /* a chart that fails to tear down cleanly must not block the next render */
    }
  }
  activeCharts = [];
}

/* ==========================================================================
   Data loading
   ========================================================================== */

async function loadBoard(id) {
  if (boardCache && boardCache.id === id) return boardCache.data;
  const data = await api.get(`/boards/${id}`);
  boardCache = { id, data };
  return data;
}

async function loadProjects() {
  if (projectsCache) return projectsCache;
  const payload = await api.get("/projects", { silent: true }).catch(() => ({ items: [] }));
  projectsCache = Array.isArray(payload) ? payload : (payload.items || []);
  return projectsCache;
}

function applyPresetDates(board) {
  const now = new Date();
  const toStr = now.toISOString().slice(0, 10);
  const preset = DATE_PRESETS.find(([key]) => key === filters.preset);
  const days = preset ? preset[2] : 90;

  filters.to = toStr;
  if (days) {
    filters.from = new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10);
  } else {
    // "All" — as far back as the board itself, so the window still has a floor.
    filters.from = board?.created_at ? String(board.created_at).slice(0, 10) : "2000-01-01";
  }
}

function buildQuery() {
  const query = { from: filters.from, to: filters.to, segment: store.get().segment || "unified" };
  if (filters.projectId) query.project_id = filters.projectId;
  if (filters.serviceClass) query.service_class = filters.serviceClass;
  return query;
}

/**
 * Fetch every metrics endpoint in parallel. A single failure never blocks the
 * others: each key gets either its payload or a paired `${key}Error`.
 */
async function fetchMetricsData(boardId, query, flags) {
  const keys = Object.keys(ENDPOINT_PATH).filter((key) => {
    if (key === "kmm") return flags.kmm;
    if (ADVANCED_ONLY.has(key)) return flags.advanced;
    return true;
  });

  const settled = await Promise.allSettled(
    keys.map((key) => api.get(`/boards/${boardId}/metrics/${ENDPOINT_PATH[key]}`, { query, silent: true }))
  );

  const out = {};
  keys.forEach((key, i) => {
    const result = settled[i];
    out[key] = result.status === "fulfilled" ? result.value : null;
    out[`${key}Error`] = result.status === "rejected" ? result.reason : null;
  });
  return out;
}

/**
 * Fetch board + projects + every metric, then hand off to `renderPage`.
 * @param {boolean} [initial] Full-page skeleton vs. a dimmed in-place refresh.
 */
async function reload(initial = false) {
  generation += 1;
  const token = generation;

  if (initial) {
    paint(token, pageSkeleton());
  } else {
    const wrap = root && root.querySelector(".metrics-page");
    if (wrap) wrap.classList.add("is-busy");
  }

  let board;
  let projectsList;
  try {
    [board, projectsList] = await Promise.all([loadBoard(currentBoardId), loadProjects()]);
  } catch (error) {
    if (stale(token)) return;
    paint(
      token,
      el("div", { className: "view-pad" }, errorState(error, () => reload(true), { title: "Could not load this board" }))
    );
    return;
  }
  if (stale(token)) return;

  if (!filters.from) applyPresetDates(board);
  const query = buildQuery();
  const flags = { advanced: hasFlag("metrics_advanced"), kmm: hasFlag("kmm_dashboard") };

  const data = await fetchMetricsData(currentBoardId, query, flags);
  if (stale(token)) return;

  renderPage(token, board, projectsList, data, flags);
}

/* ==========================================================================
   Page assembly
   ========================================================================== */

function pageSkeleton() {
  return el(
    "div",
    { className: "view-pad" },
    el(
      "div",
      { className: "view-header" },
      el("div", { className: "stack stack-2" }, skeleton({ width: 140, height: 26 }), skeleton({ width: 420, height: 12 })),
      skeleton({ width: 340, height: 32, radius: "var(--radius-md)" })
    ),
    el(
      "div",
      { className: "metrics-kpi-grid" },
      Array.from({ length: 9 }, () =>
        el(
          "div",
          { className: "kpi-tile" },
          skeleton({ width: 90, height: 10 }),
          skeleton({ width: 100, height: 30 }),
          skeleton({ width: 70, height: 10 })
        )
      )
    ),
    el(
      "div",
      { className: "metrics-chart-grid" },
      Array.from({ length: 6 }, () =>
        el(
          "div",
          { className: "panel chart-panel" },
          el("div", { className: "panel-header" }, skeleton({ width: "40%", height: 16 })),
          el("div", { className: "panel-body" }, skeleton({ height: 220, radius: "var(--radius-md)" }))
        )
      )
    )
  );
}

function renderPage(token, board, projectsList, data, flags) {
  destroyCharts();
  if (stale(token)) return;

  const summary = data.summary;
  if (!summary) {
    paint(
      token,
      el(
        "div",
        { className: "view-pad" },
        errorState(
          data.summaryError || "The metrics service did not return a summary for this board.",
          () => reload(true),
          { title: "Metrics unavailable" }
        )
      )
    );
    return;
  }

  // Chart factories need to measure a live DOM node, so the tree is painted
  // first with empty chart-panel bodies; `pending` runs the actual factory
  // calls immediately after, once every host is attached and has real width.
  const pending = [];

  const chartGrid = el(
    "div",
    { className: "metrics-chart-grid" },
    flags.advanced
      ? panelWith(
          pending,
          "Cumulative Flow Diagram",
          "Each band is a column; its thickness is how many cards are in that stage right now. Healthy: bands stay evenly spaced and roughly parallel — a widening band means a queue is growing.",
          data.cfd, data.cfdError, buildCfdChart
        )
      : lockedPanel("Cumulative Flow Diagram", "See exactly where work piles up across every stage."),

    panelWith(
      pending,
      "Lead-time scatter",
      "One dot per delivered item; the lines mark the 50th/70th/85th/95th percentiles. Healthy: most dots hug the p50 line, with only a few outliers above p85.",
      data.scatter, data.scatterError, buildScatterChart
    ),

    panelWith(
      pending,
      "Throughput",
      "One column per period; the line is the rolling average of the same measure. Healthy: a flat-to-rising average with low week-to-week swings.",
      data.throughput, data.throughputError, buildThroughputChart
    ),

    flags.advanced
      ? panelWith(
          pending,
          "Aging work in progress",
          "Every in-flight item, plotted by column and age against historical percentile bands. Healthy: no dots above the p85 band — anything there is stuck and worth a look today.",
          data.aging, data.agingError, buildAgingChart
        )
      : lockedPanel("Aging work in progress", "Spot stuck items before they blow the service level."),

    flags.advanced
      ? wideFlowEfficiencyPanel(pending, data.flowEfficiency, data.flowEfficiencyError)
      : lockedPanel("Flow efficiency", "See touch vs. wait time, and exactly which column wastes the most days.", "Business", true),

    panelWith(
      pending,
      "Lead-time distribution",
      "How delivered lead times are distributed. Healthy: right-skewed with a short tail — a long tail means a few items take far longer than the rest.",
      data.distribution, data.distributionError, buildHistogramChart
    ),

    panelWith(
      pending,
      "Blocker pareto",
      "Blocked days by category, longest first. Healthy: a short list, with no single category dominating the total.",
      data.blockers, data.blockersError, buildBlockerChart
    ),

    panelWith(
      pending,
      "Class of service mix",
      "Share of delivered work by class of service. Healthy: expedite stays a small slice — a growing one means standard work is being starved.",
      data.demand, data.demandError, buildDemandDonut
    ),

    flags.advanced
      ? panelWith(
          pending,
          "Little's Law coherence",
          "Average WIP divided by throughput × cycle time. Healthy: near 1.0 (the notch) — the numbers agree with each other and can be trusted for forecasting.",
          data.littlesLaw, data.littlesLawError, buildLittlesLawChart
        )
      : lockedPanel("Little's Law coherence", "Confirm the system is stable enough to forecast.")
  );

  const kmmHost = el("div", { className: "kmm-section" });
  if (flags.kmm) mountKmmSection(kmmHost, normalizeKmm(data.kmm));

  const page = el(
    "div",
    { className: "view-pad metrics-page" },
    renderHeader(board, projectsList),

    el("div", { className: "metrics-kpi-grid" }, buildKpiTiles(summary, flags.advanced)),

    el(
      "div",
      { className: "section-head" },
      el("h2", { className: "t-lg t-semibold" }, "Charts"),
      el("span", { className: "control-note" }, "Every panel explains how to read it and what healthy looks like.")
    ),
    chartGrid,

    el(
      "div",
      { className: "section-head" },
      el("div", { className: "row row-2 kmm-heading" }, icon("sparkle"), el("h2", { className: "t-lg t-semibold" }, "Kanban Maturity Model")),
      el("span", { className: "control-note" }, "ML0 → ML6. Expand a level to see which metrics unlock it and what to do next.")
    ),
    flags.kmm
      ? kmmHost
      : upsellState({
          title: "Kanban Maturity Model needs the Business plan",
          body: "See exactly which metrics unlock each maturity level, their current values, and what to instrument next.",
          plan: "Business",
          actions: can("manage_plans")
            ? [el("a", { className: "btn btn-primary btn-sm", href: "#/admin/flags" }, icon("flag"), "Open feature flags")]
            : [],
        })
  );

  paint(token, page);
  if (stale(token)) return;
  for (const run of pending) run();
}

function normalizeKmm(payload) {
  // Older shapes (or a test fixture) may hand this a bare levels array —
  // keep that working, just without a known current/next level.
  if (Array.isArray(payload)) {
    return { levels: payload, currentLevel: null, nextLevel: null, nextActions: [] };
  }
  return {
    levels: Array.isArray(payload?.levels) ? payload.levels : [],
    currentLevel: payload?.current_level ?? null,
    nextLevel: payload?.next_level ?? null,
    nextActions: Array.isArray(payload?.next_actions) ? payload.next_actions : [],
  };
}

/* ==========================================================================
   Header & filters
   ========================================================================== */

function renderHeader(board, projectsList) {
  return el(
    "div",
    { className: "view-header" },
    el(
      "div",
      { className: "stack stack-2" },
      el(
        "div",
        { className: "view-title-row" },
        el("h1", null, "Metrics"),
        board ? el("span", { className: "badge" }, board.name) : null
      ),
      el(
        "p",
        { className: "view-lede" },
        `Flow metrics for ${board?.name || "this board"}. Every chart is read alongside the note underneath it.`
      )
    ),
    metricsToolbar(board, projectsList)
  );
}

function metricsToolbar(board, projectsList) {
  const presetControl = el(
    "div",
    { className: "segmented", role: "tablist", "aria-label": "Date range" },
    DATE_PRESETS.map(([key, label]) =>
      el(
        "button",
        {
          role: "tab",
          "aria-selected": String(filters.preset === key),
          onClick: () => {
            filters.preset = key;
            applyPresetDates(board);
            reload();
          },
        },
        label
      )
    )
  );

  const projectSelect = el(
    "select",
    {
      className: "select select-sm",
      "aria-label": "Project filter",
      onChange: (event) => {
        filters.projectId = event.target.value;
        reload();
      },
    },
    el("option", { value: "", selected: !filters.projectId }, "All projects"),
    projectsList.map((project) =>
      el("option", { value: project.id, selected: filters.projectId === project.id }, `${project.key} · ${project.name}`)
    )
  );

  const serviceClassSelect = el(
    "select",
    {
      className: "select select-sm",
      "aria-label": "Class of service filter",
      onChange: (event) => {
        filters.serviceClass = event.target.value;
        reload();
      },
    },
    el("option", { value: "", selected: !filters.serviceClass }, "All classes"),
    SERVICE_CLASS_OPTIONS.map(([value, label]) => el("option", { value, selected: filters.serviceClass === value }, label))
  );

  const segment = segmentFilterControl();

  const envelopeCounted = Boolean(board?.count_envelope_in_metrics);
  const envelopeChip = el(
    "span",
    {
      className: "chip",
      dataset: { tone: envelopeCounted ? "accent" : "neutral" },
      "data-tooltip": envelopeCounted
        ? "Customer lead time includes the days a demand sat sealed."
        : "Customer lead time starts once the envelope is opened.",
    },
    icon(envelopeCounted ? "envelope" : "envelope-open"),
    envelopeCounted ? "Envelope time counted" : "Envelope time excluded"
  );

  return el(
    "div",
    { className: "view-toolbar" },
    presetControl,
    projectSelect,
    serviceClassSelect,
    segment,
    envelopeChip
  );
}

/** The segment filter (Req. 1) — mirrors the topbar's control so it also
 * shows up inside the dashboard's own header, per Requirement 4. */
function segmentFilterControl() {
  const options = [
    { id: "upstream", label: "Upstream", icon: "queue", flag: "upstream_board" },
    { id: "downstream", label: "Downstream", icon: "activity", flag: "downstream_board" },
    { id: "unified", label: "Unified", icon: "layers", flag: "unified_flow" },
  ].filter((option) => hasFlag(option.flag));

  if (options.length < 2) return null;

  const current = store.get().segment;
  return el(
    "div",
    { className: "segmented", role: "tablist", "aria-label": "Segment filter" },
    options.map((option) =>
      el(
        "button",
        { role: "tab", "aria-selected": String(option.id === current), onClick: () => setSegment(option.id) },
        icon(option.icon),
        option.label
      )
    )
  );
}

/* ==========================================================================
   KPI tiles
   ========================================================================== */

function pickP50(summary) {
  const value = summary.percentiles?.p50 ?? null;
  return {
    value,
    unit: "days",
    trend_pct: null,
    sample_size: summary.sample_size,
    previous_value: null,
    low_confidence: summary.low_confidence,
    description: "50% of delivered work finished within this many days.",
    higher_is_better: false,
  };
}

function buildKpiTiles(summary, advancedOn) {
  return KPI_DEFS.map((def) => {
    if (def.advanced && !advancedOn) return lockedKpiTile(def.label, def.primary);
    const metric = def.pick ? def.pick(summary) : summary.metrics?.[def.key];
    return kpiTile(def.label, metric, def.primary);
  });
}

function splitMetricValue(value, unit) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return { value: "—", unit: "" };
  const n = Number(value);
  switch (unit) {
    case "percent":
      return { value: formatPercent(n), unit: "" };
    case "days":
      return { value: formatDays(n), unit: "" };
    case "cards/week":
      return { value: formatNumber(n, { decimals: n < 10 ? 1 : 0 }), unit: "/wk" };
    case "ratio":
      return { value: n.toFixed(2), unit: "×" };
    case "count":
      return { value: formatNumber(n, { decimals: 0 }), unit: "" };
    default:
      return { value: formatNumber(n), unit: unit || "" };
  }
}

function kpiTile(label, metric, primary) {
  if (!metric || metric.value === null || metric.value === undefined) {
    return el(
      "div",
      { className: "kpi-tile", dataset: { primary: primary ? "true" : undefined } },
      el("div", { className: "kpi-label" }, label),
      el("div", { className: "kpi-value" }, "—"),
      el("div", { className: "kpi-meta subtle" }, "No delivered work in this range yet.")
    );
  }

  const { value, unit } = splitMetricValue(metric.value, metric.unit);
  const trend = metric.trend_pct;
  const hasTrend = trend !== null && trend !== undefined && Number.isFinite(Number(trend));
  const dir = !hasTrend || Math.abs(trend) < 0.05 ? "flat" : trend > 0 ? "up" : "down";
  const good =
    dir === "flat" || metric.higher_is_better === null || metric.higher_is_better === undefined
      ? null
      : (dir === "up") === Boolean(metric.higher_is_better);

  return el(
    "div",
    {
      className: "kpi-tile",
      dataset: { primary: primary ? "true" : undefined },
      "data-tooltip": metric.description || label,
      "data-tooltip-pos": "bottom",
    },
    el("div", { className: "kpi-label" }, label, primary ? el("span", { className: "kpi-primary-tag" }, "Little's Law") : null),
    el("div", { className: "kpi-value" }, value, unit ? el("span", { className: "kpi-unit" }, unit) : null),
    el(
      "div",
      { className: "kpi-meta" },
      hasTrend
        ? el(
            "span",
            { className: "kpi-trend", dataset: { dir, good: good === null ? undefined : String(good) } },
            icon(dir === "up" ? "trend-up" : dir === "down" ? "trend-down" : "arrow-right"),
            `${Math.abs(trend).toFixed(1)}%`
          )
        : null,
      el("span", null, `n=${metric.sample_size ?? 0}`),
      metric.low_confidence
        ? el("span", { className: "kpi-low-confidence" }, icon("alert"), "Low confidence")
        : null
    )
  );
}

function lockedKpiTile(label, primary) {
  return el(
    "div",
    { className: "kpi-tile kpi-tile-locked", dataset: { primary: primary ? "true" : undefined } },
    el("div", { className: "kpi-label" }, label),
    el("div", { className: "row row-1 t-sm subtle" }, icon("lock"), "Business plan")
  );
}

/* ==========================================================================
   Chart panel scaffolding
   ========================================================================== */

function chartPanel(title, note, opts = {}) {
  const bodyHost = el("div", { className: "panel-body" });
  const panel = el(
    "div",
    { className: `panel chart-panel${opts.wide ? " chart-panel-wide" : ""}` },
    el(
      "div",
      { className: "panel-header" },
      el(
        "div",
        { className: "stack stack-1", style: { minWidth: 0 } },
        el("h3", { className: "panel-title" }, title),
        note ? el("p", { className: "control-note" }, note) : null
      )
    ),
    bodyHost
  );
  return { panel, bodyHost };
}

/**
 * A chart panel backed by one metrics endpoint. If that endpoint failed, the
 * panel shows its own error + retry rather than taking the dashboard down.
 * Otherwise the actual factory call is deferred into `pending`, to run only
 * once the returned node is attached to the live DOM (see `renderPage`).
 */
function panelWith(pending, title, note, data, error, buildFn, opts = {}) {
  const { panel, bodyHost } = chartPanel(title, note, opts);
  if (error) {
    render(bodyHost, errorState(error, () => reload(true), { title: "Could not load this chart" }));
  } else {
    pending.push(() => buildFn(bodyHost, data));
  }
  return panel;
}

function lockedPanel(title, note, plan = "Business", wide = false) {
  const { panel, bodyHost } = chartPanel(title, note, { wide });
  render(
    bodyHost,
    upsellState({
      title: `${title} needs the ${plan} plan`,
      body: "Ask a workspace administrator to enable this under Admin › Feature flags.",
      plan,
      actions: can("manage_plans")
        ? [el("a", { className: "btn btn-secondary btn-sm", href: "#/admin/flags" }, icon("flag"), "Open feature flags")]
        : [],
    })
  );
  return panel;
}

/* ==========================================================================
   Individual chart builders — each shapes one metrics response into the
   input contract of a charts.js factory. See charts.js for the factories
   themselves; this module never draws a mark directly.
   ========================================================================== */

/** Cumulative Flow Diagram. */
function buildCfdChart(host, data) {
  const seriesRaw = (data?.series || []).slice().reverse(); // bottom band = most downstream
  const series = seriesRaw.map((s) => ({ key: s.key, label: s.label, values: (s.points || []).map((p) => Number(p.y) || 0) }));
  const x = (data?.series?.[0]?.points || []).map((p) => p.t || p.x);

  const handle = stackedArea(host, {
    x, series,
    yLabel: "Cards",
    title: "Cumulative Flow Diagram",
    desc: "Each band is a column; band thickness is how many cards are currently in that stage.",
    height: 260,
  });
  activeCharts.push(handle);
}

/** Lead-time scatter with percentile reference lines. */
function buildScatterChart(host, data) {
  const dotSeries = (data?.series || []).find((s) => s.mark === "dot");
  const points = (dotSeries?.points || []).map((p) => ({ x: p.x, y: p.y, group: p.service_class, label: p.key, sublabel: p.title }));
  const percentiles = Object.entries(data?.percentiles || {}).map(([key, value]) => ({
    key, label: `${key.slice(1)}th pct`, value, tone: REF_TONE[key] || "neutral",
  }));

  const handle = scatter(host, {
    points, percentiles,
    title: "Lead-time scatter",
    desc: "One dot per delivered item, positioned by delivery date and lead time.",
    yLabel: "Lead time (days)",
    height: 260,
  });
  activeCharts.push(handle);
}

/** Throughput bars with rolling average overlay. */
function buildThroughputChart(host, data) {
  const throughputSeries = (data?.series || []).find((s) => s.key === "throughput");
  const rollingSeries = (data?.series || []).find((s) => s.key === "rolling");

  const items = (throughputSeries?.points || []).map((p) => ({
    label: p.x, sublabel: formatDateLong(toMs(p.t || p.x)), value: p.y,
  }));
  const overlay = rollingSeries
    ? { label: rollingSeries.label, values: (rollingSeries.points || []).map((p) => p.y) }
    : null;

  const handle = bars(host, {
    items, overlay,
    valueLabel: "Delivered",
    title: "Throughput",
    desc: `One column per ${data?.meta?.period || "week"}; the line is the rolling average.`,
    height: 220,
  });
  activeCharts.push(handle);
}

/** Aging work in progress, dots against percentile bands. */
function buildAgingChart(host, data) {
  const cardSeries = (data?.series || []).find((s) => s.key === "cards");
  const points = (cardSeries?.points || []).map((p) => ({
    column: p.column_id, y: p.y, group: p.service_class, label: p.key, sublabel: p.column_name,
  }));
  const columns = (data?.categories || []).map((c) => ({ key: c.id, label: c.name }));
  const bands = Object.entries(data?.percentiles || {}).map(([key, value]) => ({
    key, label: `${key.slice(1)}th`, value, tone: REF_TONE[key] || "neutral",
  }));

  const handle = dotPlot(host, {
    columns, points, bands,
    title: "Aging work in progress",
    desc: "Every in-flight item by column and age, against historical percentile bands.",
    yLabel: "Age (days)",
    height: 260,
  });
  activeCharts.push(handle);
}

/** Flow efficiency: aggregate touch/wait split + per-column breakdown. */
function wideFlowEfficiencyPanel(pending, data, error) {
  const { panel, bodyHost } = chartPanel(
    "Flow efficiency",
    "Touch time as a share of total lead time, and which column burns the most days waiting. Healthy: touch time above 25%, and the biggest wait bar below is the one to attack first.",
    { wide: true }
  );
  if (error) {
    render(bodyHost, errorState(error, () => reload(true), { title: "Could not load flow efficiency" }));
    return panel;
  }
  const aggHost = el("div", { className: "flow-eff-agg" });
  const colHost = el("div", { className: "flow-eff-columns" });
  render(bodyHost, aggHost, colHost);
  pending.push(() => buildFlowEfficiencyCharts(aggHost, colHost, data));
  return panel;
}

function buildFlowEfficiencyCharts(aggHost, colHost, data) {
  const meta = data?.meta || {};
  const series = [
    { key: "touch", label: "Touch time", color: "var(--success)" },
    { key: "wait", label: "Wait time", color: "var(--warning)" },
  ];
  if (meta.include_envelope) series.push({ key: "envelope", label: "Sealed envelope", color: "var(--fg-subtle)" });

  // Normalise to ONE delivered item. Summing touch/wait across every delivered card
  // produced an axis running to ~1,600 days for a single bar, which is not a quantity
  // anyone can reason about and which contradicted every other number on the page.
  // Per delivered item, the bar totals the average lead time — so it reconciles with
  // the lead-time and cycle-time KPI tiles directly above it.
  const deliveredCount = Array.isArray(meta.per_card) && meta.per_card.length
    ? meta.per_card.length
    : 0;
  const perItem = (days) => (deliveredCount ? (days || 0) / deliveredCount : days || 0);

  const aggSegments = [
    { key: "touch", value: perItem(meta.touch_days) },
    { key: "wait", value: perItem(meta.wait_days) },
  ];
  if (meta.include_envelope) aggSegments.push({ key: "envelope", value: perItem(meta.envelope_days) });

  const aggHandle = hbar(aggHost, {
    rows: [{
      label: "Average delivered item",
      segments: aggSegments,
      note: `${formatPercent(meta.flow_efficiency ?? 0)} touch`,
    }],
    series,
    formatValue: formatDays,
    title: "Flow efficiency (per delivered item)",
    desc: deliveredCount
      ? `Touch time versus wait time for the average delivered item (${deliveredCount} delivered in this window).`
      : "Touch time versus wait time for the average delivered item.",
    labelWidth: 150,
  });
  activeCharts.push(aggHandle);

  // Same normalisation, same reason: the bar was drawn from the summed days while the
  // number printed beside it was the per-card average, so a "5.5d avg" label sat on a
  // bar stretching across a 300-day axis. Plotting the average makes bar length, axis
  // and label agree, and makes columns comparable regardless of how much work happened
  // to flow through each one. The volume-weighted view is kept as the note.
  const byColumn = (meta.by_column || [])
    .slice()
    .sort((a, b) => (b.avg_days_per_card || 0) - (a.avg_days_per_card || 0))
    .slice(0, 10);
  const colRows = byColumn.map((c) => ({
    label: c.column_name,
    segments: [{ key: c.kind === "activity" ? "touch" : "wait", value: c.avg_days_per_card || 0 }],
    note: `${formatPercent(c.share_of_lead_time_pct ?? 0)} of lead time`,
  }));
  const colHandle = hbar(colHost, {
    rows: colRows,
    series,
    formatValue: formatDays,
    title: "Where time accumulates",
    desc: "Average days a card spends in each column, longest first — the queue at the top is the bottleneck.",
    labelWidth: 150,
    // "18% of lead time" needs more room than the 56px default, which clipped it.
    noteWidth: 116,
  });
  activeCharts.push(colHandle);
}

/** Lead-time histogram with percentile markers. */
function buildHistogramChart(host, data) {
  const countSeries = (data?.series || []).find((s) => s.key === "count");
  const bins = (countSeries?.points || []).map((p) => ({ from: p.x, to: p.x_end, count: p.y }));
  const markers = Object.entries(data?.percentiles || {}).map(([key, value]) => ({
    key, label: `${key.slice(1)}th`, value, tone: REF_TONE[key] || "neutral",
  }));

  const handle = histogram(host, {
    bins, markers,
    title: "Lead-time distribution",
    desc: "How many delivered items fell into each lead-time bucket.",
    height: 220,
  });
  activeCharts.push(handle);
}

/** Blocker pareto, longest category first. */
function buildBlockerChart(host, data) {
  const pareto = data?.meta?.pareto || [];
  const rows = pareto.map((p) => ({
    label: p.label,
    segments: [{ key: "days", value: p.days || 0 }],
    note: `${p.share_pct ?? 0}% · ${p.count}×`,
  }));

  const handle = hbar(host, {
    rows,
    series: [{ key: "days", label: "Blocked days", color: "var(--danger)" }],
    formatValue: formatDays,
    title: "Blocker pareto",
    desc: "Blocked days by category, longest first.",
    labelWidth: 130,
    categoryLabel: "Category",
  });
  activeCharts.push(handle);
}

/** Class-of-service donut. */
function buildDemandDonut(host, data) {
  const mix = data?.meta?.service_class_mix || [];
  const slices = mix.map((m) => ({ key: m.key, label: m.label, value: m.count }));
  const total = data?.meta?.delivered_total;

  const handle = donut(host, {
    slices,
    centerValue: Number.isFinite(total) ? String(total) : "",
    centerLabel: "delivered",
    title: "Class of service mix",
    desc: "Share of delivered work by class of service.",
    height: 200,
  });
  activeCharts.push(handle);
}

/** Little's Law coherence gauge + the plain-language verdict. */
function buildLittlesLawChart(host, data) {
  const meta = data?.meta || {};
  const coherence = meta.coherence_ratio;
  const hasValue = Number.isFinite(coherence);

  const chartHost = el("div", { className: "little-gauge" });
  const handle = gauge(chartHost, {
    value: coherence,
    min: 0,
    max: 2,
    target: 1,
    valueLabel: hasValue ? `${coherence.toFixed(2)}×` : "—",
    tone: meta.stable ? "var(--success)" : "var(--warning)",
    title: "Little's Law coherence",
    desc: "Observed average WIP divided by throughput times cycle time. Near 1.0 means the system is stable enough to forecast.",
    footer: meta.verdict,
    emptyText: "Not enough delivered work in this window to check Little's Law.",
  });
  activeCharts.push(handle);

  render(
    host,
    chartHost,
    meta.verdict ? el("p", { className: "metric-verdict" }, meta.verdict) : null,
    el(
      "dl",
      { className: "deflist" },
      el("dt", null, "Avg WIP"), el("dd", null, formatNumber(meta.avg_wip)),
      el("dt", null, "Throughput"), el("dd", null, `${formatNumber(meta.throughput_per_week)} /wk`),
      el("dt", null, "Avg cycle time"), el("dd", null, formatDays(meta.avg_system_lead_time_days))
    )
  );
}

/* ==========================================================================
   Kanban Maturity Model ladder
   ========================================================================== */

/** Percentile keys shown for a distribution value, in order of preference. */
const KMM_PERCENTILE_ORDER = ["p50", "p85", "p70", "p95"];

/**
 * Render any KMM metric `value` by its actual shape instead of interpolating
 * it raw — the source of the historical "[object Object]" leak. Every shape
 * the API is known to send today is handled explicitly; anything unrecognised
 * still falls through to a safe, empty string rather than `String(obj)`, so a
 * future metric shape can add itself here instead of leaking into the UI.
 */
function formatKmmValue(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") return Number.isFinite(value) ? formatNumber(value) : "";
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "Yes" : "No";

  if (Array.isArray(value)) {
    if (!value.length) return "";
    // A "mix" array — [{key, label, count, pct}, ...] — show the top few slices.
    if (value.every((v) => v && typeof v === "object")) {
      return value
        .slice()
        .sort((a, b) => (b.pct ?? b.count ?? 0) - (a.pct ?? a.count ?? 0))
        .slice(0, 3)
        .map(formatKmmMixItem)
        .filter(Boolean)
        .join(" · ");
    }
    return value.map(formatKmmValue).filter(Boolean).join(", ");
  }

  if (typeof value === "object") {
    const keys = Object.keys(value);
    // A percentile set — {p50, p70, p85, p95}.
    if (keys.length && keys.every((k) => /^p\d+$/.test(k))) {
      const ordered = KMM_PERCENTILE_ORDER.filter((k) => value[k] !== null && value[k] !== undefined);
      const rest = keys.filter((k) => !ordered.includes(k) && value[k] !== null && value[k] !== undefined);
      return [...ordered, ...rest]
        .slice(0, 2)
        .map((k) => `${k} ${formatDays(value[k])}`)
        .join(" · ");
    }
    // A named object — a column, a project row: {label|name|column_name, ...}.
    const name = value.label ?? value.name ?? value.column_name ?? value.key;
    if (name) {
      const days = value.days ?? value.wait_days ?? null;
      return days === null || days === undefined ? String(name) : `${name} · ${formatDays(days)}`;
    }
    // A plain counts object — {columns: 12, cards: 267}.
    const parts = Object.entries(value)
      .filter(([, v]) => typeof v === "number" && Number.isFinite(v))
      .map(([k, v]) => `${formatNumber(v)} ${k.replace(/_/g, " ")}`);
    return parts.join(" · ");
  }
  return "";
}

function formatKmmMixItem(item) {
  const label = item?.label ?? item?.key ?? item?.name;
  if (!label) return "";
  if (item.pct !== null && item.pct !== undefined) return `${label} ${formatPercent(item.pct)}`;
  if (item.count !== null && item.count !== undefined) return `${label} ${formatNumber(item.count)}`;
  return String(label);
}

function mountKmmSection(host, kmm) {
  const levels = kmm.levels || [];
  if (!levels.length) {
    render(
      host,
      emptyState({
        icon: "target-rings",
        title: "No maturity data yet",
        body: "Deliver more work through this board to populate the Kanban Maturity Model ladder.",
      })
    );
    return;
  }

  // A maturity ladder is cumulative — trust the server's `current_level`
  // (the highest rung with every level below it also satisfied) rather than
  // re-deriving "current" locally from whichever level happens to read 100%.
  const currentLevel = levels.find((l) => l.level === kmm.currentLevel) || levels[0];
  const nextLevel = kmm.nextLevel ? levels.find((l) => l.level === kmm.nextLevel) : null;

  function paintLadder() {
    const selected = levels.find((l) => l.level === expandedKmmLevel) || currentLevel;
    const currentIndex = levels.indexOf(currentLevel);
    const nodes = [];
    levels.forEach((level, index) => {
      nodes.push(
        kmmNode(level, level === currentLevel, level === selected, () => {
          expandedKmmLevel = level.level;
          paintLadder();
        // Mute levels beyond the NEXT one only. The immediate next rung is the one being
        // worked on — it keeps its readiness-driven tone so a practitioner can see how
        // close ML5 actually is (25%), which is the whole point of the ladder. Levels
        // past it are greyed regardless of their own readiness, because a later rung
        // reading 100% is "already measurable", not "attained".
        }, index > currentIndex + 1)
      );
      if (index < levels.length - 1) nodes.push(el("div", { className: "kmm-connector", "aria-hidden": "true" }));
    });

    render(
      host,
      kmmStatusBar(currentLevel, nextLevel, kmm.nextActions),
      el(
        "div",
        { className: "kmm-ladder-wrap" },
        el("div", { className: "kmm-ladder", role: "list", "aria-label": "Kanban Maturity Model levels" }, nodes)
      ),
      kmmLevelDetail(selected, currentLevel)
    );
  }

  paintLadder();
}

/** The cumulative read-out: where the board stands, and what blocks the next rung. */
function kmmStatusBar(currentLevel, nextLevel, nextActions) {
  return el(
    "div",
    { className: "kmm-status" },
    el(
      "div",
      { className: "kmm-status-current" },
      el("span", { className: "kmm-status-tag" }, "Current maturity"),
      el("span", { className: "kmm-status-value" }, `${currentLevel.level} · ${currentLevel.name}`)
    ),
    nextLevel
      ? el(
          "div",
          { className: "kmm-status-gap" },
          el("span", { className: "kmm-status-tag" }, `Blocked at ${nextLevel.level} · ${nextLevel.name}`),
          Array.isArray(nextActions) && nextActions.length
            ? el(
                "ul",
                { className: "kmm-next-actions" },
                nextActions.slice(0, 3).map((action) => el("li", null, action))
              )
            : null
        )
      : el("span", { className: "kmm-status-tag kmm-status-tag-done" }, "ML6 Congruent — fully satisfied")
  );
}

function kmmNode(level, isCurrent, isSelected, onClick, isBeyondCurrent = false) {
  const readiness = Number(level.readiness_pct || 0);
  const complete = readiness >= 100;
  // Maturity is cumulative, so a rung ABOVE the current level has not been attained no
  // matter how ready its own metrics look — ML6 reading 100% while ML5 sits at 25%
  // means "already measurable", not "achieved". Painting it in full accent like the
  // attained rungs would tell the practitioner they are further along than they are, so
  // everything past the current level is muted regardless of its own readiness.
  const tone = isBeyondCurrent
    ? "var(--fg-subtle)"
    : complete
      ? "var(--accent)"
      : "var(--warning)";
  const state = isBeyondCurrent ? "not yet attained" : complete ? "attained" : "in progress";
  return el(
    "button",
    {
      type: "button",
      className: "kmm-node",
      dataset: {
        current: isCurrent ? "true" : undefined,
        selected: isSelected ? "true" : undefined,
        beyond: isBeyondCurrent ? "true" : undefined,
      },
      "aria-expanded": String(isSelected),
      "aria-label":
        `Maturity level ${level.level}, ${level.name}, ${Math.round(readiness)} percent ready, ${state}`,
      onClick,
    },
    isCurrent ? el("span", { className: "kmm-current-badge" }, "Current") : null,
    ring(readiness, { size: 44, tone }),
    el("span", { className: "kmm-node-level" }, level.level),
    el("span", { className: "kmm-node-name" }, level.name)
  );
}

function kmmLevelDetail(level, currentLevel) {
  if (!level) return null;
  const metrics = level.metrics || [];
  const missing = metrics.filter((m) => !m.available);
  const available = metrics.filter((m) => m.available);
  const isCurrent = level === currentLevel;
  const isAhead = currentLevel && level.level > currentLevel.level;

  return el(
    "div",
    { className: "panel kmm-detail" },
    el(
      "div",
      { className: "panel-header" },
      el(
        "div",
        { className: "row row-2" },
        el("h3", { className: "panel-title" }, `${level.level} · ${level.name}`),
        isCurrent ? el("span", { className: "badge", dataset: { tone: "accent" } }, "Current level") : null
      ),
      el("span", { className: "t-xs subtle" }, `${Math.round(level.readiness_pct || 0)}% ready · ${available.length}/${metrics.length} metrics`)
    ),
    el(
      "div",
      { className: "panel-body stack stack-4" },
      level.focus ? el("p", { className: "view-lede" }, level.focus) : null,
      metrics.length
        ? el("div", { className: "kmm-metric-grid" }, metrics.map(kmmMetricRow))
        : el("p", { className: "t-sm subtle" }, "No metrics are mapped to this level."),
      missing.length
        ? el(
            "div",
            { className: "note-strip" },
            icon("sparkle"),
            el("span", null, el("strong", null, "Next to level up: "), missing.map((m) => m.label).join(", "), ".")
          )
        : el(
            "div",
            { className: "note-strip" },
            icon("check"),
            el(
              "span",
              null,
              isAhead
                ? `Every metric for ${level.level} is available — deliver more work to build the sample size and cross the threshold.`
                : `Every metric for ${level.level} is available.`
            )
          )
    )
  );
}

function kmmMetricRow(m) {
  const formatted = formatKmmValue(m.value);
  return el(
    "div",
    { className: "kmm-metric-row", dataset: { available: m.available ? "true" : "false" } },
    el("span", { className: "kmm-metric-mark" }, icon(m.available ? "check" : "x")),
    el(
      "div",
      { className: "kmm-metric-text" },
      el(
        "div",
        { className: "kmm-metric-label" },
        m.label,
        m.available && formatted ? el("span", { className: "kmm-metric-value" }, ` — ${formatted}`) : null
      ),
      m.why ? el("div", { className: "kmm-metric-why" }, m.why) : null
    )
  );
}
