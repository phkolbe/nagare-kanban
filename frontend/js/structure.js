/**
 * Nagare — board structure editor (SPEC-v2 §R2.1).
 *
 * A full-height right drawer that turns the backend's column/group API into
 * something a Kanban practitioner can actually operate: every column of the
 * board in one unified flow order, grouped under its column group, with the
 * commitment point drawn as a divider between the upstream and downstream runs
 * exactly as the board draws it.
 *
 * Three ideas drive the design:
 *
 * **The kind selector is a teaching surface, not a dropdown.** `queue` accrues
 * wait time and `activity` accrues touch time; that single choice is what makes
 * flow efficiency measurable at all. A user who guesses here produces a board
 * whose metrics are quietly wrong, so the consequence is stated inline, every
 * time, next to the control that causes it.
 *
 * **The commitment point moves before you save.** The chip strip at the top is
 * re-derived speculatively from the pending edit — mirroring the backend's
 * `_placement_order` / `_column_sort_key` — so changing a segment shows the
 * boundary jump *while the request is still in flight*, then reconciles against
 * whatever the server actually did.
 *
 * **Invariants are surfaced, never re-implemented.** `normalize_structure()`
 * owns the rules (one commitment point, envelope first, contiguous groups). This
 * module disables the moves it knows are illegal, and translates the 409s it
 * cannot pre-empt into a sentence with a remedy in it — most importantly
 * `column_not_empty`, which offers to move the cards rather than reporting a
 * failure and stopping.
 *
 * Dependencies point downwards only: ui.js, api.js, store.js, and the shared
 * column-kind vocabulary from card.js. It never imports board.js — board.js
 * imports `openStructureEditor` from here, and that one function is the whole
 * contract between them.
 */

import api, { ApiError } from "./api.js";
import store, { can, hasFlag } from "./store.js";
import {
  el, icon, render, drawer, modal, confirmDialog, toast,
  skeleton, emptyState, errorState, announce, humanize,
} from "./ui.js";
import { columnKindMeta } from "./card.js";

/* ==========================================================================
   Vocabulary — the words and the consequences behind them
   ========================================================================== */

/**
 * The four column kinds, in the order a person meets them walking the board.
 * `consequence` is shown inline under the selector: this is the sentence that
 * stops someone picking `activity` for a buffer column and wondering six weeks
 * later why their flow efficiency reads 90%.
 */
const KINDS = Object.freeze([
  {
    id: "queue",
    label: "Queue — wait time",
    consequence:
      "Time spent here counts as WAIT time. The item sits still; nobody is working on it. "
      + "Buffers, backlogs and \"…Done\" hand-off columns are all queues.",
  },
  {
    id: "activity",
    label: "Activity — touch time",
    consequence:
      "Time spent here counts as TOUCH time. Someone is actively working the item. "
      + "Analysis, Build and Verify are activities.",
  },
  {
    id: "envelope",
    label: "Envelope — sealed intake",
    consequence:
      "Demand captured but deliberately unexamined. Only ever the first upstream column, "
      + "one per board, and its time counts towards lead time only when the board's "
      + "envelope toggle is on.",
  },
  {
    id: "done",
    label: "Done — terminal",
    consequence:
      "Terminal state, downstream only. The last done column is the delivery point: "
      + "entering it stamps delivered_at and stops the lead-time clock.",
  },
]);

const SEGMENTS = Object.freeze([
  {
    id: "upstream",
    label: "Upstream — before commitment",
    consequence:
      "Options, not promises. Work here can still be dropped, and it is not counted as "
      + "work in progress by Little's Law.",
  },
  {
    id: "downstream",
    label: "Downstream — after commitment",
    consequence:
      "The FIRST downstream column is the commitment point. Entering it stamps "
      + "committed_at and starts cycle time — the team now owes a delivery.",
  },
]);

/** Mid-luminance hues that stay legible as a swatch in both themes. */
const SWATCHES = Object.freeze([
  ["#0F7B86", "Teal"],
  ["#2E6DA4", "Blue"],
  ["#5C5FA6", "Indigo"],
  ["#7B549B", "Violet"],
  ["#A24A63", "Rose"],
  ["#96702A", "Amber"],
  ["#2F7D52", "Green"],
  ["#6B7280", "Slate"],
]);

/**
 * Readable phrasing for the structural 409s.
 *
 * Every call this module makes is `silent: true`, so nothing here reaches the
 * user as a raw error code. A conflict on a structure edit is nearly always a
 * rule the user has not met yet, and a rule is only useful when it says what to
 * do instead.
 */
const ERROR_COPY = {
  envelope_column_exists:
    "This board already has an envelope column, and a board only gets one. Change the "
    + "existing envelope column instead, or make this one a queue.",
  envelope_must_be_first:
    "An envelope column can only sit at the very front of the flow. Move it to the top, "
    + "or change its kind.",
  envelope_must_be_upstream:
    "An envelope column belongs to the upstream segment — sealed demand has not been "
    + "committed to yet.",
  done_must_be_downstream:
    "A terminal \"done\" column belongs to the downstream segment. Move it downstream first.",
  segment_order_violation:
    "Every upstream column has to come before every downstream column. Change the "
    + "column's segment rather than dragging it across the commitment point.",
  invalid_wip_range: "The WIP minimum cannot be larger than the maximum.",
  commitment_point_is_derived:
    "The commitment point is always the first downstream column — it follows the flow, "
    + "it is not a flag you set.",
  delivery_point_is_derived:
    "The delivery point is always the last done column — it follows the flow, it is not "
    + "a flag you set.",
  insufficient_role:
    "Your role cannot change this board's structure. Ask a workspace administrator.",
  feature_disabled:
    "Column groups are not available on this workspace's plan.",
};

const TEACH_KEY = "nagare.structure.teach";

/** Fields a person types into character by character, and can be caught mid-word. */
const TYPED_INPUTS = new Set(["text", "number", "search", "url", "email", "tel"]);

/** The sentence shown under a kind selector. */
function kindCopy(id) {
  return KINDS.find((k) => k.id === id)?.consequence || "";
}

/** The sentence shown under a segment selector. */
function segmentCopy(id) {
  return SEGMENTS.find((s) => s.id === id)?.consequence || "";
}

/* ==========================================================================
   Module state — reset on every open
   ========================================================================== */

let handle = null;          // the drawer handle from ui.js
let bodyEl = null;          // handle.body — the scrolling region we repaint
let previewHost = null;     // the chip strip container, repainted on its own
let currentBoardId = null;
let onChangedCb = null;

let board = null;
let columns = [];           // every column of the board, server truth
let groups = [];
let wipData = null;         // {columns:{id:cell}, groups:{id:cell}, flow:cell}

let loading = true;
let loadError = null;
let busy = false;           // a mutation is in flight; the list is inert
let canEdit = false;

let expandedColumns = new Set();
let expandedGroups = new Set();
let previewOverride = null; // speculative order, cleared on every reconcile
let dragState = null;
let pendingFocusColumnId = null;

/**
 * Scheduled WIP commits, keyed by the scope that owns them (`col:<id>:wip`,
 * `group:<id>:wip`). See {@link wipLimitControl}: min and max are one policy and
 * one write, owned by the row rather than by either box.
 */
const pendingWip = new Map();

/* ==========================================================================
   Public surface — the entire contract with board.js
   ========================================================================== */

/**
 * Open the board structure editor.
 *
 * @param {string} boardId
 * @param {object} [opts]
 * @param {Function} [opts.onChanged] Invoked after every successful mutation so
 *   the board behind the drawer can repaint against the new structure.
 * @param {string} [opts.columnId] Optional column to expand and scroll to, for
 *   the column header's "Edit column…" entry point.
 * @returns {{close: Function}} A handle, so a caller can close it programmatically.
 */
export function openStructureEditor(boardId, { onChanged, columnId, focusColumnId } = {}) {
  if (!boardId) throw new Error("openStructureEditor requires a board id");

  // Re-opening replaces rather than stacks: two structure drawers over one
  // board would be two editors racing the same rows.
  if (handle) handle.close();

  currentBoardId = boardId;
  onChangedCb = typeof onChanged === "function" ? onChanged : null;
  pendingFocusColumnId = columnId || focusColumnId || null;

  board = null;
  columns = [];
  groups = [];
  wipData = null;
  loading = true;
  loadError = null;
  busy = false;
  canEdit = false;
  expandedColumns = new Set();
  expandedGroups = new Set();
  previewOverride = null;
  dragState = null;
  mutationQueue = Promise.resolve();
  cancelAllWipCommits();

  if (pendingFocusColumnId) expandedColumns.add(pendingFocusColumnId);

  handle = drawer({
    title: "Board structure",
    subtitle: "Columns, groups, WIP limits and the commitment point",
    body: buildLoadingBody(),
    footer: [
      el(
        "button",
        {
          type: "button",
          className: "btn btn-secondary",
          onClick: () => load(),
          "data-focus-key": "reload",
        },
        icon("refresh"),
        "Reload"
      ),
      el(
        "button",
        { type: "button", className: "btn btn-primary", onClick: () => handle && handle.close() },
        "Done"
      ),
    ],
    onClose: () => {
      // Closing the panel is not a reason to throw away the last thing typed
      // into it: Escape can land inside the commit window, and the write is
      // already the user's decision. Flushed here, while `bodyEl` still holds
      // the values, and completed by `runMutation` after the drawer is gone.
      flushAllWipCommits();
      handle = null;
      bodyEl = null;
      previewHost = null;
      dragState = null;
    },
  });

  handle.el.classList.add("structure-drawer");
  // A fallback focus target: a repaint can destroy whatever element had focus,
  // and focus stranded on <body> would escape the drawer's Escape/Tab trap.
  handle.el.setAttribute("tabindex", "-1");
  bodyEl = handle.body;
  bodyEl.classList.add("structure-body");
  // Delegated, so it survives every repaint: any field the user has typed into
  // is marked, and a repaint then refuses to overwrite it (see `paint`).
  // Only typed fields — a swatch or a checkbox carries no half-finished value
  // that a rebuild could truncate.
  bodyEl.addEventListener("input", (event) => {
    const node = event.target;
    if (!node || !node.dataset || !node.dataset.focusKey) return;
    if (node.tagName === "TEXTAREA" || (node.tagName === "INPUT" && TYPED_INPUTS.has(node.type))) {
      node.dataset.dirty = "true";
    }
  });

  load();
  return { close: () => handle && handle.close() };
}

/* ==========================================================================
   Data lifecycle
   ========================================================================== */

/** Fetch structure + WIP, showing the loading state. */
async function load() {
  if (!handle) return;
  loading = true;
  loadError = null;
  previewOverride = null;
  paint();
  await fetchAll();
  loading = false;
  paint();
  focusPendingColumn();
}

/** Re-fetch quietly, keeping the list on screen. Used to reconcile after writes. */
async function reload() {
  if (!handle) return;
  await fetchAll();
  previewOverride = null;
  paint();
}

async function fetchAll() {
  try {
    const [boardRow, wip] = await Promise.all([
      api.get(`/boards/${currentBoardId}`, { silent: true }),
      api.get(`/boards/${currentBoardId}/wip`, { silent: true }),
    ]);
    board = boardRow;
    columns = Array.isArray(boardRow.columns) ? boardRow.columns.slice() : [];
    groups = Array.isArray(boardRow.groups) ? boardRow.groups.slice() : [];
    wipData = wip;
    loadError = null;
    canEdit = computeCanEdit();
  } catch (error) {
    loadError = error;
  }
}

/**
 * SPEC §4 plus the backend's `require_structure_write`: master and administrator
 * shape any board, and `simulator` shapes its own simulator boards.
 */
function computeCanEdit() {
  if (can("manage_boards")) return true;
  const role = store.get().user?.role;
  return role === "simulator" && board?.kind === "simulator";
}

/** Serialises writes. Never rejects, so one failure cannot stall the chain. */
let mutationQueue = Promise.resolve();

/**
 * Queue one mutation: optional speculative preview, the call, then a full
 * reconcile against the server and a repaint of the board behind.
 *
 * Writes are **queued, not dropped**. Editing a column's WIP minimum and then
 * its maximum fires two commits within a few hundred milliseconds; an earlier
 * version refused the second while the first was in flight, which lost the edit
 * silently — the worst possible outcome for a form with no Save button.
 *
 * A full re-fetch rather than merging one response is deliberate. Almost every
 * structural write has consequences beyond the row that caused it — adding a
 * downstream column can hand the commitment point to a different column, a
 * segment change re-dense-packs every position — and a partial merge would show
 * a board that disagrees with the database.
 *
 * @param {() => Promise<any>} run
 * @param {{speculate?: Function, announcement?: string, onError?: Function}} [opts]
 * @returns {Promise<boolean>} whether it succeeded
 */
function mutate(run, opts = {}) {
  if (!canEdit) return Promise.resolve(false);
  const task = mutationQueue.then(() => runMutation(run, opts));
  mutationQueue = task.then(() => undefined, () => undefined);
  return task;
}

async function runMutation(run, opts) {
  // The drawer closed while this write was queued. The user still asked for it,
  // so it is sent — there is simply no panel left to reconcile or repaint.
  if (!handle) {
    try {
      await run();
      notifyChanged();
      return true;
    } catch (error) {
      reportStructureError(error);
      return false;
    }
  }

  busy = true;
  setBusy(true);

  if (opts.speculate) {
    try {
      previewOverride = opts.speculate();
      renderPreview();
    } catch {
      previewOverride = null;
    }
  }

  try {
    await run();
    await reload();
    if (opts.announcement) announce(opts.announcement);
    notifyChanged();
    return true;
  } catch (error) {
    if (opts.onError && opts.onError(error) === true) {
      await reload();
      return false;
    }
    reportStructureError(error);
    await reload();
    return false;
  } finally {
    busy = false;
    setBusy(false);
  }
}

function notifyChanged() {
  if (!onChangedCb) return;
  try {
    const result = onChangedCb();
    if (result && typeof result.catch === "function") {
      result.catch((error) => console.error("[structure] onChanged failed", error));
    }
  } catch (error) {
    console.error("[structure] onChanged failed", error);
  }
}

function setBusy(value) {
  if (!bodyEl) return;
  if (value) bodyEl.setAttribute("data-busy", "true");
  else bodyEl.removeAttribute("data-busy");
}

/** Turn any failure into a sentence a board designer can act on. */
function reportStructureError(error) {
  if (!(error instanceof ApiError)) {
    console.error("[structure]", error);
    toast("Something went wrong changing this board's structure.", { tone: "danger" });
    return;
  }
  if (error.code === "aborted") return;

  let message = ERROR_COPY[error.code] || error.message;
  if (error.code === "group_not_contiguous") message = contiguityMessage(error);

  toast(message, { tone: "danger", title: "Structure unchanged" });
}

/**
 * Name the columns that break a group's run, rather than dumping positions.
 *
 * The server has already done this work, and it did it against the order the
 * board would have had — which is the only order in which the group is actually
 * broken. Recomputing the gap from this module's snapshot cannot work for an
 * *indirect* break (re-segmenting a column, reordering, deleting): the write
 * failed, so the snapshot still describes a board where that group is perfectly
 * contiguous, and the search finds nothing to name. `details.separated_by` is
 * therefore the source of truth here, `error.message` the fallback, and the
 * rule-without-a-subject sentence the last resort.
 */
function contiguityMessage(error) {
  const details = error.details || {};
  const intruders = (details.separated_by || [])
    .map((column) => column && column.name)
    .filter(Boolean);
  if (intruders.length) return gapMessage(intruders, details.group_name);

  // Older payloads carry only the member ids: name the gap locally when this
  // module's own snapshot can still see one (the span-picker's direct path).
  const ids = details.column_ids || [];
  const ordered = orderColumns(columns);
  const indexes = ids
    .map((id) => ordered.findIndex((c) => c.id === id))
    .filter((i) => i >= 0);
  if (indexes.length >= 2) {
    const lo = Math.min(...indexes);
    const hi = Math.max(...indexes);
    const missing = [];
    for (let i = lo; i <= hi; i += 1) {
      if (!indexes.includes(i)) missing.push(ordered[i]);
    }
    if (missing.length) return gapMessage(missing, details.group_name);
  }

  return error.message
    || "A column group has to cover columns that sit next to each other in the flow.";
}

/**
 * One phrasing of the contiguity rule, used everywhere it can be broken.
 *
 * @param {Array<object|string>} gap Columns (or names) sitting inside the run.
 * @param {string} [groupName] Named when it is known — "A group" is true of
 *   every group on the board, which helps nobody find the one that broke.
 */
function gapMessage(gap, groupName) {
  const names = gap.map((column) => (typeof column === "string" ? column : column.name));
  const subject = groupName ? `The ${listPhrase([groupName])} group` : "A group";
  return `${subject} has to cover consecutive columns. ${listPhrase(names)} `
    + `${names.length === 1 ? "sits" : "sit"} inside this run — include `
    + `${names.length === 1 ? "it" : "them"}, or choose a shorter run.`;
}

/* ==========================================================================
   Painting
   ========================================================================== */

/**
 * Rebuild the drawer body.
 *
 * Scroll position and keyboard focus are restored across the rebuild via stable
 * `data-focus-key` attributes: without them, pressing ↑ twice to move a column
 * two places would throw focus to the top of the drawer after the first press,
 * which makes keyboard reordering unusable.
 *
 * Uncommitted keystrokes are restored the same way, and for a stronger reason.
 * Every mutation reconciles by re-fetching and repainting the whole list, so a
 * write caused by one field lands on a rebuild of *every* field — including the
 * one the user is still typing into, whose fresh node would otherwise be
 * hydrated from a server value that predates those keystrokes. A repaint may
 * never overwrite input the user has typed and not yet committed.
 */
function paint() {
  if (!handle || !bodyEl) return;

  const active = document.activeElement;
  const focusKey = bodyEl.contains(active) ? active?.dataset?.focusKey || null : null;
  const scrollTop = bodyEl.scrollTop;
  const liveEdits = captureLiveEdits(active);

  previewHost = null;
  render(bodyEl, buildBody());

  bodyEl.scrollTop = scrollTop;
  restoreLiveEdits(liveEdits);

  if (focusKey) {
    const next = bodyEl.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
    if (next) {
      next.focus();
      const edit = liveEdits.get(focusKey);
      if (edit && edit.start !== null) {
        try { next.setSelectionRange(edit.start, edit.end); } catch { /* number inputs */ }
      }
    } else {
      handle.el.focus();
    }
  }
}

/**
 * Text the user has typed and this module has not yet written, keyed by the
 * focus key that will identify the same field after the rebuild.
 */
function captureLiveEdits(active) {
  const edits = new Map();
  if (!bodyEl) return edits;
  bodyEl.querySelectorAll("input[data-dirty], textarea[data-dirty]").forEach((node) => {
    const key = node.dataset.focusKey;
    if (!key || node.dataset.dirty !== "true") return;
    const entry = { value: node.value, start: null, end: null };
    if (node === active) {
      // `selectionStart` throws on number inputs in Chrome; the caret simply
      // lands at the end there, which is where a typist already was.
      try {
        entry.start = node.selectionStart;
        entry.end = node.selectionEnd;
      } catch { /* number input */ }
    }
    edits.set(key, entry);
  });
  return edits;
}

/** Put those keystrokes back, and let the row re-derive its own feedback. */
function restoreLiveEdits(edits) {
  if (!edits.size || !bodyEl) return;
  edits.forEach((entry, key) => {
    const node = bodyEl.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
    if (!node || (node.tagName !== "INPUT" && node.tagName !== "TEXTAREA")) return;
    if (node.value === entry.value) return;
    node.value = entry.value;
    node.dataset.dirty = "true";
    // The rebuilt row derived its badge and its validation from the server row;
    // re-firing `input` makes them agree with the box again, through the very
    // handlers the new row just bound.
    node.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Committed, reconciled, or abandoned: this field no longer owns its value. */
function clearDirty(...nodes) {
  nodes.forEach((node) => { if (node) delete node.dataset.dirty; });
}

function buildBody() {
  if (loading) return buildLoadingBody();
  if (loadError) {
    return errorState(loadError, () => load(), {
      title: "Could not load this board's structure",
    });
  }

  return [
    canEdit ? null : buildReadOnlyNotice(),
    buildPreviewSection(),
    buildTeachingPanel(),
    buildColumnsSection(),
    buildGroupsSection(),
  ];
}

function buildLoadingBody() {
  return el(
    "div",
    { className: "stack stack-6", "aria-busy": "true", "aria-label": "Loading board structure" },
    el("div", { className: "structure-preview" }, skeleton({ height: 34, radius: "var(--radius-md)" })),
    el("div", { className: "stack stack-2" }, skeleton({ height: 14, width: "38%" }), skeleton({ height: 58, radius: "var(--radius-lg)" })),
    el(
      "div",
      { className: "stack stack-2" },
      Array.from({ length: 7 }, () => skeleton({ height: 46, radius: "var(--radius-lg)" }))
    )
  );
}

function buildReadOnlyNotice() {
  return el(
    "div",
    { className: "alert structure-readonly", dataset: { tone: "info" }, role: "note" },
    icon("lock"),
    el(
      "div",
      { className: "stack stack-1" },
      el("strong", null, "You are viewing this structure, not editing it."),
      el(
        "span",
        null,
        "Adding, removing and re-shaping columns changes how every metric on this board is "
        + "computed, so it is limited to the master and administrator roles. Everything below "
        + "is readable — ask a workspace administrator to make a change."
      )
    )
  );
}

/* ---- live flow preview ---------------------------------------------------- */

function buildPreviewSection() {
  previewHost = el("div", { className: "structure-preview" });
  renderPreview();
  return previewHost;
}

function renderPreview() {
  if (!previewHost) return;
  render(previewHost, buildPreviewInner(previewOverride || orderColumns(columns)));
}

/**
 * The chip strip: upstream chips, the commitment marker, downstream chips.
 *
 * @param {Array<object>} ordered Columns already in flow order.
 * @param {{draftId?: string, label?: string}} [opts]
 */
function buildPreviewInner(ordered, opts = {}) {
  const upstream = ordered.filter((c) => c.segment === "upstream");
  const downstream = ordered.filter((c) => c.segment === "downstream");
  const commitment = downstream[0] || null;
  const delivery = [...ordered].reverse().find((c) => c.kind === "done") || null;

  if (!ordered.length) {
    return [
      el("div", { className: "structure-preview-head" }, el("span", { className: "eyebrow" }, opts.label || "Flow preview")),
      el(
        "p",
        { className: "structure-preview-summary" },
        "No columns yet. The flow preview appears as soon as this board has its first column."
      ),
    ];
  }

  const items = [];
  ordered.forEach((column) => {
    if (commitment && column.id === commitment.id) {
      items.push(
        el(
          "li",
          { className: "structure-preview-mark" },
          icon("target"),
          el("span", null, "Commitment")
        )
      );
    }
    const meta = columnKindMeta(column.kind);
    items.push(
      el(
        "li",
        {
          className: "structure-preview-chip",
          dataset: {
            segment: column.segment,
            colKind: column.kind,
            ...(opts.draftId && column.id === opts.draftId ? { draft: "true" } : {}),
          },
          style: column.color ? { "--col-color": column.color } : {},
        },
        icon(meta.icon),
        el("span", { className: "truncate" }, column.name || "Untitled column"),
        opts.draftId && column.id === opts.draftId
          ? el("span", { className: "structure-preview-new" }, "new")
          : null
      )
    );
  });

  const summary = [
    `${upstream.length} upstream`,
    commitment ? `commits at ${commitment.name || "an unnamed column"}` : "no commitment point yet",
    `${downstream.length} downstream`,
    delivery ? `delivers at ${delivery.name || "an unnamed column"}` : "no delivery point yet",
  ].join(" · ");

  return [
    el(
      "div",
      { className: "structure-preview-head" },
      el("span", { className: "eyebrow" }, opts.label || "Flow preview"),
      el("span", { className: "t-xs subtle" }, `${ordered.length} column${ordered.length === 1 ? "" : "s"}`)
    ),
    el("ul", { className: "structure-preview-strip", role: "list", "aria-label": "Flow preview, in order" }, items),
    el("p", { className: "structure-preview-summary" }, summary),
  ];
}

/* ---- the concept that the whole editor turns on --------------------------- */

function buildTeachingPanel() {
  const open = readTeachOpen();

  const details = el(
    "details",
    {
      className: "structure-teach",
      open,
      onToggle: (event) => writeTeachOpen(event.target.open),
    },
    el(
      "summary",
      { className: "structure-teach-summary", "data-focus-key": "teach" },
      icon("help"),
      el("span", null, "Wait time vs touch time — how to pick a column kind"),
      icon("chevron-down", { className: "structure-teach-caret" })
    ),
    el(
      "div",
      { className: "structure-teach-body" },
      el(
        "p",
        null,
        "Every column you create is either a place where work ",
        el("strong", null, "waits"),
        " or a place where work is ",
        el("strong", null, "touched"),
        ". That single choice is the only reason flow efficiency can be measured at all, "
        + "so it is worth thirty seconds of thought per column."
      ),
      el(
        "div",
        { className: "structure-teach-grid" },
        teachCard("queue", "Queue = wait time",
          "The item sits still and nobody is working on it. Backlogs, replenishment buffers "
          + "and every \"…Done\" hand-off column are queues, even when they feel like progress."),
        teachCard("activity", "Activity = touch time",
          "Someone is actively working the item right now. Discovery, Analysis, Build and "
          + "Verify are activities — if the work would stop when that person walked away, it "
          + "is an activity.")
      ),
      el(
        "p",
        { className: "structure-teach-formula" },
        el("code", null, "flow efficiency = touch ÷ (touch + wait) × 100")
      ),
      el(
        "p",
        null,
        "Most knowledge work lands between 15% and 40%: an item spends the large majority of "
        + "its life waiting. You cannot see that — or shorten it — unless the board says which "
        + "columns are queues. Mislabel a buffer as an activity and the number flatters you."
      ),
      el(
        "p",
        { className: "structure-teach-aside" },
        "The other two kinds are structural rather than temporal: ",
        el("strong", null, "envelope"),
        " is sealed intake and may only be the first upstream column, and ",
        el("strong", null, "done"),
        " is terminal — the last one is your delivery point."
      )
    )
  );

  return details;
}

function teachCard(kind, title, body) {
  const meta = columnKindMeta(kind);
  return el(
    "div",
    { className: "structure-teach-item", dataset: { colKind: kind } },
    el("div", { className: "structure-teach-item-head" }, icon(meta.icon), el("strong", null, title)),
    el("p", null, body)
  );
}

function readTeachOpen() {
  try {
    const stored = localStorage.getItem(TEACH_KEY);
    return stored === null ? true : stored === "open";
  } catch {
    return true;
  }
}

function writeTeachOpen(open) {
  try {
    localStorage.setItem(TEACH_KEY, open ? "open" : "closed");
  } catch {
    /* private mode — the panel simply reopens next time */
  }
}

/* ==========================================================================
   The column list
   ========================================================================== */

function buildColumnsSection() {
  const ordered = orderColumns(columns);

  const head = el(
    "div",
    { className: "structure-section-head" },
    el(
      "div",
      { className: "stack stack-1" },
      el("h3", { className: "structure-section-title" }, "Columns"),
      el(
        "span",
        { className: "t-xs subtle" },
        !ordered.length
          ? "The flow is empty."
          : canEdit
            ? "In flow order. Drag a row by its handle, or use the arrow buttons, to reorder."
            : "In flow order, from intake through the commitment point to delivery."
      )
    ),
    canEdit
      ? el(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            "data-focus-key": "add-column",
            onClick: () => openColumnModal({ index: null }),
          },
          icon("plus"),
          "Add column"
        )
      : null
  );

  if (!ordered.length) {
    return el(
      "section",
      { className: "structure-section" },
      head,
      emptyState({
        icon: "layers",
        title: "This board has no columns",
        body:
          "A board is a flow: upstream columns where options are shaped, a commitment point, "
          + "then downstream columns where the promise is delivered. Add the first column to "
          + "start mapping it.",
        actions: canEdit
          ? [
              el(
                "button",
                { type: "button", className: "btn btn-primary", onClick: () => openColumnModal({ index: null }) },
                icon("plus"),
                "Add the first column"
              ),
            ]
          : [],
      })
    );
  }

  const list = el("ul", { className: "structure-list", role: "list" }, buildListItems(ordered));
  if (canEdit) attachListDnd(list, ordered);

  return el("section", { className: "structure-section" }, head, list);
}

/**
 * Walk the ordered columns once, emitting group headers and the commitment
 * divider between rows. A group is allowed to span the commitment boundary, so
 * the divider is emitted inside the group run rather than closing it — that is
 * what the board itself does.
 */
function buildListItems(ordered) {
  const items = [];
  let previousGroupId = undefined;
  let commitmentDrawn = false;

  ordered.forEach((column, index) => {
    // The commitment point is emitted first when both land on the same row:
    // it is the more fundamental boundary, and a group header sitting above it
    // would read as though the group started before commitment.
    if (!commitmentDrawn && column.segment === "downstream") {
      commitmentDrawn = true;
      items.push(buildCommitmentDivider(column));
    }

    const groupId = column.group_id || null;
    if (groupId !== previousGroupId) {
      if (groupId) {
        const group = groups.find((g) => g.id === groupId);
        if (group) items.push(...buildGroupHeader(group, ordered));
      }
      previousGroupId = groupId;
    }

    items.push(buildColumnRow(column, index, ordered));
  });

  return items;
}

function buildCommitmentDivider(column) {
  return el(
    "li",
    { className: "structure-divider", role: "separator", "aria-label": "Commitment point" },
    el(
      "span",
      { className: "structure-divider-mark" },
      icon("target"),
      "Commitment point"
    ),
    el(
      "span",
      { className: "structure-divider-note" },
      `Cards entering ${column.name} are committed to.`
    )
  );
}

function buildColumnRow(column, index, ordered) {
  const meta = columnKindMeta(column.kind);
  const cell = wipData?.columns?.[column.id] || null;
  const count = cell ? cell.count : 0;
  const isExpanded = expandedColumns.has(column.id);
  const isEnvelope = column.kind === "envelope";
  const group = column.group_id ? groups.find((g) => g.id === column.group_id) : null;

  const moves = legalMoves(column, index, ordered);

  const nameNode = el(
    "div",
    { className: "structure-row-name" },
    el("span", { className: "truncate" }, column.name),
    column.is_commitment_point
      ? icon("target", { className: "structure-point-icon", title: "Commitment point" })
      : null,
    column.is_delivery_point
      ? icon("check", { className: "structure-point-icon", title: "Delivery point" })
      : null
  );

  const metaRow = el(
    "div",
    { className: "structure-row-meta" },
    el("span", { className: "chip", dataset: { colKind: column.kind } }, icon(meta.icon), meta.label),
    group ? el("span", { className: "chip" }, group.name) : null,
    cell
      ? el("span", { className: "badge", dataset: { wip: cell.state } }, wipRangeLabel(cell.min, cell.max))
      : null,
    el("span", { className: "t-xs subtle" }, `${count} card${count === 1 ? "" : "s"}`)
  );

  const actions = el(
    "div",
    { className: "structure-row-actions" },
    canEdit
      ? el(
          "button",
          {
            type: "button",
            className: "btn btn-ghost btn-icon btn-sm",
            "aria-label": `Move ${column.name} earlier in the flow`,
            disabled: !moves.up,
            "data-focus-key": `col:${column.id}:up`,
            onClick: () => moveColumn(column, -1, ordered),
          },
          icon("arrow-up")
        )
      : null,
    canEdit
      ? el(
          "button",
          {
            type: "button",
            className: "btn btn-ghost btn-icon btn-sm",
            "aria-label": `Move ${column.name} later in the flow`,
            disabled: !moves.down,
            "data-focus-key": `col:${column.id}:down`,
            onClick: () => moveColumn(column, 1, ordered),
          },
          icon("arrow-down")
        )
      : null,
    el(
      "button",
      {
        type: "button",
        className: "btn btn-ghost btn-icon btn-sm structure-expand",
        "aria-expanded": isExpanded ? "true" : "false",
        "aria-controls": `structure-form-${column.id}`,
        "aria-label": isExpanded ? `Hide settings for ${column.name}` : `Edit ${column.name}`,
        "data-focus-key": `col:${column.id}:expand`,
        onClick: () => toggleColumn(column.id),
      },
      icon(isExpanded ? "chevron-up" : "chevron-down")
    )
  );

  // The row is only made draggable while the pointer is on its grip. A
  // permanently-draggable row swallows text selection inside the expanded form's
  // inputs, and it also implies you can grab a row anywhere, which is exactly
  // the affordance a Kanban board should not fake.
  const grip = canEdit
    ? el(
        "span",
        {
          className: "structure-grip",
          dataset: isEnvelope ? { locked: "true" } : {},
          "aria-hidden": "true",
          "data-tooltip": isEnvelope ? "The envelope column is pinned first" : "Drag to reorder",
          ...(isEnvelope
            ? {}
            : {
                onMouseDown: (event) => setRowDraggable(event.currentTarget, true),
                onMouseUp: (event) => setRowDraggable(event.currentTarget, false),
              }),
        },
        icon("drag")
      )
    : null;

  const main = el(
    "div",
    { className: "structure-row-main" },
    grip,
    el("span", { className: "structure-pos numeric" }, String(index + 1)),
    el("span", { className: "structure-swatch", style: column.color ? { "--col-color": column.color } : {} }),
    el("div", { className: "structure-row-text" }, nameNode, metaRow),
    actions
  );

  return el(
    "li",
    {
      className: "structure-row",
      dataset: {
        columnId: column.id,
        segment: column.segment,
        // Deliberately not `data-col-kind`: that attribute carries the kind
        // colour tokens, and a row-level tone would bleed into every neutral
        // control inside the expanded form.
        rowKind: column.kind,
        grouped: column.group_id ? "true" : "false",
        expanded: isExpanded ? "true" : "false",
      },
      style: group && group.color ? { "--group-color": group.color } : {},
    },
    main,
    isExpanded ? buildColumnForm(column, index, ordered) : null
  );
}

function setRowDraggable(node, value) {
  const row = node.closest(".structure-row");
  if (row) row.draggable = value;
}

/**
 * Which arrow buttons are legal for this row.
 *
 * `normalize_structure` sorts by segment first and pins an envelope column to
 * index 0, so a swap across the commitment boundary — or past the envelope —
 * would be silently undone by the server. Disabling those buttons is honest;
 * letting them fire and snap back is not.
 */
function legalMoves(column, index, ordered) {
  if (!canEdit || column.kind === "envelope") return { up: false, down: false };
  const before = ordered[index - 1];
  const after = ordered[index + 1];
  return {
    up: Boolean(before && before.segment === column.segment && before.kind !== "envelope"),
    down: Boolean(after && after.segment === column.segment),
  };
}

function toggleColumn(id) {
  // Collapsing the row destroys its boxes, so anything typed into them is
  // written first rather than dropped on the floor.
  flushWipCommit(`col:${id}:wip`);
  if (expandedColumns.has(id)) expandedColumns.delete(id);
  else expandedColumns.add(id);
  paint();
}

/* ---- the per-row editor --------------------------------------------------- */

function buildColumnForm(column, index, ordered) {
  const cell = wipData?.columns?.[column.id] || null;
  const count = cell ? cell.count : 0;
  const disabled = !canEdit;

  const kindConsequence = el(
    "p",
    { className: "structure-consequence", dataset: { colKind: column.kind } },
    kindCopy(column.kind)
  );
  const segmentConsequence = el(
    "p",
    { className: "structure-consequence" },
    segmentCopy(column.segment)
  );

  const wip = wipLimitControl({
    scope: `col:${column.id}:wip`,
    minKey: `col:${column.id}:wip-min`,
    maxKey: `col:${column.id}:wip-max`,
    minLabel: `${column.name} WIP minimum`,
    maxLabel: `${column.name} WIP maximum`,
    count,
    state: cell ? cell.state : null,
    disabled,
    resolve: () => columns.find((c) => c.id === column.id) || null,
    save: (row, min, max) =>
      patchColumn(row, { wip_min: min, wip_max: max }, {
        announcement: `${row.name} WIP limits set to ${wipRangeLabel(min, max)}.`,
      }),
  });

  const fields = el(
    "div",
    { className: "structure-form-grid" },

    field({
      label: "Name",
      span: 2,
      control: el("input", {
        type: "text",
        className: "input",
        value: column.name,
        maxLength: 500,
        disabled,
        "data-focus-key": `col:${column.id}:name`,
        onKeyDown: (event) => { if (event.key === "Enter") event.target.blur(); },
        onChange: (event) => {
          const name = event.target.value.trim();
          clearDirty(event.target);
          if (!name) {
            event.target.value = column.name;
            toast("A column needs a name.", { tone: "warning" });
            return;
          }
          if (name === column.name) return;
          patchColumn(column, { name }, { announcement: `Column renamed to ${name}.` });
        },
      }),
    }),

    field({
      label: "Kind",
      control: el(
        "select",
        {
          className: "select",
          disabled,
          "data-focus-key": `col:${column.id}:kind`,
          onChange: (event) => {
            const kind = event.target.value;
            kindConsequence.textContent = kindCopy(kind);
            kindConsequence.dataset.colKind = kind;
            patchColumn(column, { kind }, {
              speculate: () => speculate(column.id, { kind }),
              announcement: `${column.name} is now a ${kind} column.`,
            });
          },
        },
        KINDS.map((k) => el("option", { value: k.id, selected: k.id === column.kind }, k.label))
      ),
      below: kindConsequence,
    }),

    field({
      label: "Segment",
      control: el(
        "select",
        {
          className: "select",
          disabled,
          "data-focus-key": `col:${column.id}:segment`,
          onChange: (event) => {
            const segment = event.target.value;
            segmentConsequence.textContent = segmentCopy(segment);
            patchColumn(column, { segment }, {
              speculate: () => speculate(column.id, { segment }),
              announcement: `${column.name} moved to the ${segment} segment.`,
            });
          },
        },
        SEGMENTS.map((s) => el("option", { value: s.id, selected: s.id === column.segment }, s.label))
      ),
      below: segmentConsequence,
    }),

    field({
      label: "Group",
      hint: groups.length ? "A group must cover consecutive columns." : "No groups on this board yet.",
      control: el(
        "select",
        {
          className: "select",
          disabled: disabled || !groups.length,
          "data-focus-key": `col:${column.id}:group`,
          onChange: (event) => {
            const value = event.target.value || null;
            patchColumn(column, { group_id: value }, {
              announcement: value
                ? `${column.name} added to ${groups.find((g) => g.id === value)?.name}.`
                : `${column.name} removed from its group.`,
            });
          },
        },
        el("option", { value: "", selected: !column.group_id }, "No group"),
        groups.map((g) => el("option", { value: g.id, selected: g.id === column.group_id }, g.name))
      ),
    }),

    field({
      label: "WIP limits",
      hint: "Leave a box empty for no limit. The minimum is advisory — it never blocks a move.",
      control: wip.control,
      below: wip.error,
    }),

    field({
      label: "Colour",
      control: colorPicker({
        value: column.color || null,
        disabled,
        focusKey: `col:${column.id}:color`,
        label: `${column.name} colour`,
        onChange: (value) => patchColumn(column, { color: value }),
      }),
    }),

    field({
      label: "Description",
      span: 2,
      hint: "Shown as the column's policy on the board. Say what has to be true for a card to sit here.",
      control: el("textarea", {
        className: "textarea",
        rows: 2,
        disabled,
        value: column.description || "",
        "data-focus-key": `col:${column.id}:description`,
        onChange: (event) => {
          const description = event.target.value.trim() || null;
          clearDirty(event.target);
          if ((column.description || null) === description) return;
          patchColumn(column, { description });
        },
      }),
    })
  );

  const rowActions = canEdit
    ? el(
        "div",
        { className: "structure-form-actions" },
        el(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            "data-focus-key": `col:${column.id}:insert-before`,
            onClick: () => openColumnModal({ index, neighbour: column }),
          },
          icon("plus"),
          "Insert before"
        ),
        el(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            "data-focus-key": `col:${column.id}:insert-after`,
            onClick: () => openColumnModal({ index: index + 1, neighbour: column }),
          },
          icon("plus"),
          "Insert after"
        ),
        el("span", { className: "spacer" }),
        el(
          "button",
          {
            type: "button",
            className: "btn btn-danger-ghost btn-sm",
            "data-focus-key": `col:${column.id}:delete`,
            onClick: () => deleteColumn(column, ordered),
          },
          icon("trash"),
          "Delete column"
        )
      )
    : null;

  return el(
    "div",
    { className: "structure-row-form", id: `structure-form-${column.id}` },
    fields,
    rowActions
  );
}

/** PATCH one column and reconcile. */
function patchColumn(column, patch, opts = {}) {
  return mutate(() => api.patch(`/columns/${column.id}`, patch, { silent: true }), opts);
}

/* ---- reorder -------------------------------------------------------------- */

function moveColumn(column, delta, ordered) {
  const index = ordered.findIndex((c) => c.id === column.id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= ordered.length) return;
  if (ordered[target].segment !== column.segment) return;
  if (ordered[target].kind === "envelope") return;

  const next = ordered.slice();
  next.splice(index, 1);
  next.splice(target, 0, column);
  applyOrder(next, `${column.name} moved to position ${target + 1} of ${ordered.length}.`);
}

function applyOrder(nextOrder, announcement) {
  const ids = nextOrder.map((c) => c.id);
  return mutate(
    () => api.post(`/boards/${currentBoardId}/columns/reorder`, { ordered_ids: ids }, { silent: true }),
    { speculate: () => nextOrder.slice(), announcement }
  );
}

/**
 * Drag to reorder, delegated on the list so a repaint cannot orphan handlers.
 *
 * Drops are confined to the dragged column's own segment: the backend straightens
 * a cross-segment order back out, so allowing the drop would show the row
 * snapping home with no explanation.
 */
function attachListDnd(list, ordered) {
  list.addEventListener("dragstart", (event) => {
    const row = event.target.closest(".structure-row");
    if (!row || busy) return;
    const column = ordered.find((c) => c.id === row.dataset.columnId);
    if (!column || column.kind === "envelope") {
      event.preventDefault();
      return;
    }
    dragState = { id: column.id, segment: column.segment };
    event.dataTransfer.effectAllowed = "move";
    try { event.dataTransfer.setData("text/plain", column.id); } catch { /* Safari */ }
    window.setTimeout(() => row.classList.add("is-dragging"), 0);
  });

  list.addEventListener("dragover", (event) => {
    if (!dragState) return;
    const row = event.target.closest(".structure-row");
    if (!row || row.dataset.columnId === dragState.id) return;
    const column = ordered.find((c) => c.id === row.dataset.columnId);
    if (!column || column.segment !== dragState.segment || column.kind === "envelope") return;

    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const rect = row.getBoundingClientRect();
    row.dataset.drop = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
  });

  list.addEventListener("dragleave", (event) => {
    const row = event.target.closest(".structure-row");
    if (row && !row.contains(event.relatedTarget)) delete row.dataset.drop;
  });

  list.addEventListener("drop", (event) => {
    if (!dragState) return;
    const row = event.target.closest(".structure-row");
    if (!row || !row.dataset.drop) return;
    event.preventDefault();

    const placement = row.dataset.drop;
    const targetId = row.dataset.columnId;
    const dragged = ordered.find((c) => c.id === dragState.id);
    clearDropMarks(list);
    if (!dragged || targetId === dragged.id) return;

    const next = ordered.filter((c) => c.id !== dragged.id);
    const targetIndex = next.findIndex((c) => c.id === targetId);
    if (targetIndex < 0) return;
    next.splice(placement === "before" ? targetIndex : targetIndex + 1, 0, dragged);

    const finalIndex = next.findIndex((c) => c.id === dragged.id);
    applyOrder(next, `${dragged.name} moved to position ${finalIndex + 1} of ${next.length}.`);
  });

  list.addEventListener("dragend", () => {
    dragState = null;
    clearDropMarks(list);
    list.querySelectorAll(".is-dragging").forEach((n) => n.classList.remove("is-dragging"));
    list.querySelectorAll('[draggable="true"]').forEach((n) => { n.draggable = false; });
  });
}

function clearDropMarks(list) {
  list.querySelectorAll("[data-drop]").forEach((n) => delete n.dataset.drop);
}

/* ==========================================================================
   Add a column
   ========================================================================== */

/**
 * The create form, with defaults derived from the insertion point: a column
 * inserted next to an upstream column is upstream, and inherits its neighbour's
 * group so it does not break that group's run the moment it appears.
 *
 * The preview inside this modal is the answer to "see the commitment point move
 * before saving": it re-runs the backend's own placement rule against the
 * pending draft, so a downstream column typed into an all-upstream board shows
 * the commitment marker appearing in front of it while the form is still open.
 */
function openColumnModal({ index = null, neighbour = null } = {}) {
  if (!canEdit) return;

  const ordered = orderColumns(columns);
  const anchor = neighbour
    || (index !== null ? ordered[Math.min(index, ordered.length - 1)] : ordered[ordered.length - 1])
    || null;

  const draft = {
    id: "__draft__",
    name: "",
    segment: anchor ? anchor.segment : "upstream",
    kind: "queue",
    group_id: anchor ? anchor.group_id || null : null,
    color: null,
  };
  const position = index;

  let nameInput = null;
  let minInput = null;
  let maxInput = null;
  let descInput = null;
  let segmentInput = null;

  const previewNode = el("div", { className: "structure-preview structure-preview-inline" });
  function refreshPreview() {
    const ghost = { ...draft, name: draft.name.trim() || "New column" };
    render(
      previewNode,
      buildPreviewInner(placeDraft(ordered, ghost, position), {
        draftId: draft.id,
        label: "Resulting flow",
      })
    );
    revealDraftChip(previewNode);
  }

  /**
   * Keep the pending column on screen.
   *
   * On a twelve-column board the new chip is usually past the right edge of the
   * strip, and a preview you have to scroll to find does not answer "where does
   * this land?" at the moment the question is asked.
   */
  function revealDraftChip(host) {
    const strip = host.querySelector(".structure-preview-strip");
    const chip = host.querySelector('[data-draft="true"]');
    if (!strip || !chip) return;
    strip.scrollLeft = Math.max(
      0,
      chip.offsetLeft - strip.clientWidth / 2 + chip.offsetWidth / 2
    );
  }

  const kindConsequence = el(
    "p",
    { className: "structure-consequence", dataset: { colKind: draft.kind } },
    kindCopy(draft.kind)
  );
  const segmentConsequence = el("p", { className: "structure-consequence" }, segmentCopy(draft.segment));
  const nameError = el("p", { className: "field-error", hidden: true }, "Give the column a name.");

  const kindSelect = el(
    "select",
    {
      className: "select",
      onChange: (event) => {
        draft.kind = event.target.value;
        kindConsequence.textContent = kindCopy(draft.kind);
        kindConsequence.dataset.colKind = draft.kind;
        // The backend refuses an envelope outside upstream and a done column
        // outside downstream, so follow the rule here rather than letting the
        // user submit into a 409.
        if (draft.kind === "envelope") draft.segment = "upstream";
        if (draft.kind === "done") draft.segment = "downstream";
        if (segmentInput) segmentInput.value = draft.segment;
        segmentConsequence.textContent = segmentCopy(draft.segment);
        refreshPreview();
      },
    },
    KINDS.map((k) => el("option", { value: k.id, selected: k.id === draft.kind }, k.label))
  );

  const segmentSelect = el(
    "select",
    {
      className: "select",
      ref: (node) => { segmentInput = node; },
      onChange: (event) => {
        draft.segment = event.target.value;
        segmentConsequence.textContent = segmentCopy(draft.segment);
        refreshPreview();
      },
    },
    SEGMENTS.map((s) => el("option", { value: s.id, selected: s.id === draft.segment }, s.label))
  );

  const form = el(
    "div",
    { className: "stack stack-4" },
    previewNode,
    el(
      "div",
      { className: "structure-form-grid" },
      field({
        label: "Name",
        span: 2,
        control: el("input", {
          type: "text",
          className: "input",
          placeholder: "e.g. Ready for review",
          maxLength: 500,
          "data-autofocus": "",
          ref: (n) => { nameInput = n; },
          onInput: (event) => {
            draft.name = event.target.value;
            nameError.hidden = true;
            event.target.setAttribute("aria-invalid", "false");
            refreshPreview();
          },
        }),
        below: nameError,
      }),
      field({ label: "Kind", control: kindSelect, below: kindConsequence }),
      field({ label: "Segment", control: segmentSelect, below: segmentConsequence }),
      field({
        label: "Group",
        hint: groups.length
          ? "Inherited from the neighbouring column, so the group's run stays unbroken."
          : "No groups on this board yet.",
        control: el(
          "select",
          {
            className: "select",
            disabled: !groups.length,
            onChange: (event) => { draft.group_id = event.target.value || null; },
          },
          el("option", { value: "", selected: !draft.group_id }, "No group"),
          groups.map((g) => el("option", { value: g.id, selected: g.id === draft.group_id }, g.name))
        ),
      }),
      field({
        label: "WIP limits",
        hint: "Optional. Leave empty for no limit.",
        control: el(
          "div",
          { className: "structure-wip-row" },
          el("input", {
            type: "number", className: "input", min: 0, step: 1, inputMode: "numeric",
            placeholder: "min", "aria-label": "WIP minimum",
            ref: (n) => { minInput = n; },
          }),
          el("span", { className: "structure-wip-sep", "aria-hidden": "true" }, "–"),
          el("input", {
            type: "number", className: "input", min: 0, step: 1, inputMode: "numeric",
            placeholder: "max", "aria-label": "WIP maximum",
            ref: (n) => { maxInput = n; },
          })
        ),
      }),
      field({
        label: "Colour",
        control: colorPicker({
          value: null,
          label: "New column colour",
          onChange: (value) => { draft.color = value; refreshPreview(); },
        }),
      }),
      field({
        label: "Description",
        span: 2,
        hint: "The column's policy — what has to be true for a card to sit here.",
        control: el("textarea", { className: "textarea", rows: 2, ref: (n) => { descInput = n; } }),
      })
    )
  );

  const submitButton = el(
    "button",
    { type: "button", className: "btn btn-primary", onClick: () => submit() },
    icon("plus"),
    "Add column"
  );

  const dialog = modal({
    title: anchor && position !== null ? `Add a column near ${anchor.name}` : "Add a column",
    size: "lg",
    body: form,
    footer: [
      el("button", { type: "button", className: "btn btn-secondary", onClick: () => dialog.close() }, "Cancel"),
      submitButton,
    ],
  });

  refreshPreview();

  async function submit() {
    const name = (nameInput?.value || "").trim();
    if (!name) {
      nameError.hidden = false;
      nameInput?.setAttribute("aria-invalid", "true");
      nameInput?.focus();
      return;
    }
    const min = readLimit(minInput);
    const max = readLimit(maxInput);
    if (min === undefined || max === undefined) {
      toast("WIP limits must be whole numbers, or empty for no limit.", { tone: "warning" });
      return;
    }
    if (min !== null && max !== null && min > max) {
      toast("The WIP minimum cannot be larger than the maximum.", { tone: "warning" });
      return;
    }

    const payload = {
      name,
      segment: draft.segment,
      kind: draft.kind,
      wip_min: min,
      wip_max: max,
      group_id: draft.group_id,
      color: draft.color,
      description: (descInput?.value || "").trim() || null,
    };
    if (position !== null) payload.position = position;

    submitButton.classList.add("is-loading");
    const ok = await mutate(
      () => api.post(`/boards/${currentBoardId}/columns`, payload, { silent: true }),
      { announcement: `Column ${name} added to the flow.` }
    );
    submitButton.classList.remove("is-loading");
    if (ok) {
      toast(`${name} added to the flow.`, { tone: "success" });
      dialog.close();
    }
  }
}

/* ==========================================================================
   Delete a column — and the remedy when it holds cards
   ========================================================================== */

async function deleteColumn(column, ordered) {
  if (!canEdit) return;
  const known = wipData?.columns?.[column.id]?.count || 0;

  if (known > 0) {
    openDrainModal(column, known, ordered);
    return;
  }

  const confirmed = await confirmDialog({
    title: `Delete ${column.name}?`,
    message:
      "The column disappears from the flow and the board's commitment and delivery points are "
      + "recomputed. Movement history already recorded against it is kept.",
    confirmLabel: "Delete column",
    tone: "danger",
  });
  if (!confirmed) return;

  await mutate(
    () => api.del(`/columns/${column.id}`, { silent: true }),
    {
      announcement: `Column ${column.name} deleted.`,
      onError: (error) => {
        if (error instanceof ApiError && error.code === "column_not_empty") {
          const count = Number(error.details?.card_count) || 1;
          // Cards arrived between the load and the click. Offer the remedy
          // rather than reporting a race the user cannot see.
          window.setTimeout(() => openDrainModal(column, count, orderColumns(columns)), 0);
          return true;
        }
        return false;
      },
    }
  );
}

/**
 * "This column holds N cards" is not an error, it is a missing step.
 *
 * Offer the step: move the cards to a neighbour, then delete. The drain endpoint
 * is new in this increment, so a server that predates it degrades to a sentence
 * telling the user exactly what to do by hand instead of a dead end.
 */
function openDrainModal(column, count, ordered) {
  const index = ordered.findIndex((c) => c.id === column.id);
  const candidates = ordered.filter((c) => c.id !== column.id);
  if (!candidates.length) {
    toast(
      `${column.name} holds ${count} card${count === 1 ? "" : "s"} and it is the only column on `
      + "this board, so there is nowhere to move them. Archive the cards first.",
      { tone: "warning", duration: 9000 }
    );
    return;
  }

  const neighbour = ordered[index + 1] || ordered[index - 1] || candidates[0];
  let targetId = neighbour.id;

  const commitmentIndex = ordered.findIndex((c) => c.segment === "downstream");
  const deliveryColumn = [...ordered].reverse().find((c) => c.kind === "done") || null;

  /** Where a target sits relative to the column being emptied. */
  function consequencesOf(target) {
    const to = ordered.findIndex((c) => c.id === target.id);
    const from = index;
    const committed = commitmentIndex >= 0;
    return {
      backward: from >= 0 && to >= 0 && to < from,
      commits: committed && to >= commitmentIndex && from < commitmentIndex,
      uncommits: committed && to < commitmentIndex && from >= commitmentIndex,
      delivers: Boolean(deliveryColumn) && target.id === deliveryColumn.id,
    };
  }

  /**
   * Say what a backward drain costs, without taking it away.
   *
   * The API allows a backward drain on purpose — a column created by mistake
   * sits wherever it was created, and forward-only draining would make it
   * undeletable forever. But it is not free: each card is written to history as
   * a backward move, and crossing the commitment point or landing on the
   * delivery point stamps `committed_at` / `delivered_at` at today's date, which
   * silently rewrites the cycle and lead times every chart on this board is
   * built from. That is a decision, so it is put in front of the person making
   * it rather than discovered afterwards in the metrics.
   */
  function buildDrainNotice(target) {
    const facts = consequencesOf(target);
    const notes = [];
    if (facts.backward) {
      notes.push(
        `${target.name} sits earlier in the flow, so this moves work backwards. It is allowed — `
        + "a column created in the wrong place still has to be emptiable — but every card is "
        + "recorded as a backward move in its own history."
      );
    }
    if (facts.uncommits) {
      notes.push(
        `${target.name} sits before the commitment point. The cards keep the commitment they `
        + "already have, so they will show as committed work parked back in the options space."
      );
    }
    if (facts.commits) {
      notes.push(
        `${target.name} is at or past the commitment point, so every card not yet committed is `
        + "committed now: cycle time starts from today, not from when the work really began."
      );
    }
    if (facts.delivers) {
      notes.push(
        `${target.name} is this board's delivery point, so every card not yet delivered is `
        + "stamped as delivered now and counts towards throughput today."
      );
    }
    if (!notes.length) return null;

    const severe = facts.backward || facts.uncommits;
    return el(
      "div",
      { className: "alert", dataset: { tone: severe ? "warning" : "info" } },
      icon(severe ? "alert" : "info"),
      el(
        "div",
        { className: "stack stack-1" },
        el("strong", null, severe ? "This moves cards against the flow" : "This changes card timestamps"),
        ...notes.map((note) => el("span", null, note))
      )
    );
  }

  const noticeHost = el("div", { role: "status", "aria-live": "polite" });
  function renderNotice() {
    const target = candidates.find((c) => c.id === targetId) || neighbour;
    render(noticeHost, buildDrainNotice(target));
  }

  /** Option text: never leave "moves this work backwards" to be found out later. */
  function optionLabel(c) {
    const parts = [c.name];
    if (c.id === neighbour.id) parts.push("adjacent");
    parts.push(humanize(c.segment));
    if (consequencesOf(c).backward) parts.push("earlier in the flow — backward move");
    return parts.join(" · ");
  }

  function optionFor(c) {
    return el("option", { value: c.id, selected: c.id === neighbour.id }, optionLabel(c));
  }

  const forwardTargets = candidates.filter((c) => !consequencesOf(c).backward);
  const backwardTargets = candidates.filter((c) => consequencesOf(c).backward);

  const select = el(
    "select",
    {
      className: "select",
      "data-autofocus": "",
      onChange: (event) => { targetId = event.target.value; renderNotice(); },
    },
    // Grouped, so the direction of the move is visible before the option is
    // read rather than buried at the end of its label.
    forwardTargets.length
      ? el("optgroup", { label: "Later in the flow" }, forwardTargets.map(optionFor))
      : null,
    backwardTargets.length
      ? el(
          "optgroup",
          { label: "Earlier in the flow — moves cards backwards" },
          backwardTargets.map(optionFor)
        )
      : null
  );
  // The default sits inside an optgroup now, so the selection is set on the
  // select itself rather than trusted to option order.
  select.value = targetId;

  const bodyNode = el(
    "div",
    { className: "stack stack-4" },
    el(
      "p",
      { className: "muted" },
      `${column.name} holds ${count} card${count === 1 ? "" : "s"}. A column can only be deleted `
      + "once it is empty — Nagare will not quietly drop work in progress."
    ),
    field({
      label: `Move ${count === 1 ? "the card" : `all ${count} cards`} to`,
      control: select,
      hint: "Every card keeps its full history, and the move is written into it.",
    }),
    noticeHost
  );

  renderNotice();

  const confirmButton = el(
    "button",
    { type: "button", className: "btn btn-danger", onClick: () => run() },
    icon("arrow-right"),
    `Move ${count === 1 ? "1 card" : `${count} cards`} and delete`
  );

  const cancelButton = el(
    "button",
    { type: "button", className: "btn btn-secondary", onClick: () => dialog.close() },
    "Cancel"
  );

  const dialog = modal({
    title: `${column.name} is not empty`,
    body: bodyNode,
    footer: [cancelButton, confirmButton],
  });

  async function run() {
    confirmButton.classList.add("is-loading");
    const target = candidates.find((c) => c.id === targetId) || neighbour;

    const ok = await mutate(
      async () => {
        await api.post(`/columns/${column.id}/drain`, { to_column_id: target.id }, { silent: true });
        await api.del(`/columns/${column.id}`, { silent: true });
      },
      {
        announcement: `${count} card${count === 1 ? "" : "s"} moved to ${target.name}; ${column.name} deleted.`,
        onError: (error) => {
          confirmButton.classList.remove("is-loading");
          if (isMissingEndpoint(error)) {
            showManualFallback(target);
            return true;
          }
          return false;
        },
      }
    );

    confirmButton.classList.remove("is-loading");
    if (ok) {
      toast(`${count} card${count === 1 ? "" : "s"} moved to ${target.name}. ${column.name} deleted.`, {
        tone: "success",
      });
      dialog.close();
    }
  }

  function showManualFallback(target) {
    render(
      bodyNode,
      el(
        "div",
        { className: "stack stack-3" },
        el(
          "div",
          { className: "alert", dataset: { tone: "warning" } },
          icon("alert"),
          el(
            "div",
            { className: "stack stack-1" },
            el("strong", null, "This server cannot move the cards for you."),
            el(
              "span",
              null,
              `Open ${column.name} on the board and move its ${count} card`
              + `${count === 1 ? "" : "s"} into ${target.name} — drag them, or use the card `
              + "drawer's move action — then delete the column from here."
            )
          )
        )
      )
    );
    confirmButton.remove();
    cancelButton.textContent = "Close";
  }
}

/**
 * A drain call against a server that has no drain route.
 *
 * A 404 on this path is ambiguous — it could be "no such route" or "no such
 * column" — so the domain code is checked first: only an unrecognised 404/405
 * means the endpoint itself is missing.
 */
function isMissingEndpoint(error) {
  if (!(error instanceof ApiError)) return false;
  if (!error.path || !error.path.endsWith("/drain")) return false;
  if (error.code === "column_not_found" || error.code === "board_not_found") return false;
  return error.status === 404 || error.status === 405 || error.code === "invalid_response";
}

/* ==========================================================================
   Column groups
   ========================================================================== */

function buildGroupHeader(group, ordered) {
  const cell = wipData?.groups?.[group.id] || null;
  const spanned = ordered.filter((c) => c.group_id === group.id);
  const isExpanded = expandedGroups.has(group.id);

  const header = el(
    "li",
    {
      className: "structure-group-head",
      dataset: { groupId: group.id, expanded: isExpanded ? "true" : "false" },
      style: group.color ? { "--group-color": group.color } : {},
    },
    el("span", { className: "structure-group-swatch" }),
    el("span", { className: "structure-group-name truncate" }, group.name),
    cell
      ? el("span", { className: "badge", dataset: { wip: cell.state } }, wipRangeLabel(cell.min, cell.max))
      : null,
    el(
      "span",
      { className: "t-xs subtle" },
      `${spanned.length} column${spanned.length === 1 ? "" : "s"}`
      + (cell ? ` · ${cell.count} card${cell.count === 1 ? "" : "s"}` : "")
    ),
    el("span", { className: "spacer" }),
    el(
      "button",
      {
        type: "button",
        className: "btn btn-ghost btn-icon btn-sm",
        "aria-expanded": isExpanded ? "true" : "false",
        "aria-controls": `structure-group-form-${group.id}`,
        "aria-label": isExpanded ? `Hide settings for ${group.name}` : `Edit group ${group.name}`,
        "data-focus-key": `group:${group.id}:expand`,
        onClick: () => toggleGroup(group.id),
      },
      icon(isExpanded ? "chevron-up" : "chevron-down")
    )
  );

  if (!isExpanded) return [header];
  return [header, el("li", { className: "structure-group-form-row" }, buildGroupForm(group, ordered))];
}

function toggleGroup(id) {
  flushWipCommit(`group:${id}:wip`);
  if (expandedGroups.has(id)) expandedGroups.delete(id);
  else expandedGroups.add(id);
  paint();
}

function buildGroupForm(group, ordered) {
  const cell = wipData?.groups?.[group.id] || null;
  const count = cell ? cell.count : 0;
  const groupsEditable = canEdit && hasFlag("wip_group");
  const disabled = !groupsEditable;

  const wip = wipLimitControl({
    scope: `group:${group.id}:wip`,
    minKey: `group:${group.id}:wip-min`,
    maxKey: `group:${group.id}:wip-max`,
    minLabel: `${group.name} WIP minimum`,
    maxLabel: `${group.name} WIP maximum`,
    count,
    state: cell ? cell.state : null,
    disabled,
    resolve: () => groups.find((g) => g.id === group.id) || null,
    save: (row, min, max) =>
      patchGroup(row, { wip_min: min, wip_max: max }, {
        announcement: `${row.name} WIP limits set to ${wipRangeLabel(min, max)}.`,
      }),
  });

  const spanState = new Set(ordered.filter((c) => c.group_id === group.id).map((c) => c.id));
  const spanError = el("p", { className: "field-error", hidden: true });

  const spanList = el(
    "div",
    { className: "structure-span-list", role: "group", "aria-label": `Columns in ${group.name}` },
    ordered.map((column) => {
      const otherGroup = column.group_id && column.group_id !== group.id
        ? groups.find((g) => g.id === column.group_id)
        : null;
      return el(
        "label",
        { className: "checkbox-row structure-span-item" },
        el("input", {
          type: "checkbox",
          className: "checkbox",
          checked: spanState.has(column.id),
          disabled,
          "data-focus-key": `group:${group.id}:span:${column.id}`,
          onChange: (event) => commitSpan(column.id, event.target),
        }),
        el("span", { className: "truncate" }, column.name),
        otherGroup ? el("span", { className: "t-xs subtle" }, `in ${otherGroup.name}`) : null
      );
    })
  );

  /**
   * Apply a span change, refusing the ones the board cannot hold.
   *
   * The contiguity rule is checked here rather than left to the 409 so the
   * failure names the columns in the way — and the checkbox rolls back, because
   * a control left showing a state the server rejected is a lie.
   */
  function commitSpan(columnId, input) {
    const wanted = input.checked;
    if (wanted) spanState.add(columnId);
    else spanState.delete(columnId);

    const selected = ordered.filter((c) => spanState.has(c.id)).map((c) => c.id);
    const gap = contiguityGap(ordered, selected);
    if (gap) {
      spanError.hidden = false;
      spanError.textContent = gapMessage(gap);
      if (wanted) spanState.delete(columnId);
      else spanState.add(columnId);
      input.checked = !wanted;
      return;
    }

    spanError.hidden = true;
    patchGroup(group, { column_ids: selected }, {
      announcement: `${group.name} now covers ${selected.length} column${selected.length === 1 ? "" : "s"}.`,
    });
  }

  return el(
    "div",
    { className: "structure-group-form", id: `structure-group-form-${group.id}` },
    !hasFlag("wip_group")
      ? el(
          "div",
          { className: "alert", dataset: { tone: "info" } },
          icon("lock"),
          "Column groups are read-only on this workspace's plan."
        )
      : null,
    el(
      "div",
      { className: "structure-form-grid" },
      field({
        label: "Group name",
        control: el("input", {
          type: "text",
          className: "input",
          value: group.name,
          maxLength: 500,
          disabled,
          "data-focus-key": `group:${group.id}:name`,
          onKeyDown: (event) => { if (event.key === "Enter") event.target.blur(); },
          onChange: (event) => {
            const name = event.target.value.trim();
            clearDirty(event.target);
            if (!name) {
              event.target.value = group.name;
              toast("A group needs a name.", { tone: "warning" });
              return;
            }
            if (name === group.name) return;
            patchGroup(group, { name }, { announcement: `Group renamed to ${name}.` });
          },
        }),
      }),
      field({
        label: "Group WIP limits",
        hint: "Applies to every card across the columns this group covers.",
        control: wip.control,
        below: wip.error,
      }),
      field({
        label: "Colour",
        control: colorPicker({
          value: group.color || null,
          disabled,
          focusKey: `group:${group.id}:color`,
          label: `${group.name} colour`,
          onChange: (value) => patchGroup(group, { color: value }),
        }),
      }),
      field({
        label: "Columns covered",
        span: 2,
        hint: "A group must cover columns that sit next to each other in the flow.",
        control: spanList,
        below: spanError,
      })
    ),
    groupsEditable
      ? el(
          "div",
          { className: "structure-form-actions" },
          el("span", { className: "spacer" }),
          el(
            "button",
            {
              type: "button",
              className: "btn btn-danger-ghost btn-sm",
              "data-focus-key": `group:${group.id}:delete`,
              onClick: () => deleteGroup(group),
            },
            icon("trash"),
            "Delete group"
          )
        )
      : null
  );
}

function patchGroup(group, patch, opts = {}) {
  return mutate(() => api.patch(`/groups/${group.id}`, patch, { silent: true }), opts);
}

async function deleteGroup(group) {
  const confirmed = await confirmDialog({
    title: `Delete the ${group.name} group?`,
    message:
      "The header and its WIP limit disappear. Every column it covers stays exactly where it "
      + "is, just ungrouped.",
    confirmLabel: "Delete group",
    tone: "danger",
  });
  if (!confirmed) return;
  expandedGroups.delete(group.id);
  await mutate(() => api.del(`/groups/${group.id}`, { silent: true }), {
    announcement: `Group ${group.name} deleted.`,
  });
}

function buildGroupsSection() {
  const ordered = orderColumns(columns);
  const orphans = groups.filter((g) => !ordered.some((c) => c.group_id === g.id));
  const flagOn = hasFlag("wip_group");

  const head = el(
    "div",
    { className: "structure-section-head" },
    el(
      "div",
      { className: "stack stack-1" },
      el("h3", { className: "structure-section-title" }, "Column groups"),
      el(
        "span",
        { className: "t-xs subtle" },
        "A group is a stage — several columns under one header, with one shared WIP limit."
      )
    ),
    canEdit && flagOn && ordered.length
      ? el(
          "button",
          {
            type: "button",
            className: "btn btn-secondary btn-sm",
            "data-focus-key": "add-group",
            onClick: () => openGroupModal(ordered),
          },
          icon("plus"),
          "New group"
        )
      : null
  );

  const children = [head];

  if (!flagOn) {
    children.push(
      el(
        "div",
        { className: "alert", dataset: { tone: "info" } },
        icon("lock"),
        "Per-group WIP limits are a Team-plan feature. Groups on this board stay visible and "
        + "read-only until the plan is upgraded."
      )
    );
  }

  if (!groups.length) {
    children.push(
      el(
        "p",
        { className: "structure-note" },
        "No groups yet. Grouping consecutive columns — a \"Discovery\" run, a \"Delivery\" run — "
        + "lets you cap the whole stage instead of each column, which is usually where the real "
        + "bottleneck sits."
      )
    );
  } else {
    children.push(
      el(
        "p",
        { className: "structure-note" },
        `${groups.length} group${groups.length === 1 ? "" : "s"} on this board. `
        + "Edit one from its header in the column list above."
      )
    );
  }

  if (orphans.length) {
    children.push(
      el(
        "ul",
        { className: "structure-list", role: "list" },
        orphans.flatMap((group) => buildGroupHeader(group, ordered))
      )
    );
  }

  return el("section", { className: "structure-section" }, children);
}

/** Create a group over a chosen, contiguous run of columns. */
function openGroupModal(ordered) {
  const selected = new Set();
  let color = null;

  const nameError = el("p", { className: "field-error", hidden: true }, "Give the group a name.");
  const spanError = el("p", { className: "field-error", hidden: true });
  let nameInput = null;
  let minInput = null;
  let maxInput = null;

  function validateSpan() {
    const ids = ordered.filter((c) => selected.has(c.id)).map((c) => c.id);
    const gap = contiguityGap(ordered, ids);
    if (gap) {
      spanError.hidden = false;
      spanError.textContent = gapMessage(gap);
      return null;
    }
    spanError.hidden = true;
    return ids;
  }

  const spanList = el(
    "div",
    { className: "structure-span-list", role: "group", "aria-label": "Columns in the new group" },
    ordered.map((column) => {
      const otherGroup = column.group_id ? groups.find((g) => g.id === column.group_id) : null;
      return el(
        "label",
        { className: "checkbox-row structure-span-item" },
        el("input", {
          type: "checkbox",
          className: "checkbox",
          onChange: (event) => {
            if (event.target.checked) selected.add(column.id);
            else selected.delete(column.id);
            validateSpan();
          },
        }),
        el("span", { className: "truncate" }, column.name),
        otherGroup ? el("span", { className: "t-xs subtle" }, `moves out of ${otherGroup.name}`) : null
      );
    })
  );

  const form = el(
    "div",
    { className: "structure-form-grid" },
    field({
      label: "Group name",
      control: el("input", {
        type: "text",
        className: "input",
        placeholder: "e.g. Delivery",
        maxLength: 500,
        "data-autofocus": "",
        ref: (n) => { nameInput = n; },
        onInput: () => { nameError.hidden = true; },
      }),
      below: nameError,
    }),
    field({
      label: "Group WIP limits",
      hint: "Optional. Applies across every column the group covers.",
      control: el(
        "div",
        { className: "structure-wip-row" },
        el("input", {
          type: "number", className: "input", min: 0, step: 1, inputMode: "numeric",
          placeholder: "min", "aria-label": "Group WIP minimum", ref: (n) => { minInput = n; },
        }),
        el("span", { className: "structure-wip-sep", "aria-hidden": "true" }, "–"),
        el("input", {
          type: "number", className: "input", min: 0, step: 1, inputMode: "numeric",
          placeholder: "max", "aria-label": "Group WIP maximum", ref: (n) => { maxInput = n; },
        })
      ),
    }),
    field({
      label: "Colour",
      control: colorPicker({ value: null, label: "New group colour", onChange: (value) => { color = value; } }),
    }),
    field({
      label: "Columns covered",
      span: 2,
      hint: "Pick a consecutive run. Columns already in another group move into this one.",
      control: spanList,
      below: spanError,
    })
  );

  const submitButton = el(
    "button",
    { type: "button", className: "btn btn-primary", onClick: () => submit() },
    icon("plus"),
    "Create group"
  );

  const dialog = modal({
    title: "New column group",
    size: "lg",
    body: form,
    footer: [
      el("button", { type: "button", className: "btn btn-secondary", onClick: () => dialog.close() }, "Cancel"),
      submitButton,
    ],
  });

  async function submit() {
    const name = (nameInput?.value || "").trim();
    if (!name) {
      nameError.hidden = false;
      nameInput?.focus();
      return;
    }
    const ids = validateSpan();
    if (ids === null) return;

    const min = readLimit(minInput);
    const max = readLimit(maxInput);
    if (min === undefined || max === undefined) {
      toast("WIP limits must be whole numbers, or empty for no limit.", { tone: "warning" });
      return;
    }
    if (min !== null && max !== null && min > max) {
      toast("The WIP minimum cannot be larger than the maximum.", { tone: "warning" });
      return;
    }

    submitButton.classList.add("is-loading");
    const ok = await mutate(
      () =>
        api.post(
          `/boards/${currentBoardId}/groups`,
          { name, wip_min: min, wip_max: max, color, column_ids: ids },
          { silent: true }
        ),
      { announcement: `Group ${name} created.` }
    );
    submitButton.classList.remove("is-loading");
    if (ok) {
      toast(`${name} created.`, { tone: "success" });
      dialog.close();
    }
  }
}

/* ==========================================================================
   WIP limits — one policy, therefore one write
   ========================================================================== */

/**
 * How long the pair waits for the next keystroke before writing itself.
 *
 * Long enough to swallow a tab between the two boxes and the digits typed after
 * it, short enough that a user who walks away from a half-finished edit still
 * finds it saved.
 */
const WIP_COMMIT_DELAY = 300;

function scheduleWipCommit(scope, commit) {
  cancelWipCommit(scope);
  const timer = window.setTimeout(() => {
    pendingWip.delete(scope);
    commit();
  }, WIP_COMMIT_DELAY);
  pendingWip.set(scope, { timer, commit });
}

function cancelWipCommit(scope) {
  const entry = pendingWip.get(scope);
  if (!entry) return;
  window.clearTimeout(entry.timer);
  pendingWip.delete(scope);
}

/** Write a scheduled edit now — because focus left the pair, or the panel did. */
function flushWipCommit(scope) {
  const entry = pendingWip.get(scope);
  if (!entry) return;
  window.clearTimeout(entry.timer);
  pendingWip.delete(scope);
  entry.commit();
}

function flushAllWipCommits() {
  [...pendingWip.keys()].forEach(flushWipCommit);
}

function cancelAllWipCommits() {
  [...pendingWip.keys()].forEach(cancelWipCommit);
}

/** The node currently carrying a focus key — never a stale one from a repaint. */
function liveField(key) {
  if (!bodyEl) return null;
  return bodyEl.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
}

/**
 * The min/max pair, its live verdict badge, and the single write behind both.
 *
 * **Minimum and maximum are one policy, so they are one PATCH.** Committing each
 * box on its own `change` looked equivalent and was not: leaving the min box
 * fired a write whose reconcile — a re-fetch and a repaint of the entire list —
 * arrived a couple of hundred milliseconds later and rebuilt the max box from
 * the server row that write had just created, wiping whatever the user was
 * typing into it, restoring focus to an empty field, with nothing said. Queuing
 * the requests never addressed that: the requests were never the problem, an
 * unrelated repaint landing on a field mid-edit was.
 *
 * So the commit is owned by the row. Any edit to either box schedules it, each
 * further keystroke restarts it, and focus leaving the pair flushes it — one
 * request carrying both numbers, read from whichever nodes are live at the
 * moment it fires (a repaint may have replaced them) and compared against the
 * board as it stands now (a repaint may have moved it on).
 *
 * @param {object} opts
 * @param {string} opts.scope Identity of the owning row, for the commit queue.
 * @param {() => object|null} opts.resolve The current server row, or null if it
 *   has since been deleted.
 * @param {(row: object, min: number|null, max: number|null) => any} opts.save
 * @returns {{control: Node, error: Node}}
 */
function wipLimitControl(opts) {
  const {
    scope, minKey, maxKey, minLabel, maxLabel, count, state, disabled, resolve, save,
  } = opts;

  const row = resolve();
  const startMin = row?.wip_min ?? null;
  const startMax = row?.wip_max ?? null;

  const badge = el(
    "span",
    { className: "badge", dataset: { wip: state || wipState(count, startMin, startMax) } },
    wipVerdictLabel(count, startMin, startMax)
  );
  const error = el("p", { className: "field-error", hidden: true });

  let minNode = null;
  let maxNode = null;

  function boxes() {
    return { min: liveField(minKey) || minNode, max: liveField(maxKey) || maxNode };
  }

  /**
   * Repaint the verdict from what the boxes say right now.
   * @returns {{min: number|null, max: number|null}|null} null when the pair is
   *   not a policy the server would accept, which is also what blocks the write.
   */
  function feedback() {
    const { min: minBox, max: maxBox } = boxes();
    if (!minBox || !maxBox) return null;

    const min = readLimit(minBox);
    const max = readLimit(maxBox);
    const unreadable = min === undefined || max === undefined;
    const inverted = !unreadable && min !== null && max !== null && min > max;

    error.hidden = !(unreadable || inverted);
    error.textContent = unreadable
      ? "A WIP limit is a whole number of 0 or more, or empty for no limit."
      : inverted
        ? "The minimum cannot be larger than the maximum."
        : "";

    if (unreadable || inverted) {
      // Not a range at all, so it must not be dressed as one: the badge drops
      // its WIP state — there is no state to report — and says what is wrong.
      delete badge.dataset.wip;
      badge.dataset.tone = "danger";
      badge.textContent = unreadable ? "Invalid limit" : "Invalid range";
      return null;
    }

    delete badge.dataset.tone;
    badge.dataset.wip = wipState(count, min, max);
    badge.textContent = wipVerdictLabel(count, min, max);
    return { min, max };
  }

  function commit() {
    const { min: minBox, max: maxBox } = boxes();
    if (!minBox || !maxBox) return;

    const values = feedback();
    // Refused: the boxes keep the user's numbers and their field error, so the
    // edit can be finished rather than silently reverted.
    if (!values) return;

    const current = resolve();
    if (!current) return;

    clearDirty(minBox, maxBox);
    if (values.min === (current.wip_min ?? null) && values.max === (current.wip_max ?? null)) return;
    save(current, values.min, values.max);
  }

  function onEdit() {
    feedback();
    scheduleWipCommit(scope, commit);
  }

  function box({ key, label, placeholder, value, ref }) {
    return el("input", {
      type: "number",
      className: "input",
      min: 0,
      step: 1,
      inputMode: "numeric",
      placeholder,
      "aria-label": label,
      value: value ?? "",
      disabled,
      "data-focus-key": key,
      ref,
      onInput: onEdit,
      onChange: onEdit,
      onKeyDown: (event) => { if (event.key === "Enter") event.target.blur(); },
    });
  }

  const control = el(
    "div",
    {
      className: "structure-wip-row",
      onFocusOut: (event) => {
        // Tabbing from min into max is one edit, not two — the pair is only
        // finished once focus has left both of its boxes.
        if (event.currentTarget.contains(event.relatedTarget)) return;
        flushWipCommit(scope);
      },
    },
    box({ key: minKey, label: minLabel, placeholder: "min", value: startMin, ref: (n) => { minNode = n; } }),
    el("span", { className: "structure-wip-sep", "aria-hidden": "true" }, "–"),
    box({ key: maxKey, label: maxLabel, placeholder: "max", value: startMax, ref: (n) => { maxNode = n; } }),
    badge
  );

  return { control, error };
}

/* ==========================================================================
   Field factories
   ========================================================================== */

/**
 * One labelled control.
 * @param {{label: string, control: Node, hint?: string, below?: Node, span?: number}} opts
 */
function field(opts) {
  const { label, control, hint, below, span } = opts;
  const id = `structure-field-${Math.random().toString(36).slice(2, 9)}`;

  // Label the control by id where it is a real form element, so the label is
  // announced with it rather than merely sitting nearby.
  if (control && control.tagName && ["INPUT", "SELECT", "TEXTAREA"].includes(control.tagName)) {
    control.id = id;
  }

  return el(
    "div",
    { className: "field structure-field", dataset: span ? { span: String(span) } : {} },
    el("label", { className: "label", ...(control?.id === id ? { htmlFor: id } : {}) }, label),
    control,
    hint ? el("p", { className: "hint" }, hint) : null,
    below || null
  );
}

/** Swatch row + native picker + a "no colour" escape. */
function colorPicker({ value, onChange, disabled = false, focusKey, label }) {
  const host = el("div", { className: "structure-colors", role: "group", "aria-label": label || "Colour" });

  function paintSelection(next) {
    host.querySelectorAll(".structure-swatch-btn").forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.dataset.color === (next || "") ? "true" : "false");
    });
  }

  const buttons = [
    el(
      "button",
      {
        type: "button",
        className: "structure-swatch-btn structure-swatch-none",
        dataset: { color: "" },
        "aria-pressed": value ? "false" : "true",
        "aria-label": "No colour",
        "data-tooltip": "No colour",
        disabled,
        ...(focusKey ? { "data-focus-key": `${focusKey}:none` } : {}),
        onClick: () => { paintSelection(null); onChange(null); },
      }
    ),
    ...SWATCHES.map(([hex, name]) =>
      el("button", {
        type: "button",
        className: "structure-swatch-btn",
        style: { "--swatch": hex },
        dataset: { color: hex },
        "aria-pressed": value === hex ? "true" : "false",
        "aria-label": name,
        "data-tooltip": name,
        disabled,
        onClick: () => { paintSelection(hex); onChange(hex); },
      })
    ),
  ];

  host.append(
    ...buttons,
    el("input", {
      type: "color",
      className: "structure-color-input",
      value: value || "#0F7B86",
      disabled,
      "aria-label": "Custom colour",
      "data-tooltip": "Custom colour",
      ...(focusKey ? { "data-focus-key": `${focusKey}:custom` } : {}),
      onChange: (event) => { paintSelection(event.target.value); onChange(event.target.value); },
    })
  );

  return host;
}

/* ==========================================================================
   Ordering & small helpers
   ========================================================================== */

/**
 * Flow order, mirroring the backend's `_column_sort_key`: upstream before
 * downstream, an envelope column always first, then dense `position`.
 */
function orderColumns(list) {
  const rank = { upstream: 0, downstream: 1 };
  return list
    .map((column, i) => ({ column, i }))
    .sort((a, b) => {
      const segA = rank[a.column.segment] ?? 1;
      const segB = rank[b.column.segment] ?? 1;
      if (segA !== segB) return segA - segB;
      const envA = a.column.kind === "envelope" ? 0 : 1;
      const envB = b.column.kind === "envelope" ? 0 : 1;
      if (envA !== envB) return envA - envB;
      const posA = Number(a.column.position) || 0;
      const posB = Number(b.column.position) || 0;
      if (posA !== posB) return posA - posB;
      return a.i - b.i;
    })
    .map((entry) => entry.column);
}

/**
 * Where a column lands, mirroring the backend's `_placement_order`: the index is
 * clamped into the column's own segment, and an envelope column is forced to the
 * front. This is what makes the "resulting flow" preview truthful rather than
 * optimistic.
 */
function placeDraft(ordered, draft, targetIndex) {
  const others = ordered.filter((c) => c.id !== draft.id);
  const upstreamCount = others.filter((c) => c.segment === "upstream").length;
  const hasEnvelope = others.some((c) => c.kind === "envelope");

  let index;
  if (draft.kind === "envelope") {
    index = 0;
  } else {
    const low = draft.segment === "upstream" ? (hasEnvelope ? 1 : 0) : upstreamCount;
    const high = draft.segment === "upstream" ? upstreamCount : others.length;
    index = targetIndex === null || targetIndex === undefined
      ? high
      : Math.max(low, Math.min(Number(targetIndex), high));
  }

  const out = others.slice();
  out.splice(index, 0, draft);
  return out;
}

/** The order the board *will* have once `patch` lands on `columnId`. */
function speculate(columnId, patch) {
  const next = columns.map((c) => (c.id === columnId ? { ...c, ...patch } : c));
  const ordered = orderColumns(next);
  if (!("segment" in patch) && !("kind" in patch)) return ordered;
  const moved = ordered.find((c) => c.id === columnId);
  if (!moved) return ordered;
  // A segment or kind change re-places the column at the end of its (new)
  // segment, exactly as `update_column` does with `target_position = None`.
  return placeDraft(ordered, moved, null);
}

/** Local mirror of the backend's `wip_state`, for live feedback while typing. */
function wipState(count, min, max) {
  if (max !== null && max !== undefined && count > max) return "over";
  if (max !== null && max !== undefined && count === max) return "at_limit";
  if (min !== null && min !== undefined && count < min) return "under";
  return "ok";
}

/**
 * The policy itself: "3–14", "≤ 6", "≥ 3", or "No limit".
 *
 * `9–2` is not a policy, it is a typo mid-correction. Formatting it as a range
 * dresses a value the server will refuse as a working limit, so an inverted
 * pair says so instead — the field error underneath explains it, the badge only
 * has to stop agreeing with it.
 */
function wipRangeLabel(min, max) {
  const hasMin = min !== null && min !== undefined;
  const hasMax = max !== null && max !== undefined;
  if (hasMin && hasMax) return min > max ? "Invalid range" : `${min}–${max}`;
  if (hasMax) return `≤ ${max}`;
  if (hasMin) return `≥ ${min}`;
  return "No limit";
}

/** SPEC §5 `state`, in words rather than an enum value. */
const WIP_STATE_LABEL = {
  under: "Starving",
  ok: "Within limits",
  at_limit: "At limit",
  over: "Over limit",
};

/**
 * The live verdict shown beside the steppers (SPEC-v2 §R2.2): the *state* the
 * cell would be in with these limits, so a user can see they are about to put
 * a column over its own policy before they leave the field.
 */
function wipVerdictLabel(count, min, max) {
  const range = wipRangeLabel(min, max);
  if (range === "No limit") return "No limit set";
  // There is no state to report against limits that cannot hold: `wipState`
  // would answer "Over limit", which reads as a fact about the board rather
  // than about the two numbers currently in the boxes.
  if (range === "Invalid range") return range;
  return `${WIP_STATE_LABEL[wipState(count, min, max)]} · ${range}`;
}

/**
 * Read a WIP box.
 * @returns {number|null|undefined} the value, `null` for "no limit", or
 *   `undefined` when the box holds something that is not a whole number.
 */
function readLimit(input) {
  if (!input) return null;
  const raw = String(input.value || "").trim();
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0) return undefined;
  return Math.floor(value);
}

/** Columns that sit inside a selected run but are not part of it. */
function contiguityGap(ordered, selectedIds) {
  if (selectedIds.length < 2) return null;
  const indexes = selectedIds
    .map((id) => ordered.findIndex((c) => c.id === id))
    .filter((i) => i >= 0);
  if (indexes.length < 2) return null;
  const lo = Math.min(...indexes);
  const hi = Math.max(...indexes);
  const missing = [];
  for (let i = lo; i <= hi; i += 1) {
    if (!indexes.includes(i)) missing.push(ordered[i]);
  }
  return missing.length ? missing : null;
}

/** "A", "A and B", "A, B and C". */
function listPhrase(names) {
  if (!names.length) return "";
  if (names.length === 1) return `“${names[0]}”`;
  const quoted = names.map((n) => `“${n}”`);
  return `${quoted.slice(0, -1).join(", ")} and ${quoted[quoted.length - 1]}`;
}

/** Scroll to and reveal the column a caller asked to edit. */
function focusPendingColumn() {
  if (!pendingFocusColumnId || !bodyEl) return;
  const row = bodyEl.querySelector(`.structure-row[data-column-id="${CSS.escape(pendingFocusColumnId)}"]`);
  pendingFocusColumnId = null;
  if (!row) return;
  row.scrollIntoView({ block: "center" });
  const nameInput = row.querySelector("input[type='text']");
  if (nameInput) nameInput.focus();
}
