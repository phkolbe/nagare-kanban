/**
 * Nagare — board view (SPEC §9.2, Req. 1/2/3/5/9).
 *
 * `GET /boards/{id}/view` is the one call this view makes for its data; column
 * CRUD, group CRUD and board policy editing belong to a future BOARDS-owned
 * settings surface and are out of scope here — this file renders the flow and
 * moves cards through it.
 *
 * Layout: see the block comment at the top of board.css for the grid model.
 * Every card list scrolls independently (`.board-col-body`), which is what
 * makes column headers stay in view without a single `position: sticky` rule —
 * only `.board-scroll` scrolls, and only horizontally.
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
  el, icon, render, avatar, skeleton, emptyState, errorState, toast,
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
  filters.mount();
  paintSkeleton();

  unsubscribers = [
    store.select((s) => s.filters, () => reload()),
    store.select((s) => s.segment, () => reload()),
  ];

  await Promise.all([loadStructure(), loadDirectories(), reload({ initial: true })]);
}

export function unmount() {
  mounted = false;
  for (const unsub of unsubscribers) unsub();
  unsubscribers = [];
  filters.unmount();
  closeCardDrawer();
  rootEl = null;
  scrollEl = null;
  columnElById = new Map();
}

function resetState() {
  board = null; columns = []; groups = []; wip = null;
  commitmentColumnId = null; deliveryColumnId = null;
  visibleColumnIndex = new Map();
  structureColumns = []; structureIndex = new Map();
  cardsById = new Map(); columnCards = new Map();
  projectsList = []; peopleList = [];
  collapsedSet = new Set();
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
  const left = scrollEl.querySelector(".board-edge-left");
  const right = scrollEl.querySelector(".board-edge-right");
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

function buildRibbonRow(tracks) {
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
      { className: "board-ribbon", dataset: { segment: run.segment }, style: { gridColumn: `${start + 1} / ${end + 2}` } },
      run.segment === "upstream" ? "Upstream \u2014 discovery" : "Downstream \u2014 delivery"
    );
  });
}

function buildGroupRow(tracks) {
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
        { className: "board-group", style: { gridColumn: `${start + 1} / ${end + 2}`, "--group-color": group.color || null } },
        el(
          "span",
          { className: "board-group-name" },
          el("span", { className: "board-group-swatch" }),
          el("span", { className: "truncate" }, group.name)
        ),
        cell ? el("span", { className: "badge", dataset: { wip: cell.state } }, wipLabel(cell)) : null
      )
    );
  }
  return cells;
}

function buildRailCell(tracks) {
  return el(
    "div",
    {
      className: "board-rail",
      style: { gridColumn: `${tracks.railTrack + 1} / ${tracks.railTrack + 2}` },
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

function buildColumnHeader(column, cell, collapsed) {
  const meta = columnKindMeta(column.kind);
  const isCommitment = column.id === commitmentColumnId;
  const isDelivery = column.id === deliveryColumnId;

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

  const titleRow = el(
    "div",
    { className: "board-col-title-row" },
    nameRow,
    el("div", { className: "board-col-actions" }, collapseButton)
  );

  if (collapsed) {
    return el(
      "div",
      { className: "board-col-header" },
      titleRow,
      cell ? el("span", { className: "badge", dataset: { wip: cell.state } }, String(cell.count)) : null
    );
  }

  const badges = el(
    "div",
    { className: "board-col-badges" },
    el("span", { className: "board-col-kind", "data-tooltip": meta.tooltip }, icon(meta.icon), meta.label),
    cell ? el("span", { className: "badge", dataset: { wip: cell.state } }, wipLabel(cell)) : null
  );

  return el("div", { className: "board-col-header" }, titleRow, badges);
}

function buildColumnCell(column, tracks) {
  const track = tracks.trackOfColumn.get(column.id);
  const collapsed = collapsedSet.has(column.id);
  const cell = wip?.columns?.[column.id] || null;
  const cardIds = columnCards.get(column.id) || [];

  const wrapper = el("div", {
    className: `board-col${collapsed ? " is-collapsed" : ""}`,
    dataset: { segment: column.segment, columnId: column.id },
    style: { gridColumn: `${track + 1} / ${track + 2}` },
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

  const tracks = buildTracks();
  columnElById = new Map();

  const grid = el(
    "div",
    { className: "board-grid", style: { gridTemplateColumns: tracks.templateColumns } },
    buildRibbonRow(tracks),
    buildGroupRow(tracks),
    columns.map((column) => buildColumnCell(column, tracks)),
    tracks.railTrack !== null ? buildRailCell(tracks) : null
  );

  scrollEl = el(
    "div",
    { className: "board-scroll", onScroll: onBoardScroll },
    el("div", { className: "board-edge board-edge-left" }),
    el("div", { className: "board-edge board-edge-right" }),
    grid
  );

  render(rootEl, el("div", { className: "board-view" }, buildToolbar(), scrollEl));
  window.requestAnimationFrame(() => {
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

function buildToolbar() {
  const flowCell = wip?.flow;
  const showFlow = store.hasFlag("wip_flow") && flowCell && (flowCell.min != null || flowCell.max != null);
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
    showFlow
      ? el("span", { className: "board-meta-pill", dataset: { wip: flowCell.state } }, icon("layers"), `Whole flow ${wipLabel(flowCell)}`)
      : null,
    sleLabel ? el("span", { className: "board-meta-pill" }, icon("clock"), sleLabel) : null,
    el("span", { className: "spacer" }),
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
  return cardEl;
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

function moveFocus(card, key) {
  const columnIndex = columns.findIndex((c) => c.id === card.column_id);
  if (columnIndex < 0) return;
  const idsInColumn = columnCards.get(card.column_id) || [];
  const cardIndex = idsInColumn.indexOf(card.id);

  if (key === "ArrowUp" || key === "ArrowDown") {
    const nextId = idsInColumn[cardIndex + (key === "ArrowUp" ? -1 : 1)];
    if (nextId) focusCard(nextId);
    return;
  }

  const step = key === "ArrowLeft" ? -1 : 1;
  for (let i = columnIndex + step; i >= 0 && i < columns.length; i += step) {
    const targetColumn = columns[i];
    const ids = columnCards.get(targetColumn.id) || [];
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
