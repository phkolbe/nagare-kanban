/**
 * Nagare — Projects & the sealed envelope (SPEC 9.4, Requirement 8).
 *
 * Two surfaces behind one module, selected by the route:
 *   #/projects           the index — one card per project with its demand
 *                        split (sealed / upstream / downstream / delivered)
 *                        and a throughput sparkline
 *   #/projects/:id       the detail — the project's demands, with sealed ones
 *                        rendered as literal closed envelopes
 *
 * The envelope is the interesting part. A sealed demand is captured but not
 * yet read: the title is visible so a person can choose what to open, and
 * everything else — description, size, class of service, and above all the
 * assignee — is deliberately withheld, because nothing has been committed to
 * anybody. Opening it is a decision, so it gets a decision's worth of
 * ceremony: a short unsealing animation, then `POST /cards/{id}/open-envelope`,
 * then the demand visibly arriving in upstream.
 */

import api from "./api.js";
import store, { can } from "./store.js";
import {
  el, icon, modal, toast, emptyState, errorState, skeleton, confirmDialog,
  render, relativeTime, formatDate, formatNumber, humanize, avatar, debounce,
  announce,
} from "./ui.js";
import { navigate } from "./router.js";

/* ==========================================================================
   Charts — owned by the CHARTS agent; we only ever call its factories.
   ========================================================================== */

let chartsPromise = null;

/** Load `charts.js` once. A missing module degrades the sparkline, not the view. */
function loadCharts() {
  if (!chartsPromise) {
    chartsPromise = import("./charts.js").catch((error) => {
      console.warn("[projects] charts.js is unavailable; sparklines are text-only.", error);
      return null;
    });
  }
  return chartsPromise;
}

/**
 * Call a charts.js factory without hard-coding its calling convention.
 * Returns the produced node, or null if the factory is absent or unhappy.
 */
function buildChart(mod, name, spec) {
  const factory = mod && mod[name];
  if (typeof factory !== "function") return null;
  const candidates = [[spec], [spec.data ?? spec.series ?? spec.values, spec]];
  for (const args of candidates) {
    try {
      const node = factory(...args);
      if (node instanceof Node) return node;
    } catch {
      /* try the other convention before giving up */
    }
  }
  return null;
}

/* ==========================================================================
   Module state
   ========================================================================== */

const PALETTE = [
  { hex: "#0B6B78", name: "Teal" },
  { hex: "#2551A4", name: "Blue" },
  { hex: "#5B4BB8", name: "Indigo" },
  { hex: "#7A2E6E", name: "Plum" },
  { hex: "#8C3B2E", name: "Rust" },
  { hex: "#94600B", name: "Amber" },
  { hex: "#166534", name: "Green" },
  { hex: "#4A5568", name: "Slate" },
];

const STATUS_ORDER = ["discovery", "active", "on_hold", "done", "cancelled"];

const SERVICE_CLASSES = ["standard", "expedite", "fixed_date", "intangible"];
const VALUE_AREAS = ["feature", "defect", "risk", "debt", "compliance"];
const DEMAND_TYPES = ["value", "failure", "maintenance"];
const SIZES = ["xs", "s", "m", "l", "xl"];

/** Mount-scoped state. Reset on every mount so a stale response cannot land. */
let root = null;
let generation = 0;
let indexFilters = { status: "", q: "" };
let searchDebounced = null;

/* ==========================================================================
   Lifecycle
   ========================================================================== */

/**
 * Router entry point.
 * @param {HTMLElement} container
 * @param {{projectId?: string}} params
 */
export async function mount(container, params = {}) {
  root = container;
  generation += 1;
  const token = generation;

  if (params.projectId) {
    await renderDetail(params.projectId, token);
  } else {
    await renderIndex(token);
  }
}

/** Router teardown. */
export function unmount() {
  generation += 1;
  searchDebounced = null;
  root = null;
}

/** True when a newer mount has superseded this render pass. */
function stale(token) {
  return token !== generation || !root;
}

function paint(token, ...children) {
  if (stale(token)) return;
  render(root, ...children);
}

/* ==========================================================================
   Data helpers
   ========================================================================== */

/** Unwrap `{items, total}` or a bare array. */
function items(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.items) ? payload.items : [];
}

/** Operational boards, newest-configured first, capped so the index stays fast. */
function operationalBoards() {
  return store.get().boards.filter((b) => b.kind === "operational" && !b.archived).slice(0, 4);
}

/**
 * Load the board views needed to classify cards by flow position.
 *
 * `GET /projects` reports totals but not *where* the work sits, and the split
 * the index promises (sealed / upstream / downstream / delivered) is a
 * function of each card's column. One board view per operational board gives
 * columns and cards together, which is cheaper than a card list plus a board
 * fetch per board.
 */
async function loadFlowContext() {
  const boards = operationalBoards();
  const views = await Promise.all(
    boards.map((board) =>
      api.get(`/boards/${board.id}/view`, { query: { segment: "unified" }, silent: true })
        .catch(() => null)
    )
  );

  const columns = new Map();
  const cards = [];
  for (const view of views) {
    if (!view) continue;
    for (const column of view.columns || []) columns.set(column.id, column);
    for (const card of view.cards || []) cards.push(card);
  }
  return { columns, cards, boards, degraded: views.some((v) => !v) };
}

/**
 * Where a card sits in the flow.
 * @returns {"sealed"|"upstream"|"downstream"|"delivered"}
 */
function flowBucket(card, columns) {
  if (card.envelope_state === "sealed") return "sealed";
  const column = columns.get(card.column_id);
  if (card.delivered_at || column?.kind === "done") return "delivered";
  if (column?.segment === "downstream") return "downstream";
  return "upstream";
}

/** Count a project's demands per bucket from real cards. */
function bucketCounts(cards, columns) {
  const counts = { sealed: 0, upstream: 0, downstream: 0, delivered: 0 };
  for (const card of cards) {
    if (card.archived) continue;
    counts[flowBucket(card, columns)] += 1;
  }
  return counts;
}

/**
 * Fallback split when no board view could be read: `GET /projects` still
 * carries enough to be honest about sealed / in-flight / delivered.
 */
function countsFromProject(project) {
  const sealed = project.sealed_count ?? 0;
  const delivered = project.delivered_count ?? 0;
  const inFlight = project.in_flight_count ?? 0;
  const total = project.card_count ?? sealed + delivered + inFlight;
  const upstream = Math.max(0, total - sealed - delivered - inFlight);
  return { sealed, upstream, downstream: inFlight, delivered };
}

/* ==========================================================================
   Small shared pieces
   ========================================================================== */

function statusPill(status) {
  return el("span", { className: "status-pill", dataset: { status } }, humanize(status));
}

/** Target date with an urgency tone. Overdue only matters for live projects. */
function targetDate(project) {
  if (!project.target_date) return null;
  const ts = Date.parse(project.target_date);
  const live = project.status !== "done" && project.status !== "cancelled";
  const days = Number.isNaN(ts) ? null : (ts - Date.now()) / 86400000;
  const due = !live || days === null ? null : days < 0 ? "late" : days <= 14 ? "soon" : null;

  return el(
    "span",
    { className: "row row-1 target-date", dataset: due ? { due } : {} },
    icon("target"),
    el("span", null, `Target ${formatDate(project.target_date)}`)
  );
}

function ownerRow(project) {
  const owner = project.owner_name
    ? { id: project.owner_id, name: project.owner_name }
    : null;
  return el(
    "span",
    { className: "row row-1" },
    avatar(owner, { size: "xs" }),
    el("span", null, owner ? owner.name : "No owner")
  );
}

function countsGrid(counts) {
  const cells = [
    ["sealed", "Sealed", counts.sealed],
    ["upstream", "Upstream", counts.upstream],
    ["downstream", "Downstream", counts.downstream],
    ["delivered", "Delivered", counts.delivered],
  ];
  return el(
    "div",
    { className: "project-counts" },
    cells.map(([kind, label, value]) =>
      el(
        "div",
        { className: "count-cell", dataset: { kind, zero: String(!value) } },
        el("span", { className: "count-value" }, formatNumber(value)),
        el(
          "span",
          { className: "count-label", title: `${label} demands` },
          el("span", { className: "count-label-text" }, label)
        )
      )
    )
  );
}

/**
 * The per-board envelope policy toggle (Req. 8).
 *
 * Sealed time is real waiting time from the customer's point of view, but it
 * is *not* time the team could have influenced — so which of the two lead-time
 * definitions is correct is a policy choice, and it belongs to the board.
 */
function envelopePolicy(board, onChanged) {
  if (!board) return null;
  const editable = can("manage_boards");

  const input = el("input", {
    className: "switch",
    type: "checkbox",
    role: "switch",
    checked: Boolean(board.count_envelope_in_metrics),
    disabled: !editable,
    "aria-label": "Count sealed-envelope time in metrics",
    onChange: async (event) => {
      const next = event.target.checked;
      input.disabled = true;
      try {
        const updated = await api.patch(`/boards/${board.id}`, { count_envelope_in_metrics: next });
        board.count_envelope_in_metrics = next;
        toast(
          next
            ? "Customer lead time now starts when a demand is sealed."
            : "Customer lead time now starts when the envelope is opened.",
          { tone: "success", title: `${board.name} · envelope policy` }
        );
        if (onChanged) onChanged(updated || board);
      } catch {
        input.checked = !next; // the toast from api.js already explained why
      } finally {
        input.disabled = !editable;
      }
    },
  });

  return el(
    "div",
    { className: "envelope-policy" },
    el("span", { className: "envelope-policy-icon" }, icon("clock")),
    el(
      "label",
      { className: "switch-row", style: { flex: "1 1 auto" } },
      el(
        "span",
        { className: "envelope-policy-text" },
        el("span", { className: "envelope-policy-title" }, "Count sealed-envelope time in metrics"),
        el(
          "span",
          { className: "envelope-policy-note" },
          `On, customer lead time for ${board.name} includes the days a demand sat sealed; off, the clock starts the moment the envelope is opened.`
        ),
        !editable
          ? el("span", { className: "control-note" }, "Only an administrator can change a board policy.")
          : null
      ),
      input
    )
  );
}

/* ==========================================================================
   INDEX
   ========================================================================== */

async function renderIndex(token) {
  paint(token, indexSkeleton());

  let projects;
  let flow;
  try {
    const query = {};
    if (indexFilters.status) query.status = indexFilters.status;
    if (indexFilters.q) query.q = indexFilters.q;

    [projects, flow] = await Promise.all([
      api.get("/projects", { query }).then(items),
      loadFlowContext(),
    ]);
  } catch (error) {
    paint(token, el("div", { className: "view-pad" }, errorState(error, () => renderIndex(++generation))));
    return;
  }

  if (stale(token)) return;

  const board = flow.boards[0] || null;
  const byProject = new Map();
  for (const card of flow.cards) {
    if (!card.project_id) continue;
    if (!byProject.has(card.project_id)) byProject.set(card.project_id, []);
    byProject.get(card.project_id).push(card);
  }

  const cards = projects.map((project) => {
    const own = byProject.get(project.id);
    const counts = own && !flow.degraded ? bucketCounts(own, flow.columns) : countsFromProject(project);
    return projectCard(project, counts);
  });

  const grid = projects.length
    ? el("div", { className: "projects-grid" }, cards)
    : emptyState({
        icon: "layers",
        title: indexFilters.q || indexFilters.status ? "No projects match this filter" : "No projects yet",
        body: indexFilters.q || indexFilters.status
          ? "Try a broader search, or clear the status filter."
          : "A project groups demand so lead time, throughput and delivery can be rolled up per initiative.",
        actions: indexFilters.q || indexFilters.status
          ? [el("button", { className: "btn btn-secondary", onClick: () => { indexFilters = { status: "", q: "" }; renderIndex(++generation); } }, "Clear filters")]
          : can("manage_boards")
            ? [el("button", { className: "btn btn-primary", onClick: () => openProjectForm() }, icon("plus"), "New project")]
            : [],
      });

  paint(
    token,
    el(
      "div",
      { className: "view-pad" },
      el(
        "div",
        { className: "view-header" },
        el(
          "div",
          { className: "stack stack-2" },
          el("div", { className: "view-title-row" }, el("h1", null, "Projects"), el("span", { className: "badge" }, `${projects.length}`)),
          el(
            "p",
            { className: "view-lede" },
            "Every demand belongs to a project. Sealed demands are captured but unread — they carry no assignee and no commitment until someone opens the envelope."
          )
        ),
        can("manage_boards")
          ? el("button", { className: "btn btn-primary", onClick: () => openProjectForm() }, icon("plus"), "New project")
          : null
      ),
      indexToolbar(),
      board ? el("div", { style: { marginBottom: "var(--space-5)" } }, envelopePolicy(board, () => {})) : null,
      grid
    )
  );

  if (projects.length && board) attachSparklines(projects, board, token);
}

function indexToolbar() {
  if (!searchDebounced) {
    searchDebounced = debounce((value) => {
      indexFilters.q = value;
      renderIndex(++generation);
    }, 300);
  }

  const search = el("input", {
    className: "input input-sm",
    type: "search",
    placeholder: "Search projects",
    "aria-label": "Search projects",
    value: indexFilters.q,
    onInput: (event) => searchDebounced(event.target.value.trim()),
  });

  const status = el(
    "select",
    {
      className: "select select-sm",
      "aria-label": "Filter by status",
      style: { width: "auto" },
      onChange: (event) => {
        indexFilters.status = event.target.value;
        renderIndex(++generation);
      },
    },
    el("option", { value: "" }, "All statuses"),
    STATUS_ORDER.map((s) => el("option", { value: s, selected: indexFilters.status === s }, humanize(s)))
  );

  return el(
    "div",
    { className: "view-toolbar" },
    el("div", { className: "input-group" }, icon("search"), search),
    status,
    indexFilters.q || indexFilters.status
      ? el(
          "button",
          {
            className: "btn btn-ghost btn-sm",
            onClick: () => { indexFilters = { status: "", q: "" }; renderIndex(++generation); },
          },
          icon("x"),
          "Clear"
        )
      : null
  );
}

function projectCard(project, counts) {
  const color = project.color || PALETTE[0].hex;

  return el(
    "a",
    {
      className: "project-card",
      href: `#/projects/${project.id}`,
      style: { "--project-color": color },
      "aria-label": `${project.key} — ${project.name}`,
    },
    el(
      "div",
      { className: "project-card-head" },
      el(
        "div",
        { className: "stack stack-1", style: { minWidth: 0 } },
        el(
          "div",
          { className: "row row-2" },
          el("span", { className: "project-key" }, project.key),
          project.archived ? el("span", { className: "badge" }, "Archived") : null
        ),
        el("span", { className: "project-name truncate" }, project.name)
      ),
      statusPill(project.status)
    ),
    project.description
      ? el("p", { className: "project-desc clamp-2" }, project.description)
      : null,
    el("div", { className: "project-meta" }, ownerRow(project), targetDate(project)),
    countsGrid(counts),
    el(
      "div",
      { className: "project-spark", dataset: { project: project.id } },
      el("div", { className: "project-spark-chart" }, skeleton({ height: 24, radius: "var(--radius-sm)" })),
      el("div", { className: "project-spark-label subtle" }, "throughput")
    )
  );
}

/**
 * Fill in the per-project throughput sparklines after first paint.
 *
 * Deliberately a second pass: the grid is useful immediately, and one metrics
 * call per project must never hold up the page it decorates.
 */
async function attachSparklines(projects, board, token) {
  const mod = await loadCharts();

  await Promise.all(projects.map(async (project) => {
    if (stale(token)) return;

    let series = null;
    try {
      series = await api.get(`/boards/${board.id}/metrics/throughput`, {
        query: { project_id: project.id },
        silent: true,
      });
    } catch {
      series = null;
    }
    if (stale(token)) return;

    const host = root.querySelector(`.project-spark[data-project="${CSS.escape(project.id)}"]`);
    if (!host) return;

    const points = (series?.series?.[0]?.points || []).map((p) => Number(p.y) || 0);
    const chartHost = host.querySelector(".project-spark-chart");
    const label = host.querySelector(".project-spark-label");

    if (!points.length) {
      chartHost.replaceChildren();
      render(label, el("span", { className: "subtle" }, "no delivery yet"));
      return;
    }

    const total = points.reduce((a, b) => a + b, 0);
    const avg = total / points.length;
    const recent = points.slice(-4);
    const recentAvg = recent.reduce((a, b) => a + b, 0) / recent.length;

    const chart = buildChart(mod, "bars", {
      data: points.map((y, i) => ({ x: i + 1, y })),
      values: points,
      series: [{ key: "throughput", label: "Delivered per week", points: points.map((y, i) => ({ x: i + 1, y })) }],
      width: 200,
      height: 30,
      compact: true,
      sparkline: true,
      axes: false,
      showAxes: false,
      grid: false,
      legend: false,
      color: "var(--accent)",
      ariaLabel: `${project.name}: ${points.length} weeks of throughput, average ${avg.toFixed(1)} per week`,
      title: `${project.name} throughput`,
    });

    chartHost.replaceChildren();
    if (chart) {
      chartHost.append(chart);
    } else {
      // No chart module: the number still has to be readable.
      chartHost.append(el("span", { className: "t-xs subtle" }, `${points.length}w history`));
    }

    render(
      label,
      el("b", null, `${formatNumber(recentAvg, { decimals: 1 })}/wk`),
      el("br"),
      el("span", null, "last 4 weeks")
    );
  }));
}

function indexSkeleton() {
  return el(
    "div",
    { className: "view-pad" },
    el(
      "div",
      { className: "view-header" },
      el("div", { className: "stack stack-2" }, skeleton({ width: 180, height: 26 }), skeleton({ width: 420, height: 12 })),
      skeleton({ width: 120, height: 32, radius: "var(--radius-md)" })
    ),
    el(
      "div",
      { className: "projects-grid" },
      Array.from({ length: 4 }, () =>
        el(
          "div",
          { className: "project-card" },
          el("div", { className: "row row-between" }, skeleton({ width: 56, height: 18 }), skeleton({ width: 64, height: 18 })),
          skeleton({ width: "70%", height: 18 }),
          skeleton({ width: "100%", height: 12 }),
          skeleton({ height: 58, radius: "var(--radius-md)" }),
          skeleton({ height: 30 })
        )
      )
    )
  );
}

/* ==========================================================================
   DETAIL
   ========================================================================== */

/** Highlighted card id, set by an envelope opening so the arrival is visible. */
let arrivingCardId = null;

async function renderDetail(projectId, token, opts = {}) {
  if (!opts.silent) paint(token, detailSkeleton());

  let project;
  let columns = new Map();
  let boards = new Map();

  try {
    project = await api.get(`/projects/${projectId}`);

    const boardIds = Array.from(
      new Set((project.cards || []).map((c) => c.board_id).filter(Boolean))
    ).slice(0, 4);

    // Fall back to the workspace's operational boards when the project has no
    // cards yet — the envelope policy toggle still needs a board to talk about.
    const ids = boardIds.length ? boardIds : operationalBoards().map((b) => b.id).slice(0, 1);

    const loaded = await Promise.all(
      ids.map((id) => api.get(`/boards/${id}`, { silent: true }).catch(() => null))
    );
    for (const board of loaded) {
      if (!board) continue;
      boards.set(board.id, board);
      for (const column of board.columns || []) columns.set(column.id, column);
    }
  } catch (error) {
    paint(
      token,
      el(
        "div",
        { className: "view-pad" },
        errorState(error, () => renderDetail(projectId, ++generation), { title: "Could not load this project" })
      )
    );
    return;
  }

  if (stale(token)) return;

  const cards = (project.cards || []).filter((c) => !c.archived);
  const counts = columns.size ? bucketCounts(cards, columns) : countsFromProject(project);
  const board = boards.get(cards[0]?.board_id) || Array.from(boards.values())[0] || null;

  const grouped = { sealed: [], upstream: [], downstream: [], delivered: [] };
  for (const card of cards) grouped[flowBucket(card, columns)].push(card);

  grouped.sealed.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  for (const key of ["upstream", "downstream"]) {
    grouped[key].sort((a, b) => (columnPosition(a, columns) - columnPosition(b, columns)));
  }
  grouped.delivered.sort((a, b) => String(b.delivered_at || "").localeCompare(String(a.delivered_at || "")));

  const reload = () => renderDetail(projectId, ++generation, { silent: true });

  paint(
    token,
    el(
      "div",
      { className: "view-pad" },
      el(
        "a",
        { className: "row row-1 t-sm muted", href: "#/projects", style: { marginBottom: "var(--space-3)", width: "fit-content" } },
        icon("chevron-left"),
        "All projects"
      ),
      detailHero(project, counts, reload),
      board
        ? el("div", { style: { marginBottom: "var(--space-5)" } }, envelopePolicy(board, () => {}))
        : null,
      sealedSection(project, grouped.sealed, board, columns, reload),
      flowSection("In upstream", "Discovery: options being shaped before the commitment point.", grouped.upstream, columns, "layers"),
      flowSection("In downstream", "Committed work in delivery.", grouped.downstream, columns, "board"),
      flowSection("Delivered", "Finished and out of the system.", grouped.delivered, columns, "check")
    )
  );

  if (arrivingCardId) {
    const node = root.querySelector(`.demand-row[data-card="${CSS.escape(arrivingCardId)}"]`);
    if (node) {
      node.classList.add("is-arriving");
      node.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    arrivingCardId = null;
  }
}

function columnPosition(card, columns) {
  return columns.get(card.column_id)?.position ?? 0;
}

function detailHero(project, counts, reload) {
  const color = project.color || PALETTE[0].hex;

  return el(
    "div",
    { className: "project-hero", style: { "--project-color": color } },
    el(
      "div",
      { className: "project-hero-main" },
      el(
        "div",
        { className: "row row-2 row-wrap" },
        el("span", { className: "project-key" }, project.key),
        statusPill(project.status),
        project.archived ? el("span", { className: "badge" }, "Archived") : null
      ),
      el("h1", null, project.name),
      project.description ? el("p", { className: "view-lede" }, project.description) : null,
      el(
        "div",
        { className: "project-meta" },
        ownerRow(project),
        project.started_at
          ? el("span", { className: "row row-1" }, icon("play"), `Started ${formatDate(project.started_at)}`)
          : null,
        targetDate(project),
        el("span", { className: "row row-1" }, icon("calendar"), `Created ${relativeTime(project.created_at)}`)
      )
    ),
    el(
      "div",
      { className: "project-hero-side" },
      el(
        "div",
        { className: "row row-2" },
        can("manage_boards")
          ? el("button", { className: "btn btn-secondary btn-sm", onClick: () => openProjectForm(project, reload) }, icon("edit"), "Edit")
          : null,
        can("manage_cards")
          ? el("button", { className: "btn btn-primary btn-sm", onClick: () => openDemandForm(project, reload) }, icon("plus"), "New demand")
          : null
      ),
      el("div", { style: { minWidth: "300px" } }, countsGrid(counts))
    )
  );
}

/* ---- sealed envelopes ----------------------------------------------------- */

function sealedSection(project, sealed, board, columns, reload) {
  const body = sealed.length
    ? el("div", { className: "envelope-grid" }, sealed.map((card) => envelopeCard(card, columns, reload)))
    : emptyState({
        icon: "envelope-open",
        title: "No sealed demands",
        body: "Every demand in this project has been opened. New demands arrive sealed so they can be captured without being triaged on the spot.",
        actions: can("manage_cards")
          ? [el("button", { className: "btn btn-secondary", onClick: () => openDemandForm(project, reload) }, icon("plus"), "New demand")]
          : [],
      });

  return el(
    "section",
    { className: "stack stack-3", style: { marginBottom: "var(--space-7)" } },
    el(
      "div",
      { className: "section-head" },
      el(
        "div",
        { className: "row row-2" },
        icon("envelope"),
        el("h2", { className: "t-lg t-semibold" }, "Sealed envelopes"),
        el("span", { className: "badge" }, String(sealed.length))
      ),
      el(
        "span",
        { className: "control-note" },
        "Captured, not yet read. Contents stay withheld and no one is assigned until the envelope is opened."
      )
    ),
    body
  );
}

/**
 * One closed envelope.
 *
 * Note what is *absent*: no assignee, no class-of-service stripe, no size, no
 * description. That absence is the feature.
 */
function envelopeCard(card, columns, reload) {
  const node = el(
    "article",
    {
      className: "envelope",
      dataset: { card: card.id },
      "aria-label": `Sealed demand ${card.key}: ${card.title}`,
    },
    el("div", { className: "envelope-flap", "aria-hidden": "true" }),
    el("div", { className: "envelope-seal", "aria-hidden": "true" }, "封"),
    el(
      "div",
      { className: "envelope-head" },
      el("span", null, card.key),
      el("span", { className: "envelope-age" }, `sealed ${relativeTime(card.created_at)}`)
    ),
    el("h3", { className: "envelope-title" }, card.title),
    el(
      "div",
      { className: "envelope-withheld", "aria-hidden": "true" },
      el("span", { className: "envelope-withheld-label" }, icon("lock"), "Contents withheld")
    ),
    el(
      "div",
      { className: "envelope-foot" },
      el("span", { className: "envelope-age" }, card.requester ? `from ${card.requester}` : "no requester recorded"),
      can("manage_cards")
        ? el(
            "button",
            {
              className: "btn btn-sm btn-wax",
              onClick: (event) => openEnvelope(card, node, event.currentTarget, columns, reload),
            },
            icon("envelope-open"),
            "Open envelope"
          )
        : el("span", { className: "t-xs subtle" }, "Editor access required")
    )
  );

  return node;
}

/** Resolve after the unsealing animation, or immediately under reduced motion. */
function unsealingDelay() {
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return Promise.resolve();
  // Matches the longest keyframe in views.css (`nagare-envelope-settle`).
  return new Promise((resolve) => window.setTimeout(resolve, 900));
}

/**
 * Break the seal: animate, call the API, then show the demand arriving
 * upstream. The animation and the request run together so the ceremony never
 * makes the product feel slow.
 */
async function openEnvelope(card, node, button, columns, reload) {
  button.disabled = true;
  node.classList.add("is-opening");
  announce(`Opening envelope ${card.key}`);

  let result;
  try {
    [result] = await Promise.all([
      api.post(`/cards/${card.id}/open-envelope`),
      unsealingDelay(),
    ]);
  } catch {
    node.classList.remove("is-opening");
    button.disabled = false;
    return;
  }

  const opened = result?.card || result || {};
  const column = columns.get(opened.column_id);
  arrivingCardId = card.id;

  toast(`${card.key} is now in ${column ? column.name : "upstream"}.`, {
    tone: "success",
    title: "Envelope opened",
  });
  announce(`${card.key} opened and moved to ${column ? column.name : "upstream"}`);

  await reload();
}

/* ---- the rest of the flow -------------------------------------------------- */

function flowSection(title, note, cards, columns, iconName) {
  return el(
    "section",
    { className: "stack stack-3", style: { marginBottom: "var(--space-7)" } },
    el(
      "div",
      { className: "section-head" },
      el(
        "div",
        { className: "row row-2" },
        icon(iconName),
        el("h2", { className: "t-lg t-semibold" }, title),
        el("span", { className: "badge" }, String(cards.length))
      ),
      el("span", { className: "control-note" }, note)
    ),
    cards.length
      ? el("div", { className: "demand-list" }, cards.map((card) => demandRow(card, columns)))
      : el(
          "div",
          { className: "note-strip" },
          icon("info"),
          el("span", null, `Nothing here right now — no demand from this project is ${title.toLowerCase()}.`)
        )
  );
}

function demandRow(card, columns) {
  const column = columns.get(card.column_id);
  const age = card.age_days;

  return el(
    "article",
    {
      className: "demand-row",
      dataset: { sc: card.service_class || "standard", card: card.id },
    },
    el("div", { className: "demand-stripe", "aria-hidden": "true" }),
    el(
      "div",
      { className: "demand-main" },
      el("div", { className: "demand-title truncate" }, card.title),
      el(
        "div",
        { className: "demand-meta" },
        el("span", { className: "demand-key" }, card.key),
        column ? el("span", null, column.name) : null,
        el("span", null, humanize(card.service_class)),
        card.demand_type && card.demand_type !== "value"
          ? el("span", { className: "chip", dataset: { tone: card.demand_type === "failure" ? "danger" : "neutral" } }, humanize(card.demand_type))
          : null,
        card.is_blocked ? el("span", { className: "chip", dataset: { tone: "danger" } }, icon("block"), "Blocked") : null,
        card.delivered_at
          ? el("span", null, `delivered ${relativeTime(card.delivered_at)}`)
          : age !== null && age !== undefined
            ? el("span", null, `${formatNumber(age, { decimals: 1 })}d in flow`)
            : null
      )
    ),
    el(
      "div",
      { className: "demand-side" },
      card.due_date ? el("span", { className: "t-xs subtle" }, `due ${formatDate(card.due_date)}`) : null,
      card.assignee_name
        ? avatar({ id: card.assignee_id, name: card.assignee_name }, { size: "sm" })
        : el("span", { className: "t-xs subtle" }, "unassigned")
    )
  );
}

function detailSkeleton() {
  return el(
    "div",
    { className: "view-pad" },
    el(
      "div",
      { className: "project-hero" },
      el(
        "div",
        { className: "project-hero-main" },
        skeleton({ width: 70, height: 18 }),
        skeleton({ width: 320, height: 26 }),
        skeleton({ width: "80%", height: 12 })
      ),
      el("div", { style: { width: "300px" } }, skeleton({ height: 58, radius: "var(--radius-md)" }))
    ),
    el(
      "div",
      { className: "envelope-grid" },
      Array.from({ length: 3 }, () => skeleton({ height: 150, radius: "var(--radius-md)" }))
    )
  );
}

/* ==========================================================================
   FORMS
   ========================================================================== */

/** Build a labelled field with an inline error slot. */
function field(label, control, opts = {}) {
  const error = el("div", { className: "field-error", hidden: true });
  control.dataset.field = opts.name || "";
  return {
    node: el(
      "label",
      { className: "field" },
      el("span", { className: "label" }, label, opts.required ? el("span", { className: "subtle" }, " *") : null),
      control,
      opts.hint ? el("span", { className: "hint" }, opts.hint) : null,
      error
    ),
    control,
    setError(message) {
      error.textContent = message || "";
      error.hidden = !message;
      control.setAttribute("aria-invalid", message ? "true" : "false");
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
    options.map(([v, labelText]) => el("option", { value: v, selected: v === value }, labelText))
  );
}

/**
 * Create or edit a project.
 * @param {object|null} existing
 * @param {Function} [onSaved]
 */
async function openProjectForm(existing = null, onSaved = null) {
  const editing = Boolean(existing);

  // Owner assignment needs the user list, which only admins may read. A
  // non-admin still gets the form — minus a control they could not use.
  const users = await api.get("/users", { silent: true }).then(items).catch(() => []);

  const name = field("Name", inputEl({ value: existing?.name || "", maxLength: 120, "data-autofocus": "" }), { required: true, name: "name" });
  const key = field(
    "Key",
    inputEl({
      value: existing?.key || "",
      maxLength: 8,
      disabled: editing, // the key is baked into every card reference
      placeholder: "NGR",
      style: { textTransform: "uppercase", fontFamily: "var(--font-mono)" },
    }),
    { required: true, name: "key", hint: editing ? "Card references depend on the key, so it cannot change." : "2–8 letters or digits, used as the card prefix (NGR-104)." }
  );

  // Derive the key from the name until the user takes it over.
  let keyTouched = editing;
  key.control.addEventListener("input", () => { keyTouched = true; });
  name.control.addEventListener("input", () => {
    if (keyTouched) return;
    const derived = String(name.control.value).replace(/[^a-zA-Z0-9 ]/g, "").trim().split(/\s+/)
      .map((word) => word[0] || "").join("").toUpperCase().slice(0, 4);
    key.control.value = derived.length >= 2 ? derived : String(name.control.value).replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 3);
  });

  let color = existing?.color || PALETTE[0].hex;
  const swatches = el(
    "div",
    { className: "row row-2 row-wrap", role: "radiogroup", "aria-label": "Project colour" },
    PALETTE.map((entry) => {
      const button = el("button", {
        type: "button",
        role: "radio",
        "aria-checked": String(entry.hex === color),
        "aria-label": entry.name,
        title: entry.name,
        style: {
          width: "26px",
          height: "26px",
          borderRadius: "var(--radius-md)",
          background: entry.hex,
          border: entry.hex === color ? "2px solid var(--fg)" : "1px solid var(--border)",
        },
        onClick: (event) => {
          color = entry.hex;
          for (const sibling of event.currentTarget.parentElement.children) {
            const on = sibling === event.currentTarget;
            sibling.setAttribute("aria-checked", String(on));
            sibling.style.border = on ? "2px solid var(--fg)" : "1px solid var(--border)";
          }
        },
      });
      return button;
    })
  );

  const status = field("Status", selectEl(STATUS_ORDER.map((s) => [s, humanize(s)]), existing?.status || "active"), { name: "status" });
  const owner = users.length
    ? field(
        "Owner",
        selectEl([["", "Unassigned"], ...users.map((u) => [u.id, `${u.name} (${humanize(u.role)})`])], existing?.owner_id || ""),
        { name: "owner_id" }
      )
    : null;
  const started = field("Start date", inputEl({ type: "date", value: (existing?.started_at || "").slice(0, 10) }), { name: "started_at" });
  const target = field("Target date", inputEl({ type: "date", value: (existing?.target_date || "").slice(0, 10) }), { name: "target_date" });
  const description = field(
    "Description",
    el("textarea", { className: "textarea", maxLength: 2000 }, existing?.description || ""),
    { name: "description" }
  );

  const submit = el("button", { className: "btn btn-primary", type: "submit" }, editing ? "Save project" : "Create project");

  const form = el(
    "form",
    {
      className: "stack stack-4",
      noValidate: true,
      onSubmit: async (event) => {
        event.preventDefault();
        for (const f of [name, key, status, started, target]) f.setError("");

        const values = {
          name: name.control.value.trim(),
          key: key.control.value.trim().toUpperCase(),
          color,
          status: status.control.value,
          description: description.control.value.trim() || null,
          started_at: started.control.value || null,
          target_date: target.control.value || null,
        };
        if (owner) values.owner_id = owner.control.value || null;

        let ok = true;
        if (!values.name) { name.setError("A project needs a name."); ok = false; }
        if (!editing && !/^[A-Z][A-Z0-9]{1,7}$/.test(values.key)) {
          key.setError("2–8 characters, starting with a letter, e.g. NGR.");
          ok = false;
        }
        if (values.started_at && values.target_date && values.target_date < values.started_at) {
          target.setError("The target date cannot be before the start date.");
          ok = false;
        }
        if (!ok) return;

        submit.classList.add("is-loading");
        submit.disabled = true;
        try {
          const saved = editing
            ? await api.patch(`/projects/${existing.id}`, { ...values, key: undefined })
            : await api.post("/projects", values);
          handle.close();
          toast(editing ? "Project saved." : `${saved?.key || values.key} created.`, { tone: "success" });
          api.invalidate("/projects");
          if (onSaved) await onSaved(saved);
          else if (!editing && saved?.id) navigate(`/projects/${saved.id}`);
          else renderIndex(++generation);
        } catch (error) {
          if (error?.code === "duplicate_key" || /key/i.test(error?.message || "")) key.setError(error.message);
          submit.classList.remove("is-loading");
          submit.disabled = false;
        }
      },
    },
    el("div", { className: "grid grid-2" }, name.node, key.node),
    el("div", { className: "field" }, el("span", { className: "label" }, "Colour"), swatches),
    el("div", { className: "grid grid-2" }, status.node, owner ? owner.node : el("div")),
    el("div", { className: "grid grid-2" }, started.node, target.node),
    description.node
  );

  const handle = modal({
    title: editing ? `Edit ${existing.key}` : "New project",
    size: "lg",
    body: form,
    footer: [
      el("button", { className: "btn btn-secondary", onClick: () => handle.close() }, "Cancel"),
      submit,
    ],
  });

  // The footer submit lives outside <form>, so wire it explicitly.
  submit.addEventListener("click", (event) => {
    event.preventDefault();
    form.requestSubmit();
  });
}

/**
 * Capture a new demand. Sealed by default — that is the whole point of Req. 8.
 */
function openDemandForm(project, onSaved) {
  const boards = operationalBoards();

  const title = field("Title", inputEl({ maxLength: 200, "data-autofocus": "" }), {
    required: true,
    name: "title",
    hint: "Visible on the sealed envelope. Everything else stays withheld until it is opened.",
  });
  const requester = field("Requester", inputEl({ maxLength: 120, placeholder: "Who asked for this?" }), { name: "requester" });
  const boardField = boards.length > 1
    ? field("Board", selectEl(boards.map((b) => [b.id, b.name]), boards[0].id), { name: "board_id" })
    : null;

  const serviceClass = field("Class of service", selectEl(SERVICE_CLASSES.map((s) => [s, humanize(s)]), "standard"), { name: "service_class" });
  const valueArea = field("Value area", selectEl(VALUE_AREAS.map((s) => [s, humanize(s)]), "feature"), { name: "value_area" });
  const demandType = field("Demand type", selectEl(DEMAND_TYPES.map((s) => [s, humanize(s)]), "value"), {
    name: "demand_type",
    hint: "Failure demand is work caused by something not done right the first time.",
  });
  const size = field("Size", selectEl([["", "Not sized"], ...SIZES.map((s) => [s, s.toUpperCase()])], ""), { name: "size" });
  const dueDate = field("Due date", inputEl({ type: "date" }), {
    name: "due_date",
    hint: "Only meaningful for the fixed-date class of service.",
  });
  const tags = field("Tags", inputEl({ placeholder: "billing, mobile" }), { name: "tags", hint: "Comma separated." });
  const description = field("Description", el("textarea", { className: "textarea", maxLength: 4000 }), { name: "description" });

  const sealedInput = el("input", { className: "checkbox", type: "checkbox", checked: true });

  const submit = el("button", { className: "btn btn-primary", type: "submit" }, "Capture demand");

  const form = el(
    "form",
    {
      className: "stack stack-4",
      noValidate: true,
      onSubmit: async (event) => {
        event.preventDefault();
        title.setError("");
        dueDate.setError("");

        const value = title.control.value.trim();
        if (!value) { title.setError("A demand needs a title."); return; }
        if (serviceClass.control.value === "fixed_date" && !dueDate.control.value) {
          dueDate.setError("A fixed-date demand needs a due date.");
          return;
        }

        const payload = {
          title: value,
          description: description.control.value.trim() || null,
          requester: requester.control.value.trim() || null,
          service_class: serviceClass.control.value,
          value_area: valueArea.control.value,
          demand_type: demandType.control.value,
          size: size.control.value || null,
          due_date: dueDate.control.value || null,
          tags: tags.control.value.split(",").map((t) => t.trim()).filter(Boolean),
          sealed: sealedInput.checked,
        };
        if (boardField) payload.board_id = boardField.control.value;
        else if (boards.length === 1) payload.board_id = boards[0].id;

        submit.classList.add("is-loading");
        submit.disabled = true;
        try {
          const created = await api.post(`/projects/${project.id}/demands`, payload);
          handle.close();
          toast(
            sealedInput.checked
              ? `${created?.key || "Demand"} sealed into an envelope.`
              : `${created?.key || "Demand"} added to upstream.`,
            { tone: "success" }
          );
          if (onSaved) await onSaved(created);
        } catch {
          submit.classList.remove("is-loading");
          submit.disabled = false;
        }
      },
    },
    title.node,
    el("div", { className: "grid grid-2" }, requester.node, boardField ? boardField.node : el("div")),
    el("div", { className: "grid grid-2" }, serviceClass.node, dueDate.node),
    el("div", { className: "grid grid-2" }, valueArea.node, demandType.node),
    el("div", { className: "grid grid-2" }, size.node, tags.node),
    description.node,
    el(
      "div",
      { className: "envelope-policy" },
      el("span", { className: "envelope-policy-icon" }, icon("envelope")),
      el(
        "label",
        { className: "switch-row", style: { flex: "1 1 auto" } },
        el(
          "span",
          { className: "envelope-policy-text" },
          el("span", { className: "envelope-policy-title" }, "Seal this demand in an envelope"),
          el(
            "span",
            { className: "envelope-policy-note" },
            "Sealed demands sit in the envelope column with their contents withheld and nobody assigned, until someone deliberately opens them."
          )
        ),
        sealedInput
      )
    )
  );

  const handle = modal({
    title: `New demand · ${project.key}`,
    size: "lg",
    body: form,
    footer: [
      el("button", { className: "btn btn-secondary", onClick: () => handle.close() }, "Cancel"),
      submit,
    ],
  });

  submit.addEventListener("click", (event) => {
    event.preventDefault();
    form.requestSubmit();
  });
}

/* Re-exported so a future view can reuse the confirm helper without importing
   ui.js twice; kept explicit rather than implicit. */
export { confirmDialog };
