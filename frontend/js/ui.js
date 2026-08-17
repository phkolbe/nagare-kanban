/**
 * Nagare — UI kit.
 *
 * Hyperscript, the icon set, focus-trapped overlays (drawer / modal / confirm),
 * toasts, the standard loading / empty / error states, and the small formatters
 * every view needs.
 *
 * This module deliberately imports nothing. It is the bottom of the frontend
 * dependency graph: api.js depends on it for toasts, so it must never depend
 * on api.js in return.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

/* ==========================================================================
   Hyperscript
   ========================================================================== */

/**
 * Create an element.
 *
 * `el("div", { className: "row", onClick: fn }, "text", childNode, [more])`
 *
 * Props are applied as DOM properties where one exists (className, value,
 * checked, disabled...) and as attributes otherwise, which is what makes
 * `aria-*`, `data-*` and `viewBox` work without special-casing each of them.
 *
 * Special props:
 *   - `class` / `className` — string, or array/object of conditional classes
 *   - `style`               — string or {prop: value} (supports --custom-props)
 *   - `dataset`             — {key: value}
 *   - `on<Event>`           — listener, e.g. `onClick`, `onKeyDown`
 *   - `ref`                 — callback receiving the node
 *   - `html`                — trusted innerHTML (used only for our own SVG)
 *
 * @param {string} tag
 * @param {object|null} [props]
 * @param {...any} children Strings, numbers, Nodes, arrays; null/false skipped.
 * @returns {HTMLElement}
 */
export function el(tag, props, ...children) {
  const node = document.createElement(tag);
  applyProps(node, props);
  append(node, children);
  return node;
}

/** Same as {@link el} but in the SVG namespace, for hand-built charts. */
export function svg(tag, props, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  applyProps(node, props, true);
  append(node, children);
  return node;
}

function applyProps(node, props, isSvg = false) {
  if (!props) return;
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;

    if (key === "class" || key === "className") {
      const cls = classNames(value);
      if (cls) node.setAttribute("class", cls);
    } else if (key === "style") {
      applyStyle(node, value);
    } else if (key === "dataset") {
      for (const [k, v] of Object.entries(value)) {
        if (v !== null && v !== undefined) node.dataset[k] = v;
      }
    } else if (key === "html") {
      node.innerHTML = value;
    } else if (key === "ref") {
      if (typeof value === "function") value(node);
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (!isSvg && key in node && key !== "list" && key !== "form") {
      // Properties beat attributes for value/checked/disabled so state sticks.
      node[key] = value;
    } else {
      node.setAttribute(key, value === true ? "" : String(value));
    }
  }
}

function applyStyle(node, value) {
  if (typeof value === "string") {
    node.setAttribute("style", value);
    return;
  }
  for (const [k, v] of Object.entries(value)) {
    if (v === null || v === undefined) continue;
    if (k.startsWith("--")) node.style.setProperty(k, String(v));
    else node.style[k] = v;
  }
}

/** Build a class string from a string, array, or {name: truthy} map. */
export function classNames(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.filter(Boolean).map(classNames).join(" ");
  if (value && typeof value === "object") {
    return Object.entries(value).filter(([, v]) => v).map(([k]) => k).join(" ");
  }
  return "";
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Remove every child of `node`. */
export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** Replace the contents of `node` with `children`. */
export function render(node, ...children) {
  clear(node);
  append(node, children);
  return node;
}

/** A text-only fragment helper for conditional lists. */
export function frag(...children) {
  const f = document.createDocumentFragment();
  append(f, children);
  return f;
}

/* ==========================================================================
   Icons — one coherent set: 24 viewBox, 1.5 stroke, round caps/joins,
   rendered at 20px. currentColor throughout so tone tokens apply.
   ========================================================================== */

const ICONS = {
  board: "M3 4.5h5.5v15H3zM9.25 4.5h5.5v10h-5.5zM15.5 4.5H21v13h-5.5z",
  layers: "m12 3 8.5 4.5L12 12 3.5 7.5 12 3ZM3.5 12 12 16.5 20.5 12M3.5 16.5 12 21l8.5-4.5",
  chart: "M3.5 20.5h17M6.5 20.5v-6M11 20.5V8M15.5 20.5v-9M20 20.5V4.5",
  flask: "M9.5 3v5.2L4.7 17a2.2 2.2 0 0 0 1.9 3.3h10.8a2.2 2.2 0 0 0 1.9-3.3L14.5 8.2V3M8.5 3h7M7.4 14.2h9.2",
  users: "M16.5 20v-1.6a3.4 3.4 0 0 0-3.4-3.4H6.9a3.4 3.4 0 0 0-3.4 3.4V20M20.5 20v-1.6a3.4 3.4 0 0 0-2.6-3.3M15.6 4.7a3.4 3.4 0 0 1 0 6.1",
  flag: "M4.5 15s1-1 3.5-1 4 2 6.5 2 3.5-1 3.5-1V4.5s-1 1-3.5 1-4-2-6.5-2-3.5 1-3.5 1zM4.5 21v-6",
  plus: "M12 5.5v13M5.5 12h13",
  minus: "M5.5 12h13",
  filter: "M4 5.5h16l-6.2 7.3v5.4l-3.6 2v-7.4z",
  search: "m19.5 19.5-4.3-4.3",
  sun: "M12 4V2.5M12 21.5V20M18 6l1-1M5 19l1-1M20 12h1.5M2.5 12H4M18 18l1 1M5 5l1 1",
  moon: "M20.5 14.3A8.5 8.5 0 0 1 9.7 3.5a8.5 8.5 0 1 0 10.8 10.8Z",
  monitor: "M3.5 4.5h17v11h-17zM9 20h6M12 15.5V20",
  "chevron-down": "m6 9.5 6 6 6-6",
  "chevron-up": "m6 14.5 6-6 6 6",
  "chevron-left": "m14.5 6-6 6 6 6",
  "chevron-right": "m9.5 6 6 6-6 6",
  "chevrons-up-down": "m8 9 4-4 4 4M8 15l4 4 4-4",
  x: "m6 6 12 12M18 6 6 18",
  check: "m5 12.5 4.5 4.5L19 7.5",
  alert: "M12 8v5M12 16.5v.5",
  clock: "M12 7v5.3l3.2 1.9",
  bolt: "M13.2 2.5 4.5 13.4h6.6l-.8 8.1 8.7-10.9h-6.6z",
  lock: "M6.5 10.5h11v9h-11zM8.75 10.5V7.6a3.25 3.25 0 0 1 6.5 0v2.9",
  envelope: "M3.5 6.5h17v11h-17zM3.5 7l8.5 6 8.5-6",
  "envelope-open": "m3.5 10.2 8.5-6 8.5 6v9.3h-17zM3.5 10.2l8.5 5.8 8.5-5.8",
  drag: "M9 6.5h.01M9 12h.01M9 17.5h.01M15 6.5h.01M15 12h.01M15 17.5h.01",
  block: "m5.6 5.6 12.8 12.8",
  more: "M6 12h.01M12 12h.01M18 12h.01",
  settings: "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z",
  logout: "M9.5 20.5h-4a1.5 1.5 0 0 1-1.5-1.5v-14a1.5 1.5 0 0 1 1.5-1.5h4M15 16.5l4.5-4.5L15 7.5M19.5 12h-11",
  trash: "M4.5 6.5h15M9.5 6.5v-2h5v2M6.5 6.5l1 13h9l1-13M10 10v6M14 10v6",
  edit: "M4.5 19.5h4l10-10a2.1 2.1 0 0 0-3-3l-10 10zM14 6l4 4",
  refresh: "M20 11.5A8 8 0 0 0 6.3 6.3L4 8.5M4 12.5a8 8 0 0 0 13.7 5.2L20 15.5M4 4v4.5h4.5M20 20v-4.5h-4.5",
  "arrow-right": "M4.5 12h15M13.5 6l6 6-6 6",
  "arrow-up": "M12 19.5v-15M6 10.5l6-6 6 6",
  "arrow-down": "M12 4.5v15M6 13.5l6 6 6-6",
  "trend-up": "m3.5 16.5 5.5-5.5 3.5 3.5 8-8M20.5 6.5h-5M20.5 6.5v5",
  "trend-down": "m3.5 7.5 5.5 5.5 3.5-3.5 8 8M20.5 17.5h-5M20.5 17.5v-5",
  calendar: "M4.5 6.5h15v13h-15zM8 3.5v4M16 3.5v4M4.5 11h15",
  tag: "M3.5 11.2V4.5h6.7l9.3 9.3-6.7 6.7z",
  user: "M18 20v-1.8a3.6 3.6 0 0 0-3.6-3.6H9.6A3.6 3.6 0 0 0 6 18.2V20",
  info: "M12 11v5.5M12 7.5v.5",
  help: "M9.6 9.3a2.5 2.5 0 1 1 3.3 2.9c-.6.2-.9.8-.9 1.5v.6M12 17.5v.5",
  external: "M14 4.5h5.5V10M19.5 4.5 11 13M17 13.5v5a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 4 18.5v-10A1.5 1.5 0 0 1 5.5 7h5",
  sparkle: "M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9zM18.5 16l.7 2.1 2.1.7-2.1.7-.7 2.1-.7-2.1-2.1-.7 2.1-.7z",
  activity: "M3.5 12h4l2.5-6.5 4 13 2.5-6.5h4",
  queue: "M4.5 7h15M4.5 12h15M4.5 17h9",
  target: "M12 12h.01",
  play: "M7.5 4.8v14.4L19.5 12z",
  "target-rings": "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17ZM12 7.8a4.2 4.2 0 1 0 0 8.4 4.2 4.2 0 0 0 0-8.4Z",
  copy: "M9.5 9.5h9v9h-9zM6 14.5H5A1.5 1.5 0 0 1 3.5 13V5A1.5 1.5 0 0 1 5 3.5h8A1.5 1.5 0 0 1 14.5 5v1",
  download: "M12 3.5v11M7.5 10.5 12 15l4.5-4.5M4.5 19.5h15",
  keyboard: "M3.5 6.5h17v11h-17zM7 10h.01M10.5 10h.01M14 10h.01M17.5 10h.01M7 13.5h.01M17.5 13.5h.01M10 13.5h4.5",
  eye: "M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z",
  archive: "M3.5 4.5h17V8h-17zM5 8v11.5h14V8M10 11.5h4",
  link: "M10 13.5a3.5 3.5 0 0 0 5.1.4l2.4-2.4a3.5 3.5 0 0 0-5-5l-1.3 1.3M14 10.5a3.5 3.5 0 0 0-5.1-.4l-2.4 2.4a3.5 3.5 0 0 0 5 5l1.3-1.3",
};

/** Icons that need a circle in addition to (or instead of) their path. */
const ICON_CIRCLES = {
  search: [10.75, 10.75, 6.25],
  sun: [12, 12, 4],
  alert: [12, 12, 9],
  clock: [12, 12, 8.5],
  info: [12, 12, 9],
  help: [12, 12, 9],
  block: [12, 12, 9],
  user: [12, 8.5, 3.6],
  target: [12, 12, 3.2],
  eye: [12, 12, 2.6],
};

/** Extra sub-paths that should be filled rather than stroked. */
const ICON_EXTRA = {
  settings:
    "M19.4 14.5a1.5 1.5 0 0 0 .3 1.7l.1.1a1.8 1.8 0 1 1-2.6 2.6l-.1-.1a1.5 1.5 0 0 0-2.6 1.1v.3a1.8 1.8 0 1 1-3.6 0v-.2a1.5 1.5 0 0 0-2.7-1l-.1.1A1.8 1.8 0 1 1 5.4 16l.1-.1a1.5 1.5 0 0 0-1.1-2.6h-.3a1.8 1.8 0 1 1 0-3.6h.2a1.5 1.5 0 0 0 1-2.7l-.1-.1A1.8 1.8 0 1 1 8 4.4l.1.1a1.5 1.5 0 0 0 1.7.3h.1a1.5 1.5 0 0 0 .9-1.4v-.3a1.8 1.8 0 1 1 3.6 0v.2a1.5 1.5 0 0 0 2.6 1.1l.1-.1A1.8 1.8 0 1 1 19.7 7l-.1.1a1.5 1.5 0 0 0 1.1 2.6h.3a1.8 1.8 0 1 1 0 3.6h-.2a1.5 1.5 0 0 0-1.4.9Z",
};

/**
 * Return an inline `<svg>` for a named icon.
 * Unknown names fall back to a dot rather than throwing — a missing glyph must
 * never take a view down.
 *
 * Always carries an explicit width/height (default 18px) so a bare
 * `icon("x")` with no wrapping CSS rule renders at icon size instead of
 * stretching to fill its container — SVG width/height attributes act as a
 * low-specificity presentational default, so any real CSS rule (`.btn svg`,
 * `.chip svg`, a `size` in `className`, ...) still wins over it.
 *
 * @param {string} name
 * @param {{size?: number, className?: string, title?: string}} [opts]
 * @returns {SVGElement}
 */
export function icon(name, opts = {}) {
  const { size = 18, className, title } = opts;
  const node = document.createElementNS(SVG_NS, "svg");
  node.setAttribute("viewBox", "0 0 24 24");
  node.setAttribute("fill", "none");
  node.setAttribute("stroke", "currentColor");
  node.setAttribute("stroke-width", "1.5");
  node.setAttribute("stroke-linecap", "round");
  node.setAttribute("stroke-linejoin", "round");
  node.setAttribute("width", size);
  node.setAttribute("height", size);
  if (className) node.setAttribute("class", className);

  if (title) {
    const t = document.createElementNS(SVG_NS, "title");
    t.textContent = title;
    node.append(t);
  } else {
    node.setAttribute("aria-hidden", "true");
  }

  const circle = ICON_CIRCLES[name];
  if (circle) {
    const c = document.createElementNS(SVG_NS, "circle");
    c.setAttribute("cx", circle[0]);
    c.setAttribute("cy", circle[1]);
    c.setAttribute("r", circle[2]);
    node.append(c);
  }

  const d = ICONS[name] || (circle ? null : "M12 12h.01");
  if (d) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    node.append(p);
  }

  if (ICON_EXTRA[name]) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", ICON_EXTRA[name]);
    node.append(p);
  }

  return node;
}

/** Names of every available icon — useful for admin/debug surfaces. */
export const iconNames = Object.freeze(
  Array.from(new Set([...Object.keys(ICONS), ...Object.keys(ICON_CIRCLES)])).sort()
);

/* ==========================================================================
   Tab-strip edge fade
   --------------------------------------------------------------------------
   `.tabs` bars are built ad hoc by whichever view needs one (card drawer,
   admin) — there is no single call site to wire a scroll listener into, so
   this watches the DOM instead: any element with class `.tabs` that appears
   anywhere gets a scroll/resize-driven `data-fade-start`/`data-fade-end`
   toggle, mirroring the board scroller's `.board-edge` affordance, without
   requiring every caller to remember to opt in.
   ========================================================================== */

const tabsEdgeWatched = new WeakSet();

function updateTabsEdge(node) {
  const { scrollLeft, scrollWidth, clientWidth } = node;
  node.toggleAttribute("data-fade-start", scrollLeft > 2);
  // -1px slack absorbs sub-pixel rounding on fractional-DPI displays, which
  // would otherwise leave a 1px-overflow bar permanently "faded" at rest.
  node.toggleAttribute("data-fade-end", scrollLeft + clientWidth < scrollWidth - 1);
}

function watchTabsEdge(node) {
  if (tabsEdgeWatched.has(node)) return;
  tabsEdgeWatched.add(node);
  const update = () => updateTabsEdge(node);
  node.addEventListener("scroll", update, { passive: true });
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(update).observe(node);
  } else {
    window.addEventListener("resize", update);
  }
  update();
}

function scanForTabsEdge(node) {
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  if (node.classList?.contains("tabs")) watchTabsEdge(node);
  node.querySelectorAll?.(".tabs").forEach(watchTabsEdge);
}

if (typeof document !== "undefined") {
  const startTabsEdgeWatch = () => {
    scanForTabsEdge(document.body);
    new MutationObserver((mutations) => {
      for (const mutation of mutations) mutation.addedNodes.forEach(scanForTabsEdge);
    }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.body) startTabsEdgeWatch();
  else document.addEventListener("DOMContentLoaded", startTabsEdgeWatch);
}

/* ==========================================================================
   Portals & accessibility plumbing
   ========================================================================== */

function portal(id) {
  let node = document.getElementById(id);
  if (!node) {
    node = el("div", { id });
    document.body.append(node);
  }
  return node;
}

/** Announce a message to screen readers without stealing focus. */
export function announce(message) {
  const region = document.getElementById("live-region");
  if (!region) return;
  // Clearing first forces AT to re-read an identical consecutive message.
  region.textContent = "";
  window.setTimeout(() => { region.textContent = message; }, 30);
}

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableIn(container) {
  return Array.from(container.querySelectorAll(FOCUSABLE))
    .filter((n) => n.offsetParent !== null || n === document.activeElement);
}

/**
 * Trap Tab focus inside `container` and route Escape to `onEscape`.
 * Returns a teardown function that also restores focus to wherever it was.
 */
function trapFocus(container, onEscape) {
  const previous = document.activeElement;

  function onKeyDown(event) {
    if (event.key === "Escape") {
      event.stopPropagation();
      event.preventDefault();
      onEscape();
      return;
    }
    if (event.key !== "Tab") return;

    const items = focusableIn(container);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && (active === first || !container.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  container.addEventListener("keydown", onKeyDown);

  // Focus the first meaningful control; fall back to the container itself so
  // the overlay is never left with focus stranded on the page behind it.
  const initial = container.querySelector("[data-autofocus]") || focusableIn(container)[0];
  if (initial) initial.focus();
  else {
    container.setAttribute("tabindex", "-1");
    container.focus();
  }

  return function release() {
    container.removeEventListener("keydown", onKeyDown);
    if (previous && typeof previous.focus === "function" && document.contains(previous)) {
      previous.focus();
    }
  };
}

/** Number of overlays currently open — used to keep the body from scrolling. */
let overlayCount = 0;

function lockScroll() {
  overlayCount += 1;
  document.body.style.overflow = "hidden";
}

function unlockScroll() {
  overlayCount = Math.max(0, overlayCount - 1);
  if (overlayCount === 0) document.body.style.overflow = "";
}

/* ==========================================================================
   Toasts
   ========================================================================== */

const TOAST_ICON = {
  success: "check",
  danger: "alert",
  warning: "alert",
  info: "info",
  accent: "info",
  neutral: "info",
};

/**
 * Show a transient notification.
 *
 * @param {string|{title?: string, message?: string}} message
 * @param {object} [opts]
 * @param {"neutral"|"accent"|"success"|"warning"|"danger"|"info"} [opts.tone="neutral"]
 * @param {string} [opts.title]
 * @param {number} [opts.duration] ms; 0 keeps it until dismissed. Errors default
 *   to a longer dwell because they usually carry something worth reading.
 * @param {{label: string, onClick: Function}} [opts.action]
 * @returns {{close: Function}}
 */
export function toast(message, opts = {}) {
  const tone = opts.tone || "neutral";
  const text = typeof message === "string" ? message : message?.message || "";
  const title = opts.title || (typeof message === "object" ? message?.title : null);
  const duration = opts.duration ?? (tone === "danger" ? 8000 : 4200);

  const region = portal("toast-portal");
  let timer = null;
  let closed = false;

  const node = el(
    "div",
    {
      className: "toast",
      dataset: { tone },
      role: tone === "danger" ? "alert" : "status",
      "aria-live": tone === "danger" ? "assertive" : "polite",
    },
    icon(TOAST_ICON[tone] || "info"),
    el(
      "div",
      { className: "toast-content" },
      title ? el("div", { className: "toast-title" }, title) : null,
      text ? el("div", { className: title ? "toast-message" : "toast-title" }, text) : null,
      opts.action
        ? el(
            "button",
            {
              className: "btn btn-sm btn-secondary toast-action",
              onClick: () => { opts.action.onClick(); close(); },
            },
            opts.action.label
          )
        : null
    ),
    el(
      "button",
      { className: "toast-close", "aria-label": "Dismiss notification", onClick: () => close() },
      icon("x")
    )
  );

  function close() {
    if (closed) return;
    closed = true;
    if (timer) window.clearTimeout(timer);
    node.classList.add("is-leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
    // Belt and braces: if the animation is disabled by reduced-motion, the
    // animationend event never fires, so remove on a timer too.
    window.setTimeout(() => node.remove(), 400);
  }

  // Pause the dismissal timer while the pointer is over the toast, so a user
  // reading a long error message does not lose it mid-sentence.
  function startTimer() {
    if (!duration) return;
    timer = window.setTimeout(close, duration);
  }
  node.addEventListener("mouseenter", () => { if (timer) window.clearTimeout(timer); });
  node.addEventListener("mouseleave", startTimer);

  region.append(node);
  startTimer();

  // Keep the stack shallow; oldest goes first.
  while (region.children.length > 4) region.firstElementChild.remove();

  return { close, el: node };
}

/* ==========================================================================
   Modal
   ========================================================================== */

/**
 * Open a centred modal dialog.
 *
 * @param {object} opts
 * @param {string} [opts.title]
 * @param {Node|string|Array} [opts.body]
 * @param {Node|Array} [opts.footer]
 * @param {"md"|"lg"|"xl"} [opts.size="md"]
 * @param {boolean} [opts.dismissible=true] Allow Esc / backdrop click.
 * @param {Function} [opts.onClose]
 * @returns {{close: Function, el: HTMLElement, body: HTMLElement}}
 */
export function modal(opts = {}) {
  const { title, body, footer, size = "md", dismissible = true, onClose } = opts;
  const root = portal("modal-portal");
  const titleId = `modal-title-${Math.random().toString(36).slice(2, 9)}`;

  const bodyNode = el("div", { className: "modal-body" }, body);

  const dialog = el(
    "div",
    {
      className: `modal${size === "md" ? "" : ` modal-${size}`}`,
      role: "dialog",
      "aria-modal": "true",
      ...(title ? { "aria-labelledby": titleId } : { "aria-label": "Dialog" }),
    },
    title
      ? el(
          "div",
          { className: "modal-header" },
          el("h2", { className: "modal-title", id: titleId }, title),
          dismissible
            ? el(
                "button",
                { className: "btn btn-ghost btn-icon btn-sm", "aria-label": "Close dialog", onClick: () => close() },
                icon("x")
              )
            : null
        )
      : null,
    bodyNode,
    footer ? el("div", { className: "modal-footer" }, footer) : null
  );

  const scrim = el("div", {
    className: "scrim",
    onClick: () => { if (dismissible) close(); },
  });

  const container = el("div", { className: "modal-root" }, scrim, dialog);

  let closed = false;
  let release = null;

  function close(reason) {
    if (closed) return;
    closed = true;
    if (release) release();
    container.remove();
    unlockScroll();
    if (onClose) onClose(reason);
  }

  root.append(container);
  lockScroll();
  release = trapFocus(dialog, () => { if (dismissible) close("escape"); });

  return { close, el: dialog, body: bodyNode };
}

/**
 * A yes/no dialog that resolves rather than throwing callbacks around.
 *
 * Resolves to `false` on cancel. On confirm it resolves to `true`, or — when
 * `checkbox` is supplied — to `{ confirmed: true, checked: boolean }`, which is
 * what the board's "don't ask again on this board" affordance needs.
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {string|Node} [opts.message]
 * @param {string} [opts.confirmLabel="Confirm"]
 * @param {string} [opts.cancelLabel="Cancel"]
 * @param {"accent"|"danger"} [opts.tone="accent"]
 * @param {string} [opts.checkbox] Label for an optional checkbox.
 * @returns {Promise<boolean|{confirmed: boolean, checked: boolean}>}
 */
export function confirmDialog(opts = {}) {
  const {
    title,
    message,
    confirmLabel = "Confirm",
    cancelLabel = "Cancel",
    tone = "accent",
    checkbox,
  } = opts;

  return new Promise((resolve) => {
    let settled = false;
    let checkboxInput = null;

    function settle(confirmed) {
      if (settled) return;
      settled = true;
      const checked = Boolean(checkboxInput && checkboxInput.checked);
      resolve(checkbox ? { confirmed, checked } : confirmed);
      handle.close();
    }

    const handle = modal({
      title,
      size: "md",
      body: el(
        "div",
        { className: "stack stack-4" },
        typeof message === "string"
          ? el("p", { className: "muted" }, message)
          : message || null,
        checkbox
          ? el(
              "label",
              { className: "checkbox-row" },
              el("input", {
                className: "checkbox",
                type: "checkbox",
                ref: (n) => { checkboxInput = n; },
              }),
              el("span", null, checkbox)
            )
          : null
      ),
      footer: [
        el("button", { className: "btn btn-secondary", onClick: () => settle(false) }, cancelLabel),
        el(
          "button",
          {
            className: `btn ${tone === "danger" ? "btn-danger" : "btn-primary"}`,
            "data-autofocus": "",
            onClick: () => settle(true),
          },
          confirmLabel
        ),
      ],
      onClose: () => { if (!settled) { settled = true; resolve(checkbox ? { confirmed: false, checked: false } : false); } },
    });
  });
}

/* ==========================================================================
   Drawer
   ========================================================================== */

/**
 * Open a right-hand slide-over panel. Focus-trapped, Esc to close, restores
 * focus to the trigger on close.
 *
 * @param {object} opts
 * @param {string} [opts.title]
 * @param {Node|string|Array} [opts.subtitle] Rendered under the title.
 * @param {Node|string|Array} [opts.body]
 * @param {Node|Array} [opts.footer]
 * @param {Node|Array} [opts.actions] Header-level actions, left of the close button.
 * @param {Function} [opts.onClose]
 * @returns {{close: Function, el: HTMLElement, body: HTMLElement, setTitle: Function}}
 */
export function drawer(opts = {}) {
  const { title, subtitle, body, footer, actions, onClose } = opts;
  const root = portal("drawer-portal");
  const titleId = `drawer-title-${Math.random().toString(36).slice(2, 9)}`;

  const bodyNode = el("div", { className: "drawer-body" }, body);
  const titleNode = el("h2", { className: "drawer-title truncate", id: titleId }, title || "Details");

  const panel = el(
    "aside",
    {
      className: "drawer",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
    },
    el(
      "div",
      { className: "drawer-header" },
      el(
        "div",
        { className: "stack stack-1", style: { minWidth: 0 } },
        titleNode,
        subtitle ? el("div", { className: "t-xs subtle" }, subtitle) : null
      ),
      el(
        "div",
        { className: "row row-1" },
        actions || null,
        el(
          "button",
          { className: "btn btn-ghost btn-icon btn-sm", "aria-label": "Close panel", onClick: () => close() },
          icon("x")
        )
      )
    ),
    bodyNode,
    footer ? el("div", { className: "drawer-footer" }, footer) : null
  );

  const scrim = el("div", { className: "scrim", onClick: () => close() });
  const container = el("div", { className: "drawer-root" }, scrim, panel);

  let closed = false;
  let release = null;

  function close(reason) {
    if (closed) return;
    closed = true;
    if (release) release();
    container.remove();
    unlockScroll();
    if (onClose) onClose(reason);
  }

  root.append(container);
  lockScroll();
  release = trapFocus(panel, () => close("escape"));

  return {
    close,
    el: panel,
    body: bodyNode,
    setTitle(text) { titleNode.textContent = text; },
  };
}

/** Close every open overlay. Used by the global Escape handler as a backstop. */
export function closeAllOverlays() {
  document.querySelectorAll(".drawer-root, .modal-root").forEach((n) => n.remove());
  overlayCount = 0;
  document.body.style.overflow = "";
}

/* ==========================================================================
   Loading / empty / error states
   ========================================================================== */

/**
 * A shimmering placeholder.
 *
 * @param {object} [opts]
 * @param {number|string} [opts.width]
 * @param {number|string} [opts.height=10]
 * @param {string} [opts.radius]
 * @param {string} [opts.className]
 */
export function skeleton(opts = {}) {
  const { width, height = 10, radius, className } = opts;
  return el("div", {
    className: classNames(["skeleton", className]),
    "aria-hidden": "true",
    style: {
      width: typeof width === "number" ? `${width}px` : width || "100%",
      height: typeof height === "number" ? `${height}px` : height,
      ...(radius ? { borderRadius: radius } : {}),
    },
  });
}

/** A stack of `lines` text skeletons with a natural ragged right edge. */
export function skeletonText(lines = 3) {
  const widths = ["100%", "92%", "68%", "84%", "58%"];
  return el(
    "div",
    { className: "stack stack-2" },
    Array.from({ length: lines }, (_, i) => skeleton({ width: widths[i % widths.length], height: 10 }))
  );
}

/**
 * The standard "nothing here" panel.
 *
 * @param {object} opts
 * @param {string} [opts.icon="layers"]
 * @param {string} opts.title
 * @param {string|Node} [opts.body]
 * @param {Array<Node>} [opts.actions]
 * @param {string} [opts.tone]
 */
export function emptyState(opts = {}) {
  const { icon: iconName = "layers", title, body, actions, tone } = opts;
  return el(
    "div",
    { className: "empty-state", dataset: tone ? { tone } : {} },
    el("div", { className: "empty-state-icon" }, icon(iconName)),
    el("div", { className: "empty-state-title" }, title),
    body ? el("div", { className: "empty-state-body" }, body) : null,
    actions && actions.length ? el("div", { className: "empty-state-actions" }, actions) : null
  );
}

/**
 * The standard failure panel, with a retry affordance.
 *
 * Accepts an ApiError, a plain Error, or a string. The machine-readable code is
 * surfaced in a monospace detail line because "something went wrong" is useless
 * to whoever has to fix it.
 *
 * @param {Error|string} error
 * @param {Function} [onRetry]
 * @param {{title?: string}} [opts]
 */
export function errorState(error, onRetry, opts = {}) {
  const message = typeof error === "string" ? error : error?.message || "Unexpected error";
  const code = typeof error === "object" && error ? error.code : null;
  const status = typeof error === "object" && error ? error.status : null;

  return el(
    "div",
    { className: "empty-state error-state" },
    el("div", { className: "empty-state-icon" }, icon("alert")),
    el("div", { className: "empty-state-title" }, opts.title || "Could not load this view"),
    el("div", { className: "empty-state-body" }, message),
    code || status
      ? el("div", { className: "error-state-detail" }, [code, status ? `HTTP ${status}` : null].filter(Boolean).join(" · "))
      : null,
    onRetry
      ? el(
          "div",
          { className: "empty-state-actions" },
          el("button", { className: "btn btn-secondary", onClick: () => onRetry() }, icon("refresh"), "Try again")
        )
      : null
  );
}

/**
 * The gated-feature panel. Names the plan that unlocks the capability rather
 * than pretending the route does not exist — discoverability is the point.
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {string} opts.body
 * @param {string} opts.plan Plan slug that unlocks it, e.g. "business".
 * @param {Array<Node>} [opts.actions]
 */
export function upsellState(opts = {}) {
  const { title, body, plan, actions } = opts;
  return el(
    "div",
    { className: "empty-state upsell" },
    el("div", { className: "empty-state-icon" }, icon("lock")),
    el("div", { className: "empty-state-title" }, title),
    el("div", { className: "empty-state-body" }, body),
    plan
      ? el("div", { className: "upsell-plan" }, icon("sparkle"), `${plan} plan`)
      : null,
    actions && actions.length ? el("div", { className: "empty-state-actions" }, actions) : null
  );
}

/* ==========================================================================
   Formatters
   ========================================================================== */

const MINUTE = 60, HOUR = 3600, DAY = 86400, WEEK = 604800, MONTH = 2629800, YEAR = 31557600;

/**
 * Human "3d ago" / "in 2h" phrasing.
 * Returns "" for null input so callers can render it unconditionally.
 *
 * @param {string|number|Date} value ISO-8601 string, epoch ms, or Date.
 * @param {{now?: number}} [opts]
 */
export function relativeTime(value, opts = {}) {
  const ts = toTime(value);
  if (ts === null) return "";

  const now = opts.now ?? Date.now();
  const deltaSec = (now - ts) / 1000;
  const abs = Math.abs(deltaSec);
  const past = deltaSec >= 0;

  let text;
  if (abs < 45) text = "just now";
  else if (abs < HOUR) text = `${Math.round(abs / MINUTE)}m`;
  else if (abs < DAY) text = `${Math.round(abs / HOUR)}h`;
  else if (abs < WEEK) text = `${Math.round(abs / DAY)}d`;
  else if (abs < MONTH) text = `${Math.round(abs / WEEK)}w`;
  else if (abs < YEAR) text = `${Math.round(abs / MONTH)}mo`;
  // A decimal only earns its place below ~18 months, where "1.4y" says
  // something "1y" does not. Beyond that it is false precision.
  else text = `${(abs / YEAR).toFixed(abs < YEAR * 1.5 ? 1 : 0)}y`;

  if (text === "just now") return text;
  return past ? `${text} ago` : `in ${text}`;
}

/**
 * The one locale the UI formats dates in.
 *
 * Passing `undefined` hands formatting to the operating system, which produced
 * Portuguese dates ("10 de set. de 2026") inside an English interface on a pt-BR
 * machine. The product ships one language, so it pins one locale.
 */
export const LOCALE = "en-GB";

/** Absolute timestamp for tooltips: "16 Aug 2026, 13:45". */
export function absoluteTime(value) {
  const ts = toTime(value);
  if (ts === null) return "";
  return new Date(ts).toLocaleString(LOCALE, {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/** Date only: "16 Aug 2026". */
export function formatDate(value) {
  const ts = toTime(value);
  if (ts === null) return "";
  return new Date(ts).toLocaleDateString(LOCALE, { day: "numeric", month: "short", year: "numeric" });
}

function toTime(value) {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value;
  // The API emits ISO-8601 UTC without a zone designator in some rows; assume Z.
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
  const ts = Date.parse(normalized);
  return Number.isNaN(ts) ? null : ts;
}

/**
 * Format a duration in seconds.
 *
 * `compact` (default) gives at most two units — "4d 6h", "3h 12m", "45s".
 * `days` gives a single decimal day figure, which is the right unit for lead
 * time and cycle time where sub-hour precision is noise.
 *
 * @param {number} seconds
 * @param {{style?: "compact"|"days"|"long"}} [opts]
 */
export function formatDuration(seconds, opts = {}) {
  const style = opts.style || "compact";
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";

  const s = Math.max(0, Math.round(Number(seconds)));

  if (style === "days") {
    const d = s / DAY;
    if (d >= 10) return `${Math.round(d)}d`;
    if (d >= 1) return `${d.toFixed(1)}d`;
    return `${(s / HOUR).toFixed(1)}h`;
  }

  const units = [
    ["d", DAY],
    ["h", HOUR],
    ["m", MINUTE],
    ["s", 1],
  ];

  if (style === "long") {
    const names = { d: "day", h: "hour", m: "minute", s: "second" };
    const parts = [];
    let rest = s;
    for (const [suffix, size] of units) {
      const n = Math.floor(rest / size);
      if (n > 0) {
        parts.push(`${n} ${names[suffix]}${n === 1 ? "" : "s"}`);
        rest -= n * size;
      }
      if (parts.length === 2) break;
    }
    return parts.length ? parts.join(", ") : "0 seconds";
  }

  const parts = [];
  let rest = s;
  for (const [suffix, size] of units) {
    const n = Math.floor(rest / size);
    if (n > 0 || parts.length) {
      if (n > 0) parts.push(`${n}${suffix}`);
      rest -= n * size;
    }
    if (parts.length === 2) break;
  }
  return parts.length ? parts.join(" ") : "0s";
}

/** Compact counts: 1200 -> "1.2k". */
export function formatNumber(value, opts = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  const decimals = opts.decimals;
  if (Math.abs(n) >= 10000) return `${(n / 1000).toFixed(1)}k`;
  if (decimals !== undefined) return n.toFixed(decimals);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Percentage with a single decimal below 10%: 34.2 -> "34%". */
export function formatPercent(value, opts = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  const decimals = opts.decimals ?? (Math.abs(n) < 10 ? 1 : 0);
  return `${n.toFixed(decimals)}%`;
}

/**
 * Up to two initials from a display name, falling back to an email local part.
 * "Ana Beatriz Souza" -> "AS"; "ops@nagare.app" -> "OP".
 */
export function initials(name) {
  const raw = String(name || "").trim();
  if (!raw) return "?";

  if (raw.includes("@") && !raw.includes(" ")) {
    return raw.split("@")[0].slice(0, 2).toUpperCase();
  }

  const words = raw.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/**
 * Deterministic avatar hue for a seed (user id, name or email).
 *
 * Returns a hue in degrees. The saturation and lightness that make it legible
 * live in components.css, per theme — so the same user keeps the same hue in
 * light and dark while contrast stays AA in both.
 */
export function avatarColor(seed) {
  const str = String(seed || "");
  let hash = 0;
  for (let i = 0; i < str.length; i += 1) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0; // force int32
  }
  // 12 evenly-spaced hues read as a deliberate set rather than a random smear.
  return (Math.abs(hash) % 12) * 30;
}

/**
 * Build an avatar element for a user-like object.
 *
 * An unassigned card (`user` falsy) deliberately gets a quiet dashed outline
 * rather than a fully-coloured circle with a literal "?" — a solid glyph in
 * the identity-colour palette reads as an error state ("this failed to load
 * a person") rather than as "nobody is assigned yet".
 *
 * @param {{id?: string, name?: string, email?: string}|null} user
 * @param {{size?: "xs"|"sm"|"md"|"lg"|"xl", title?: boolean}} [opts]
 */
export function avatar(user, opts = {}) {
  const { size, title = true } = opts;
  const sizeClass = size ? `avatar-${size}` : null;

  if (!user) {
    return el("span", {
      className: classNames(["avatar", "avatar-unassigned", sizeClass]),
      ...(title ? { "data-tooltip": "Unassigned" } : {}),
      "aria-hidden": title ? null : "true",
    });
  }

  const name = user.name || user.email || "Unassigned";
  const hue = avatarColor(user.id || user.email || name);
  return el("span", {
    className: classNames(["avatar", sizeClass]),
    style: { "--avatar-hue": hue },
    dataset: { hue: "" },
    ...(title ? { "data-tooltip": name } : {}),
    "aria-hidden": title ? null : "true",
  }, initials(name));
}

/* ==========================================================================
   Small helpers shared across views
   ========================================================================== */

/** Title-case a snake_case enum for display: "fixed_date" -> "Fixed date". */
export function humanize(value) {
  if (!value) return "";
  const s = String(value).replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Debounce, for search inputs and resize handlers. */
export function debounce(fn, wait = 200) {
  let timer = null;
  return function debounced(...args) {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(() => fn.apply(this, args), wait);
  };
}

/**
 * Open a dropdown anchored to `anchor`. Closes on outside click, Escape, or
 * any selection. Returns a handle so callers can close it themselves.
 *
 * @param {HTMLElement} anchor
 * @param {Array<Node>} items
 * @param {{align?: "left"|"right", minWidth?: number}} [opts]
 */
export function menu(anchor, items, opts = {}) {
  const { align = "left", minWidth } = opts;
  document.querySelectorAll(".menu[data-managed]").forEach((n) => n.remove());

  const node = el("div", {
    className: "menu",
    dataset: { managed: "" },
    role: "menu",
    style: minWidth ? { minWidth: `${minWidth}px` } : {},
  }, items);

  document.body.append(node);

  const rect = anchor.getBoundingClientRect();
  const width = node.offsetWidth;
  const height = node.offsetHeight;

  // Flip above / inward when the menu would leave the viewport.
  let top = rect.bottom + 4;
  if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);

  let left = align === "right" ? rect.right - width : rect.left;
  left = Math.min(Math.max(8, left), window.innerWidth - width - 8);

  node.style.position = "fixed";
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
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
      anchor.focus();
    }
  }

  // Deferred so the click that opened the menu does not immediately close it.
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
