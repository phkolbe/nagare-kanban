/**
 * Nagare — the simulator laboratory (SPEC 8, 9.5, Requirement 13 — "the crown
 * jewel").
 *
 * Two surfaces behind one module, selected by the route:
 *   #/simulator             the index — every simulator board, newest first
 *   #/simulator/:boardId    the lab bench: a scenario builder feeding five
 *                           kinds of run, a results panel per run, a run
 *                           history with a compare mode, and a "generate
 *                           demand into board" action that materialises the
 *                           scenario as real cards on the board.
 *
 * Everything here is sandboxed. `kind='simulator'` boards never feed
 * operational metrics (SPEC §8), so every surface in this module wears the
 * `.lab` chrome and opens with the sandbox banner — a number produced here
 * must never be mistaken for one produced by the real board.
 */

import api from "./api.js";
import store, { can } from "./store.js";
import {
  el, icon, modal, toast, emptyState, errorState, skeleton,
  confirmDialog, render, relativeTime, formatNumber, humanize, announce,
} from "./ui.js";
import { navigate } from "./router.js";

/* ==========================================================================
   Charts — owned by the CHARTS agent; we only ever call its factories.
   ========================================================================== */

let chartsPromise = null;

function loadCharts() {
  if (!chartsPromise) {
    chartsPromise = import("./charts.js").catch((error) => {
      console.warn("[simulator] charts.js is unavailable; results render as tables only.", error);
      return null;
    });
  }
  return chartsPromise;
}

/* ==========================================================================
   Constants
   ========================================================================== */

const CLASS_KEYS = ["expedite", "standard", "fixed_date", "intangible"];
const CLASS_LABEL = { expedite: "Expedite", standard: "Standard", fixed_date: "Fixed date", intangible: "Intangible" };
const DEFAULT_MIX = { expedite: 8, standard: 70, fixed_date: 12, intangible: 10 };
const DEFAULT_SEED = 20260816;

const RUN_KINDS = [
  {
    key: "monte_carlo_when",
    label: "Monte Carlo — when will N items be done?",
    note: "Resamples weekly throughput 10,000 times to answer \"how many weeks until the next N items ship\", as a distribution, not a guess.",
    flag: "monte_carlo",
  },
  {
    key: "monte_carlo_how_many",
    label: "Monte Carlo — how many by a date?",
    note: "The mirror question: given a target date, how many items are likely to be delivered by then, at each confidence level.",
    flag: "monte_carlo",
  },
  {
    key: "wip_experiment",
    label: "WIP experiment",
    note: "Replays the same demand through six WIP policies, from unlimited to starved, and shows Little's Law happening: tighter WIP, shorter lead time.",
  },
  {
    key: "class_of_service_mix",
    label: "Class-of-service mix experiment",
    note: "Grows the expedite share from 0% to 35% against the same demand and measures what it costs the standard class.",
  },
  {
    key: "replay",
    label: "Replay this board's history",
    note: "Re-runs this board's own arrivals and observed service times under alternate WIP policies.",
  },
];

const KIND_LABEL = Object.fromEntries(RUN_KINDS.map((k) => [k.key, k.label]));

/* ==========================================================================
   Module state
   ========================================================================== */

let root = null;
let generation = 0;

/** Scenario-builder state, persisted across re-renders of the same board. */
let bench = null;
let benchBoardId = null;
let selectedRunId = null;
let compareIds = new Set();

/* ==========================================================================
   Lifecycle
   ========================================================================== */

export async function mount(container, params = {}) {
  root = container;
  generation += 1;
  const token = generation;

  if (params.boardId && params.boardId !== "none") {
    if (benchBoardId !== params.boardId) {
      bench = null;
      selectedRunId = null;
      compareIds = new Set();
    }
    await renderBoard(params.boardId, token);
  } else {
    await renderIndex(token);
  }
}

export function unmount() {
  generation += 1;
  root = null;
}

function stale(token) {
  return token !== generation || !root;
}

function paint(token, node) {
  if (stale(token)) return;
  render(root, node);
}

/* ==========================================================================
   Shared chrome
   ========================================================================== */

function items(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.items) ? payload.items : [];
}

function sandboxBanner() {
  return el(
    "div",
    { className: "lab-banner" },
    el("div", { className: "lab-banner-icon" }, icon("flask")),
    el(
      "div",
      { className: "stack stack-1" },
      el("div", { className: "lab-banner-title" }, "Sandbox — not counted in operational metrics"),
      el(
        "div",
        { className: "lab-banner-note" },
        "Everything on this page runs against synthetic or replayed demand on a board with kind=\"simulator\". Nothing here ever feeds the real board's KPIs, CFD or forecasts."
      )
    )
  );
}

function labField(label, control, opts = {}) {
  const error = el("div", { className: "field-error", hidden: true });
  return {
    node: el(
      "label",
      { className: "field" },
      el("span", { className: "label" }, label),
      control,
      opts.hint ? el("span", { className: "hint" }, opts.hint) : null,
      error
    ),
    control,
    setError(message) {
      error.textContent = message || "";
      error.hidden = !message;
    },
  };
}

function inputEl(props) {
  return el("input", { className: "input", ...props });
}

function selectEl(options, value, props = {}) {
  return el(
    "select",
    { className: "select", ...props },
    options.map(([v, label]) => el("option", { value: v, selected: v === value }, label))
  );
}

/**
 * Paint a `<input type="range">`'s current value onto `--range-pct` (views.css
 * reads it to fill the track) so the control reads at a glance instead of
 * showing a flat bar regardless of value. Call on creation and on every
 * `input` event — including for sliders other than the one the user is
 * dragging, when an action (e.g. mix rebalancing) moves their values too.
 */
function updateRangeFill(input) {
  const min = Number(input.min) || 0;
  const max = Number(input.max) || 100;
  const pct = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
  input.style.setProperty("--range-pct", `${Math.max(0, Math.min(100, pct))}%`);
}

/* ==========================================================================
   INDEX — every simulator board
   ========================================================================== */

async function renderIndex(token) {
  paint(token, el("div", { className: "view-pad lab" }, sandboxBanner(), indexSkeleton()));

  let boards;
  let operational;
  try {
    [boards, operational] = await Promise.all([
      api.get("/simulator/boards").then(items),
      api.get("/boards", { query: { kind: "operational" }, silent: true }).then(items).catch(() => []),
    ]);
  } catch (error) {
    paint(
      token,
      el(
        "div",
        { className: "view-pad lab" },
        sandboxBanner(),
        errorState(error, () => renderIndex(++generation))
      )
    );
    return;
  }
  if (stale(token)) return;

  const grid = boards.length
    ? el("div", { className: "sim-board-grid" }, boards.map((board) => simBoardCard(board)))
    : emptyState({
        icon: "flask",
        title: "No simulator boards yet",
        body: "Clone an operational board's columns and policies into a sandbox, then generate synthetic demand and run experiments against it.",
        actions: can("simulator")
          ? [el("button", { className: "btn btn-primary", onClick: () => openBoardForm(operational, () => renderIndex(++generation)) }, icon("plus"), "New simulator board")]
          : [],
      });

  paint(
    token,
    el(
      "div",
      { className: "view-pad lab" },
      sandboxBanner(),
      el(
        "div",
        { className: "view-header" },
        el(
          "div",
          { className: "stack stack-2" },
          el("div", { className: "view-title-row" }, el("h1", null, "Simulator"), el("span", { className: "badge" }, `${boards.length}`)),
          el(
            "p",
            { className: "view-lede" },
            "Every board here is an isolated laboratory: clone a real board's structure, generate demand deterministically from a seed, then run Monte Carlo forecasts and WIP experiments without touching production."
          )
        ),
        can("simulator")
          ? el("button", { className: "btn btn-primary", onClick: () => openBoardForm(operational, () => renderIndex(++generation)) }, icon("plus"), "New simulator board")
          : null
      ),
      grid
    )
  );
}

function simBoardCard(board) {
  return el(
    "a",
    { className: "sim-board-card", href: `#/simulator/${board.id}` },
    el(
      "div",
      { className: "row row-between" },
      el("span", { className: "lab-chip" }, icon("flask"), "Simulator"),
      board.archived ? el("span", { className: "badge" }, "Archived") : null
    ),
    el("h3", null, board.name),
    board.description ? el("p", { className: "t-sm muted clamp-2" }, board.description) : null,
    el(
      "div",
      { className: "row row-1 t-xs subtle" },
      icon("calendar"),
      el("span", null, `Created ${relativeTime(board.created_at)}`)
    ),
    board.source_board_id
      ? el("div", { className: "row row-1 t-xs subtle" }, icon("link"), "Cloned from an operational board")
      : null
  );
}

function indexSkeleton() {
  return el(
    "div",
    null,
    el(
      "div",
      { className: "view-header" },
      el("div", { className: "stack stack-2" }, skeleton({ width: 160, height: 26 }), skeleton({ width: 420, height: 12 })),
      skeleton({ width: 160, height: 32, radius: "var(--radius-md)" })
    ),
    el(
      "div",
      { className: "sim-board-grid" },
      Array.from({ length: 3 }, () =>
        el("div", { className: "sim-board-card" }, skeleton({ width: 90, height: 20 }), skeleton({ width: "70%", height: 20 }), skeleton({ width: "100%", height: 12 }))
      )
    )
  );
}

/**
 * Clone an operational board into a new simulator board.
 * @param {Array<object>} operationalBoards
 * @param {Function} onCreated
 */
function openBoardForm(operationalBoards, onCreated) {
  const name = labField("Name", inputEl({ value: "Scenario Lab", maxLength: 120, "data-autofocus": "" }), { hint: "A descriptive name for this sandbox." });
  const source = labField(
    "Clone structure from",
    selectEl([["", "Blank board (no columns)"], ...operationalBoards.map((b) => [b.id, b.name])], ""),
    { hint: "Copies columns, groups and WIP policies. Cards are never copied — demand is always generated." }
  );

  const submit = el("button", { className: "btn btn-primary", type: "submit" }, "Create simulator board");

  const form = el(
    "form",
    {
      className: "stack stack-4",
      noValidate: true,
      onSubmit: async (event) => {
        event.preventDefault();
        const value = name.control.value.trim();
        if (!value) { name.setError("A name is required."); return; }

        submit.classList.add("is-loading");
        submit.disabled = true;
        try {
          const payload = { name: value };
          if (source.control.value) payload.source_board_id = source.control.value;
          else payload.template = "default";
          const created = await api.post("/simulator/boards", payload);
          handle.close();
          toast(`${value} is ready.`, { tone: "success", title: "Simulator board created" });
          if (created?.id) navigate(`/simulator/${created.id}`);
          else await onCreated();
        } catch {
          submit.classList.remove("is-loading");
          submit.disabled = false;
        }
      },
    },
    name.node,
    source.node
  );

  const handle = modal({
    title: "New simulator board",
    size: "md",
    body: form,
    footer: [el("button", { className: "btn btn-secondary", onClick: () => handle.close() }, "Cancel"), submit],
  });
  submit.addEventListener("click", (e) => { e.preventDefault(); form.requestSubmit(); });
}

/* ==========================================================================
   BOARD — the bench + results
   ========================================================================== */

async function renderBoard(boardId, token) {
  if (!bench) {
    paint(token, el("div", { className: "view-pad lab" }, sandboxBanner(), boardSkeleton()));
  }

  let board;
  let runsPayload;
  try {
    [board, runsPayload] = await Promise.all([
      api.get(`/boards/${boardId}`),
      api.get("/simulator/runs", { query: { board_id: boardId }, silent: true }).catch(() => ({ items: [] })),
    ]);
  } catch (error) {
    paint(
      token,
      el(
        "div",
        { className: "view-pad lab" },
        sandboxBanner(),
        errorState(error, () => renderBoard(boardId, ++generation), { title: "Could not load this simulator board" })
      )
    );
    return;
  }
  if (stale(token)) return;

  if (board.kind !== "simulator") {
    paint(
      token,
      el(
        "div",
        { className: "view-pad" },
        emptyState({
          icon: "flask",
          title: "This is not a simulator board",
          body: `"${board.name}" is an operational board. Open it from the regular board view instead.`,
          actions: [el("a", { className: "btn btn-secondary", href: `#/board/${board.id}` }, "Open board"), el("a", { className: "btn btn-primary", href: "#/simulator" }, "Back to simulator boards")],
        })
      )
    );
    return;
  }

  const runs = items(runsPayload).sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));

  if (benchBoardId !== boardId || !bench) {
    bench = createBenchState(board);
    benchBoardId = boardId;
  }
  if (!selectedRunId && runs.length) selectedRunId = runs[0].id;

  const reload = (opts = {}) => renderBoard(boardId, ++generation, opts);
  const ctx = { board, runs, reload };

  paint(token, boardPage(board, runs, ctx));
  await mountResultsAndCharts(ctx, token);
}

function boardSkeleton() {
  return el(
    "div",
    { className: "lab-grid", style: { marginTop: "var(--space-5)" } },
    el(
      "div",
      { className: "lab-bench" },
      skeleton({ height: 300, radius: "var(--radius-lg)" }),
      skeleton({ height: 220, radius: "var(--radius-lg)" })
    ),
    el("div", { className: "lab-results" }, skeleton({ height: 420, radius: "var(--radius-lg)" }))
  );
}

/* ---- scenario builder state ------------------------------------------------ */

function applicableColumns(board) {
  return (board.columns || []).filter((c) => c.kind !== "envelope").sort((a, b) => a.position - b.position);
}

function createBenchState(board) {
  const wipLimits = {};
  for (const col of applicableColumns(board)) {
    if (col.kind !== "done") wipLimits[col.id] = col.wip_max ?? 0;
  }
  return {
    weeks: 12,
    arrivalRate: 8,
    mix: { ...DEFAULT_MIX },
    wipLimits,
    seed: DEFAULT_SEED,
    trials: 10000,
    kind: "monte_carlo_when",
    backlogSize: 40,
    targetDate: new Date(Date.now() + 60 * 86400000).toISOString().slice(0, 10),
    running: false,
  };
}

function classMixFraction() {
  const total = CLASS_KEYS.reduce((sum, k) => sum + (bench.mix[k] || 0), 0) || 1;
  const out = {};
  for (const k of CLASS_KEYS) out[k] = (bench.mix[k] || 0) / total;
  return out;
}

/** Rebalance the four mix sliders so they always total 100. */
function rebalanceMix(changedKey, newValue) {
  const value = Math.max(0, Math.min(100, Math.round(newValue)));
  const others = CLASS_KEYS.filter((k) => k !== changedKey);
  const remaining = 100 - value;
  const sumOthers = others.reduce((s, k) => s + (bench.mix[k] || 0), 0);

  const next = { ...bench.mix, [changedKey]: value };
  if (sumOthers <= 0) {
    const share = Math.floor(remaining / others.length);
    others.forEach((k, i) => { next[k] = i === others.length - 1 ? remaining - share * (others.length - 1) : share; });
  } else {
    let assigned = 0;
    others.forEach((k, i) => {
      if (i === others.length - 1) { next[k] = remaining - assigned; return; }
      const share = Math.round(((bench.mix[k] || 0) / sumOthers) * remaining);
      next[k] = share;
      assigned += share;
    });
  }
  bench.mix = next;
}

/* ---- page assembly --------------------------------------------------------- */

function boardPage(board, runs, ctx) {
  const columns = applicableColumns(board);

  return el(
    "div",
    { className: "view-pad lab" },
    sandboxBanner(),
    el(
      "a",
      { className: "row row-1 t-sm muted", href: "#/simulator", style: { marginBottom: "var(--space-3)", width: "fit-content" } },
      icon("chevron-left"),
      "All simulator boards"
    ),
    el(
      "div",
      { className: "view-header" },
      el(
        "div",
        { className: "stack stack-2" },
        el(
          "div",
          { className: "view-title-row" },
          el("h1", null, board.name),
          el("span", { className: "lab-chip" }, icon("flask"), "Sandbox")
        ),
        el(
          "p",
          { className: "view-lede" },
          board.description || "Build a scenario on the left, run it, and read the forecast or experiment on the right."
        )
      ),
      el(
        "div",
        { className: "row row-2" },
        el("a", { className: "btn btn-secondary btn-sm", href: `#/board/${board.id}` }, icon("board"), "Open board view"),
        can("simulator")
          ? el("button", { className: "btn btn-secondary btn-sm", onClick: () => resetBoard(board, ctx.reload) }, icon("refresh"), "Reset board")
          : null
      )
    ),
    el(
      "div",
      { className: "lab-grid" },
      el("div", { id: "lab-bench-slot", className: "lab-bench" }, benchPanel(board, columns, ctx)),
      el(
        "div",
        { className: "lab-results" },
        el("div", { id: "lab-results-slot" }, runs.length ? resultsSkeletonSlot() : noRunsYet(ctx)),
        el("div", { id: "lab-history-slot" }, runHistoryPanel(runs, ctx))
      )
    )
  );
}

function resultsSkeletonSlot() {
  return el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body" }, skeleton({ height: 260, radius: "var(--radius-md)" })));
}

function noRunsYet() {
  return el(
    "div",
    { className: "lab-panel" },
    el(
      "div",
      { className: "lab-panel-body" },
      emptyState({
        icon: "flask",
        title: "No runs yet",
        body: "Set the scenario on the left and press Run to get a forecast, an experiment, or a replay.",
      })
    )
  );
}

/* ---- the bench -------------------------------------------------------------- */

function benchPanel(board, columns, ctx) {
  const paramSummary = el("div", { className: "param-summary" });
  const mixBar = el("div", { className: "mix-bar" });
  const mixTotal = el("div", { className: "mix-total" });
  const kindExtra = el("div", { className: "stack stack-3" });

  const refreshSummary = () => {
    const frac = classMixFraction();
    render(
      paramSummary,
      el("b", null, bench.weeks), " weeks", el("span", { className: "sep" }, " · "),
      el("b", null, bench.arrivalRate), " /wk arrivals", el("span", { className: "sep" }, " · "),
      el("b", null, `${bench.mix.expedite}/${bench.mix.standard}/${bench.mix.fixed_date}/${bench.mix.intangible}`), " mix (E/S/F/I)",
      el("span", { className: "sep" }, " · "),
      el("b", null, `seed ${bench.seed}`),
      el("span", { className: "sep" }, " · "),
      el("b", null, formatNumber(bench.trials)), " trials"
    );
    render(
      mixBar,
      CLASS_KEYS.map((k) => el("div", { className: "mix-bar-seg", dataset: { sc: k }, style: { flexBasis: `${frac[k] * 100}%` } }))
    );
    const total = CLASS_KEYS.reduce((sum, k) => sum + (bench.mix[k] || 0), 0);
    render(mixTotal, el("span", null, "Total"), el("b", null, `${total}%`));
  };

  function weeksField() {
    const valueEl = el("span", { className: "slider-value" }, `${bench.weeks} wk`);
    const input = el("input", {
      className: "range", type: "range", min: 2, max: 52, step: 1, value: bench.weeks,
      "aria-label": "Simulated weeks",
      onInput: (e) => {
        bench.weeks = Number(e.target.value);
        valueEl.textContent = `${bench.weeks} wk`;
        updateRangeFill(e.target);
        refreshSummary();
      },
    });
    updateRangeFill(input);
    return el(
      "div", { className: "slider-field" },
      el("div", { className: "slider-head" }, el("span", { className: "slider-label" }, "Simulated weeks"), valueEl),
      input,
      el("div", { className: "range-scale" }, el("span", null, "2"), el("span", null, "52"))
    );
  }

  function arrivalField() {
    const valueEl = el("span", { className: "slider-value" }, `${bench.arrivalRate} /wk`);
    const input = el("input", {
      className: "range", type: "range", min: 1, max: 50, step: 0.5, value: bench.arrivalRate,
      "aria-label": "Weekly arrival rate",
      onInput: (e) => {
        bench.arrivalRate = Number(e.target.value);
        valueEl.textContent = `${bench.arrivalRate} /wk`;
        updateRangeFill(e.target);
        refreshSummary();
      },
    });
    updateRangeFill(input);
    return el(
      "div", { className: "slider-field" },
      el("div", { className: "slider-head" }, el("span", { className: "slider-label" }, "Weekly arrival rate"), valueEl),
      input,
      el("div", { className: "range-scale" }, el("span", null, "1"), el("span", null, "50"))
    );
  }

  function mixRows() {
    const rows = CLASS_KEYS.map((key) => {
      const pct = el("span", { className: "mix-pct" }, `${bench.mix[key]}%`);
      const input = el("input", {
        className: "range", type: "range", min: 0, max: 100, step: 1, value: bench.mix[key], dataset: { sc: key },
        "aria-label": `${CLASS_LABEL[key]} share`,
        onInput: (e) => {
          rebalanceMix(key, Number(e.target.value));
          for (const row of mixInputs) {
            row.input.value = bench.mix[row.key];
            row.pct.textContent = `${bench.mix[row.key]}%`;
            updateRangeFill(row.input);
          }
          refreshSummary();
        },
      });
      return { key, input, pct, node: el("div", { className: "mix-row", dataset: { sc: key } }, el("span", { className: "mix-name" }, CLASS_LABEL[key]), input, pct) };
    });
    return rows;
  }

  const mixInputs = mixRows();
  mixInputs.forEach((row) => updateRangeFill(row.input));
  const wipRows = columns
    .filter((c) => c.kind !== "done")
    .map((col) => {
      const input = el("input", {
        className: "input", type: "number", min: 0, step: 1,
        value: bench.wipLimits[col.id] || "",
        placeholder: "no limit",
        "aria-label": `${col.name} WIP limit`,
        onInput: (e) => { bench.wipLimits[col.id] = e.target.value === "" ? 0 : Number(e.target.value); },
      });
      return el(
        "div", { className: "wip-limit-row" },
        el("div", { className: "wip-limit-name" }, icon(col.kind === "activity" ? "activity" : "queue"), el("span", { className: "truncate" }, col.name)),
        input
      );
    });

  function renderKindExtra() {
    render(
      kindExtra,
      bench.kind === "monte_carlo_when"
        ? labField(
            "Backlog size",
            inputEl({
              type: "number", min: 1, step: 1, value: bench.backlogSize,
              onInput: (e) => { bench.backlogSize = Math.max(1, Number(e.target.value) || 1); },
            }),
            { hint: "How many items need to ship." }
          ).node
        : null,
      bench.kind === "monte_carlo_how_many"
        ? labField(
            "Target date",
            inputEl({
              type: "date", value: bench.targetDate,
              onInput: (e) => { bench.targetDate = e.target.value; },
            }),
            { hint: "How many items are likely delivered by this date." }
          ).node
        : null
    );
  }

  const kindPicker = el(
    "div",
    { className: "kind-picker", role: "radiogroup", "aria-label": "Run kind" },
    RUN_KINDS.map((k) => {
      const locked = k.flag && !store.hasFlag(k.flag);
      const radio = el("input", {
        className: "radio", type: "radio", name: "run-kind", value: k.key,
        checked: bench.kind === k.key, disabled: locked,
        onChange: () => { bench.kind = k.key; renderKindExtra(); },
      });
      return el(
        "label",
        { className: "kind-option", ...(locked ? { "data-tooltip": "This flag is off for the workspace. Ask a master to enable it under Admin → Feature flags." } : {}) },
        radio,
        el(
          "div",
          { className: "stack stack-1" },
          el("div", { className: "kind-option-title" }, k.label),
          el("div", { className: "kind-option-note" }, locked ? "Requires the Monte Carlo forecasting flag." : k.note)
        )
      );
    })
  );

  const runButton = el("button", { className: "btn btn-primary btn-block", onClick: () => runScenario(board, ctx) }, icon("play"), "Run");
  const generateButton = el(
    "button",
    { className: "btn btn-secondary btn-block", onClick: () => generateDemand(board, ctx) },
    icon("sparkle"),
    "Generate demand into board"
  );

  refreshSummary();
  renderKindExtra();

  return el(
    "div",
    { className: "stack stack-4" },
    el(
      "div",
      { className: "lab-panel" },
      el("div", { className: "lab-panel-head" }, el("span", { className: "lab-panel-title" }, icon("flask"), "Scenario")),
      el(
        "div",
        { className: "lab-panel-body" },
        weeksField(),
        arrivalField(),
        el(
          "div",
          { className: "stack stack-2" },
          el("span", { className: "label" }, "Class-of-service mix"),
          mixBar,
          el("div", { className: "stack stack-1" }, mixInputs.map((r) => r.node)),
          mixTotal
        ),
        wipRows.length
          ? el("div", { className: "stack stack-2" }, el("span", { className: "label" }, "Per-column WIP limits"), el("div", { className: "wip-grid" }, wipRows))
          : null,
        el(
          "div",
          { className: "grid grid-2" },
          labField("Seed", inputEl({ type: "number", value: bench.seed, onInput: (e) => { bench.seed = Number(e.target.value) || DEFAULT_SEED; } }), { hint: "Deterministic — same seed, same demand." }).node,
          labField("Trials", inputEl({ type: "number", value: bench.trials, min: 100, step: 100, onInput: (e) => { bench.trials = Math.max(100, Number(e.target.value) || 10000); } }), { hint: "Monte Carlo only." }).node
        ),
        paramSummary
      )
    ),
    el(
      "div",
      { className: "lab-panel" },
      el("div", { className: "lab-panel-head" }, el("span", { className: "lab-panel-title" }, icon("target-rings"), "Run kind")),
      el("div", { className: "lab-panel-body" }, kindPicker, kindExtra, runButton, can("simulator") ? generateButton : null)
    )
  );
}

/* ---- actions ---------------------------------------------------------------- */

function scenarioParams() {
  const frac = classMixFraction();
  return {
    weeks: bench.weeks,
    arrival_rate: bench.arrivalRate,
    class_mix: frac,
    seed: bench.seed,
    wip_limits: { ...bench.wipLimits },
  };
}

async function runScenario(board, ctx) {
  const kind = bench.kind;
  const params = scenarioParams();
  if (kind === "monte_carlo_when") { params.backlog_size = bench.backlogSize; params.trials = bench.trials; }
  if (kind === "monte_carlo_how_many") {
    const days = Math.max(1, Math.round((new Date(bench.targetDate).getTime() - Date.now()) / 86400000));
    params.days = days;
    params.trials = bench.trials;
  }

  announce(`Running ${KIND_LABEL[kind]}`);
  toast(`Running “${KIND_LABEL[kind]}”…`, { tone: "accent", duration: 2200 });

  try {
    const run = await api.post("/simulator/runs", { board_id: board.id, kind, params });
    selectedRunId = run?.id || null;
    toast(`${KIND_LABEL[kind]} finished.`, { tone: "success", title: "Run complete" });
    await ctx.reload();
  } catch {
    /* the toast from api.js already explains why */
  }
}

async function generateDemand(board, ctx) {
  const params = scenarioParams();
  const confirmed = await confirmDialog({
    title: "Generate demand into this board",
    message: `Materialise ${bench.weeks} weeks of synthetic demand (about ${Math.round(bench.weeks * bench.arrivalRate)} cards) onto “${board.name}”, seeded deterministically so it is reproducible. Existing simulator cards on this board are cleared first.`,
    confirmLabel: "Generate demand",
  });
  if (!confirmed) return;

  try {
    await api.post(`/simulator/boards/${board.id}/generate`, { ...params, reset: true });
    toast("Demand generated. Open the board view to watch it flow.", {
      tone: "success",
      title: "Demand materialised",
      action: { label: "Open board", onClick: () => navigate(`/board/${board.id}`) },
    });
    await ctx.reload();
  } catch {
    /* the toast from api.js already explains why */
  }
}

async function resetBoard(board, reload) {
  const confirmed = await confirmDialog({
    title: "Reset this simulator board",
    message: `Remove every generated card and movement from “${board.name}”. Saved runs are kept.`,
    confirmLabel: "Reset board",
    tone: "danger",
  });
  if (!confirmed) return;
  try {
    await api.post(`/simulator/boards/${board.id}/reset`);
    toast(`${board.name} was reset.`, { tone: "success" });
    await reload();
  } catch {
    /* toast already shown */
  }
}

/* ==========================================================================
   RUN HISTORY & COMPARE
   ========================================================================== */

function runHistoryPanel(runs, ctx) {
  if (!runs.length) return null;

  const compareBar = el("div", { className: "compare-bar", hidden: compareIds.size < 2 });
  render(
    compareBar,
    el("span", { className: "t-sm" }, `${compareIds.size} runs selected`),
    el("div", { className: "spacer" }),
    el("button", { className: "btn btn-ghost btn-sm", onClick: () => { compareIds = new Set(); refreshHistorySlot(runs, ctx); } }, "Clear"),
    el("button", { className: "btn btn-primary btn-sm", onClick: () => compareSelected(ctx) }, icon("chart"), "Compare selected")
  );

  const list = el(
    "div",
    { className: "run-list", role: "list" },
    runs.map((run) => runItem(run, ctx))
  );

  return el(
    "div",
    { className: "lab-panel", style: { marginTop: "var(--space-4)" } },
    el(
      "div",
      { className: "lab-panel-head" },
      el("span", { className: "lab-panel-title" }, icon("clock"), "Run history"),
      el("span", { className: "badge" }, String(runs.length))
    ),
    list,
    compareBar
  );
}

function runItem(run, ctx) {
  const checkbox = el("input", {
    className: "checkbox", type: "checkbox", checked: compareIds.has(run.id),
    "aria-label": `Select ${run.name} for comparison`,
    onChange: (e) => {
      if (e.target.checked) compareIds.add(run.id); else compareIds.delete(run.id);
      refreshHistorySlot(ctx.runs, ctx);
    },
  });

  return el(
    "div",
    { className: "run-item", role: "listitem", dataset: { selected: String(compareIds.has(run.id)), current: String(run.id === selectedRunId) } },
    checkbox,
    el(
      "button",
      { className: "run-open", onClick: () => { selectedRunId = run.id; refreshResultsSlot(ctx); } },
      el("span", { className: "run-name" }, run.name || KIND_LABEL[run.kind] || run.kind),
      el(
        "span",
        { className: "run-meta" },
        el("span", { className: "lab-chip" }, KIND_LABEL[run.kind] || humanize(run.kind)),
        el("span", null, relativeTime(run.created_at)),
        run.created_by_name ? el("span", null, `by ${run.created_by_name}`) : null
      )
    ),
    el("button", { className: "btn btn-ghost btn-icon btn-sm", "aria-label": `Open ${run.name}`, onClick: () => { selectedRunId = run.id; refreshResultsSlot(ctx); } }, icon("chevron-right"))
  );
}

function refreshHistorySlot(runs, ctx) {
  const slot = root?.querySelector("#lab-history-slot");
  if (!slot) return;
  render(slot, runHistoryPanel(runs, ctx));
}

async function compareSelected(ctx) {
  if (compareIds.size < 2) return;
  const slot = root?.querySelector("#lab-results-slot");
  if (slot) render(slot, resultsSkeletonSlot());
  try {
    const compare = await api.post("/simulator/compare", { run_ids: Array.from(compareIds) });
    if (!root) return;
    const target = root.querySelector("#lab-results-slot");
    if (target) render(target, compareView(compare));
  } catch {
    /* toast already shown */
  }
}

/* ==========================================================================
   RESULTS — per run kind
   ========================================================================== */

async function mountResultsAndCharts(ctx, token) {
  await refreshResultsSlot(ctx, token);
}

async function refreshResultsSlot(ctx, token) {
  const slot = root?.querySelector("#lab-results-slot");
  if (!slot) return;
  const run = ctx.runs.find((r) => r.id === selectedRunId);
  if (!run) { render(slot, noRunsYet()); return; }

  render(slot, resultsSkeletonSlot());
  const mod = await loadCharts();
  if (token !== undefined && stale(token)) return;
  const freshSlot = root?.querySelector("#lab-results-slot");
  if (!freshSlot) return;
  render(freshSlot, renderRunResults(run, mod));
}

function resultHeader(run) {
  return el(
    "div",
    { className: "result-head" },
    el(
      "div",
      { className: "stack stack-1" },
      el("h2", { className: "t-lg t-semibold" }, run.name || KIND_LABEL[run.kind]),
      el("span", { className: "t-xs subtle" }, `${KIND_LABEL[run.kind] || humanize(run.kind)} · run ${relativeTime(run.created_at)}`)
    ),
    el("span", { className: "lab-chip" }, icon("flask"), "Sandbox result")
  );
}

function renderRunResults(run, mod) {
  const results = run.results || {};
  const kind = results.kind || run.kind;

  if (kind === "monte_carlo_when" || kind === "monte_carlo_how_many") return monteCarloView(run, results, mod, kind);
  if (kind === "wip_experiment" || kind === "replay") return experimentView(run, results, mod);
  if (kind === "class_of_service_mix") return classMixView(run, results, mod);
  return el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body" }, resultHeader(run), el("p", { className: "muted" }, "This run has no renderable result yet.")));
}

/* ---- Monte Carlo -------------------------------------------------------- */

function toHistogramBins(bins) {
  return (bins || []).map((b) => ({
    from: b.x0 !== undefined ? b.x0 : b.x - 0.5,
    to: b.x1 !== undefined ? b.x1 : b.x + 0.5,
    count: b.count,
  }));
}

function pctLegend(levels, valueLabel, noteLabel) {
  return el(
    "div",
    { className: "pct-legend" },
    (levels || []).map((l) =>
      el(
        "div",
        { className: "pct-cell", dataset: { p: String(l.confidence), recommended: String(l.confidence === 85) } },
        el("span", { className: "pct-label" }, `${l.confidence}% confidence`),
        el("span", { className: "pct-value" }, valueLabel(l)),
        el("span", { className: "pct-note" }, noteLabel(l))
      )
    )
  );
}

function monteCarloView(run, results, mod, kind) {
  const isWhen = kind === "monte_carlo_when";
  const bins = toHistogramBins(results.histogram);
  const markers = Object.entries(results.percentiles || {}).map(([key, value]) => ({
    key,
    label: key.toUpperCase(),
    value,
    tone: key === "p85" ? "accent" : "neutral",
  }));

  const chartHost = el("div", { className: "chart-frame" });
  if (mod?.histogram) {
    mod.histogram(chartHost, {
      bins,
      markers,
      title: isWhen ? "Weeks-to-delivery distribution" : "Items-delivered distribution",
      desc: isWhen
        ? `Distribution of simulated weeks to deliver ${results.backlog_size} items across ${formatNumber(results.trials)} trials.`
        : `Distribution of simulated items delivered in ${results.days} days across ${formatNumber(results.trials)} trials.`,
      formatX: (v) => (isWhen ? `${Math.round(v)} wk` : `${Math.round(v)}`),
      yLabel: "Trials",
    });
  } else {
    chartHost.append(el("p", { className: "muted t-sm" }, `${bins.length} distribution buckets — chart module unavailable.`));
  }

  return el(
    "div",
    { className: "stack stack-4" },
    el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body stack stack-4" },
      resultHeader(run),
      el("div", { className: "confidence-sentence" }, el("b", null, results.sentence || "")),
      results.low_confidence
        ? el("div", { className: "note-strip" }, icon("alert"), el("span", null, `Based on only ${results.samples?.weeks ?? "a few"} weeks of throughput samples — treat this as low confidence.`))
        : null,
      isWhen
        ? pctLegend(results.levels, (l) => `${l.weeks} wk`, (l) => `by ${l.date_label}`)
        : pctLegend(results.levels, (l) => `${formatNumber(l.items)} items`, () => `by ${results.target_date_label}`),
      chartHost
    )),
    results.verdict
      ? el("div", { className: "verdict" }, el("div", { className: "verdict-icon" }, icon("sparkle")), el("div", { className: "verdict-body" }, results.verdict))
      : null
  );
}

/* ---- WIP experiment / replay --------------------------------------------- */

function bestRowIndex(rows) {
  const usable = rows.map((r, i) => ({ r, i })).filter((x) => x.r.lead_time_p85 != null);
  if (!usable.length) return -1;
  const bestThroughput = Math.max(...usable.map((x) => x.r.throughput_per_week || 0));
  const healthy = usable.filter((x) => (x.r.throughput_per_week || 0) >= bestThroughput * 0.95);
  const pool = healthy.length ? healthy : usable;
  return pool.reduce((best, x) => (x.r.lead_time_p85 < (best?.r.lead_time_p85 ?? Infinity) ? x : best), pool[0]).i;
}

function experimentView(run, results, mod) {
  const rows = results.rows || [];
  const bestIndex = bestRowIndex(rows);

  const leadHost = el("div", { className: "chart-frame" });
  const wipHost = el("div", { className: "chart-frame" });

  if (mod?.bars) {
    mod.bars(leadHost, {
      items: rows.map((r) => ({ label: r.name, value: r.lead_time_p85 ?? 0 })),
      title: "System lead time (85th percentile) per configuration",
      desc: "The headline of Little's Law: as the WIP policy tightens, lead time falls — until the board starves.",
      valueLabel: "Lead time p85 (days)",
      yLabel: "Days",
    });
    mod.bars(wipHost, {
      items: rows.map((r) => ({ label: r.name, value: r.avg_wip ?? 0 })),
      title: "Average work in progress per configuration",
      desc: "Average number of items in flow under each policy.",
      valueLabel: "Avg WIP (items)",
      yLabel: "Items",
      color: "var(--lab)",
    });
  } else {
    const fallback = () => el("p", { className: "muted t-sm" }, "Chart module unavailable — see the table below.");
    leadHost.append(fallback());
    wipHost.append(fallback());
  }

  const table = el(
    "div",
    { className: "table-wrap" },
    el(
      "table",
      { className: "table" },
      el(
        "thead",
        null,
        el(
          "tr",
          null,
          ["Configuration", "Lead time avg", "Lead time p85", "Throughput/wk", "Flow efficiency", "Avg WIP", "Delivered"].map((h) =>
            el("th", { className: h === "Configuration" ? null : "num" }, h)
          )
        )
      ),
      el(
        "tbody",
        null,
        rows.map((r, i) =>
          el(
            "tr",
            { dataset: i === bestIndex ? { best: "true" } : {} },
            el("td", null, r.name),
            el("td", { className: "num" }, r.lead_time_avg != null ? `${r.lead_time_avg}d` : "—"),
            el("td", { className: "num" }, r.lead_time_p85 != null ? `${r.lead_time_p85}d` : "—"),
            el("td", { className: "num" }, r.throughput_per_week ?? "—"),
            el("td", { className: "num" }, r.flow_efficiency_pct != null ? `${r.flow_efficiency_pct}%` : "—"),
            el("td", { className: "num" }, r.avg_wip ?? "—"),
            el("td", { className: "num" }, r.delivered ?? "—")
          )
        )
      )
    )
  );

  return el(
    "div",
    { className: "stack stack-4" },
    el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body stack stack-4" }, resultHeader(run), leadHost, wipHost)),
    el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body" }, el("h3", { className: "t-sm t-semibold", style: { marginBottom: "var(--space-3)" } }, "Per-configuration metrics"), table)),
    results.verdict
      ? el("div", { className: "verdict" }, el("div", { className: "verdict-icon" }, icon("sparkle")), el("div", { className: "verdict-body" }, results.verdict))
      : null
  );
}

/* ---- class-of-service mix experiment ------------------------------------- */

function classMixView(run, results, mod) {
  const rows = results.rows || [];

  const leadHost = el("div", { className: "chart-frame" });
  const throughputHost = el("div", { className: "chart-frame" });

  if (mod?.bars) {
    mod.bars(leadHost, {
      items: rows.map((r) => ({ label: `${r.expedite_share_pct}%`, value: r.standard_lead_time_p85 ?? 0 })),
      overlay: { label: "Expedite lead time p85 (days)", values: rows.map((r) => r.expedite_lead_time_p85 ?? null) },
      title: "Standard-class lead time as expedite share grows",
      desc: "Bars are the standard class's 85th-percentile lead time; the line is the expedited class's own lead time at the same share.",
      valueLabel: "Standard lead time p85 (days)",
      yLabel: "Days",
      xLabel: "Expedite share",
    });
    mod.bars(throughputHost, {
      items: rows.map((r) => ({ label: `${r.expedite_share_pct}%`, value: r.throughput_per_week ?? 0 })),
      title: "Total throughput as expedite share grows",
      desc: "Expediting reorders the queue; it does not create capacity.",
      valueLabel: "Throughput (items/week)",
      yLabel: "Items/week",
      color: "var(--lab)",
    });
  }

  const table = el(
    "div",
    { className: "table-wrap" },
    el(
      "table",
      { className: "table" },
      el(
        "thead",
        null,
        el(
          "tr",
          null,
          ["Expedite share", "Standard lead p85", "Expedite lead p85", "Throughput/wk", "Flow efficiency", "Avg WIP"].map((h) =>
            el("th", { className: h === "Expedite share" ? null : "num" }, h)
          )
        )
      ),
      el(
        "tbody",
        null,
        rows.map((r) =>
          el(
            "tr",
            null,
            el("td", null, `${r.expedite_share_pct}%`),
            el("td", { className: "num" }, r.standard_lead_time_p85 != null ? `${r.standard_lead_time_p85}d` : "—"),
            el("td", { className: "num" }, r.expedite_lead_time_p85 != null ? `${r.expedite_lead_time_p85}d` : "—"),
            el("td", { className: "num" }, r.throughput_per_week ?? "—"),
            el("td", { className: "num" }, r.flow_efficiency_pct != null ? `${r.flow_efficiency_pct}%` : "—"),
            el("td", { className: "num" }, r.avg_wip ?? "—")
          )
        )
      )
    )
  );

  return el(
    "div",
    { className: "stack stack-4" },
    el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body stack stack-4" }, resultHeader(run), leadHost, throughputHost)),
    el("div", { className: "lab-panel" }, el("div", { className: "lab-panel-body" }, el("h3", { className: "t-sm t-semibold", style: { marginBottom: "var(--space-3)" } }, "Per-share metrics"), table)),
    results.verdict
      ? el("div", { className: "verdict" }, el("div", { className: "verdict-icon" }, icon("sparkle")), el("div", { className: "verdict-body" }, results.verdict))
      : null
  );
}

/* ---- compare view --------------------------------------------------------- */

function deltaChip(cell) {
  if (cell.delta_pct === null || cell.delta_pct === undefined) return el("span", { className: "t-xs subtle" }, "—");
  const dir = cell.improved === true ? "better" : cell.improved === false ? "worse" : "flat";
  const sign = cell.delta_pct > 0 ? "+" : "";
  return el("span", { className: "delta", dataset: { dir } }, `${sign}${cell.delta_pct}%`);
}

function compareView(compare) {
  const runs = compare.runs || [];
  const rows = compare.rows || [];
  const baselineId = compare.baseline_run_id || runs[0]?.id;

  return el(
    "div",
    { className: "stack stack-4" },
    el(
      "div",
      { className: "lab-panel" },
      el(
        "div",
        { className: "lab-panel-head" },
        el("span", { className: "lab-panel-title" }, icon("chart"), "Comparing runs"),
        el("span", { className: "badge" }, String(runs.length))
      ),
      el(
        "div",
        { className: "lab-panel-body" },
        el(
          "div",
          { className: "table-wrap" },
          el(
            "table",
            { className: "table compare-table" },
            el(
              "thead",
              null,
              el(
                "tr",
                null,
                el("th", { className: "metric-col" }, "Metric"),
                runs.map((r) => el("th", { className: "num" }, r.id === baselineId ? `${r.name} (baseline)` : r.name))
              )
            ),
            el(
              "tbody",
              null,
              rows.map((row) =>
                el(
                  "tr",
                  null,
                  el("th", { scope: "row" }, `${row.label}${row.unit ? ` (${row.unit})` : ""}`),
                  runs.map((r) => {
                    const cell = (row.cells || []).find((c) => c.run_id === r.id) || {};
                    return el(
                      "td",
                      { className: "num" },
                      el(
                        "div",
                        { className: "stack stack-1", style: { alignItems: "flex-end" } },
                        el("span", null, cell.value ?? "—"),
                        r.id === baselineId ? null : deltaChip(cell)
                      )
                    );
                  })
                )
              )
            )
          )
        )
      )
    ),
    compare.verdict
      ? el("div", { className: "verdict" }, el("div", { className: "verdict-icon" }, icon("sparkle")), el("div", { className: "verdict-body" }, compare.verdict))
      : null
  );
}
