/**
 * Nagare — forward-only drag & drop (SPEC §9.2, §6).
 *
 * Everything a card can move through — a mouse drag, the drawer's "Move card"
 * menu (card.js), and the keyboard alternative here — funnels through the one
 * `moveCard()` below, so the confirm-moves dialog, the optimistic update and
 * the error translation exist exactly once.
 *
 * board.js owns the DOM and the in-memory model; this module only *decides*
 * (is this move forward? is the target over its WIP limit?) and *asks*
 * board.js to apply the consequence through the small `ctx` contract:
 *
 *   ctx.getBoard()                the current board row (for confirm_moves)
 *   ctx.patchBoardConfirm(bool)   PATCH confirm_moves, update local state
 *   ctx.getCardById(id)           in-memory card
 *   ctx.columnById(id)            column row (id, name, position, kind, ...)
 *   ctx.visibleColumns()          currently rendered columns, in flow order
 *   ctx.allColumns()              every column of the board, in flow order
 *   ctx.getColumnEl(id)           the `.board-col` DOM node for a column
 *   ctx.wipCellFor(id)            {count,min,max,state} for a column
 *   ctx.canEdit()                 may this user write this board?
 *   ctx.moveCardLocally(id, to)   optimistic model update; returns previous column id
 *   ctx.rollbackMove(id, from)    undo the optimistic update
 *   ctx.applyMoveResult(result)   replace the optimistic guess with the server's truth
 */

import api from "./api.js";
import { el, icon, toast, confirmDialog, menu } from "./ui.js";
import { columnKindMeta } from "./card.js";

/* ==========================================================================
   Shared move — drag, drawer menu and keyboard all call this
   ========================================================================== */

const MOVE_ERROR_TITLES = {
  wip_limit_exceeded: "WIP limit reached",
  backward_move_blocked: "Cards flow forward only",
  envelope_sealed: "Envelope still sealed",
  card_archived: "Card is archived",
  same_column_move: "Already there",
  card_moved_elsewhere: "Moved elsewhere",
};

function friendlyMoveError(error) {
  return error?.message || "This move could not be completed.";
}

/**
 * Move `cardId` into `toColumnId`, enforcing SPEC §6 on the client (forward
 * only, sealed envelopes, the board's confirm-moves policy) before ever
 * calling the API, then applies the result optimistically with rollback.
 */
export async function moveCard(cardId, toColumnId, ctx) {
  const card = ctx.getCardById(cardId);
  if (!card) return;
  const fromColumn = ctx.columnById(card.column_id);
  const toColumn = ctx.columnById(toColumnId);
  if (!fromColumn || !toColumn || fromColumn.id === toColumn.id) return;

  if (card.envelope_state === "sealed") {
    toast("Sealed envelope demands must be opened before they can move.", {
      tone: "warning",
      title: "Envelope still sealed",
    });
    return;
  }

  if (toColumn.position <= fromColumn.position) {
    toast(
      `Cards flow forward only — “${fromColumn.name}” already comes after “${toColumn.name}” in this value stream.`,
      { tone: "warning", title: "Cards flow forward only" }
    );
    return;
  }

  const board = ctx.getBoard();
  if (board?.confirm_moves) {
    const result = await confirmDialog({
      title: "Confirm move",
      message: `Move ${card.key ? `${card.key} — ` : ""}“${card.title}” from “${fromColumn.name}” to “${toColumn.name}”?`,
      confirmLabel: "Move card",
      checkbox: "Don't ask again on this board",
    });
    const confirmed = typeof result === "object" ? result.confirmed : result;
    if (!confirmed) return;
    if (typeof result === "object" && result.checked) {
      ctx.patchBoardConfirm(false)
        .then(() => toast("Move confirmations turned off for this board.", { tone: "neutral" }))
        .catch(() => { /* the move still proceeds even if the preference failed to save */ });
    }
  }

  const previousColumnId = ctx.moveCardLocally(cardId, toColumnId);

  try {
    const result = await api.post(
      `/cards/${cardId}/move`,
      { to_column_id: toColumnId },
      { silent: true }
    );
    ctx.applyMoveResult(result);
    if (Array.isArray(result.warnings) && result.warnings.length) {
      toast(result.warnings.join(" "), { tone: "info" });
    }
  } catch (error) {
    ctx.rollbackMove(cardId, previousColumnId);
    toast(friendlyMoveError(error), {
      tone: "danger",
      title: MOVE_ERROR_TITLES[error?.code] || "Move failed",
    });
  }
}

/* ==========================================================================
   Drag & drop
   ========================================================================== */

let draggingCardId = null;
let draggingFromColumnId = null;
let placeholder = null;

function ensurePlaceholder() {
  if (!placeholder) {
    placeholder = document.createElement("div");
    placeholder.className = "k-drop-indicator";
    placeholder.setAttribute("aria-hidden", "true");
  }
  return placeholder;
}

function removePlaceholder() {
  if (placeholder && placeholder.parentNode) placeholder.parentNode.removeChild(placeholder);
}

/** Insert (or move) the insertion-affordance line at the pointer's height. */
function showPlaceholder(bodyEl, clientY) {
  const node = ensurePlaceholder();
  const cards = Array.from(bodyEl.querySelectorAll(":scope > .k-card"));
  let before = null;
  for (const cardNode of cards) {
    const rect = cardNode.getBoundingClientRect();
    if (clientY < rect.top + rect.height / 2) { before = cardNode; break; }
  }
  if (before) bodyEl.insertBefore(node, before);
  else bodyEl.appendChild(node);
}

/** Dim invalid (backward/current) columns and warn-tint over-WIP forward ones. */
function markDropzones(card, ctx) {
  const from = ctx.columnById(card.column_id);
  for (const column of ctx.visibleColumns()) {
    const node = ctx.getColumnEl(column.id);
    if (!node) continue;
    node.classList.remove("is-drop-valid", "is-drop-invalid", "is-drop-warn", "is-drop-current");
    node.removeAttribute("aria-disabled");

    if (column.id === card.column_id) {
      node.classList.add("is-drop-current");
      continue;
    }
    if (column.position <= from.position) {
      node.classList.add("is-drop-invalid");
      node.setAttribute("aria-disabled", "true");
      continue;
    }
    node.classList.add("is-drop-valid");
    const cell = ctx.wipCellFor(column.id);
    if (cell && (cell.state === "at_limit" || cell.state === "over") && card.service_class !== "expedite") {
      node.classList.add("is-drop-warn");
    }
  }
}

function clearDropzoneMarks(ctx) {
  for (const column of ctx.visibleColumns()) {
    const node = ctx.getColumnEl(column.id);
    if (!node) continue;
    node.classList.remove("is-drop-valid", "is-drop-invalid", "is-drop-warn", "is-drop-current");
    node.removeAttribute("aria-disabled");
  }
}

/**
 * Wire native HTML5 drag behaviour onto one rendered card element.
 * Sealed cards and cards on a read-only board are simply not draggable.
 */
export function attachCardDrag(cardEl, card, ctx) {
  const draggable = ctx.canEdit() && card.envelope_state !== "sealed";
  cardEl.draggable = draggable;
  if (!draggable) return;

  cardEl.addEventListener("dragstart", (event) => {
    draggingCardId = card.id;
    draggingFromColumnId = card.column_id;
    event.dataTransfer.effectAllowed = "move";
    try { event.dataTransfer.setData("text/plain", card.id); } catch { /* some browsers restrict custom data */ }
    // Deferred so the drag image is captured before the class dims the source.
    window.setTimeout(() => cardEl.classList.add("is-dragging"), 0);
    markDropzones(card, ctx);
  });

  cardEl.addEventListener("dragend", () => {
    cardEl.classList.remove("is-dragging");
    clearDropzoneMarks(ctx);
    removePlaceholder();
    draggingCardId = null;
    draggingFromColumnId = null;
  });
}

/** Wire one column body as a drop target. */
export function attachDropzone(bodyEl, column, ctx) {
  bodyEl.addEventListener("dragover", (event) => {
    if (!draggingCardId) return;
    const wrapper = ctx.getColumnEl(column.id);
    if (!wrapper || !wrapper.classList.contains("is-drop-valid")) return; // invalid: no preventDefault -> browser refuses the drop
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    showPlaceholder(bodyEl, event.clientY);
  });

  bodyEl.addEventListener("dragleave", (event) => {
    if (event.target === bodyEl) removePlaceholder();
  });

  bodyEl.addEventListener("drop", (event) => {
    event.preventDefault();
    removePlaceholder();
    const cardId = draggingCardId || event.dataTransfer.getData("text/plain");
    const fromColumnId = draggingFromColumnId;
    draggingCardId = null;
    draggingFromColumnId = null;
    if (!cardId || column.id === fromColumnId) return;
    moveCard(cardId, column.id, ctx);
  });
}

/* ==========================================================================
   Keyboard alternative — focus a card, press `m`, choose a target column
   ========================================================================== */

/** Open a menu of valid forward columns for `card`, anchored at `anchorEl`. */
export function openMoveMenu(anchorEl, card, ctx) {
  if (card.envelope_state === "sealed") {
    toast("Sealed envelope demands must be opened before they can move.", {
      tone: "warning",
      title: "Envelope still sealed",
    });
    return;
  }

  const current = ctx.columnById(card.column_id);
  const forward = ctx.allColumns().filter((column) => !current || column.position > current.position);

  const items = forward.length
    ? forward.map((column) =>
        el(
          "button",
          { className: "menu-item", onClick: () => moveCard(card.id, column.id, ctx) },
          icon(columnKindMeta(column.kind).icon),
          el("span", { className: "truncate" }, column.name)
        )
      )
    : [el("div", { className: "menu-label" }, "No forward columns available")];

  menu(anchorEl, items, { minWidth: 220 });
}

export const dnd = { moveCard, attachCardDrag, attachDropzone, openMoveMenu };
export default dnd;
