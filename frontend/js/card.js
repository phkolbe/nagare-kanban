/**
 * Nagare — card detail drawer (SPEC §9.2, Req. 5).
 *
 * The board stays deliberately compact: a stripe, a title, a chip, a dot. All
 * of the richness lives here, one click away, in a focus-trapped slide-over
 * with five tabs — Details, Tasks, History, Blockers, Comments.
 *
 * This module is also the frontend's *card vocabulary*: the service classes,
 * demand types, value areas, sizes, column kinds and blocker categories, each
 * with the sentence that explains what it actually means for flow. Those are
 * exported because a class of service that is never explained is just a colour.
 *
 * Editing is inline and optimistic: a control PATCHes on blur, the local model
 * is updated immediately, and a failure rolls the control *and* the model back
 * and says why. Nothing here has a Save button.
 *
 * Dependencies point downwards only — ui.js, api.js, store.js. `board.js`
 * passes a `ctx` object in; this module never imports it back.
 */

import api from "./api.js";
import store, { can } from "./store.js";
import {
  el, icon, render, clear, drawer, modal, confirmDialog, toast, avatar,
  skeleton, skeletonText, emptyState, errorState, announce,
  relativeTime, absoluteTime, formatDate, formatDuration, formatPercent, humanize,
} from "./ui.js";

/* ==========================================================================
   Card vocabulary — shared with board.js and filters.js
   ========================================================================== */

/**
 * Classes of service (Req. 6). `policy` is the sentence shown next to the
 * selector: the whole point of a class of service is the *policy* attached to
 * it, so the UI states it rather than assuming everyone read the wiki.
 */
export const SERVICE_CLASSES = Object.freeze([
  {
    id: "expedite",
    label: "Expedite",
    policy:
      "Drop everything. Pulled ahead of all other work and allowed past column WIP limits. "
      + "Every expedite steals capacity from committed work — keep the share small and deliberate.",
  },
  {
    id: "standard",
    label: "Standard",
    policy:
      "The default. Pulled in order from the replenishment queue and measured against the "
      + "board's service level expectation. Most work should be standard.",
  },
  {
    id: "fixed_date",
    label: "Fixed date",
    policy:
      "Has a hard external date — a contract, a regulation, a launch. Started late enough to "
      + "be efficient and early enough to make the date at the chosen percentile.",
  },
  {
    id: "intangible",
    label: "Intangible",
    policy:
      "No immediate customer value but a real future cost: debt, learning, maintenance. "
      + "Pulled when there is slack, so the cost of delay stays invisible until it is not.",
  },
]);

/** @type {Record<string, {id: string, label: string, policy: string}>} */
export const SERVICE_CLASS_MAP = Object.freeze(
  Object.fromEntries(SERVICE_CLASSES.map((item) => [item.id, item]))
);

/** Demand types — the failure-demand metric depends on this being honest. */
export const DEMAND_TYPES = Object.freeze([
  { id: "value", label: "Value demand", policy: "Work a customer actually asked for." },
  {
    id: "failure",
    label: "Failure demand",
    policy: "Work caused by something we did wrong earlier. The one number worth driving to zero.",
  },
  {
    id: "maintenance",
    label: "Maintenance",
    policy: "Keeping the lights on: upgrades, renewals, routine operational work.",
  },
]);

export const VALUE_AREAS = Object.freeze([
  { id: "feature", label: "Feature" },
  { id: "defect", label: "Defect" },
  { id: "risk", label: "Risk" },
  { id: "debt", label: "Debt" },
  { id: "compliance", label: "Compliance" },
]);

export const CARD_SIZES = Object.freeze([
  { id: "xs", label: "XS" },
  { id: "s", label: "S" },
  { id: "m", label: "M" },
  { id: "l", label: "L" },
  { id: "xl", label: "XL" },
]);

export const BLOCKER_CATEGORIES = Object.freeze([
  { id: "dependency", label: "Dependency" },
  { id: "external_vendor", label: "External vendor" },
  { id: "waiting_on_customer", label: "Waiting on customer" },
  { id: "technical", label: "Technical" },
  { id: "people", label: "People" },
  { id: "compliance", label: "Compliance" },
  { id: "environment", label: "Environment" },
  { id: "other", label: "Other" },
]);

/**
 * Column kinds (Req. 2). `accrues` is what the clock in that column becomes,
 * which is the single most misunderstood idea in Kanban — hence the tooltip.
 */
export const COLUMN_KINDS = Object.freeze({
  queue: {
    id: "queue",
    label: "Wait",
    icon: "clock",
    accrues: "wait",
    tooltip:
      "Queue column — time here is WAIT time.\nThe item sits still; nobody is working on it.\n"
      + "Wait time is the numerator of every flow-efficiency problem.",
  },
  activity: {
    id: "activity",
    label: "Touch",
    icon: "bolt",
    accrues: "touch",
    tooltip:
      "Activity column — time here is TOUCH time.\nSomeone is actively working the item.\n"
      + "Flow efficiency = touch / (touch + wait) × 100.",
  },
  envelope: {
    id: "envelope",
    label: "Sealed",
    icon: "envelope",
    accrues: "envelope",
    tooltip:
      "Sealed envelope — demand captured but deliberately unexamined.\n"
      + "Counted in lead time only when the board's envelope toggle is on.",
  },
  done: {
    id: "done",
    label: "Delivered",
    icon: "check",
    accrues: "none",
    tooltip:
      "Delivery point — entering this column stamps delivered_at\nand stops the lead-time clock.",
  },
});

/** Metadata for a column kind, with a safe fallback for unknown values. */
export function columnKindMeta(kind) {
  return COLUMN_KINDS[kind] || {
    id: kind || "queue",
    label: humanize(kind || "queue"),
    icon: "queue",
    accrues: "wait",
    tooltip: "Column kind.",
  };
}

/** Display label for a service class id. */
export function serviceClassLabel(id) {
  return SERVICE_CLASS_MAP[id]?.label || humanize(id);
}

/* ==========================================================================
   Drawer lifecycle
   ========================================================================== */

/** The single open drawer, if any. Only one card is ever inspected at a time. */
let openHandle = null;
let openCardId = null;
let activeTab = "details";

/** Close the card drawer if it is open. Safe to call unconditionally. */
export function closeCardDrawer() {
  if (openHandle) {
    const handle = openHandle;
    openHandle = null;
    openCardId = null;
    handle.close();
  }
}

/** The id of the card currently open in the drawer, or null. */
export function currentCardId() {
  return openCardId;
}

/**
 * Open the detail drawer for a card.
 *
 * @param {string} cardId
 * @param {object} ctx Board context — see `board.js`. Uses:
 *   `columnById`, `allColumns`, `projects()`, `people()`, `canEdit()`,
 *   `applyCardPatch(card)`, `removeCard(id)`, `moveCard(id, toId)`, `refresh()`.
 */
export async function openCardDrawer(cardId, ctx = {}) {
  if (!cardId) return;

  // Re-opening the same card is a no-op rather than a stacked drawer.
  if (openCardId === cardId && openHandle) return;
  closeCardDrawer();

  openCardId = cardId;
  const handle = drawer({
    title: "Loading card…",
    body: el("div", { className: "stack stack-4" }, skeleton({ height: 28 }), skeletonText(6)),
    onClose: () => {
      if (openHandle === handle) {
        openHandle = null;
        openCardId = null;
      }
    },
  });
  openHandle = handle;

  let card;
  try {
    card = await api.get(`/cards/${cardId}`, { silent: true });
  } catch (error) {
    if (openHandle !== handle) return;
    render(handle.body, errorState(error, () => {
      closeCardDrawer();
      openCardDrawer(cardId, ctx);
    }, { title: "This card could not be loaded" }));
    return;
  }

  if (openHandle !== handle) return; // the user closed it while we fetched
  renderDrawer(handle, card, ctx);
}

/* ==========================================================================
   Rendering
   ========================================================================== */

const TABS = [
  { id: "details", label: "Details", icon: "info" },
  { id: "tasks", label: "Tasks", icon: "check" },
  { id: "history", label: "History", icon: "clock" },
  { id: "blockers", label: "Blockers", icon: "block" },
  { id: "comments", label: "Comments", icon: "users" },
];

function renderDrawer(handle, card, ctx) {
  const editable = typeof ctx.canEdit === "function" ? ctx.canEdit() : can("manage_cards");
  const column = ctx.columnById ? ctx.columnById(card.column_id) : null;
  const panel = el("div", { className: "cd-panel" });

  /** Re-render only the active panel; the header and tabs are stable. */
  function paintPanel() {
    clear(panel);
    if (activeTab === "details") panel.append(detailsTab(card, ctx, editable));
    else if (activeTab === "tasks") panel.append(tasksTab(card, ctx, editable));
    else if (activeTab === "history") panel.append(historyTab(card, ctx));
    else if (activeTab === "blockers") panel.append(blockersTab(card, ctx, editable, refreshCard));
    else if (activeTab === "comments") panel.append(commentsTab(card, ctx));
  }

  /** Pull the card again after an action that changes several collections. */
  async function refreshCard() {
    try {
      const fresh = await api.get(`/cards/${card.id}`, { silent: true });
      Object.assign(card, fresh);
      if (ctx.applyCardPatch) ctx.applyCardPatch(fresh);
      paintTabs();
      paintPanel();
    } catch {
      /* the drawer keeps the last good state; the action already toasted */
    }
  }

  const tabsBar = el("div", { className: "cd-tabs tabs", role: "tablist", "aria-label": "Card sections" });

  function paintTabs() {
    clear(tabsBar);
    for (const tab of TABS) {
      const count = tabCount(tab.id, card);
      tabsBar.append(
        el(
          "button",
          {
            className: "tab",
            role: "tab",
            id: `cd-tab-${tab.id}`,
            "aria-selected": tab.id === activeTab ? "true" : "false",
            "aria-controls": "cd-panel",
            onClick: () => { activeTab = tab.id; paintTabs(); paintPanel(); },
            onKeyDown: (event) => onTabKey(event, tab.id, () => { paintTabs(); paintPanel(); }),
          },
          icon(tab.icon),
          tab.label,
          count !== null ? el("span", { className: "badge" }, String(count)) : null
        )
      );
    }
  }

  paintTabs();
  paintPanel();

  handle.setTitle(card.title || "Untitled card");

  render(
    handle.body,
    el(
      "div",
      { className: "cd-root" },
      cardIdentityRow(card, column, ctx),
      tabsBar,
      el("div", { className: "cd-panel-wrap", id: "cd-panel", role: "tabpanel", "aria-labelledby": `cd-tab-${activeTab}` }, panel)
    )
  );

  // Header actions: move, open envelope, delete. Rendered into the drawer's
  // own header so they stay reachable while the body scrolls.
  const headerActions = handle.el.querySelector(".drawer-header .row");
  if (headerActions && editable) {
    const actions = el(
      "button",
      {
        className: "btn btn-ghost btn-icon btn-sm",
        "aria-label": "Card actions",
        "aria-haspopup": "menu",
        "aria-expanded": "false",
        onClick: (event) => openCardActionsMenu(event.currentTarget, card, ctx, refreshCard),
      },
      icon("more")
    );
    headerActions.prepend(actions);
  }

  // Subtitle: key · column · segment. Written directly because drawer() takes
  // its subtitle at construction time and we only know it after the fetch.
  const titleBox = handle.el.querySelector(".drawer-header .stack");
  if (titleBox && !titleBox.querySelector(".cd-subtitle")) {
    titleBox.append(
      el(
        "div",
        { className: "cd-subtitle t-xs subtle row row-1" },
        el("span", { className: "t-mono" }, card.key || ""),
        column ? el("span", null, "·") : null,
        column ? el("span", { className: "truncate" }, column.name) : null
      )
    );
  }
}

function tabCount(tabId, card) {
  if (tabId === "tasks") return (card.tasks || []).length || null;
  if (tabId === "blockers") return (card.blockers || []).length || null;
  if (tabId === "comments") return (card.comments || []).length || null;
  return null;
}

/** Left/Right arrows move between tabs, as WAI-ARIA expects of a tablist. */
function onTabKey(event, tabId, repaint) {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
  event.preventDefault();
  const index = TABS.findIndex((t) => t.id === tabId);
  const next = TABS[(index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
  activeTab = next.id;
  repaint();
  const button = document.getElementById(`cd-tab-${next.id}`);
  if (button) button.focus();
}

/* ---- identity strip ------------------------------------------------------ */

function cardIdentityRow(card, column, ctx) {
  const sc = SERVICE_CLASS_MAP[card.service_class];
  const blocker = (card.blockers || []).find((b) => !b.ended_at);

  return el(
    "div",
    { className: "cd-identity" },
    el(
      "div",
      { className: "row row-wrap row-1" },
      el(
        "span",
        { className: "chip", dataset: { sc: card.service_class } },
        el("span", { className: "sc-dot" }),
        sc?.label || humanize(card.service_class)
      ),
      card.project_key
        ? el("span", { className: "chip" }, el("span", { className: "cd-project-swatch", style: card.project_color ? { background: card.project_color } : {} }), card.project_key)
        : null,
      column ? el("span", { className: "chip" }, icon(columnKindMeta(column.kind).icon), column.name) : null,
      card.envelope_state === "sealed"
        ? el("span", { className: "chip", dataset: { tone: "warning" } }, icon("envelope"), "Sealed")
        : null,
      blocker
        ? el("span", { className: "chip", dataset: { tone: "danger" } }, icon("block"), `Blocked ${relativeTime(blocker.started_at)}`)
        : null
    ),
    el(
      "div",
      { className: "cd-timestamps" },
      timestampCell("Created", card.created_at),
      card.opened_at ? timestampCell("Envelope opened", card.opened_at) : null,
      timestampCell("Committed", card.committed_at, "Not yet committed"),
      timestampCell("Delivered", card.delivered_at, "In flight"),
      leadTimeCell(card)
    )
  );
}

function timestampCell(label, value, fallback = "—") {
  return el(
    "div",
    { className: "cd-ts" },
    el("div", { className: "cd-ts-label" }, label),
    value
      ? el("div", { className: "cd-ts-value", title: absoluteTime(value) }, formatDate(value))
      : el("div", { className: "cd-ts-value subtle" }, fallback)
  );
}

/**
 * The single number a reader wants first: how long has this taken so far, or
 * how long did it take. System lead time (commitment → delivery) per SPEC §7.1,
 * falling back to age since commitment while the card is still in flight.
 */
function leadTimeCell(card) {
  const committed = parseTime(card.committed_at);
  const delivered = parseTime(card.delivered_at);

  if (committed && delivered) {
    return el(
      "div",
      { className: "cd-ts" },
      el("div", { className: "cd-ts-label" }, "System lead time"),
      el("div", { className: "cd-ts-value t-semibold" }, formatDuration((delivered - committed) / 1000, { style: "days" }))
    );
  }
  if (committed) {
    return el(
      "div",
      { className: "cd-ts" },
      el("div", { className: "cd-ts-label" }, "Age since commitment"),
      el("div", { className: "cd-ts-value t-semibold" }, formatDuration((Date.now() - committed) / 1000, { style: "days" }))
    );
  }
  const created = parseTime(card.opened_at || card.created_at);
  return el(
    "div",
    { className: "cd-ts" },
    el("div", { className: "cd-ts-label" }, "Age in discovery"),
    el("div", { className: "cd-ts-value t-semibold" }, created ? formatDuration((Date.now() - created) / 1000, { style: "days" }) : "—")
  );
}

function parseTime(value) {
  if (!value) return null;
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
  const ts = Date.parse(normalized);
  return Number.isNaN(ts) ? null : ts;
}

/* ==========================================================================
   Tab — Details
   ========================================================================== */

function detailsTab(card, ctx, editable) {
  const projects = typeof ctx.projects === "function" ? ctx.projects() : [];
  const people = typeof ctx.people === "function" ? ctx.people() : [];
  const flagCustomFields = store.hasFlag("custom_fields");
  const flagServiceClasses = store.hasFlag("service_classes");

  const root = el("div", { className: "stack stack-6" });

  /* -- title & description ------------------------------------------------ */
  const titleInput = autoTextarea({
    className: "cd-title-input",
    value: card.title || "",
    ariaLabel: "Card title",
    disabled: !editable,
    onCommit: (value) => {
      const trimmed = value.trim();
      if (!trimmed || trimmed === card.title) return null;
      return { title: trimmed };
    },
    card,
    ctx,
    revert: () => card.title || "",
    onApplied: () => {
      if (openHandle) openHandle.setTitle(card.title || "Untitled card");
    },
  });

  const descriptionInput = autoTextarea({
    className: "textarea cd-description",
    value: card.description || "",
    placeholder: editable ? "Add context: what problem does this solve, and how will we know it worked?" : "No description",
    ariaLabel: "Card description",
    disabled: !editable,
    minRows: 3,
    onCommit: (value) => (value === (card.description || "") ? null : { description: value }),
    card,
    ctx,
    revert: () => card.description || "",
  });

  root.append(
    el("div", { className: "stack stack-2" }, titleInput, descriptionInput)
  );

  /* -- classification grid ------------------------------------------------ */
  const grid = el("div", { className: "cd-grid" });

  grid.append(
    fieldRow(
      "Project",
      selectControl({
        value: card.project_id || "",
        disabled: !editable,
        ariaLabel: "Project",
        options: [{ value: "", label: "No project" }].concat(
          projects.map((p) => ({ value: p.id, label: p.key ? `${p.key} · ${p.name}` : p.name }))
        ),
        onCommit: (value) => (value === (card.project_id || "") ? null : { project_id: value || null }),
        card,
        ctx,
        revert: () => card.project_id || "",
      })
    )
  );

  if (flagServiceClasses) {
    const scSelect = selectControl({
      value: card.service_class,
      disabled: !editable,
      ariaLabel: "Class of service",
      options: SERVICE_CLASSES.map((s) => ({ value: s.id, label: s.label })),
      onCommit: (value) => (value === card.service_class ? null : { service_class: value }),
      card,
      ctx,
      revert: () => card.service_class,
      onApplied: () => { policyNote.textContent = SERVICE_CLASS_MAP[card.service_class]?.policy || ""; },
    });
    const policyNote = el("p", { className: "cd-policy" }, SERVICE_CLASS_MAP[card.service_class]?.policy || "");
    grid.append(
      fieldRow(
        "Class of service",
        el("div", { className: "stack stack-2" }, scSelect, policyNote)
      )
    );
  }

  grid.append(
    fieldRow(
      "Assignee",
      selectControl({
        value: card.assignee_id || "",
        disabled: !editable,
        ariaLabel: "Assignee",
        options: [{ value: "", label: "Unassigned" }].concat(
          people.map((p) => ({ value: p.id, label: p.name }))
        ),
        onCommit: (value) => (value === (card.assignee_id || "") ? null : { assignee_id: value || null }),
        card,
        ctx,
        revert: () => card.assignee_id || "",
      })
    ),
    fieldRow(
      "Requester",
      inputControl({
        value: card.requester || "",
        disabled: !editable,
        placeholder: "Who is asking for this?",
        ariaLabel: "Requester",
        onCommit: (value) => (value === (card.requester || "") ? null : { requester: value || null }),
        card,
        ctx,
        revert: () => card.requester || "",
      })
    ),
    fieldRow(
      "Size",
      selectControl({
        value: card.size || "",
        disabled: !editable,
        ariaLabel: "Size",
        options: [{ value: "", label: "Unsized" }].concat(CARD_SIZES.map((s) => ({ value: s.id, label: s.label }))),
        onCommit: (value) => (value === (card.size || "") ? null : { size: value || null }),
        card,
        ctx,
        revert: () => card.size || "",
      })
    ),
    fieldRow(
      "Value area",
      selectControl({
        value: card.value_area || "feature",
        disabled: !editable,
        ariaLabel: "Value area",
        options: VALUE_AREAS.map((v) => ({ value: v.id, label: v.label })),
        onCommit: (value) => (value === card.value_area ? null : { value_area: value }),
        card,
        ctx,
        revert: () => card.value_area || "feature",
      })
    )
  );

  const demandSelect = selectControl({
    value: card.demand_type || "value",
    disabled: !editable,
    ariaLabel: "Demand type",
    options: DEMAND_TYPES.map((d) => ({ value: d.id, label: d.label })),
    onCommit: (value) => (value === card.demand_type ? null : { demand_type: value }),
    card,
    ctx,
    revert: () => card.demand_type || "value",
    onApplied: () => {
      demandNote.textContent = DEMAND_TYPES.find((d) => d.id === card.demand_type)?.policy || "";
    },
  });
  const demandNote = el("p", { className: "cd-policy" },
    DEMAND_TYPES.find((d) => d.id === (card.demand_type || "value"))?.policy || "");

  grid.append(
    fieldRow("Demand type", el("div", { className: "stack stack-2" }, demandSelect, demandNote)),
    fieldRow(
      "Due date",
      el(
        "div",
        { className: "stack stack-2" },
        inputControl({
          type: "date",
          value: (card.due_date || "").slice(0, 10),
          disabled: !editable,
          ariaLabel: "Due date",
          onCommit: (value) => (value === (card.due_date || "").slice(0, 10) ? null : { due_date: value || null }),
          card,
          ctx,
          revert: () => (card.due_date || "").slice(0, 10),
        }),
        card.service_class === "fixed_date" && !card.due_date
          ? el("p", { className: "cd-policy cd-policy-warn" },
              "A fixed-date card without a date cannot be scheduled or measured for due-date performance.")
          : null
      )
    )
  );

  root.append(grid);

  /* -- tags --------------------------------------------------------------- */
  root.append(
    section(
      "Tags",
      tagEditor(card, ctx, editable)
    )
  );

  /* -- custom fields ------------------------------------------------------ */
  if (flagCustomFields) {
    root.append(section("Custom fields", customFieldsEditor(card, ctx, editable)));
  }

  return root;
}

function section(title, body) {
  return el(
    "section",
    { className: "stack stack-2" },
    el("div", { className: "eyebrow" }, title),
    body
  );
}

function fieldRow(label, control) {
  return el(
    "div",
    { className: "cd-row" },
    el("div", { className: "cd-row-label" }, label),
    el("div", { className: "cd-row-control" }, control)
  );
}

/* ---- inline-editing controls --------------------------------------------
   Each control PATCHes on blur (or on change, for selects — waiting for blur
   on a <select> makes it feel broken), applies optimistically, and restores
   the previous value if the server refuses. */

async function commitPatch({ card, ctx, patch, revertValue, node, onApplied }) {
  const before = {};
  for (const key of Object.keys(patch)) before[key] = card[key];

  Object.assign(card, patch);
  if (ctx.applyCardPatch) ctx.applyCardPatch(card);
  if (onApplied) onApplied();
  if (node) node.classList.add("is-saving");

  try {
    const updated = await api.patch(`/cards/${card.id}`, patch, { silent: true });
    if (updated && typeof updated === "object") Object.assign(card, updated);
    if (ctx.applyCardPatch) ctx.applyCardPatch(card);
    if (onApplied) onApplied();
    if (node) {
      node.classList.remove("is-saving");
      node.classList.add("is-saved");
      window.setTimeout(() => node.classList.remove("is-saved"), 900);
    }
  } catch (error) {
    Object.assign(card, before);
    if (ctx.applyCardPatch) ctx.applyCardPatch(card);
    if (node) {
      node.classList.remove("is-saving");
      if (revertValue !== undefined) node.value = revertValue;
      node.classList.add("is-error");
      window.setTimeout(() => node.classList.remove("is-error"), 1600);
    }
    if (onApplied) onApplied();
    toast(error?.message || "That change could not be saved.", {
      tone: "danger",
      title: "Change reverted",
    });
  }
}

function inputControl({ value, type = "text", placeholder, disabled, ariaLabel, onCommit, card, ctx, revert }) {
  const node = el("input", {
    className: "input input-sm cd-input",
    type,
    value: value ?? "",
    placeholder: placeholder || "",
    disabled: Boolean(disabled),
    "aria-label": ariaLabel,
  });

  node.addEventListener("blur", () => {
    const patch = onCommit(node.value);
    if (patch) commitPatch({ card, ctx, patch, revertValue: revert(), node });
  });
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && type !== "date") { event.preventDefault(); node.blur(); }
    if (event.key === "Escape") { event.stopPropagation(); node.value = revert(); node.blur(); }
  });
  return node;
}

function selectControl({ value, options, disabled, ariaLabel, onCommit, card, ctx, revert, onApplied }) {
  const node = el(
    "select",
    { className: "select select-sm cd-input", disabled: Boolean(disabled), "aria-label": ariaLabel },
    options.map((opt) => el("option", { value: opt.value }, opt.label))
  );
  // Options must exist before the value takes, so assign after construction.
  node.value = value ?? "";

  node.addEventListener("change", () => {
    const patch = onCommit(node.value);
    if (patch) commitPatch({ card, ctx, patch, revertValue: revert(), node, onApplied });
  });
  return node;
}

function autoTextarea({ className, value, placeholder, ariaLabel, disabled, minRows = 1, onCommit, card, ctx, revert, onApplied }) {
  const node = el("textarea", {
    className,
    value: value ?? "",
    placeholder: placeholder || "",
    rows: minRows,
    disabled: Boolean(disabled),
    "aria-label": ariaLabel,
  });

  function grow() {
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight + 2}px`;
  }
  node.addEventListener("input", grow);
  node.addEventListener("blur", () => {
    const patch = onCommit(node.value);
    if (patch) commitPatch({ card, ctx, patch, revertValue: revert(), node, onApplied });
  });
  node.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.stopPropagation(); node.value = revert(); grow(); node.blur(); }
  });
  // The textarea is not in the document yet, so scrollHeight is 0 until layout.
  window.requestAnimationFrame(grow);
  return node;
}

/* ---- tags ---------------------------------------------------------------- */

function tagEditor(card, ctx, editable) {
  const wrap = el("div", { className: "cd-tags" });

  function paint() {
    clear(wrap);
    const tags = Array.isArray(card.tags) ? card.tags : [];

    if (!tags.length && !editable) {
      wrap.append(el("span", { className: "t-sm subtle" }, "No tags"));
      return;
    }

    for (const tag of tags) {
      wrap.append(
        el(
          "span",
          { className: "chip" },
          icon("tag"),
          tag,
          editable
            ? el(
                "button",
                {
                  className: "chip-remove",
                  "aria-label": `Remove tag ${tag}`,
                  onClick: () => {
                    const next = tags.filter((t) => t !== tag);
                    commitPatch({ card, ctx, patch: { tags: next } }).then(paint);
                  },
                },
                icon("x")
              )
            : null
        )
      );
    }

    if (editable) {
      const input = el("input", {
        className: "input input-sm cd-tag-input",
        placeholder: "Add tag…",
        "aria-label": "Add a tag",
      });
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== ",") return;
        event.preventDefault();
        const value = input.value.trim().replace(/,+$/, "");
        if (!value) return;
        const current = Array.isArray(card.tags) ? card.tags : [];
        if (current.includes(value)) { input.value = ""; return; }
        commitPatch({ card, ctx, patch: { tags: current.concat(value) } }).then(() => {
          paint();
          const next = wrap.querySelector(".cd-tag-input");
          if (next) next.focus();
        });
        input.value = "";
      });
      wrap.append(input);
    }
  }

  paint();
  return wrap;
}

/* ---- custom fields ------------------------------------------------------- */

function customFieldsEditor(card, ctx, editable) {
  const wrap = el("div", { className: "stack stack-2" });

  function paint() {
    clear(wrap);
    const fields = card.custom_fields && typeof card.custom_fields === "object" ? card.custom_fields : {};
    const entries = Object.entries(fields);

    if (!entries.length) {
      wrap.append(el("p", { className: "t-sm subtle" }, editable ? "No custom fields yet." : "No custom fields."));
    }

    for (const [key, value] of entries) {
      const valueInput = el("input", {
        className: "input input-sm",
        value: value === null || value === undefined ? "" : String(value),
        disabled: !editable,
        "aria-label": `Value of ${key}`,
      });
      valueInput.addEventListener("blur", () => {
        const current = { ...fields };
        if (String(current[key] ?? "") === valueInput.value) return;
        current[key] = valueInput.value;
        commitPatch({ card, ctx, patch: { custom_fields: current }, node: valueInput }).then(paint);
      });

      wrap.append(
        el(
          "div",
          { className: "cd-cf-row" },
          el("span", { className: "cd-cf-key t-mono truncate", title: key }, key),
          valueInput,
          editable
            ? el(
                "button",
                {
                  className: "btn btn-ghost btn-icon btn-sm",
                  "aria-label": `Remove field ${key}`,
                  onClick: () => {
                    const current = { ...fields };
                    delete current[key];
                    commitPatch({ card, ctx, patch: { custom_fields: current } }).then(paint);
                  },
                },
                icon("trash")
              )
            : null
        )
      );
    }

    if (editable) {
      const keyInput = el("input", { className: "input input-sm", placeholder: "Field name", "aria-label": "New field name" });
      const valueInput = el("input", { className: "input input-sm", placeholder: "Value", "aria-label": "New field value" });
      const addButton = el(
        "button",
        {
          className: "btn btn-secondary btn-sm",
          onClick: () => {
            const key = keyInput.value.trim();
            if (!key) { keyInput.focus(); return; }
            const current = { ...fields, [key]: valueInput.value };
            commitPatch({ card, ctx, patch: { custom_fields: current } }).then(paint);
          },
        },
        icon("plus"),
        "Add"
      );
      wrap.append(el("div", { className: "cd-cf-row" }, keyInput, valueInput, addButton));
    }
  }

  paint();
  return wrap;
}

/* ==========================================================================
   Tab — Tasks
   ========================================================================== */

function tasksTab(card, ctx, editable) {
  const root = el("div", { className: "stack stack-4" });
  const list = el("div", { className: "stack stack-1" });
  const progressWrap = el("div", { className: "stack stack-2" });

  function tasks() {
    return Array.isArray(card.tasks) ? card.tasks : [];
  }

  function paintProgress() {
    clear(progressWrap);
    const all = tasks();
    const done = all.filter((t) => t.is_done).length;
    const pct = all.length ? (done / all.length) * 100 : 0;
    progressWrap.append(
      el(
        "div",
        { className: "row row-between" },
        el("span", { className: "eyebrow" }, "Checklist"),
        el("span", { className: "t-sm muted numeric" }, all.length ? `${done}/${all.length} · ${formatPercent(pct)}` : "No tasks")
      ),
      el(
        "div",
        { className: "progress", role: "progressbar", "aria-valuenow": String(Math.round(pct)), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": "Checklist progress" },
        el("div", { className: "progress-bar", style: { width: `${pct}%` } })
      )
    );
    // Keep the compact board card in step with the drawer.
    card.task_total = all.length;
    card.task_done = done;
    if (ctx.applyCardPatch) ctx.applyCardPatch(card);
  }

  function paintList() {
    clear(list);
    const all = tasks();

    if (!all.length) {
      list.append(el("p", { className: "t-sm subtle" }, editable
        ? "Break the work down so progress is visible before the card moves."
        : "This card has no checklist."));
      return;
    }

    for (const task of all) {
      const checkbox = el("input", {
        className: "checkbox",
        type: "checkbox",
        checked: Boolean(task.is_done),
        disabled: !editable,
        "aria-label": task.title,
      });

      checkbox.addEventListener("change", async () => {
        const next = checkbox.checked;
        task.is_done = next;
        paintProgress();
        row.classList.toggle("is-done", next);
        try {
          const updated = await api.patch(`/tasks/${task.id}`, { is_done: next }, { silent: true });
          if (updated && typeof updated === "object") Object.assign(task, updated);
          paintProgress();
        } catch (error) {
          task.is_done = !next;
          checkbox.checked = !next;
          row.classList.toggle("is-done", !next);
          paintProgress();
          toast(error?.message || "The task could not be updated.", { tone: "danger", title: "Change reverted" });
        }
      });

      const row = el(
        "div",
        { className: `cd-task${task.is_done ? " is-done" : ""}` },
        checkbox,
        el("span", { className: "cd-task-title" }, task.title),
        editable
          ? el(
              "button",
              {
                className: "btn btn-ghost btn-icon btn-sm cd-task-delete",
                "aria-label": `Delete task ${task.title}`,
                onClick: async () => {
                  const index = tasks().indexOf(task);
                  card.tasks = tasks().filter((t) => t !== task);
                  paintList();
                  paintProgress();
                  try {
                    await api.del(`/tasks/${task.id}`, { silent: true });
                  } catch (error) {
                    const restored = tasks().slice();
                    restored.splice(index < 0 ? restored.length : index, 0, task);
                    card.tasks = restored;
                    paintList();
                    paintProgress();
                    toast(error?.message || "The task could not be deleted.", { tone: "danger", title: "Change reverted" });
                  }
                },
              },
              icon("trash")
            )
          : null
      );

      list.append(row);
    }
  }

  paintProgress();
  paintList();
  root.append(progressWrap, list);

  if (editable) {
    const input = el("input", { className: "input input-sm", placeholder: "Add a task and press Enter", "aria-label": "New task" });

    async function addTask() {
      const title = input.value.trim();
      if (!title) return;
      input.value = "";

      // Optimistic row with a temporary id; replaced by the server's task.
      const optimistic = { id: `pending-${Date.now()}`, card_id: card.id, title, is_done: false, position: tasks().length };
      card.tasks = tasks().concat(optimistic);
      paintList();
      paintProgress();

      try {
        const created = await api.post(`/cards/${card.id}/tasks`, { title }, { silent: true });
        if (created && created.id) Object.assign(optimistic, created);
        paintList();
        paintProgress();
      } catch (error) {
        card.tasks = tasks().filter((t) => t !== optimistic);
        paintList();
        paintProgress();
        toast(error?.message || "The task could not be added.", { tone: "danger", title: "Not saved" });
      }
    }

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); addTask(); }
    });

    root.append(
      el(
        "div",
        { className: "row row-1" },
        input,
        el("button", { className: "btn btn-secondary btn-sm", onClick: addTask }, icon("plus"), "Add")
      )
    );
  }

  return root;
}

/* ==========================================================================
   Tab — History
   ========================================================================== */

function historyTab(card, ctx) {
  const root = el("div", { className: "stack stack-6" });

  if (!store.hasFlag("card_history")) {
    root.append(emptyState({
      icon: "lock",
      title: "Movement history is switched off",
      body: "The card history capability is disabled for this workspace. An administrator can enable it under Admin › Feature flags.",
    }));
    return root;
  }

  root.append(el("div", { className: "stack stack-4" }, skeleton({ height: 54 }), skeletonText(5)));

  api.get(`/cards/${card.id}/history`, { silent: true })
    .then((history) => {
      clear(root);
      root.append(flowEfficiencyPanel(card, history, ctx), timeline(card, history, ctx));
    })
    .catch((error) => {
      clear(root);
      root.append(errorState(error, () => {
        clear(root);
        root.append(historyTab(card, ctx));
      }, { title: "History could not be loaded" }));
    });

  return root;
}

/**
 * Sum the card's per-column dwell into touch / wait / envelope buckets.
 *
 * `seconds_in_from_column` on each movement is authoritative for closed
 * intervals (SPEC §6.8). The open interval — the column the card sits in right
 * now — is computed from the last movement, because the server has not had a
 * reason to write it down yet.
 */
function dwellBreakdown(card, movements, ctx) {
  const byColumn = new Map();
  const buckets = { touch: 0, wait: 0, envelope: 0, none: 0 };
  const sorted = movements.slice().sort((a, b) => String(a.moved_at).localeCompare(String(b.moved_at)));

  const add = (columnId, seconds) => {
    if (!columnId || !seconds || seconds < 0) return;
    const column = ctx.columnById ? ctx.columnById(columnId) : null;
    const kind = column?.kind || "queue";
    byColumn.set(columnId, (byColumn.get(columnId) || 0) + seconds);
    buckets[columnKindMeta(kind).accrues] += seconds;
  };

  for (const movement of sorted) {
    add(movement.from_column_id, Number(movement.seconds_in_from_column || 0));
  }

  const last = sorted[sorted.length - 1];
  let openSeconds = 0;
  if (last && !card.delivered_at) {
    const since = parseTime(last.moved_at);
    if (since) {
      openSeconds = Math.max(0, (Date.now() - since) / 1000);
      add(last.to_column_id, openSeconds);
    }
  }

  const efficiencyBase = buckets.touch + buckets.wait;
  return {
    byColumn,
    buckets,
    openSeconds,
    openColumnId: last && !card.delivered_at ? last.to_column_id : null,
    flowEfficiency: efficiencyBase > 0 ? (buckets.touch / efficiencyBase) * 100 : null,
    sorted,
  };
}

function flowEfficiencyPanel(card, history, ctx) {
  const movements = history?.movements || card.movements || [];
  const breakdown = dwellBreakdown(card, movements, ctx);
  const { touch, wait, envelope } = breakdown.buckets;
  const total = touch + wait + envelope;

  if (!total) {
    return el(
      "div",
      { className: "panel" },
      el("div", { className: "panel-body" },
        el("p", { className: "t-sm subtle" }, "This card has not accumulated measurable time yet."))
    );
  }

  const pct = (value) => (value / total) * 100;

  return el(
    "div",
    { className: "panel cd-flow" },
    el(
      "div",
      { className: "panel-header" },
      el("span", { className: "panel-title" }, "Touch vs wait"),
      el(
        "span",
        { className: "badge", dataset: { tone: breakdown.flowEfficiency !== null && breakdown.flowEfficiency < 15 ? "danger" : "accent" } },
        breakdown.flowEfficiency === null ? "—" : `${formatPercent(breakdown.flowEfficiency)} flow efficiency`
      )
    ),
    el(
      "div",
      { className: "panel-body stack stack-3" },
      el(
        "div",
        { className: "cd-tw-bar", role: "img", "aria-label": `Touch time ${formatDuration(touch)}, wait time ${formatDuration(wait)}` },
        touch > 0 ? el("span", { dataset: { seg: "touch" }, style: { width: `${pct(touch)}%` }, title: `Touch · ${formatDuration(touch)}` }) : null,
        wait > 0 ? el("span", { dataset: { seg: "wait" }, style: { width: `${pct(wait)}%` }, title: `Wait · ${formatDuration(wait)}` }) : null,
        envelope > 0 ? el("span", { dataset: { seg: "envelope" }, style: { width: `${pct(envelope)}%` }, title: `Sealed · ${formatDuration(envelope)}` }) : null
      ),
      el(
        "div",
        { className: "cd-tw-legend" },
        legendItem("touch", "Touch", touch),
        legendItem("wait", "Wait", wait),
        envelope > 0 ? legendItem("envelope", "Sealed", envelope) : null
      ),
      el("p", { className: "cd-policy" },
        "Touch time is time somebody was working on this card; wait time is time it sat in a queue. "
        + "Lead time falls when wait time falls — not when people work faster.")
    )
  );
}

function legendItem(seg, label, seconds) {
  return el(
    "span",
    { className: "cd-tw-legend-item" },
    el("span", { className: "cd-tw-swatch", dataset: { seg } }),
    el("span", { className: "t-xs muted" }, label),
    el("span", { className: "t-xs t-semibold numeric" }, formatDuration(seconds))
  );
}

/** Event types whose story is already told by the movement rows. */
const REDUNDANT_EVENTS = new Set(["moved"]);

const EVENT_META = {
  created: { icon: "sparkle", text: () => "Card created" },
  updated: { icon: "edit", text: (e) => `${humanize(e.field || "field")} changed` },
  blocked: { icon: "block", text: () => "Blocked", tone: "danger" },
  unblocked: { icon: "check", text: () => "Unblocked", tone: "success" },
  opened_envelope: { icon: "envelope-open", text: () => "Envelope opened", tone: "accent" },
  archived: { icon: "archive", text: () => "Archived" },
  commented: { icon: "users", text: () => "Comment added" },
  task_added: { icon: "plus", text: (e) => `Task added${e.new_value ? `: ${e.new_value}` : ""}` },
  task_completed: { icon: "check", text: (e) => `Task completed${e.new_value ? `: ${e.new_value}` : ""}`, tone: "success" },
};

function timeline(card, history, ctx) {
  const movements = (history?.movements || card.movements || []).slice();
  const events = (history?.events || []).filter((e) => !REDUNDANT_EVENTS.has(e.event_type));
  const breakdown = dwellBreakdown(card, movements, ctx);

  const entries = [];

  for (const movement of movements) {
    entries.push({ at: movement.moved_at, kind: "movement", data: movement });
  }
  for (const event of events) {
    entries.push({ at: event.created_at, kind: "event", data: event });
  }
  entries.sort((a, b) => String(a.at).localeCompare(String(b.at)));

  if (!entries.length) {
    return emptyState({
      icon: "clock",
      title: "No history yet",
      body: "Movements and edits appear here as soon as this card starts flowing.",
    });
  }

  const list = el("ol", { className: "cd-timeline", role: "list" });

  for (const entry of entries) {
    list.append(entry.kind === "movement"
      ? movementNode(entry.data, ctx)
      : eventNode(entry.data));
  }

  // The open interval: where the card is right now, and for how long.
  if (breakdown.openColumnId) {
    const column = ctx.columnById ? ctx.columnById(breakdown.openColumnId) : null;
    const meta = columnKindMeta(column?.kind);
    list.append(
      el(
        "li",
        { className: "cd-tl-item is-open" },
        el("span", { className: "cd-tl-dot", dataset: { tone: "accent" } }, icon("target")),
        el(
          "div",
          { className: "cd-tl-body" },
          el("div", { className: "cd-tl-title" }, "Here now", el("span", { className: "cd-tl-col" }, column?.name || "current column")),
          el(
            "div",
            { className: "cd-tl-meta" },
            el("span", { className: "cd-dwell", dataset: { accrues: meta.accrues } },
              icon(meta.icon), `${formatDuration(breakdown.openSeconds)} ${meta.accrues === "touch" ? "touch" : "wait"} so far`)
          )
        )
      )
    );
  }

  return el(
    "div",
    { className: "stack stack-3" },
    el("div", { className: "eyebrow" }, "Timeline"),
    list
  );
}

function movementNode(movement, ctx) {
  const fromColumn = movement.from_column_id && ctx.columnById ? ctx.columnById(movement.from_column_id) : null;
  const fromName = movement.from_column_name || fromColumn?.name;
  const toName = movement.to_column_name
    || (ctx.columnById ? ctx.columnById(movement.to_column_id)?.name : null)
    || "a column";

  const dwellSeconds = Number(movement.seconds_in_from_column || 0);
  const fromMeta = columnKindMeta(fromColumn?.kind);

  return el(
    "li",
    {
      className: "cd-tl-item",
      dataset: movement.is_backward ? { backward: "" } : {},
    },
    el(
      "span",
      { className: "cd-tl-dot", dataset: { tone: movement.is_backward ? "danger" : "neutral" } },
      icon(movement.is_backward ? "arrow-up" : "arrow-right")
    ),
    el(
      "div",
      { className: "cd-tl-body" },
      el(
        "div",
        { className: "cd-tl-title" },
        fromName ? el("span", { className: "cd-tl-col" }, fromName) : el("span", { className: "cd-tl-col" }, "Created"),
        icon("arrow-right"),
        el("span", { className: "cd-tl-col" }, toName),
        movement.is_backward ? el("span", { className: "badge", dataset: { tone: "danger" } }, "Backward") : null
      ),
      el(
        "div",
        { className: "cd-tl-meta" },
        el("span", { title: absoluteTime(movement.moved_at) }, relativeTime(movement.moved_at)),
        fromName && dwellSeconds > 0
          ? el(
              "span",
              { className: "cd-dwell", dataset: { accrues: fromMeta.accrues } },
              icon(fromMeta.icon),
              `${formatDuration(dwellSeconds)} in ${fromName}`
            )
          : null,
        movement.moved_by_name ? el("span", null, `by ${movement.moved_by_name}`) : null,
        movement.source && movement.source !== "ui"
          ? el("span", { className: "badge" }, humanize(movement.source))
          : null,
        movement.note ? el("span", { className: "cd-tl-note" }, movement.note) : null
      )
    )
  );
}

function eventNode(event) {
  const meta = EVENT_META[event.event_type] || { icon: "info", text: () => humanize(event.event_type) };

  return el(
    "li",
    { className: "cd-tl-item is-event" },
    el("span", { className: "cd-tl-dot", dataset: { tone: meta.tone || "neutral" } }, icon(meta.icon)),
    el(
      "div",
      { className: "cd-tl-body" },
      el("div", { className: "cd-tl-title" }, meta.text(event)),
      el(
        "div",
        { className: "cd-tl-meta" },
        el("span", { title: absoluteTime(event.created_at) }, relativeTime(event.created_at)),
        event.actor_name ? el("span", null, `by ${event.actor_name}`) : null,
        event.event_type === "updated" && (event.old_value || event.new_value)
          ? el(
              "span",
              { className: "cd-tl-diff" },
              el("s", null, truncate(event.old_value) || "empty"),
              icon("arrow-right"),
              el("strong", null, truncate(event.new_value) || "empty")
            )
          : null
      )
    )
  );
}

function truncate(value, max = 48) {
  const text = value === null || value === undefined ? "" : String(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/* ==========================================================================
   Tab — Blockers
   ========================================================================== */

function blockersTab(card, ctx, editable, refreshCard) {
  const root = el("div", { className: "stack stack-5" });
  const blockers = Array.isArray(card.blockers) ? card.blockers.slice() : [];
  blockers.sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
  const open = blockers.find((b) => !b.ended_at);

  if (editable) {
    root.append(open ? unblockForm(card, ctx, open, refreshCard) : blockForm(card, ctx, refreshCard));
  } else if (open) {
    root.append(el("div", { className: "alert", dataset: { tone: "danger" } }, icon("block"),
      `Blocked ${relativeTime(open.started_at)} — ${open.reason}`));
  }

  if (!blockers.length) {
    root.append(emptyState({
      icon: "check",
      title: "Never blocked",
      body: "Blockers recorded here feed the blocker pareto and the blocked-time metric.",
    }));
    return root;
  }

  const list = el("div", { className: "stack stack-2" });
  for (const blocker of blockers) {
    const started = parseTime(blocker.started_at);
    const ended = parseTime(blocker.ended_at);
    const seconds = blocker.duration_seconds ?? (started ? ((ended || Date.now()) - started) / 1000 : null);

    list.append(
      el(
        "div",
        { className: "cd-blocker", dataset: blocker.ended_at ? {} : { open: "" } },
        el(
          "div",
          { className: "row row-between row-start" },
          el("div", { className: "stack stack-1" },
            el("div", { className: "t-sm t-semibold" }, blocker.reason),
            el("div", { className: "row row-1 row-wrap t-xs subtle" },
              el("span", { className: "chip" }, humanize(blocker.category || "other")),
              el("span", { title: absoluteTime(blocker.started_at) }, `Started ${relativeTime(blocker.started_at)}`),
              blocker.ended_at
                ? el("span", { title: absoluteTime(blocker.ended_at) }, `Cleared ${relativeTime(blocker.ended_at)}`)
                : null)),
          el("span", { className: "badge", dataset: { tone: blocker.ended_at ? "neutral" : "danger" } },
            seconds !== null ? formatDuration(seconds) : "—")
        )
      )
    );
  }
  root.append(section("Blocker history", list));

  return root;
}

function blockForm(card, ctx, refreshCard) {
  const reason = el("input", { className: "input input-sm", placeholder: "What is blocking this card?", "aria-label": "Blocker reason" });
  const category = el(
    "select",
    { className: "select select-sm", "aria-label": "Blocker category" },
    BLOCKER_CATEGORIES.map((c) => el("option", { value: c.id }, c.label))
  );
  category.value = "dependency";

  const button = el(
    "button",
    {
      className: "btn btn-danger btn-sm",
      onClick: async () => {
        const text = reason.value.trim();
        if (!text) { reason.focus(); return; }
        button.classList.add("is-loading");
        try {
          await api.post(`/cards/${card.id}/block`, { reason: text, category: category.value }, { silent: true });
          card.is_blocked = true;
          if (ctx.applyCardPatch) ctx.applyCardPatch(card);
          announce("Card blocked");
          await refreshCard();
        } catch (error) {
          toast(error?.message || "The card could not be blocked.", { tone: "danger" });
        } finally {
          button.classList.remove("is-loading");
        }
      },
    },
    icon("block"),
    "Block card"
  );

  return el(
    "div",
    { className: "panel" },
    el("div", { className: "panel-body stack stack-3" },
      el("p", { className: "cd-policy" },
        "Blocking stops the card's progress but not its clock. Record the reason — the pareto of blocker "
        + "categories is usually the cheapest improvement a team can find."),
      reason,
      el("div", { className: "row row-2" }, category, el("div", { className: "spacer" }), button))
  );
}

function unblockForm(card, ctx, blocker, refreshCard) {
  const button = el(
    "button",
    {
      className: "btn btn-secondary btn-sm",
      onClick: async () => {
        button.classList.add("is-loading");
        try {
          await api.post(`/cards/${card.id}/unblock`, {}, { silent: true });
          card.is_blocked = false;
          if (ctx.applyCardPatch) ctx.applyCardPatch(card);
          announce("Card unblocked");
          await refreshCard();
        } catch (error) {
          toast(error?.message || "The card could not be unblocked.", { tone: "danger" });
        } finally {
          button.classList.remove("is-loading");
        }
      },
    },
    icon("check"),
    "Unblock"
  );

  return el(
    "div",
    { className: "alert", dataset: { tone: "danger" } },
    icon("block"),
    el(
      "div",
      { className: "stack stack-1", style: { flex: "1 1 auto" } },
      el("div", { className: "t-semibold" }, blocker.reason),
      el("div", { className: "t-xs" }, `${humanize(blocker.category || "other")} · blocked ${relativeTime(blocker.started_at)}`)
    ),
    button
  );
}

/* ==========================================================================
   Tab — Comments
   ========================================================================== */

function commentsTab(card, ctx) {
  const root = el("div", { className: "stack stack-4" });
  const list = el("div", { className: "stack stack-3" });

  function paint() {
    clear(list);
    const comments = (Array.isArray(card.comments) ? card.comments : [])
      .slice()
      .sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));

    if (!comments.length) {
      list.append(el("p", { className: "t-sm subtle" }, "No comments yet."));
      return;
    }

    for (const comment of comments) {
      list.append(
        el(
          "article",
          { className: "cd-comment" },
          avatar({ id: comment.author_id, name: comment.author_name }, { size: "sm" }),
          el(
            "div",
            { className: "stack stack-1", style: { minWidth: 0 } },
            el(
              "div",
              { className: "row row-1 t-xs subtle" },
              el("span", { className: "t-semibold", style: { color: "var(--fg)" } }, comment.author_name || "Someone"),
              el("span", { title: absoluteTime(comment.created_at) }, relativeTime(comment.created_at))
            ),
            el("div", { className: "cd-comment-body" }, comment.body)
          )
        )
      );
    }
  }

  paint();
  root.append(list);

  const user = store.get().user;
  if (user) {
    const box = el("textarea", {
      className: "textarea",
      rows: 3,
      placeholder: "Add a comment…",
      "aria-label": "New comment",
    });

    const submit = el(
      "button",
      {
        className: "btn btn-primary btn-sm",
        onClick: async () => {
          const body = box.value.trim();
          if (!body) { box.focus(); return; }
          submit.classList.add("is-loading");

          const optimistic = {
            id: `pending-${Date.now()}`,
            card_id: card.id,
            author_id: user.id,
            author_name: user.name,
            body,
            created_at: new Date().toISOString(),
          };
          card.comments = (card.comments || []).concat(optimistic);
          box.value = "";
          paint();

          try {
            const created = await api.post(`/cards/${card.id}/comments`, { body }, { silent: true });
            if (created && created.id) Object.assign(optimistic, created);
            paint();
          } catch (error) {
            card.comments = (card.comments || []).filter((c) => c !== optimistic);
            box.value = body;
            paint();
            toast(error?.message || "The comment could not be posted.", { tone: "danger", title: "Not saved" });
          } finally {
            submit.classList.remove("is-loading");
          }
        },
      },
      "Comment"
    );

    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); submit.click(); }
    });

    root.append(
      el("div", { className: "stack stack-2" }, box,
        el("div", { className: "row row-end" },
          el("span", { className: "t-xs subtle spacer" }, "⌘/Ctrl + Enter to post"), submit))
    );
  }

  return root;
}

/* ==========================================================================
   Header actions menu
   ========================================================================== */

function openCardActionsMenu(anchor, card, ctx, refreshCard) {
  const items = [];

  if (ctx.moveCard) {
    items.push(
      el(
        "button",
        {
          className: "menu-item",
          onClick: () => openMoveTargets(anchor, card, ctx),
        },
        icon("arrow-right"),
        "Move card",
        el("span", { className: "menu-shortcut" }, "m")
      )
    );
  }

  if (card.envelope_state === "sealed") {
    items.push(
      el(
        "button",
        {
          className: "menu-item",
          onClick: async () => {
            try {
              await api.post(`/cards/${card.id}/open-envelope`, {}, { silent: true });
              toast("Envelope opened. The demand is now in the upstream flow.", { tone: "success" });
              if (ctx.refresh) ctx.refresh({ silent: true });
              await refreshCard();
            } catch (error) {
              toast(error?.message || "The envelope could not be opened.", { tone: "danger" });
            }
          },
        },
        icon("envelope-open"),
        "Open envelope"
      )
    );
  }

  items.push(
    el("div", { className: "menu-separator" }),
    el(
      "button",
      {
        className: "menu-item is-danger",
        onClick: async () => {
          const confirmed = await confirmDialog({
            title: "Delete this card?",
            message: `${card.key || "This card"} and its movement history will be removed from the board. This cannot be undone.`,
            confirmLabel: "Delete card",
            tone: "danger",
          });
          if (!confirmed) return;
          try {
            await api.del(`/cards/${card.id}`, { silent: true });
            if (ctx.removeCard) ctx.removeCard(card.id);
            closeCardDrawer();
            toast(`${card.key || "Card"} deleted.`, { tone: "neutral" });
          } catch (error) {
            toast(error?.message || "The card could not be deleted.", { tone: "danger" });
          }
        },
      },
      icon("trash"),
      "Delete card"
    )
  );

  // The drawer traps focus; the menu is a child of <body>, so it must be
  // appended inside the drawer to stay reachable by Tab.
  const menuHandle = buildMenu(anchor, items);
  return menuHandle;
}

function openMoveTargets(anchor, card, ctx) {
  const columns = (typeof ctx.allColumns === "function" ? ctx.allColumns() : []) || [];
  const current = ctx.columnById ? ctx.columnById(card.column_id) : null;
  const items = [];

  for (const column of columns) {
    const forward = current ? column.position > current.position : true;
    const isCurrent = column.id === card.column_id;
    items.push(
      el(
        "button",
        {
          className: "menu-item",
          disabled: !forward || isCurrent,
          onClick: () => ctx.moveCard(card.id, column.id),
        },
        icon(columnKindMeta(column.kind).icon),
        el("span", { className: "truncate" }, column.name),
        isCurrent
          ? el("span", { className: "menu-shortcut" }, "current")
          : !forward
            ? el("span", { className: "menu-shortcut" }, "backward")
            : null
      )
    );
  }

  if (!items.length) items.push(el("div", { className: "menu-label" }, "No columns available"));
  buildMenu(anchor, items);
}

/**
 * A menu anchored inside the drawer.
 *
 * `ui.menu()` appends to <body>, which the drawer's focus trap cannot see, so
 * card-level menus are rendered into the drawer panel instead. Behaviour —
 * outside click, Escape, close on select — matches `ui.menu()` exactly.
 */
function buildMenu(anchor, items) {
  document.querySelectorAll(".menu[data-card-menu]").forEach((n) => n.remove());

  const container = openHandle ? openHandle.el : document.body;
  const node = el("div", { className: "menu", dataset: { cardMenu: "" }, role: "menu" }, items);
  container.append(node);

  const anchorRect = anchor.getBoundingClientRect();
  const hostRect = container.getBoundingClientRect();
  const width = node.offsetWidth;
  let top = anchorRect.bottom - hostRect.top + 4;
  let left = anchorRect.right - hostRect.left - width;
  if (left < 8) left = 8;
  if (top + node.offsetHeight > hostRect.height - 8) {
    top = Math.max(8, anchorRect.top - hostRect.top - node.offsetHeight - 4);
  }
  node.style.top = `${top}px`;
  node.style.left = `${left}px`;

  anchor.setAttribute("aria-expanded", "true");

  function close() {
    node.remove();
    anchor.setAttribute("aria-expanded", "false");
    document.removeEventListener("mousedown", onOutside, true);
    document.removeEventListener("keydown", onKey, true);
  }

  function onOutside(event) {
    if (!node.contains(event.target) && !anchor.contains(event.target)) close();
  }

  function onKey(event) {
    if (event.key === "Escape") { event.stopPropagation(); close(); anchor.focus(); }
  }

  window.setTimeout(() => {
    document.addEventListener("mousedown", onOutside, true);
    document.addEventListener("keydown", onKey, true);
  }, 0);

  node.addEventListener("click", (event) => {
    if (event.target.closest(".menu-item")) close();
  });

  const first = node.querySelector(".menu-item:not(:disabled)");
  if (first) first.focus();

  return { close, el: node };
}

/* ==========================================================================
   Quick-create modal (used by the board's per-column "+" affordance)
   ========================================================================== */

/**
 * Create a card directly in a column.
 *
 * @param {object} opts
 * @param {object} opts.board
 * @param {object} opts.column
 * @param {Array} opts.projects
 * @param {Function} opts.onCreated Called with the created card.
 */
export function openCreateCardModal({ board, column, projects = [], onCreated }) {
  const title = el("textarea", {
    className: "textarea",
    rows: 2,
    placeholder: "What needs to happen?",
    "aria-label": "Card title",
    "data-autofocus": "",
  });

  const project = el(
    "select",
    { className: "select", "aria-label": "Project" },
    [el("option", { value: "" }, "No project")].concat(
      projects.map((p) => el("option", { value: p.id }, p.key ? `${p.key} · ${p.name}` : p.name))
    )
  );

  const serviceClass = el(
    "select",
    { className: "select", "aria-label": "Class of service" },
    SERVICE_CLASSES.map((s) => el("option", { value: s.id }, s.label))
  );
  serviceClass.value = "standard";

  const policy = el("p", { className: "cd-policy" }, SERVICE_CLASS_MAP.standard.policy);
  serviceClass.addEventListener("change", () => {
    policy.textContent = SERVICE_CLASS_MAP[serviceClass.value]?.policy || "";
  });

  const submit = el("button", { className: "btn btn-primary" }, "Create card");

  const handle = modal({
    title: `New card in ${column.name}`,
    body: el(
      "div",
      { className: "stack stack-4" },
      el("div", { className: "field" }, el("label", { className: "label" }, "Title"), title),
      el(
        "div",
        { className: "grid grid-2" },
        el("div", { className: "field" }, el("label", { className: "label" }, "Project"), project),
        el("div", { className: "field" }, el("label", { className: "label" }, "Class of service"), serviceClass)
      ),
      policy
    ),
    footer: [
      el("button", { className: "btn btn-secondary", onClick: () => handle.close() }, "Cancel"),
      submit,
    ],
  });

  async function create() {
    const text = title.value.trim();
    if (!text) { title.focus(); return; }
    submit.classList.add("is-loading");
    try {
      const created = await api.post("/cards", {
        board_id: board.id,
        column_id: column.id,
        title: text,
        project_id: project.value || null,
        service_class: serviceClass.value,
      }, { silent: true });
      handle.close();
      toast(`${created?.key || "Card"} created in ${column.name}.`, { tone: "success" });
      if (onCreated) onCreated(created);
    } catch (error) {
      submit.classList.remove("is-loading");
      toast(error?.message || "The card could not be created.", { tone: "danger", title: "Not created" });
    }
  }

  submit.addEventListener("click", create);
  title.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); create(); }
  });

  window.setTimeout(() => title.focus(), 0);
}
