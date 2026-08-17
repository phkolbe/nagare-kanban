/**
 * Nagare — board filter bar (SPEC §9.2 filter bar).
 *
 * Owns `#filter-slot`, a `display:contents` child of `.topbar` (see index.html):
 * everything appended here becomes a direct flex item of the top bar, which is
 * why the DOM this module renders is flat — a trigger button, a scrollable row
 * of active-filter chips, and a result count — rather than a boxed widget.
 *
 * The actual controls (project, service class, assignee, tag, blocked, demand
 * type, free text) live in a popover panel opened from the trigger, built with
 * `ui.menu()` so it inherits positioning, outside-click and Escape for free.
 * Every control reads and writes `store.filters` directly; board.js never sees
 * a filter value — it only reacts to `store.select(s => s.filters, ...)`.
 *
 * `service_class` is the one facet the board API cannot filter server-side
 * (`GET /boards/{id}/view` takes a single `service_class`, not a list) even
 * though the UI needs a multi-select — board.js applies it as a client-side
 * post-filter, so this module simply keeps it as the array SPEC asks for.
 */

import api from "./api.js";
import store, { setFilters, clearFilters, activeFilterCount } from "./store.js";
import { el, icon, render, clear, menu, debounce, humanize } from "./ui.js";
import { SERVICE_CLASSES, SERVICE_CLASS_MAP, DEMAND_TYPES } from "./card.js";

let slot = null;
let unsubscribe = null;
let projects = [];
let people = [];
let tags = [];
let resultShown = null;
let resultTotal = null;
let openMenuHandle = null;

/* ==========================================================================
   Lifecycle
   ========================================================================== */

/** Mount into `#filter-slot`. Safe to call once per board-view mount. */
export function mount() {
  slot = document.getElementById("filter-slot");
  if (!slot) return;

  loadDirectories();

  unsubscribe = store.select(
    (state) => state.filters,
    () => paint(),
    { immediate: true }
  );
}

/** Tear down: clear the slot and drop the subscription. Idempotent. */
export function unmount() {
  if (unsubscribe) { unsubscribe(); unsubscribe = null; }
  if (openMenuHandle) { openMenuHandle.close(); openMenuHandle = null; }
  if (slot) clear(slot);
  slot = null;
  resultShown = null;
  resultTotal = null;
  tags = [];
}

/** board.js calls this once per fetch so the tag dropdown reflects real data. */
export function setAvailableTags(cardTags) {
  tags = Array.from(new Set(cardTags)).sort((a, b) => a.localeCompare(b));
  if (slot) paint();
}

/** board.js calls this after every successful (re)fetch. */
export function setResultCount(shown, total) {
  resultShown = shown;
  resultTotal = total;
  if (slot) paintCount();
}

async function loadDirectories() {
  const [projectRes, peopleRes] = await Promise.all([
    api.get("/projects", { query: { archived: false }, silent: true, cache: 30000 }).catch(() => null),
    api.get("/users", { query: { is_active: true }, silent: true, cache: 30000 }).catch(() => null),
  ]);
  projects = Array.isArray(projectRes?.items) ? projectRes.items : [];
  people = Array.isArray(peopleRes?.items) ? peopleRes.items : [];
  if (slot) paint();
}

/* ==========================================================================
   Chips row + trigger
   ========================================================================== */

function paint() {
  if (!slot) return;
  const filters = store.get().filters;
  const count = activeFilterCount(filters);

  const trigger = el(
    "button",
    {
      className: "btn btn-secondary btn-sm board-filter-trigger",
      "aria-haspopup": "menu",
      "aria-expanded": openMenuHandle ? "true" : "false",
      onClick: (event) => openFilterPanel(event.currentTarget),
    },
    icon("filter"),
    "Filters",
    count ? el("span", { className: "badge", "data-tone": "accent" }, String(count)) : null
  );

  const chips = el("div", { className: "board-filter-chips" }, buildChips(filters));

  const clearButton = count
    ? el(
        "button",
        { className: "btn btn-ghost btn-sm", onClick: () => clearFilters() },
        icon("x"),
        "Clear"
      )
    : null;

  const countLabel = el("span", { className: "board-filter-count" });

  render(slot, el("div", { className: "board-filter-bar" }, trigger, chips, clearButton, countLabel));
  paintCount();
}

function paintCount() {
  if (!slot) return;
  const label = slot.querySelector(".board-filter-count");
  if (!label) return;
  if (resultShown === null) { label.textContent = ""; return; }
  label.textContent = resultShown === resultTotal
    ? `${resultShown} card${resultShown === 1 ? "" : "s"}`
    : `${resultShown} of ${resultTotal} card${resultTotal === 1 ? "" : "s"}`;
}

function buildChips(filters) {
  const chips = [];

  if (filters.project_id) {
    const project = projects.find((p) => p.id === filters.project_id);
    chips.push(chip(`Project: ${project ? project.name : "…"}`, () => setFilters({ project_id: null })));
  }
  for (const sc of filters.service_class) {
    chips.push(
      chip(SERVICE_CLASS_MAP[sc]?.label || humanize(sc), () => {
        setFilters({ service_class: filters.service_class.filter((v) => v !== sc) });
      }, sc)
    );
  }
  if (filters.assignee_id) {
    const person = people.find((p) => p.id === filters.assignee_id);
    chips.push(chip(`Assignee: ${person ? person.name : "…"}`, () => setFilters({ assignee_id: null })));
  }
  if (filters.tag) {
    chips.push(chip(`Tag: ${filters.tag}`, () => setFilters({ tag: null })));
  }
  if (filters.is_blocked !== null) {
    chips.push(chip(filters.is_blocked ? "Blocked only" : "Unblocked only", () => setFilters({ is_blocked: null })));
  }
  if (filters.demand_type) {
    chips.push(chip(DEMAND_TYPES.find((d) => d.id === filters.demand_type)?.label || humanize(filters.demand_type), () => setFilters({ demand_type: null })));
  }
  if (filters.q) {
    chips.push(chip(`"${filters.q}"`, () => setFilters({ q: "" })));
  }
  return chips;
}

function chip(label, onRemove, sc) {
  return el(
    "span",
    { className: "chip", dataset: sc ? { sc } : {} },
    el("span", { className: "truncate", style: { maxWidth: "160px" } }, label),
    el(
      "button",
      { className: "chip-remove", "aria-label": `Remove filter ${label}`, onClick: onRemove },
      icon("x")
    )
  );
}

/* ==========================================================================
   The filter panel
   ========================================================================== */

const debouncedText = debounce((value) => setFilters({ q: value }), 260);

function openFilterPanel(anchor) {
  const filters = store.get().filters;
  const showServiceClasses = store.hasFlag("service_classes");

  const textInput = el("input", {
    className: "input input-sm",
    type: "search",
    placeholder: "Title, key or description…",
    value: filters.q || "",
    "aria-label": "Filter by text",
    "data-autofocus": "",
  });
  textInput.addEventListener("input", () => debouncedText(textInput.value));
  textInput.addEventListener("keydown", (event) => event.stopPropagation());

  const projectSelect = el(
    "select",
    { className: "select select-sm", "aria-label": "Filter by project" },
    [el("option", { value: "" }, "All projects")].concat(
      projects.map((p) => el("option", { value: p.id }, p.key ? `${p.key} · ${p.name}` : p.name))
    )
  );
  projectSelect.value = filters.project_id || "";
  projectSelect.addEventListener("change", () => setFilters({ project_id: projectSelect.value || null }));

  const assigneeSelect = el(
    "select",
    { className: "select select-sm", "aria-label": "Filter by assignee" },
    [el("option", { value: "" }, "Everyone")].concat(
      people.map((p) => el("option", { value: p.id }, p.name))
    )
  );
  assigneeSelect.value = filters.assignee_id || "";
  assigneeSelect.addEventListener("change", () => setFilters({ assignee_id: assigneeSelect.value || null }));

  const tagSelect = el(
    "select",
    { className: "select select-sm", "aria-label": "Filter by tag" },
    [el("option", { value: "" }, "Any tag")].concat(tags.map((t) => el("option", { value: t }, t)))
  );
  tagSelect.value = filters.tag || "";
  tagSelect.addEventListener("change", () => setFilters({ tag: tagSelect.value || null }));

  const demandSelect = el(
    "select",
    { className: "select select-sm", "aria-label": "Filter by demand type" },
    [el("option", { value: "" }, "Any demand type")].concat(
      DEMAND_TYPES.map((d) => el("option", { value: d.id }, d.label))
    )
  );
  demandSelect.value = filters.demand_type || "";
  demandSelect.addEventListener("change", () => setFilters({ demand_type: demandSelect.value || null }));

  const blockedRow = el(
    "div",
    { className: "filter-tri-row" },
    triButton("Any", filters.is_blocked === null, () => setFilters({ is_blocked: null })),
    triButton("Blocked", filters.is_blocked === true, () => setFilters({ is_blocked: true })),
    triButton("Unblocked", filters.is_blocked === false, () => setFilters({ is_blocked: false }))
  );

  const panelChildren = [
    el(
      "div",
      { className: "filter-panel-header" },
      el("span", { className: "panel-title" }, "Filter cards"),
      activeFilterCount(filters)
        ? el("button", { className: "btn btn-ghost btn-sm", onClick: () => { clearFilters(); openMenuHandle?.close(); } }, "Clear all")
        : null
    ),
    field("Free text", textInput),
    field("Project", projectSelect),
  ];

  if (showServiceClasses) {
    const scRow = el(
      "div",
      { className: "filter-sc-row" },
      SERVICE_CLASSES.map((sc) =>
        el(
          "button",
          {
            type: "button",
            className: filters.service_class.includes(sc.id) ? "chip is-selected" : "chip",
            dataset: { sc: sc.id },
            "aria-pressed": filters.service_class.includes(sc.id) ? "true" : "false",
            onClick: () => {
              const active = store.get().filters.service_class;
              const next = active.includes(sc.id) ? active.filter((v) => v !== sc.id) : active.concat(sc.id);
              setFilters({ service_class: next });
              // repaint the toggle state in place without closing the popover
              refreshServiceClassButtons(scRow);
            },
          },
          el("span", { className: "sc-dot" }),
          sc.label
        )
      )
    );
    panelChildren.push(field("Class of service", scRow));
  }

  panelChildren.push(
    field("Assignee", assigneeSelect),
    field("Tag", tagSelect),
    field("Demand type", demandSelect),
    field("Blocked", blockedRow)
  );

  openMenuHandle = menu(anchor, [el("div", { className: "filter-panel" }, panelChildren)], { minWidth: 320 });
  // `ui.menu()` closes on any click inside `.menu-item`; our controls are not
  // menu items, so clicks on selects/inputs must not bubble into that logic —
  // they don't, since menu() only special-cases `.menu-item`. Re-sync the
  // "expanded" state on the trigger when the panel closes.
  const originalClose = openMenuHandle.close;
  openMenuHandle.close = () => { originalClose(); openMenuHandle = null; if (slot) paint(); };
}

function refreshServiceClassButtons(scRow) {
  const active = store.get().filters.service_class;
  for (const button of scRow.querySelectorAll("button")) {
    const sc = button.dataset.sc;
    const isOn = active.includes(sc);
    button.classList.toggle("is-selected", isOn);
    button.setAttribute("aria-pressed", isOn ? "true" : "false");
  }
}

function triButton(label, active, onClick) {
  return el(
    "button",
    {
      type: "button",
      className: active ? "btn btn-secondary btn-sm is-active" : "btn btn-ghost btn-sm",
      "aria-pressed": active ? "true" : "false",
      onClick,
    },
    label
  );
}

function field(label, control) {
  return el("div", { className: "filter-field" }, el("span", { className: "filter-field-label" }, label), control);
}

export const filters = { mount, unmount, setAvailableTags, setResultCount };
export default filters;
