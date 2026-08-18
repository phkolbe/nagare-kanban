/**
 * Nagare — board view (SPEC §9.2, Req. 1/2/3/5/9 + SPEC-v2 §R2.2/§R2.3).
 *
 * `GET /boards/{id}/view` is the one call this view makes for its data. Column
 * and group *structure* editing lives in structure.js, which this file only
 * ever opens (lazily, so a missing module degrades to a toast instead of a
 * blank board); WIP *limits* are edited in place, from the badges themselves.
 *
 * Layout: see the block comment at the top of board.css for the grid model.
 * There are two of them, and only one is live at a time:
 *
 *   flat (default)  — three grid rows; every card list scrolls independently,
 *                     so column headers stay put with no `position: sticky`.
 *   swimlanes       — one grid row per lane band; the whole grid scrolls in
 *                     both axes and the ribbon, group, column-header and lane
 *                     rows are sticky. Lanes are a *view*: crossing one never
 *                     changes a card's class of service.
 *
 * State model: everything the render needs lives in module-level variables,
 * rebuilt from the server on `reload()` and mutated in place by drag/keyboard
 * moves and drawer edits (via the `ctx` contracts below), each mutation ending
 * in a full `paint()`. The board is small enough (dozens of columns, hundreds
 * of cards) that a full rebuild is simpler and safer than incremental DOM
 * patching, and — because every mutating call site awaits past the point
 * where a "drop" event would still be dispatching — it never tears a live
 * drag operation out from under the browser.
 */

import api from "./api.js";
import store from "./store.js";
import {
  el, icon, render, avatar, menu, skeleton, emptyState, errorState, toast,
  relativeTime, formatDate, formatDuration, announce,
} from "./ui.js";
import filters from "./filters.js";
import dnd from "./dnd.js";
import { openCardDrawer, closeCardDrawer, openCreateCardModal, columnKindMeta, SERVICE_CLASS_MAP } from "./card.js";

/* ==========================================================================
   Module state — reset on every mount()
   ========================================================================== */

let rootEl = null;
let mounted = false;
let boardId = null;

let board = null;                 // the view's board row (name, confirm_moves, sle...)
let columns = [];                 // currently visible columns (per segment), flow order
let groups = [];                  // currently visible groups
let visibleColumnIndex = new Map();
let wip = null;                   // {columns:{id:cell}, groups:{id:cell}, flow:cell}
let commitmentColumnId = null;
let deliveryColumnId = null;

let structureColumns = [];        // EVERY column of the board (all segments), for
let structureIndex = new Map();   // forward-only math and the keyboard move menu

let cardsById = new Map();
let columnCards = new Map();      // columnId -> [cardId] in server position order

let projectsList = [];
let peopleList = [];

let collapsedSet = new Set();
let unsubscribers = [];
let loadToken = 0;

let scrollEl = null;
let columnElById = new Map();
let didRevealRail = false; // the commitment rail is auto-revealed once per mount

let laneMode = "off";        // "off" | "sc" — swimlanes by class of service
let collapsedLanes = new Set();
let wipEditorHandle = null;  // the open WIP popover, so a repaint can close it
let onResize = null;

/* ==========================================================================
   Lifecycle
   ========================================================================== */

export async function mount(container, params) {
  rootEl = container;
  mounted = true;
  boardId = params?.boardId || null;
  resetState();

  if (!boardId || boardId === "none") {
    render(
      rootEl,
      el(
        "div",
        { className: "board-view" },
        emptyState({
          icon: "board",
          title: "No boards yet",
          body: store.can("manage_boards")
            ? "Create a board to start tracking flow."
            : "Ask an administrator to create the first board.",
        })
      )
    );
    return;
  }

  loadCollapsed(boardId);
  loadLaneState(boardId, params?.search);
  filters.mount();
  paintSkeleton();

  // store.writeFiltersToUrl() rebuilds the hash query from the filter set
  // alone, so `?lanes=sc` has to be stamped back on after every filter or
  // segment change or a shared lane view would evaporate on the first click.
  unsubscribers = [
    store.select((s) => s.filters, () => { syncLanesUrl(); reload(); }),
    store.select((s) => s.segment, () => { syncLanesUrl(); reload(); }),
  ];

  onResize = () => syncLaneMetrics();
  window.addEventListener("resize", onResize, { passive: true });

  syncLanesUrl();
  await Promise.all([loadStructure(), loadDirectories(), reload({ initial: true })]);
}

export function unmount() {
  mounted = false;
  for (const unsub of unsubscribers) unsub();
  unsubscribers = [];
  if (onResize) { window.removeEventListener("resize", onResize); onResize = null; }
  closeWipEditor();
  filters.unmount();
  closeCardDrawer();
  rootEl = null;
  scrollEl = null;
  columnElById = new Map();
}

/**
 * A query-string-only navigation (a pasted link into the open tab). Filters are
 * already in the store by the time the router calls this; only the lane view
 * needs picking up.
 */
export function onQuery(params) {
  const requested = params?.search?.get?.("lanes");
  const next = requested === "sc" ? "sc" : requested === "off" ? "off" : laneMode;
  if (next === laneMode) return;
  laneMode = next;
  saveLaneState();
  paint();
}

function resetState() {
  board = null; columns = []; groups = []; wip = null;
  commitmentColumnId = null; deliveryColumnId = null;
  visibleColumnIndex = new Map();
  structureColumns = []; structureIndex = new Map();
  cardsById = new Map(); columnCards = new Map();
  projectsList = []; peopleList = [];
  collapsedSet = new Set();
  laneMode = "off"; collapsedLanes = new Set();
  closeWipEditor();
  scrollEl = null; columnElById = new Map();
  didRevealRail = false;
  loadToken += 1; // orphan any reload() still in flight from a previous mount
}

/* ==========================================================================
   Data loading
   ========================================================================== */

async function loadDirectories() {
  const [projectsRes, peopleRes] = await Promise.all([
    api.get("/projects", { query: { archived: false }, silent: true, cache: 30000 }).catch(() => null),
    api.get("/users", { query: { is_active: true }, silent: true, cache: 30000 }).catch(() => null),
  ]);
  projectsList = Array.isArray(projectsRes?.items) ? projectsRes.items : [];
  peopleList = Array.isArray(peopleRes?.items) ? peopleRes.items : [];
}

/** The board's full, unsegmented column list — for forward-only math beyond
 * whatever segment is currently on screen. Cheap: no cards, no WIP. */
async function loadStructure() {
  try {
    const data = await api.get(`/boards/${boardId}`, { silent: true });
    structureColumns = Array.isArray(data?.columns) ? data.columns : [];
    structureIndex = new Map(structureColumns.map((c) => [c.id, c]));
  } catch {
    structureColumns = [];
    structureIndex = new Map();
  }
}

/**
 * Query sent to `/boards/{id}/view`. `service_class` is deliberately omitted:
 * that endpoint accepts one value, not the list the filter bar's multi-select
 * needs, so it is applied as a client-side post-filter in `applyViewData()`
 * instead of narrowing the request.
 */
function buildViewQuery() {
  const f = store.get().filters;
  const query = { segment: store.get().segment };
  if (f.project_id) query.project_id = f.project_id;
  if (f.assignee_id) query.assignee_id = f.assignee_id;
  if (f.tag) query.tag = f.tag;
  if (f.q) query.q = f.q;
  if (f.is_blocked !== null) query.is_blocked = f.is_blocked;
  if (f.demand_type) query.demand_type = f.demand_type;
  return query;
}

function filterCardsClientSide(cards) {
  const sc = store.get().filters.service_class;
  if (!sc.length) return cards;
  return cards.filter((card) => sc.includes(card.service_class));
}

function collectTags(cards) {
  const set = new Set();
  for (const card of cards) for (const tag of card.tags || []) set.add(tag);
  return Array.from(set);
}

async function reload({ initial = false } = {}) {
  if (!boardId || boardId === "none") return;
  const token = ++loadToken;
  if (initial) paintSkeleton(); else setBoardBusy(true);

  try {
    const data = await api.get(`/boards/${boardId}/view`, { query: buildViewQuery(), silent: true });
    if (token !== loadToken || !mounted) return;
    applyViewData(data);
    setBoardBusy(false);
    paint();
  } catch (error) {
    if (token !== loadToken || !mounted) return;
    setBoardBusy(false);
    if (initial) {
      render(rootEl, errorState(error, () => reload({ initial: true }), { title: "The board could not be loaded" }));
    } else {
      toast(error?.message || "Could not refresh the board.", { tone: "danger", title: "Refresh failed" });
    }
  }
}

function applyViewData(data) {
  board = data.board;
  columns = data.columns || [];
  groups = data.groups || [];
  wip = data.wip || { columns: {}, groups: {}, flow: {} };
  commitmentColumnId = data.commitment_column_id || null;
  deliveryColumnId = data.delivery_column_id || null;
  visibleColumnIndex = new Map(columns.map((c) => [c.id, c]));

  const allCards = data.cards || [];
  const shown = filterCardsClientSide(allCards);

  cardsById = new Map();
  columnCards = new Map();
  for (const column of columns) columnCards.set(column.id, []);
  for (const card of shown) {
    cardsById.set(card.id, card);
    pushIdToColumn(card.column_id, card.id);
  }

  filters.setAvailableTags(collectTags(allCards));
  filters.setResultCount(shown.length, allCards.length);
}

/* ==========================================================================
   Model mutation helpers — shared by ctx.applyCardPatch / dnd.js's ctx
   ========================================================================== */

function pushIdToColumn(columnId, cardId) {
  if (!columnCards.has(columnId)) columnCards.set(columnId, []);
  const list = columnCards.get(columnId);
  if (!list.includes(cardId)) list.push(cardId);
}

function removeIdFromColumn(columnId, cardId) {
  const list = columnCards.get(columnId);
  if (!list) return;
  const index = list.indexOf(cardId);
  if (index >= 0) list.splice(index, 1);
}

function wipStateFor(count, min, max) {
  if (max != null && count > max) return "over";
  if (max != null && count === max) return "at_limit";
  if (min != null && count < min) return "under";
  return "ok";
}

/** Nudge a column's WIP cell so the badge feels live before the network confirms it. */
function bumpWip(columnId, delta) {
  const cell = wip?.columns?.[columnId];
  if (!cell) return;
  cell.count = Math.max(0, cell.count + delta);
  cell.state = wipStateFor(cell.count, cell.min, cell.max);
}

function allColumnsList() {
  return structureColumns.length ? structureColumns : columns;
}

function columnLookup(id) {
  return structureIndex.get(id) || visibleColumnIndex.get(id) || null;
}

function canEditBoard() {
  if (!board) return false;
  const role = store.get().user?.role;
  if (board.kind === "simulator") return ["master", "administrator", "simulator"].includes(role);
  if (["master", "administrator"].includes(role)) return true;
  if (["editor", "system"].includes(role)) return board.kind === "operational";
  return false;
}

/**
 * May this user reshape the board — columns, groups and WIP policies?
 *
 * `manage_boards` (master/administrator) per SPEC §4, plus the one exception
 * the backend's `require_structure_write()` makes: a `simulator` owns the
 * structure of simulator boards. Mirroring the server exactly means a control
 * is never offered that would come back 403.
 */
function canManageStructure() {
  if (!board) return false;
  if (store.get().user?.role === "simulator") return board.kind === "simulator";
  return store.can("manage_boards");
}

/** dnd.js calls this before the network round trip (drag drop / keyboard move). */
function moveCardLocally(cardId, toColumnId) {
  const card = cardsById.get(cardId);
  if (!card) return null;
  const fromColumnId = card.column_id;
  removeIdFromColumn(fromColumnId, cardId);
  card.column_id = toColumnId;
  pushIdToColumn(toColumnId, cardId);
  bumpWip(fromColumnId, -1);
  bumpWip(toColumnId, +1);
  paint();
  return fromColumnId;
}

/** dnd.js calls this when the API rejects a move it had already applied optimistically. */
function rollbackMove(cardId, fromColumnId) {
  const card = cardsById.get(cardId);
  if (!card) return;
  const currentColumnId = card.column_id;
  removeIdFromColumn(currentColumnId, cardId);
  card.column_id = fromColumnId;
  pushIdToColumn(fromColumnId, cardId);
  bumpWip(currentColumnId, -1);
  bumpWip(fromColumnId, +1);
  paint();
}

/** dnd.js calls this once the move succeeds — the server's card and WIP win. */
function applyMoveResult(result) {
  if (!result?.card) return;
  const card = result.card;
  const previousColumnId = cardsById.has(card.id) ? cardsById.get(card.id).column_id : card.column_id;
  cardsById.set(card.id, card);
  removeIdFromColumn(previousColumnId, card.id);
  pushIdToColumn(card.column_id, card.id);
  if (result.wip) wip = result.wip;
  paint();
}

/** card.js's drawer calls this after any field edit that is not a move. */
function applyCardPatch(card) {
  if (!card?.id) return;
  const existing = cardsById.get(card.id);
  const merged = existing ? { ...existing, ...card } : card;
  cardsById.set(card.id, merged);
  if (existing && existing.column_id !== merged.column_id) {
    removeIdFromColumn(existing.column_id, card.id);
    pushIdToColumn(merged.column_id, card.id);
  } else if (!existing) {
    pushIdToColumn(merged.column_id, card.id);
  }
  paint();
}

function removeCardFromModel(id) {
  const card = cardsById.get(id);
  if (!card) return;
  removeIdFromColumn(card.column_id, id);
  cardsById.delete(id);
  bumpWip(card.column_id, -1);
  paint();
}

/* ==========================================================================
   Context objects handed to card.js (the drawer) and dnd.js (drag/keyboard)
   ========================================================================== */

const boardCtx = {
  columnById: columnLookup,
  allColumns: allColumnsList,
  projects: () => projectsList,
  people: () => peopleList,
  canEdit: canEditBoard,
  applyCardPatch,
  removeCard: removeCardFromModel,
  moveCard: (id, toColumnId) => dnd.moveCard(id, toColumnId, dndCtx),
  refresh: (opts) => reload(opts),
};

const dndCtx = {
  getBoard: () => board,
  patchBoardConfirm: async (value) => {
    const updated = await api.patch(`/boards/${boardId}`, { confirm_moves: value }, { silent: true });
    board = { ...board, ...updated };
  },
  getCardById: (id) => cardsById.get(id),
  columnById: columnLookup,
  visibleColumns: () => columns,
  allColumns: allColumnsList,
  getColumnEl: (id) => columnElById.get(id),
  wipCellFor: (id) => wip?.columns?.[id] || null,
  canEdit: canEditBoard,
  moveCardLocally,
  rollbackMove,
  applyMoveResult,
};

/* ==========================================================================
   Column collapse — persisted per board
   ========================================================================== */

function collapsedStorageKey(id) {
  return `nagare.board.collapsed.${id}`;
}

function loadCollapsed(id) {
  try {
    const raw = localStorage.getItem(collapsedStorageKey(id));
    collapsedSet = raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    collapsedSet = new Set();
  }
}

function saveCollapsed() {
  try {
    localStorage.setItem(collapsedStorageKey(boardId), JSON.stringify(Array.from(collapsedSet)));
  } catch { /* private mode — the choice lasts for this render only */ }
}

function toggleCollapsed(columnId) {
  if (collapsedSet.has(columnId)) collapsedSet.delete(columnId);
  else collapsedSet.add(columnId);
  saveCollapsed();
  paint();
}

function setAllCollapsed(collapse) {
  collapsedSet = collapse ? new Set(columns.map((c) => c.id)) : new Set();
  saveCollapsed();
  paint();
}

/* ==========================================================================
   Swimlane preference (SPEC-v2 §R2.3) — per user, per board, and shareable
   --------------------------------------------------------------------------
   `localStorage` holds the durable preference (mode + which lanes are rolled
   up); the URL holds the *shareable* half of it, so pasting a board link with
   `?lanes=sc` hands the recipient the same view without touching their own
   stored default beyond this board.
   ========================================================================== */

/** Lanes in fixed Kanban priority order. Labels come from card.js so the two
 * surfaces can never drift apart. */
const LANE_ORDER = Object.freeze(["expedite", "fixed_date", "standard", "intangible"]);

/** Why an empty lane is worth a strip of its own rather than being hidden. */
const LANE_EMPTY_COPY = Object.freeze({
  expedite: "No expedite work in flight — nothing is jumping the queue.",
  fixed_date: "No fixed-date work in flight — no external date is at risk.",
  standard: "No standard work in flight — the system is not pulling its normal demand.",
  intangible: "No intangible work in flight — no slack is going to debt or learning.",
});

const LANE_IDS = new Set(LANE_ORDER);

function laneStorageKey(id) {
  return `nagare.lanes.${id}`;
}

/** Which lane a card belongs to. An unrecognised class falls into standard —
 * the schema's own default — rather than vanishing from every lane. */
function laneIdOf(card) {
  const sc = card?.service_class;
  return LANE_IDS.has(sc) ? sc : "standard";
}

function lanesActive() {
  return laneMode === "sc" && store.hasFlag("service_classes");
}

function loadLaneState(id, search) {
  let stored = null;
  try {
    const raw = localStorage.getItem(laneStorageKey(id));
    stored = raw ? JSON.parse(raw) : null;
  } catch {
    stored = null;
  }
  laneMode = stored?.mode === "sc" ? "sc" : "off";
  collapsedLanes = new Set(
    Array.isArray(stored?.collapsed) ? stored.collapsed.filter((lane) => LANE_IDS.has(lane)) : []
  );

  // A link wins over the stored preference: the sender chose the view.
  const fromUrl = search?.get?.("lanes");
  if (fromUrl === "sc") laneMode = "sc";
  else if (fromUrl === "off") laneMode = "off";
}

function saveLaneState() {
  try {
    localStorage.setItem(
      laneStorageKey(boardId),
      JSON.stringify({ mode: laneMode, collapsed: Array.from(collapsedLanes) })
    );
  } catch { /* private mode — the choice lasts for this session only */ }
}

/** Keep `?lanes=sc` in the address bar without disturbing the filter params. */
function syncLanesUrl() {
  const hash = window.location.hash.replace(/^#/, "") || "/";
  const [path, search = ""] = hash.split("?");
  const params = new URLSearchParams(search);
  if (laneMode === "sc") params.set("lanes", "sc");
  else params.delete("lanes");

  const qs = params.toString();
  const next = `#${path}${qs ? `?${qs}` : ""}`;
  // replaceState, like store.writeFiltersToUrl: changing a view option is not
  // a navigation, and it must not fire hashchange and remount the board.
  if (next !== window.location.hash) window.history.replaceState(window.history.state, "", next);
}

function setLaneMode(mode) {
  const next = mode === "sc" ? "sc" : "off";
  if (next === laneMode) return;
  laneMode = next;
  saveLaneState();
  syncLanesUrl();
  paint();
  announce(
    next === "sc"
      ? "Swimlanes on, grouped by class of service."
      : "Swimlanes off. The board is showing one flat flow."
  );
}

function toggleLane(laneId) {
  if (collapsedLanes.has(laneId)) collapsedLanes.delete(laneId);
  else collapsedLanes.add(laneId);
  saveLaneState();
  paint();
  const label = SERVICE_CLASS_MAP[laneId]?.label || laneId;
  announce(collapsedLanes.has(laneId) ? `${label} lane collapsed.` : `${label} lane expanded.`);
}

/* ==========================================================================
   Rendering — skeleton, error/empty, and the live grid
   ========================================================================== */

function paintSkeleton() {
  render(
    rootEl,
    el(
      "div",
      { className: "board-view" },
      el("div", { className: "board-toolbar" }, skeleton({ width: 160, height: 20, radius: "var(--radius-md)" })),
      el(
        "div",
        { className: "board-scroll-wrap" },
        el(
        "div",
        { className: "board-scroll" },
        el(
          "div",
          { className: "board-grid", style: { gridTemplateColumns: "repeat(5, 272px)" } },
          Array.from({ length: 5 }, () =>
            el(
              "div",
              { className: "board-col" },
              el(
                "div",
                { className: "board-col-skeleton" },
                skeleton({ height: 16, width: "55%" }),
                skeleton({ height: 64, radius: "var(--radius-md)" }),
                skeleton({ height: 64, radius: "var(--radius-md)" }),
                skeleton({ height: 64, radius: "var(--radius-md)" })
              )
            )
          )
        )
        )
      )
    )
  );
}

function setBoardBusy(busy) {
  if (!rootEl) return;
  const existing = rootEl.querySelector(":scope > .route-progress");
  if (busy && !existing) rootEl.prepend(el("div", { className: "route-progress", role: "presentation" }));
  else if (!busy && existing) existing.remove();
}

/**
 * "current · min–max" for every WIP badge on the board.
 *
 * One grammar, always. Columns without a minimum used to switch to "N/max", so two
 * notations for the same idea sat side by side in a single header row and the eye had
 * to re-parse each column instead of pattern-matching one shape. An en dash stands in
 * for an absent bound.
 */
function wipLabel(cell) {
  if (!cell) return "";
  const { count, min, max } = cell;
  if (min == null && max == null) return String(count);
  const lower = min == null ? "–" : String(min);
  const upper = max == null ? "–" : String(max);
  return `${count} · ${lower}–${upper}`;
}

function updateEdgeShadows() {
  if (!scrollEl) return;
  // The edges live in `.board-scroll-wrap`, not in the scroller itself: in lane
  // mode the scroller also scrolls vertically, and an absolutely positioned
  // child of a scroll container travels with its content.
  const wrap = scrollEl.parentElement;
  const left = wrap?.querySelector(".board-edge-left");
  const right = wrap?.querySelector(".board-edge-right");
  if (!left || !right) return;
  left.classList.toggle("is-visible", scrollEl.scrollLeft > 4);
  right.classList.toggle("is-visible", scrollEl.scrollLeft + scrollEl.clientWidth < scrollEl.scrollWidth - 4);
}

function prefersReducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

/**
 * The commitment point is only "unmistakable" (Req. 1) if it is discoverable
 * without scrolling. `.board-edge-*` already hints more content exists off
 * screen, but that hint is generic — it says nothing about *why* the far side
 * matters. This paints an always-visible pill in the toolbar (never scrolls
 * away with the columns) that names which side of the commitment point the
 * viewport is currently showing, so a user who never scrolls still learns the
 * board has an upstream/downstream split, and one who does scroll gets a live
 * answer to "which side am I on".
 */
function updateRailIndicator() {
  const indicator = rootEl?.querySelector(".board-position-indicator");
  if (!indicator || !scrollEl) return;
  const railNode = scrollEl.querySelector(".board-rail");
  if (!railNode) return;

  const viewport = scrollEl.getBoundingClientRect();
  const rail = railNode.getBoundingClientRect();
  const region = rail.right <= viewport.left ? "downstream" : rail.left >= viewport.right ? "upstream" : "boundary";

  if (indicator.dataset.region === region) return;
  indicator.dataset.region = region;
  const meta = RAIL_REGION_META[region];
  render(indicator, icon(meta.icon), el("span", { className: "board-position-text" }, meta.label));
}

const RAIL_REGION_META = {
  upstream: { icon: "search", label: "Viewing upstream — before commitment" },
  boundary: { icon: "target", label: "At the commitment point" },
  downstream: { icon: "check", label: "Viewing downstream — committed" },
};

/**
 * On first paint of a Unified board, scroll just far enough that the
 * commitment rail sits in view with a neighbour column on each side, so the
 * upstream/downstream split is legible before the user touches the scrollbar.
 * Fires at most once per mount and never fights a scroll position the user
 * already chose (e.g. after a reload triggered by a filter change).
 */
function revealCommitmentRailOnce() {
  if (didRevealRail || !scrollEl) return;
  const railNode = scrollEl.querySelector(".board-rail");
  if (!railNode) return;
  didRevealRail = true;

  const viewportWidth = scrollEl.clientWidth;
  const railCenter = railNode.offsetLeft + railNode.offsetWidth / 2;
  const maxScroll = Math.max(0, scrollEl.scrollWidth - viewportWidth);
  const target = Math.min(maxScroll, Math.max(0, railCenter - viewportWidth / 2));

  if (target > 0) {
    scrollEl.scrollTo({ left: target, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }
}

function onBoardScroll() {
  updateEdgeShadows();
  updateRailIndicator();
}

/* ==========================================================================
   Grid geometry
   --------------------------------------------------------------------------
   Both layouts share the same *column* tracks; they differ only in rows.

   flat   rows: [ribbon] [groups] [columns]        (the column owns its scroll)
   lanes  rows: [ribbon] [groups] [col headers]
                then, per lane, [lane header] (+ [lane body] when it has cards)
                then one `1fr` filler so the column tint reaches the bottom.

   In lane mode the ribbon, group and column headers span from their own row to
   the end of the grid with `align-self: start`: they must stay put for the
   whole scroll, so being clipped to the grid is exactly right for them.

   A LANE header must not. It belongs to one band and has to leave with it, so
   it is not a grid item at all — `buildLaneHead()` wraps it in a `.board-lane`
   band that spans that lane's rows, and the sticky header lives inside. Its
   sticky travel is then bounded by its own band's box rather than by the grid,
   which is what makes it release instead of piling up at the top.

   The rows these headers sit in have to be fixed-height (they cannot be
   `auto`, or the grid would give a spanning item's height to the rows below and
   collapse them); `syncLaneMetrics()` measures the real heights after paint and
   corrects the custom properties, so nothing depends on a hard-coded guess.
   ========================================================================== */

/** The flat board's fixed three-row plan. */
const FLAT_ROWS = Object.freeze({
  lanes: null,
  template: null, // board.css declares grid-template-rows for the flat grid
  ribbon: [1, 2],
  group: [2, 3],
  header: [3, 4],
  rail: [1, 4],
});

function rowArea(pair) {
  return `${pair[0]} / ${pair[1]}`;
}

/**
 * How tall one lane's body row should be.
 *
 * `minmax(auto, cap)` gave every band the same growth limit, and a grid sized
 * against indefinite available space grows every track straight to its growth
 * limit — so a fifteen-card expedite lane and a one-hundred-and-forty-card
 * standard lane both came out at exactly the cap, the sparse one padded with
 * empty cells. That destroys the only thing swimlanes are for: comparing the
 * classes against each other.
 *
 * So the band is sized from its own volume instead, bounded at both ends: a
 * floor so a one-card lane is still a band, and the shared cap so one dense
 * column cannot push the lanes below it off the screen (past the cap the cells
 * scroll internally, exactly as the flat board's columns do). The card-count
 * estimate is what the first frame uses; `measureLaneBands()` replaces it in
 * the same frame with a height measured from the lane's own cards and weighted
 * against the other lanes, so nothing rests on a guess about card height.
 */
function laneBandTrack(lane) {
  const estimate = `calc(${lane.busiest} * var(--board-lane-card-h) + var(--board-lane-cell-pad))`;
  return `clamp(var(--board-lane-min-h), var(--board-lane-h-${lane.id}, ${estimate}), var(--board-lane-max-h))`;
}

/** Row plan for the lane grid. `lanes` is the model from `buildLaneModel()`. */
function buildLaneRows(lanes) {
  const sizes = [];
  const add = (size) => { sizes.push(size); return sizes.length; };

  const ribbon = add("var(--board-ribbon-h)");
  const group = add("var(--board-group-h)");
  const header = add("var(--board-colhead-h)");
  const laneRows = lanes.map((lane) => ({
    lane,
    head: add("var(--board-lane-head-h)"),
    body: lane.open ? add(laneBandTrack(lane)) : null,
  }));
  add("1fr"); // filler: keeps the column tint and hairlines running to the edge

  return {
    lanes: laneRows,
    template: sizes.join(" "),
    ribbon: [ribbon, -1],
    group: [group, -1],
    header: [header, -1],
    rail: [1, -1],
  };
}

/**
 * Measure what the sticky rows actually need and write it back as custom
 * properties. Cheap (three reads), idempotent, and it runs inside the same
 * animation frame as the paint, so the corrected geometry is never seen
 * mid-flight.
 */
function syncLaneMetrics() {
  const grid = rootEl?.querySelector(".board-grid.has-lanes");
  if (!grid) return;

  const measure = (selector, prop) => {
    const node = grid.querySelector(selector);
    if (!node) return;
    const height = Math.ceil(node.getBoundingClientRect().height);
    if (!height) return;
    if (grid.style.getPropertyValue(prop) !== `${height}px`) {
      grid.style.setProperty(prop, `${height}px`);
    }
  };

  if (grid.querySelector(".board-group")) measure(".board-group", "--board-group-h");
  measure(".board-col:not(.is-collapsed) > .board-col-header", "--board-colhead-h");
  measureLaneBands(grid);
}

/**
 * The height a cell's cards occupy, independent of the height the cell has
 * been *given*. `scrollHeight` cannot answer this — on a cell with room to
 * spare it reports the cell's own height, so feeding it back into the track
 * size would ratchet a band up and never let it shrink again.
 */
function cellContentHeight(cell) {
  const kids = cell.children;
  if (!kids.length) return 0;
  const first = kids[0].getBoundingClientRect();
  const last = kids[kids.length - 1].getBoundingClientRect();
  const styles = window.getComputedStyle(cell);
  const padding = (Number.parseFloat(styles.paddingTop) || 0) + (Number.parseFloat(styles.paddingBottom) || 0);
  return (last.bottom - first.top) + padding;
}

/**
 * Give every open band a height that says how much work is in it, and write it
 * out as `--board-lane-h-<lane>` for `laneBandTrack()` to clamp.
 *
 * Sizing a band to its own fullest cell is not enough on a real board: one
 * dense column (a Delivered column holds cards of every class) puts every
 * lane's fullest cell past the cap, so every band saturates and a 15-card lane
 * looks exactly like a 140-card one. So the bands *share out* the height the
 * reader can actually see at once, weighted by how much work each is holding —
 * then a sparse lane is visibly the short one, and every lane fits on screen
 * together, which is the comparison swimlanes exist to make.
 *
 * The weight is the square root of the lane's volume, not the volume itself:
 * proportional weights let one lane with an order of magnitude more cards take
 * everything and leave the rest on the floor, and a lane you cannot see is
 * worse than a lane drawn slightly too short. A band never asks for more than
 * its own content needs, so a nearly-empty board does not stretch to fill the
 * viewport, and board.css clamps the result at both ends.
 */
function measureLaneBands(grid) {
  const content = new Map();
  for (const cell of grid.querySelectorAll(".board-lane-cell")) {
    const lane = cell.dataset.lane;
    if (!lane) continue;
    const height = cellContentHeight(cell);
    if (height > (content.get(lane) || 0)) content.set(lane, height);
  }
  if (!content.size) return;

  // What is left of the scrollport once the rows that never scroll away have
  // taken their share: the ribbon, the group band, the column headers and one
  // head per lane (empty and rolled-up lanes keep theirs).
  const viewport = grid.parentElement?.clientHeight || 0;
  const fixedRows = [".board-ribbon", ".board-group", ".board-col:not(.is-collapsed) > .board-col-header"]
    .reduce((total, selector) => total + (grid.querySelector(selector)?.getBoundingClientRect().height || 0), 0);
  const heads = Array.from(grid.querySelectorAll(".board-lane-head"))
    .reduce((total, node) => total + node.getBoundingClientRect().height, 0);
  const budget = viewport - fixedRows - heads;

  const weights = new Map(Array.from(content, ([lane, height]) => [lane, Math.sqrt(Math.max(height, 1))]));
  const totalWeight = Array.from(weights.values()).reduce((a, b) => a + b, 0);

  for (const [lane, height] of content) {
    // Below one usable band the share is meaningless — board.css's floor takes
    // over from here, and the grid scrolls, which the sticky heads now survive.
    const share = budget > 0 && totalWeight > 0 ? (budget * weights.get(lane)) / totalWeight : height;
    const prop = `--board-lane-h-${lane}`;
    const value = `${Math.ceil(Math.min(height, share))}px`;
    if (grid.style.getPropertyValue(prop) !== value) grid.style.setProperty(prop, value);
  }
}

/** Grid track layout: one track per visible column, plus a rail track at the
 * upstream/downstream boundary when the unified segment shows both halves. */
function buildTracks() {
  const widths = [];
  const trackOfColumn = new Map();
  let railTrack = null;
  let index = 0;
  const unified = store.get().segment === "unified";

  columns.forEach((column, i) => {
    if (unified && i > 0 && railTrack === null
      && columns[i - 1].segment === "upstream" && column.segment === "downstream") {
      widths.push("var(--board-rail-w)");
      railTrack = index;
      index += 1;
    }
    trackOfColumn.set(column.id, index);
    widths.push(collapsedSet.has(column.id) ? "var(--board-col-w-collapsed)" : "var(--board-col-w)");
    index += 1;
  });

  return { templateColumns: widths.join(" "), trackOfColumn, railTrack };
}

function buildRibbonRow(tracks, rows) {
  const runs = [];
  let current = null;
  for (const column of columns) {
    if (!current || current.segment !== column.segment) {
      current = { segment: column.segment, first: column, last: column };
      runs.push(current);
    } else {
      current.last = column;
    }
  }
  return runs.map((run) => {
    const start = tracks.trackOfColumn.get(run.first.id);
    const end = tracks.trackOfColumn.get(run.last.id);
    return el(
      "div",
      {
        className: "board-ribbon",
        dataset: { segment: run.segment },
        style: { gridColumn: `${start + 1} / ${end + 2}`, gridRow: rowArea(rows.ribbon) },
      },
      run.segment === "upstream" ? "Upstream \u2014 discovery" : "Downstream \u2014 delivery"
    );
  });
}

function buildGroupRow(tracks, rows) {
  const cells = [];
  for (const group of groups) {
    const memberIndexes = columns.reduce((acc, c, i) => (c.group_id === group.id ? acc.concat(i) : acc), []);
    if (!memberIndexes.length) continue;
    const first = columns[memberIndexes[0]];
    const last = columns[memberIndexes[memberIndexes.length - 1]];
    const start = tracks.trackOfColumn.get(first.id);
    const end = tracks.trackOfColumn.get(last.id);
    const cell = wip?.groups?.[group.id];
    cells.push(
      el(
        "div",
        {
          className: "board-group",
          dataset: { groupId: group.id },
          style: {
            gridColumn: `${start + 1} / ${end + 2}`,
            gridRow: rowArea(rows.group),
            "--group-color": group.color || null,
          },
        },
        el(
          "span",
          { className: "board-group-name" },
          el("span", { className: "board-group-swatch" }),
          el("span", { className: "truncate" }, group.name)
        ),
        cell
          ? wipBadge(cell, {
              kind: "group",
              id: group.id,
              name: group.name,
              span: memberIndexes.length,
            })
          : null
      )
    );
  }
  return cells;
}

function buildRailCell(tracks, rows) {
  return el(
    "div",
    {
      className: "board-rail",
      style: {
        gridColumn: `${tracks.railTrack + 1} / ${tracks.railTrack + 2}`,
        gridRow: rowArea(rows.rail),
      },
      "data-tooltip": "Moving a card past this line commits the team to deliver it.",
      "data-tooltip-pos": "right",
    },
    el(
      "div",
      { className: "board-rail-marker" },
      icon("target", { className: "board-rail-icon" }),
      el("span", { className: "board-rail-label" }, "Commitment Point")
    )
  );
}

function buildColumnHeader(column, cell, collapsed, opts = {}) {
  const meta = columnKindMeta(column.kind);
  const isCommitment = column.id === commitmentColumnId;
  const isDelivery = column.id === deliveryColumnId;
  const inLanes = Boolean(opts.lanes);
  const wipScope = { kind: "column", id: column.id, name: column.name, kindLabel: meta.label };

  const nameRow = el(
    "div",
    { className: "board-col-name" },
    isCommitment ? icon("target", { className: "board-col-point-icon", title: "Commitment point" }) : null,
    isDelivery ? icon("check", { className: "board-col-point-icon", title: "Delivery point" }) : null,
    el("span", { className: "truncate" }, column.name)
  );

  const collapseButton = el(
    "button",
    {
      className: "btn btn-ghost btn-icon btn-sm",
      "aria-label": collapsed ? `Expand ${column.name}` : `Collapse ${column.name}`,
      onClick: () => toggleCollapsed(column.id),
    },
    icon(collapsed ? "chevron-right" : "chevron-left")
  );

  // In lane mode a column has no single "bottom", so the add affordance moves
  // into the header rather than being repeated once per lane — which would
  // also imply the new card takes that lane's class, which it does not.
  const addButton = inLanes && !collapsed && canEditBoard() && column.kind !== "done"
    ? el(
        "button",
        {
          className: "btn btn-ghost btn-icon btn-sm",
          "aria-label": `Add a card to ${column.name}`,
          "data-tooltip": "Add card",
          onClick: () => openAddCard(column),
        },
        icon("plus")
      )
    : null;

  const titleRow = el(
    "div",
    { className: "board-col-title-row" },
    collapsed && inLanes ? null : nameRow,
    el(
      "div",
      { className: "board-col-actions" },
      addButton,
      buildColumnMenuButton(column, collapsed),
      collapseButton
    )
  );

  if (collapsed) {
    return el(
      "div",
      {
        className: "board-col-header",
        style: opts.style || null,
        dataset: opts.dataset || {},
        ...(inLanes ? { "data-tooltip": column.name, "data-tooltip-pos": "bottom" } : {}),
      },
      titleRow,
      cell ? wipBadge(cell, wipScope, { label: String(cell.count) }) : null
    );
  }

  const badges = el(
    "div",
    { className: "board-col-badges" },
    el("span", { className: "board-col-kind", "data-tooltip": meta.tooltip }, icon(meta.icon), meta.label),
    cell ? wipBadge(cell, wipScope) : null
  );

  return el(
    "div",
    { className: "board-col-header", style: opts.style || null, dataset: opts.dataset || {} },
    titleRow,
    badges
  );
}

function buildColumnCell(column, tracks) {
  const track = tracks.trackOfColumn.get(column.id);
  const collapsed = collapsedSet.has(column.id);
  const cell = wip?.columns?.[column.id] || null;
  const cardIds = columnCards.get(column.id) || [];

  const wrapper = el("div", {
    className: `board-col${collapsed ? " is-collapsed" : ""}`,
    dataset: { segment: column.segment, columnId: column.id },
    style: { gridColumn: `${track + 1} / ${track + 2}`, gridRow: rowArea(FLAT_ROWS.header) },
  });
  columnElById.set(column.id, wrapper);

  wrapper.append(buildColumnHeader(column, cell, collapsed));

  if (!collapsed) {
    const body = el("div", { className: "board-col-body" });
    if (!cardIds.length) {
      body.append(el("div", { className: "board-col-empty" }, "No cards here."));
    } else {
      for (const id of cardIds) {
        const card = cardsById.get(id);
        if (card) body.append(buildCardEl(card, column));
      }
    }
    wrapper.append(body);
    dnd.attachDropzone(body, column, dndCtx);

    if (canEditBoard() && column.kind !== "done") {
      wrapper.append(
        el("button", { className: "board-col-add", onClick: () => openAddCard(column) }, icon("plus"), "Add card")
      );
    }
  }

  return wrapper;
}

function paint() {
  if (!rootEl) return;

  if (!columns.length) {
    render(
      rootEl,
      el(
        "div",
        { className: "board-view" },
        emptyState({
          icon: "board",
          title: "This board has no columns yet",
          body: store.can("manage_boards")
            ? "Add columns to start visualising work."
            : "Ask an administrator to set up this board's columns.",
        })
      )
    );
    return;
  }

  closeWipEditor();

  const tracks = buildTracks();
  const lanes = lanesActive() ? buildLaneModel() : null;
  const rows = lanes ? buildLaneRows(lanes) : FLAT_ROWS;
  columnElById = new Map();

  const groupCells = buildGroupRow(tracks, rows);

  const grid = el(
    "div",
    {
      className: `board-grid${lanes ? " has-lanes" : ""}`,
      style: {
        gridTemplateColumns: tracks.templateColumns,
        ...(rows.template ? { gridTemplateRows: rows.template } : {}),
        // No group row to draw means no group band to reserve, and the sticky
        // offsets below it must close up rather than leave a dead strip.
        ...(lanes && !groupCells.length ? { "--board-group-h": "0px" } : {}),
      },
    },
    buildRibbonRow(tracks, rows),
    groupCells,
    columns.map((column) => (lanes ? buildLaneColumn(column, tracks, rows) : buildColumnCell(column, tracks))),
    lanes ? rows.lanes.map((entry) => buildLaneHead(entry)) : null,
    tracks.railTrack !== null ? buildRailCell(tracks, rows) : null
  );

  scrollEl = el(
    "div",
    { className: `board-scroll${lanes ? " has-lanes" : ""}`, onScroll: onBoardScroll },
    grid
  );

  const scrollWrap = el(
    "div",
    { className: "board-scroll-wrap" },
    el("div", { className: "board-edge board-edge-left" }),
    el("div", { className: "board-edge board-edge-right" }),
    scrollEl
  );

  render(rootEl, el("div", { className: "board-view" }, buildToolbar(), scrollWrap));
  window.requestAnimationFrame(() => {
    syncLaneMetrics();
    updateEdgeShadows();
    updateRailIndicator();
    revealCommitmentRailOnce();
  });
}

/** Whether the current column set actually straddles a commitment boundary
 * (only true for the Unified segment, and only once both halves are loaded). */
function hasCommitmentBoundary() {
  return store.get().segment === "unified"
    && columns.some((c, i) => i > 0 && columns[i - 1].segment === "upstream" && c.segment === "downstream");
}

/**
 * The whole-flow WIP pill.
 *
 * It used to render only when a limit already existed, which made the one
 * scope you cannot reach any other way \u2014 the flow policy \u2014 unreachable on a
 * board that had never had one. Now the pill appears whenever the flow policy
 * is available at all, so "no limit yet" is a state you can click on.
 */
function buildFlowPill() {
  const cell = wip?.flow;
  if (!store.hasFlag("wip_flow") || !cell) return null;

  const scope = { kind: "flow", id: boardId, name: board?.name || "Whole flow" };
  const label = `Whole flow ${wipLabel(cell)}`;

  if (!canManageStructure()) {
    return el(
      "span",
      {
        className: "board-meta-pill",
        dataset: { wip: cell.state },
        "data-tooltip": `${wipReadOnlyTooltip(cell, scope)}`,
        "data-tooltip-pos": "bottom",
      },
      icon("layers"),
      label
    );
  }

  return el(
    "button",
    {
      type: "button",
      className: "board-meta-pill board-wip-pill",
      dataset: { wip: cell.state },
      // Not a menu: what opens is a form panel (two steppers, "no limit"
      // checkboxes, Cancel/Save). Announcing "menu" tells a screen-reader user
      // to expect arrow-key item navigation that does not exist here.
      "aria-haspopup": "dialog",
      "aria-expanded": "false",
      "aria-label": `Whole-flow WIP limits, ${wipAriaValue(cell)}. Edit.`,
      "data-tooltip": "Edit the whole-flow WIP limits",
      "data-tooltip-pos": "bottom",
      onClick: (event) => openWipEditor(event.currentTarget, scope),
    },
    icon("layers"),
    label
  );
}

/** Lanes: off / class of service (SPEC-v2 \u00a7R2.3). Default off. */
function buildLanesControl() {
  if (!store.hasFlag("service_classes")) return null;
  const active = lanesActive();

  const option = (mode, iconName, label, hint) =>
    el(
      "button",
      {
        type: "button",
        role: "tab",
        "aria-selected": (mode === "sc") === active ? "true" : "false",
        "data-tooltip": hint,
        "data-tooltip-pos": "bottom",
        onClick: () => setLaneMode(mode),
      },
      icon(iconName),
      label
    );

  return el(
    "div",
    { className: "board-lane-switch" },
    el("span", { className: "board-lane-switch-label", id: "board-lanes-label" }, "Lanes"),
    el(
      "div",
      { className: "segmented", role: "tablist", "aria-labelledby": "board-lanes-label" },
      option("off", "minus", "Off", "One flat flow \u2014 the default."),
      option("sc", "layers", "Class of service", "Split the board into expedite, fixed date,\nstandard and intangible lanes.")
    )
  );
}

/**
 * The toolbar's way into the structure editor (SPEC-v2 \u00a7R2.1).
 *
 * Never hidden. The structure of a board \u2014 what the columns are, where the
 * commitment point falls, which columns accrue wait time \u2014 is the explanation
 * of every number the reader is looking at, so a role that may read the board
 * may read its shape. R2.1 is explicit: everyone else "sees the structure
 * read-only, with an explanation rather than a hidden button". structure.js
 * owns that distinction (it renders the read-only notice and locks its
 * controls); this button's only job is to be reachable. The lock glyph says up
 * front that the drawer will open in reading mode, so the promise the label
 * makes is not broken by the drawer that answers it.
 */
function buildStructureButton() {
  const editable = canManageStructure();
  return el(
    "button",
    {
      className: `btn btn-ghost btn-sm${editable ? "" : " board-structure-readonly"}`,
      "data-tooltip": editable
        ? "Add, reorder, re-kind or remove columns"
        : "See how this board's flow is structured.\nOnly administrators can change it.",
      "data-tooltip-pos": "bottom",
      ...(editable
        ? {}
        : { "aria-label": "Edit columns \u2014 read-only. Only administrators can change this board's structure." }),
      onClick: () => openStructure(),
    },
    icon("board"),
    "Edit columns",
    editable ? null : icon("lock", { className: "board-structure-lock" })
  );
}

function buildToolbar() {
  const sleLabel = board?.sle_days ? `SLE ${board.sle_percentile ?? 85}% \u2264 ${board.sle_days}d` : null;
  const anyCollapsed = collapsedSet.size > 0;

  return el(
    "div",
    { className: "board-toolbar" },
    hasCommitmentBoundary()
      ? el(
          "span",
          {
            className: "board-position-indicator",
            dataset: { region: "upstream" },
            "data-tooltip": "Shows which side of the commitment point the board is scrolled to.",
          },
          icon(RAIL_REGION_META.upstream.icon),
          el("span", { className: "board-position-text" }, RAIL_REGION_META.upstream.label)
        )
      : null,
    buildFlowPill(),
    sleLabel ? el("span", { className: "board-meta-pill" }, icon("clock"), sleLabel) : null,
    el("span", { className: "spacer" }),
    buildLanesControl(),
    buildStructureButton(),
    el(
      "button",
      { className: "btn btn-ghost btn-sm", onClick: () => setAllCollapsed(!anyCollapsed) },
      icon(anyCollapsed ? "chevrons-up-down" : "minus"),
      anyCollapsed ? "Expand all" : "Collapse all"
    )
  );
}

/* ==========================================================================
   Cards — compact by construction (Req. 5)
   ========================================================================== */

function cardAriaLabel(card) {
  const isSealed = card.envelope_state === "sealed";
  const bits = [card.key, card.title];
  if (isSealed) {
    bits.push("sealed envelope");
  } else {
    if (card.assignee_name) bits.push(`assigned to ${card.assignee_name}`);
    if (card.task_total) bits.push(`${card.task_done} of ${card.task_total} tasks done`);
  }
  if (card.is_blocked) bits.push("blocked");
  return bits.filter(Boolean).join(", ");
}

function buildDuePill(card) {
  if (card.service_class !== "fixed_date" || !card.due_date) return null;
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(card.due_date) ? card.due_date : `${card.due_date}Z`;
  const due = new Date(normalized).getTime();
  const overdue = !card.delivered_at && Number.isFinite(due) && due < Date.now();
  const soon = !overdue && !card.delivered_at && Number.isFinite(due) && due - Date.now() < 3 * 86400000;
  return el(
    "span",
    { className: "k-due-pill", dataset: overdue ? { tone: "danger" } : soon ? { tone: "warning" } : {} },
    icon("calendar"),
    formatDate(card.due_date)
  );
}

function buildTaskProgress(card) {
  if (!card.task_total) return null;
  const complete = card.task_done >= card.task_total;
  return el(
    "span",
    { className: `k-task-progress${complete ? " is-complete" : ""}` },
    icon("check"),
    `${card.task_done}/${card.task_total}`
  );
}

/**
 * The board view has no percentile distribution to draw on (that lives in
 * metrics.js), so the dot buckets a card's time-since-commitment against the
 * board's own Service Level Expectation as a practical proxy: comfortably
 * under half the SLE reads healthy, past 85% reads at risk.
 */
function buildAgingDot(card) {
  if (!card.committed_at || card.delivered_at) return null;
  const days = card.age_days;
  const sleDays = board?.sle_days;
  let tone = null;
  if (sleDays && days != null) {
    const ratio = days / sleDays;
    tone = ratio >= 0.85 ? "danger" : ratio >= 0.5 ? "warning" : "success";
  }
  const tooltip = days != null
    ? `${formatDuration(days * 86400, { style: "days" })} since commitment${sleDays ? ` \u00b7 SLE ${sleDays}d` : ""}`
    : "In flight";
  return el("span", { className: "k-aging-dot", dataset: tone ? { tone } : {}, "data-tooltip": tooltip });
}

function buildCardEl(card, column) {
  const sc = card.service_class;
  const isSealed = card.envelope_state === "sealed";
  const project = card.project_key
    ? el("span", { className: "k-project-chip", style: { "--proj-color": card.project_color || null } }, card.project_key)
    : null;

  const cardEl = el(
    "div",
    {
      className: `k-card${isSealed ? " is-sealed" : ""}`,
      tabIndex: 0,
      role: "button",
      "aria-label": cardAriaLabel(card),
      dataset: { cardId: card.id, columnId: column.id },
      onClick: () => openCardDrawer(card.id, boardCtx),
      onKeyDown: (event) => onCardKeyDown(event, card),
    },
    el("span", { className: "sc-stripe", dataset: { sc }, title: SERVICE_CLASS_MAP[sc]?.label || sc }),
    el(
      "div",
      { className: "k-card-body" },
      el(
        "div",
        { className: "k-card-row1" },
        el("span", { className: "k-card-key" }, card.key),
        el("span", { className: "spacer" }),
        isSealed ? icon("envelope", { className: "k-sealed-flag", title: "Sealed envelope" }) : null,
        card.is_blocked
          ? icon("block", {
              className: "k-blocked-flag",
              title: card.blocked_since ? `Blocked ${relativeTime(card.blocked_since)}` : "Blocked",
            })
          : null,
        // A sealed demand is captured but deliberately unexamined (SPEC §9.4) —
        // a due-date readout implies scheduling work that has not happened yet.
        isSealed ? null : buildDuePill(card)
      ),
      el("div", { className: "k-card-title clamp-2" }, card.title),
      el(
        "div",
        { className: "k-card-row3" },
        project,
        el("span", { className: "spacer" }),
        // Same reasoning: a checklist breakdown and an assignee are both
        // examined-work details a sealed envelope has not received yet.
        isSealed ? null : buildTaskProgress(card),
        buildAgingDot(card),
        isSealed ? null : avatar(card.assignee_id ? { id: card.assignee_id, name: card.assignee_name } : null, { size: "xs" })
      )
    )
  );

  dnd.attachCardDrag(cardEl, card, dndCtx);

  // Lanes are a view, not an editor: whichever lane cell the pointer lands on,
  // the card keeps its class and reappears in its own lane. Marking that lane
  // during the drag says so before the drop rather than after it. These
  // listeners run alongside dnd.js's own; they touch nothing it owns.
  if (cardEl.draggable) {
    cardEl.addEventListener("dragstart", () => setDragLane(laneIdOf(card)));
    cardEl.addEventListener("dragend", () => setDragLane(null));
  }

  return cardEl;
}

/** Flag the destination lane on the grid so CSS can highlight its cells. */
function setDragLane(laneId) {
  const grid = rootEl?.querySelector(".board-grid.has-lanes");
  if (!grid) return;
  if (laneId) grid.dataset.dragLane = laneId;
  else delete grid.dataset.dragLane;
}

/* ==========================================================================
   Keyboard: focus a card with arrows, Enter opens it, `m` moves it
   ========================================================================== */

function cssEscape(value) {
  return window.CSS && typeof CSS.escape === "function" ? CSS.escape(value) : String(value).replace(/["\\]/g, "\\$&");
}

function focusCard(id) {
  const node = rootEl?.querySelector(`.k-card[data-card-id="${cssEscape(id)}"]`);
  if (node) node.focus();
}

/**
 * The cards arrow keys should walk through inside one column. With lanes on,
 * that is the cards in the *same lane* — jumping between bands on ArrowDown
 * would make the focus ring teleport across the screen.
 */
function navIdsIn(columnId, card) {
  const ids = columnCards.get(columnId) || [];
  if (!lanesActive()) return ids;
  const lane = laneIdOf(card);
  const sameLane = ids.filter((id) => laneIdOf(cardsById.get(id)) === lane);
  return sameLane.length ? sameLane : ids;
}

function moveFocus(card, key) {
  const columnIndex = columns.findIndex((c) => c.id === card.column_id);
  if (columnIndex < 0) return;
  const idsInColumn = navIdsIn(card.column_id, card);
  const cardIndex = idsInColumn.indexOf(card.id);

  if (key === "ArrowUp" || key === "ArrowDown") {
    const nextId = idsInColumn[cardIndex + (key === "ArrowUp" ? -1 : 1)];
    if (nextId) focusCard(nextId);
    return;
  }

  const step = key === "ArrowLeft" ? -1 : 1;
  for (let i = columnIndex + step; i >= 0 && i < columns.length; i += step) {
    const targetColumn = columns[i];
    const ids = navIdsIn(targetColumn.id, card);
    if (ids.length) {
      focusCard(ids[Math.min(Math.max(cardIndex, 0), ids.length - 1)]);
      return;
    }
    const node = columnElById.get(targetColumn.id);
    const body = node?.querySelector(".board-col-body");
    if (body) {
      body.setAttribute("tabindex", "-1");
      body.focus();
      announce(`${targetColumn.name} \u2014 no cards`);
      return;
    }
  }
}

function onCardKeyDown(event, card) {
  const key = event.key;
  if (key === "Enter" || key === " ") {
    event.preventDefault();
    openCardDrawer(card.id, boardCtx);
    return;
  }
  if (key.toLowerCase() === "m" && !event.metaKey && !event.ctrlKey && !event.altKey) {
    event.preventDefault();
    dnd.openMoveMenu(event.currentTarget, card, dndCtx);
    return;
  }
  if (key === "ArrowUp" || key === "ArrowDown" || key === "ArrowLeft" || key === "ArrowRight") {
    event.preventDefault();
    moveFocus(card, key);
  }
}

/* ==========================================================================
   Card creation
   ========================================================================== */

function openAddCard(column) {
  openCreateCardModal({
    board,
    column,
    projects: projectsList,
    onCreated: () => reload(),
  });
}

/* ==========================================================================
   SWIMLANES — the lane grid (SPEC-v2 §R2.3)
   --------------------------------------------------------------------------
   One `.board-col` wrapper per column survives here even though the column's
   cells are scattered down the grid, because it is `display: contents`: it
   generates no box (its children are laid out by `.board-grid` directly) but
   it is still a real element, so dnd.js's `ctx.getColumnEl(id)` keeps working
   unchanged and one class on it repaints every cell of that column at once.

   WIP stays a *column* policy. Lane cells carry no limits and no counts of
   their own beyond the lane header's informational tally, because the system
   does not enforce anything per lane and the UI must not imply that it does.
   ========================================================================== */

/** Bucket the visible cards into lanes, preserving column order within each. */
function buildLaneModel() {
  const cardsPerLane = new Map(LANE_ORDER.map((id) => [id, new Map()]));
  const counts = new Map(LANE_ORDER.map((id) => [id, 0]));

  for (const column of columns) {
    for (const cardId of columnCards.get(column.id) || []) {
      const card = cardsById.get(cardId);
      if (!card) continue;
      const laneId = laneIdOf(card);
      const perColumn = cardsPerLane.get(laneId);
      if (!perColumn.has(column.id)) perColumn.set(column.id, []);
      perColumn.get(column.id).push(cardId);
      counts.set(laneId, counts.get(laneId) + 1);
    }
  }

  return LANE_ORDER.map((id) => {
    const count = counts.get(id);
    const collapsed = collapsedLanes.has(id);
    const perColumn = cardsPerLane.get(id);

    // How tall this band actually needs to be is decided by its *fullest
    // visible cell*, not by its total — thirty cards spread over ten columns
    // need three cards' worth of height, not thirty. A collapsed column shows
    // no cells, so its cards cannot make a band taller either.
    let busiest = 0;
    for (const [columnId, ids] of perColumn) {
      if (collapsedSet.has(columnId)) continue;
      if (ids.length > busiest) busiest = ids.length;
    }

    return {
      id,
      label: SERVICE_CLASS_MAP[id]?.label || id,
      policy: SERVICE_CLASS_MAP[id]?.policy || "",
      empty: LANE_EMPTY_COPY[id],
      count,
      busiest,
      collapsed,
      cards: perColumn,
      // An empty lane keeps its strip — its emptiness is the signal — but it
      // needs no body row, which is what makes the strip slim.
      open: count > 0 && !collapsed,
    };
  });
}

function buildLaneColumn(column, tracks, rows) {
  const track = tracks.trackOfColumn.get(column.id);
  const collapsed = collapsedSet.has(column.id);
  const cell = wip?.columns?.[column.id] || null;
  const columnArea = `${track + 1} / ${track + 2}`;

  const wrapper = el("div", {
    className: `board-col is-lane-col${collapsed ? " is-collapsed" : ""}`,
    dataset: { segment: column.segment, columnId: column.id },
  });
  columnElById.set(column.id, wrapper);

  // The backdrop is what a column *is* in lane mode: one full-height surface
  // carrying the segment tint, the hairline and every drag state, behind cells
  // that would otherwise only paint where cards happen to be.
  wrapper.append(
    el(
      "div",
      {
        className: "board-col-backdrop",
        dataset: { segment: column.segment },
        style: { gridColumn: columnArea, gridRow: rowArea(rows.header) },
      },
      collapsed ? el("span", { className: "board-col-spine" }, column.name) : null
    )
  );

  wrapper.append(
    buildColumnHeader(column, cell, collapsed, {
      lanes: true,
      dataset: { segment: column.segment },
      style: { gridColumn: columnArea, gridRow: rowArea(rows.header) },
    })
  );

  if (!collapsed) {
    for (const entry of rows.lanes) {
      if (!entry.body) continue;
      const ids = entry.lane.cards.get(column.id) || [];
      const body = el("div", {
        className: "board-col-body board-lane-cell",
        dataset: { lane: entry.lane.id, columnId: column.id },
        style: { gridColumn: columnArea, gridRow: `${entry.body} / ${entry.body + 1}` },
      });
      for (const id of ids) {
        const card = cardsById.get(id);
        if (card) body.append(buildCardEl(card, column));
      }
      wrapper.append(body);
      dnd.attachDropzone(body, column, dndCtx);
    }
  }

  return wrapper;
}

const LANE_COUNT_TOOLTIP =
  "Cards in this lane across every column.\nWIP limits are set per column, never per lane.";

function buildLaneHead(entry) {
  const { lane } = entry;
  const rowEnd = entry.body ? entry.body + 1 : entry.head + 1;
  const cardWord = `${lane.count} card${lane.count === 1 ? "" : "s"}`;

  const toggle = el(
    "button",
    {
      className: "btn btn-ghost btn-icon btn-sm board-lane-toggle",
      "aria-expanded": lane.collapsed ? "false" : "true",
      "aria-label": lane.collapsed
        ? `Expand the ${lane.label} lane, ${cardWord}`
        : `Collapse the ${lane.label} lane, ${cardWord}`,
      disabled: lane.count === 0,
      onClick: () => toggleLane(lane.id),
    },
    icon(lane.collapsed || lane.count === 0 ? "chevron-right" : "chevron-down")
  );

  const note = lane.count === 0
    ? el("span", { className: "board-lane-note" }, lane.empty)
    : lane.collapsed
      ? el("span", { className: "board-lane-note" }, `${cardWord} rolled up`)
      : null;

  const head = el(
    "div",
    {
      className: `board-lane-head${lane.count === 0 ? " is-empty" : ""}${lane.collapsed ? " is-rolled-up" : ""}`,
      dataset: { sc: lane.id, lane: lane.id },
    },
    el(
      "div",
      { className: "board-lane-label" },
      toggle,
      el("span", { className: "board-lane-name", "data-tooltip": lane.policy, "data-tooltip-pos": "bottom" }, lane.label),
      el(
        "span",
        { className: "badge board-lane-count", dataset: { sc: lane.id }, "data-tooltip": LANE_COUNT_TOOLTIP, "data-tooltip-pos": "bottom" },
        String(lane.count)
      ),
      note
    )
  );

  // The band itself: a real element spanning this lane's rows, which is what
  // gives the sticky header a box to stick *inside*. As a bare grid item the
  // header was clipped to the grid container instead of to its own area, so
  // once it stuck it never came unstuck — four headers piled up at the top of
  // the viewport, each labelling a lane the reader had already scrolled past.
  // The band paints nothing and takes no pointer events (the header inside
  // takes its own back), so the cells underneath keep every click and drop.
  return el(
    "div",
    {
      className: `board-lane${lane.count === 0 ? " is-empty" : ""}${lane.collapsed ? " is-rolled-up" : ""}`,
      dataset: { sc: lane.id, lane: lane.id },
      style: { gridColumn: "1 / -1", gridRow: `${entry.head} / ${rowEnd}` },
    },
    head
  );
}

/* ==========================================================================
   WIP LIMITS — editable in place at column, group and flow scope (§R2.2)
   ========================================================================== */

/** "5 cards, minimum 2, maximum 5" — for the badge's accessible name. */
function wipAriaValue(cell) {
  const bits = [`${cell.count} card${cell.count === 1 ? "" : "s"}`];
  bits.push(cell.min == null ? "no minimum" : `minimum ${cell.min}`);
  bits.push(cell.max == null ? "no maximum" : `maximum ${cell.max}`);
  return bits.join(", ");
}

function wipScopeNoun(scope) {
  return scope.kind === "column" ? "column" : scope.kind === "group" ? "group" : "flow";
}

/** What the current numbers mean, in one sentence, for the scope in hand. */
function wipStateSentence(scope, count, min, max) {
  const state = wipStateFor(count, min, max);
  const subject = scope.kind === "flow" ? "The flow is" : `This ${wipScopeNoun(scope)} is`;
  switch (state) {
    case "over":
      return `${subject} over its limit — ${count} against a maximum of ${max}, ${count - max} too many.`;
    case "at_limit":
      return `${subject} at its limit — ${count} of ${max}. The next pull breaks the policy.`;
    case "under":
      return `${subject} starving — ${count} against a minimum of ${min}. A minimum never blocks a move; it asks for more work to be pulled in.`;
    default:
      return max == null
        ? `${subject} within policy. With no maximum, nothing here will ever block a pull.`
        : `${subject} within policy — ${count} of ${max}.`;
  }
}

function wipReadOnlyTooltip(cell, scope) {
  return `${wipAriaValue(cell)}\nOnly administrators change ${wipScopeNoun(scope)} WIP limits.`;
}

/**
 * A WIP badge — a button when the user may edit the policy, a plain badge with
 * an explanatory tooltip when they may not. Never hidden: a limit the reader
 * cannot see is a limit they cannot respect.
 */
function wipBadge(cell, scope, opts = {}) {
  const label = opts.label ?? wipLabel(cell);
  const enriched = { ...scope, cell };

  if (!canManageStructure()) {
    return el(
      "span",
      {
        className: "badge",
        dataset: { wip: cell.state },
        "data-tooltip": wipReadOnlyTooltip(cell, scope),
        "data-tooltip-pos": "bottom",
      },
      label
    );
  }

  return el(
    "button",
    {
      type: "button",
      className: "badge board-wip-badge",
      dataset: { wip: cell.state },
      // A form panel, not a menu — see buildFlowPill().
      "aria-haspopup": "dialog",
      "aria-expanded": "false",
      "aria-label": `${scope.name} WIP limits, ${wipAriaValue(cell)}. Edit.`,
      "data-tooltip": "Edit WIP limits",
      "data-tooltip-pos": "bottom",
      onClick: (event) => {
        event.stopPropagation();
        openWipEditor(event.currentTarget, enriched);
      },
    },
    label
  );
}

function closeWipEditor() {
  if (!wipEditorHandle) return;
  const handle = wipEditorHandle;
  wipEditorHandle = null;
  handle.close();
}

function currentWipCell(scope) {
  if (scope.kind === "column") return wip?.columns?.[scope.id] || null;
  if (scope.kind === "group") return wip?.groups?.[scope.id] || null;
  return wip?.flow || null;
}

/** One number stepper: [−] [input] [+] plus a "no limit" escape hatch. */
function wipStepper({ id, label, value, noneLabel, hint, onChange }) {
  let current = value;

  const input = el("input", {
    className: "input input-sm",
    type: "number",
    min: "0",
    step: "1",
    inputMode: "numeric",
    id,
    value: current == null ? "" : String(current),
    disabled: current == null,
    placeholder: "None",
    "aria-describedby": `${id}-hint`,
  });

  const none = el("input", {
    className: "checkbox",
    type: "checkbox",
    checked: current == null,
    id: `${id}-none`,
  });

  function emit(next) {
    current = next;
    onChange(next);
  }

  function step(delta) {
    if (none.checked) {
      none.checked = false;
      input.disabled = false;
    }
    const base = Number.isFinite(Number(input.value)) && input.value !== "" ? Number(input.value) : 0;
    const next = Math.max(0, base + delta);
    input.value = String(next);
    emit(next);
    input.focus();
  }

  input.addEventListener("input", () => {
    if (input.value === "") { emit(null); return; }
    const parsed = Number.parseInt(input.value, 10);
    emit(Number.isFinite(parsed) && parsed >= 0 ? parsed : Number.NaN);
  });

  none.addEventListener("change", () => {
    input.disabled = none.checked;
    if (none.checked) {
      emit(null);
    } else {
      if (input.value === "") input.value = "0";
      emit(Number.parseInt(input.value, 10));
      input.focus();
    }
  });

  return {
    input,
    node: el(
      "div",
      { className: "wip-field" },
      el("label", { className: "filter-field-label", htmlFor: id }, label),
      el(
        "div",
        { className: "wip-stepper" },
        el(
          "button",
          { type: "button", className: "btn btn-secondary btn-icon btn-sm", "aria-label": `Decrease ${label.toLowerCase()}`, onClick: () => step(-1) },
          icon("minus")
        ),
        input,
        el(
          "button",
          { type: "button", className: "btn btn-secondary btn-icon btn-sm", "aria-label": `Increase ${label.toLowerCase()}`, onClick: () => step(1) },
          icon("plus")
        )
      ),
      el("label", { className: "checkbox-row wip-none" }, none, el("span", null, noneLabel)),
      el("span", { className: "hint", id: `${id}-hint` }, hint)
    ),
  };
}

/**
 * The WIP popover. Escape cancels (ui.menu's own handler), Enter commits, the
 * preview recomputes on every keystroke so the user sees the state they are
 * about to create *before* they create it.
 */
function openWipEditor(anchorEl, scope) {
  closeWipEditor();

  const cell = scope.cell || currentWipCell(scope) || { count: 0, min: null, max: null, state: "ok" };
  const count = cell.count ?? 0;
  let min = cell.min ?? null;
  let max = cell.max ?? null;

  const preview = el("span", { className: "badge" });
  const previewText = el("span", { className: "wip-preview-text" });
  const error = el("div", { className: "field-error", role: "alert" });
  const save = el(
    "button",
    { type: "button", className: "btn btn-primary btn-sm", onClick: () => commit() },
    "Save limits"
  );

  function validate() {
    if (Number.isNaN(min) || Number.isNaN(max)) return "Enter whole numbers, or tick “no limit”.";
    if ((min != null && min < 0) || (max != null && max < 0)) return "Limits cannot be negative.";
    if (min != null && max != null && min > max) return "The minimum cannot be greater than the maximum.";
    return null;
  }

  function refresh() {
    const problem = validate();
    error.textContent = problem || "";
    error.hidden = !problem;
    save.disabled = Boolean(problem);

    if (problem) {
      preview.removeAttribute("data-wip");
      preview.textContent = "—";
      previewText.textContent = "Fix the numbers above to see the resulting state.";
      return;
    }
    const state = wipStateFor(count, min, max);
    preview.dataset.wip = state;
    preview.textContent = wipLabel({ count, min, max });
    previewText.textContent = wipStateSentence(scope, count, min, max);
  }

  const minField = wipStepper({
    id: "wip-min-field",
    label: "Minimum",
    value: min,
    noneLabel: "No minimum",
    hint: "Advisory only. A column below its minimum is starving; nothing is ever blocked.",
    onChange: (value) => { min = value; refresh(); },
  });

  const maxField = wipStepper({
    id: "wip-max-field",
    label: "Maximum",
    value: max,
    noneLabel: "No maximum",
    hint: "Enforced. A move that would breach the maximum is rejected — except expedite work.",
    onChange: (value) => { max = value; refresh(); },
  });

  const scopeHint = scope.kind === "column"
    ? `${scope.kindLabel ? `${scope.kindLabel} column` : "Column"} policy — ${count} card${count === 1 ? "" : "s"} here now.`
    : scope.kind === "group"
      ? `Group policy — ${count} card${count === 1 ? "" : "s"} across ${scope.span ?? "its"} column${scope.span === 1 ? "" : "s"}.`
      : `Whole-flow policy — ${count} card${count === 1 ? "" : "s"} committed and not yet delivered.`;

  const panel = el(
    "div",
    { className: "wip-pop", role: "group", "aria-label": `WIP limits for ${scope.name}` },
    el(
      "div",
      { className: "wip-pop-head" },
      el("span", { className: "panel-title truncate" }, scope.name),
      el("span", { className: "badge", dataset: { wip: cell.state } }, wipLabel(cell))
    ),
    el("p", { className: "hint" }, scopeHint),
    el("div", { className: "wip-pop-fields" }, minField.node, maxField.node),
    el("div", { className: "wip-pop-preview" }, preview, previewText),
    error,
    el(
      "div",
      { className: "wip-pop-actions" },
      el("span", { className: "hint" }, "Enter saves · Esc cancels"),
      el("button", { type: "button", className: "btn btn-ghost btn-sm", onClick: () => closeWipEditor() }, "Cancel"),
      save
    )
  );

  panel.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (!save.disabled) commit();
  });

  async function commit() {
    if (validate()) return;
    save.classList.add("is-loading");
    save.disabled = true;
    try {
      await applyWipChange(scope, { wip_min: min, wip_max: max });
    } catch (apiError) {
      save.classList.remove("is-loading");
      save.disabled = false;
      error.hidden = false;
      error.textContent = apiError?.message || "The limits could not be saved.";
      return;
    }
    closeWipEditor();
  }

  refresh();
  wipEditorHandle = menu(anchorEl, [panel], { minWidth: 300, align: "left" });
  const closeMenu = wipEditorHandle.close;
  wipEditorHandle.close = () => {
    closeMenu();
    anchorEl.setAttribute?.("aria-expanded", "false");
    wipEditorHandle = null;
  };
  // The minimum is often "none", and a disabled input cannot take focus — land
  // on its "no minimum" toggle instead so the popover always opens focused.
  if (minField.input.disabled) {
    panel.querySelector(".wip-none .checkbox")?.focus();
  } else {
    minField.input.focus();
    minField.input.select?.();
  }
}

/** Every WIP state on the board right now, keyed by scope — the before/after
 * pair that lets a change announce what it did to policies it did not touch. */
function snapshotWipStates() {
  const snapshot = new Map();
  for (const [id, cellValue] of Object.entries(wip?.columns || {})) snapshot.set(`column:${id}`, cellValue.state);
  for (const [id, cellValue] of Object.entries(wip?.groups || {})) snapshot.set(`group:${id}`, cellValue.state);
  if (wip?.flow) snapshot.set("flow", wip.flow.state);
  return snapshot;
}

function nameForScopeKey(key) {
  const [kind, id] = key.split(":");
  if (kind === "flow") return "The whole flow";
  if (kind === "column") return columnLookup(id)?.name || "A column";
  return groups.find((g) => g.id === id)?.name || "A group";
}

const WIP_FLIP_WORD = { over: "over its limit", at_limit: "at its limit", under: "starving", ok: "back within policy" };

/**
 * PATCH one WIP policy, then re-read the whole WIP block and repaint.
 *
 * Re-reading is not defensive padding: raising a column's maximum can push its
 * *group* or the whole flow over, and neither of those badges is on the
 * response of the call that caused it.
 */
async function applyWipChange(scope, payload) {
  const before = snapshotWipStates();
  const path = scope.kind === "column"
    ? `/columns/${scope.id}`
    : scope.kind === "group"
      ? `/groups/${scope.id}`
      : `/boards/${boardId}`;

  const updated = await api.patch(path, payload, { silent: true });
  mergeWipTarget(scope, updated);

  const fresh = await api.get(`/boards/${boardId}/wip`, { silent: true }).catch(() => null);
  if (fresh) wip = fresh;
  paint();

  const after = snapshotWipStates();
  const own = scope.kind === "flow" ? "flow" : `${scope.kind}:${scope.id}`;
  const sentences = [
    `${scope.name}: ${payload.wip_min == null ? "no minimum" : `minimum ${payload.wip_min}`}, `
    + `${payload.wip_max == null ? "no maximum" : `maximum ${payload.wip_max}`}.`,
    wipStateSentence(scope, currentWipCell(scope)?.count ?? 0, payload.wip_min ?? null, payload.wip_max ?? null),
  ];

  const knockOns = [];
  for (const [key, state] of after) {
    if (key === own || before.get(key) === state) continue;
    if (state !== "over" && state !== "at_limit") continue;
    knockOns.push(`${nameForScopeKey(key)} is now ${WIP_FLIP_WORD[state]}.`);
  }

  announce([...sentences, ...knockOns].join(" "));
  if (knockOns.length) {
    toast(knockOns.join(" "), { tone: "warning", title: "Another policy changed state" });
  }

  // paint() replaced the badge the popover was anchored to, taking focus with
  // it. Put focus back on its replacement so keyboard users are not dropped
  // onto <body> after every edit.
  window.requestAnimationFrame(() => refocusWipControl(scope));
}

function refocusWipControl(scope) {
  if (!rootEl) return;
  const selector = scope.kind === "flow"
    ? ".board-wip-pill"
    : scope.kind === "group"
      ? `.board-group[data-group-id="${cssEscape(scope.id)}"] .board-wip-badge`
      : `.board-col[data-column-id="${cssEscape(scope.id)}"] .board-wip-badge`;
  rootEl.querySelector(selector)?.focus();
}

/** Fold the server's authoritative row back into the local model. */
function mergeWipTarget(scope, updated) {
  if (!updated) return;
  if (scope.kind === "column") {
    const index = columns.findIndex((c) => c.id === scope.id);
    if (index >= 0) columns[index] = { ...columns[index], ...updated };
    visibleColumnIndex = new Map(columns.map((c) => [c.id, c]));
    const structural = structureIndex.get(scope.id);
    if (structural) structureIndex.set(scope.id, { ...structural, ...updated });
  } else if (scope.kind === "group") {
    const index = groups.findIndex((g) => g.id === scope.id);
    if (index >= 0) groups[index] = { ...groups[index], ...updated };
  } else {
    board = { ...board, ...updated };
  }
}

/* ==========================================================================
   STRUCTURE EDITOR — entry points (SPEC-v2 §R2.1)
   --------------------------------------------------------------------------
   structure.js is imported lazily and defensively: the board is the product's
   front door and must render even if that module is absent or broken. Its one
   exported function, `openStructureEditor(boardId, {onChanged})`, is the whole
   contract between the two files; the extra hints below are optional context
   an implementation may use to preselect a row, and are safe to ignore.
   ========================================================================== */

async function openStructure(intent = {}) {
  try {
    const module = await import("./structure.js");
    if (typeof module.openStructureEditor !== "function") {
      throw new TypeError("structure.js does not export openStructureEditor()");
    }
    module.openStructureEditor(boardId, {
      onChanged: async () => {
        await loadStructure();
        await reload();
      },
      ...intent,
    });
  } catch (error) {
    console.warn("[board] the column editor could not be opened", error);
    toast("The column editor could not be loaded. Reload the page and try again.", {
      tone: "danger",
      title: "Editor unavailable",
    });
  }
}

/**
 * The ⋯ menu on a column header — the second entry point R2.1 requires, and
 * like the toolbar button it is offered to every role that can read the board.
 * What it *contains* narrows to what the role can actually do: a reader gets
 * the way into the read-only structure view and the collapse toggle, never a
 * "Delete column…" item that could only ever end in a 403.
 */
function buildColumnMenuButton(column, collapsed) {
  return el(
    "button",
    {
      className: "btn btn-ghost btn-icon btn-sm",
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      "aria-label": `Actions for the ${column.name} column`,
      "data-tooltip": "Column actions",
      "data-tooltip-pos": "bottom",
      onClick: (event) => {
        event.stopPropagation();
        openColumnMenu(event.currentTarget, column, collapsed);
      },
    },
    icon("more")
  );
}

function openColumnMenu(anchorEl, column, collapsed) {
  const cell = wip?.columns?.[column.id] || null;

  const item = (iconName, label, onClick, extraClass) =>
    el(
      "button",
      { className: `menu-item${extraClass ? ` ${extraClass}` : ""}`, onClick },
      icon(iconName),
      el("span", { className: "truncate" }, label)
    );

  const collapseItem = item(
    collapsed ? "chevron-right" : "chevron-left",
    collapsed ? "Expand column" : "Collapse column",
    () => toggleCollapsed(column.id)
  );

  if (!canManageStructure()) {
    menu(
      anchorEl,
      [
        el("div", { className: "menu-label" }, column.name),
        // Same drawer, same row scrolled into view — structure.js opens it in
        // its read-only mode and explains why, which is the whole point of
        // still offering the door.
        item("board", "View column structure…", () => openStructure({ focusColumnId: column.id, intent: "view" })),
        el("div", { className: "menu-separator" }),
        collapseItem,
      ],
      { minWidth: 220, align: "right" }
    );
    return;
  }

  menu(
    anchorEl,
    [
      el("div", { className: "menu-label" }, column.name),
      // `focusColumnId` is the half of this structure.js is contracted to
      // honour (it expands and scrolls to that row); `intent` is a hint it is
      // free to ignore, which is why every item still lands somewhere useful.
      item("edit", "Edit column…", () => openStructure({ focusColumnId: column.id, intent: "edit" })),
      item("plus", "Insert column before", () =>
        openStructure({ focusColumnId: column.id, relativeToColumnId: column.id, intent: "insert_before" })),
      item("plus", "Insert column after", () =>
        openStructure({ focusColumnId: column.id, relativeToColumnId: column.id, intent: "insert_after" })),
      el("div", { className: "menu-separator" }),
      cell
        ? item("settings", "Edit WIP limits…", () =>
            openWipEditor(anchorEl, {
              kind: "column",
              id: column.id,
              name: column.name,
              kindLabel: columnKindMeta(column.kind).label,
              cell,
            }))
        : null,
      collapseItem,
      el("div", { className: "menu-separator" }),
      item("trash", "Delete column…", () => openStructure({ focusColumnId: column.id, intent: "delete" }), "is-danger"),
    ].filter(Boolean),
    { minWidth: 220, align: "right" }
  );
}
