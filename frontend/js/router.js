/**
 * Nagare — hash router.
 *
 * Owns the route table from SPEC 9.1, lazy-loads view modules, enforces the
 * mount/unmount lifecycle, and gates routes on feature flags and roles.
 *
 * Views are plain ES modules exporting:
 *     export function mount(el, params) {}
 *     export function unmount() {}
 *     export function onQuery(params) {}   // optional
 *
 * `onQuery` is an optimisation, not a requirement: when only the query string
 * changes (a filter was toggled) the router calls it instead of tearing the
 * whole view down, so the board does not flash on every filter click.
 */

import store, { readFiltersFromUrl, hasFlag, can, defaultBoard } from "./store.js";
import { el, render, clear, emptyState, errorState, upsellState, icon, announce } from "./ui.js";

/* ==========================================================================
   Plan metadata
   --------------------------------------------------------------------------
   `GET /flags/effective` returns booleans only — it deliberately does not say
   *why* a flag is off. To name the unlocking plan in the upsell we need the
   flag -> min_plan mapping, which is canonical in SPEC section 3 and is
   transcribed here rather than fetched, because `GET /flags` (which carries
   min_plan) is admin-guarded and a viewer must still see a correct upsell.
   ========================================================================== */

export const FLAG_MIN_PLAN = Object.freeze({
  upstream_board: "free",
  downstream_board: "free",
  unified_flow: "team",
  wip_column: "free",
  wip_group: "team",
  wip_flow: "team",
  envelope: "team",
  service_classes: "free",
  custom_fields: "team",
  card_history: "free",
  metrics_basic: "free",
  metrics_advanced: "business",
  monte_carlo: "business",
  kmm_dashboard: "business",
  simulator: "business",
  api_access: "team",
  mcp_server: "business",
  rbac_advanced: "business",
  multi_project: "free",
});

const PLAN_LABEL = Object.freeze({
  free: "Free",
  team: "Team",
  business: "Business",
  enterprise: "Enterprise",
});

/* ==========================================================================
   Route table (SPEC 9.1)
   ========================================================================== */

/**
 * `module` is imported lazily on first visit. `flag` gates on an effective
 * feature flag; `capability` gates on the role matrix. `chrome: false` means
 * the route renders without the rail/topbar (login).
 */
const ROUTES = [
  { path: "/login", view: "login", chrome: false, anonymous: true, title: "Sign in" },

  {
    path: "/board/:boardId",
    module: "./board.js",
    title: "Board",
    nav: "board",
  },
  { path: "/board", redirect: () => boardPath("board") },

  {
    path: "/projects/:projectId",
    module: "./projects.js",
    flag: "multi_project",
    title: "Project",
    nav: "projects",
  },
  {
    path: "/projects",
    module: "./projects.js",
    flag: "multi_project",
    title: "Projects",
    nav: "projects",
  },

  {
    path: "/metrics/:boardId",
    module: "./metrics.js",
    flag: "metrics_basic",
    title: "Metrics",
    nav: "metrics",
  },
  { path: "/metrics", redirect: () => boardPath("metrics") },

  {
    path: "/simulator/:boardId",
    module: "./simulator.js",
    flag: "simulator",
    capability: "simulator",
    title: "Simulator",
    nav: "simulator",
  },
  {
    path: "/simulator",
    module: "./simulator.js",
    flag: "simulator",
    capability: "simulator",
    title: "Simulator",
    nav: "simulator",
  },

  {
    path: "/admin/users",
    module: "./admin.js",
    capability: "manage_users",
    params: { section: "users" },
    title: "Users",
    nav: "admin",
  },
  {
    path: "/admin/flags",
    module: "./admin.js",
    capability: "manage_plans",
    params: { section: "flags" },
    title: "Feature flags",
    nav: "admin",
  },
  {
    path: "/admin/plans",
    module: "./admin.js",
    capability: "manage_plans",
    params: { section: "plans" },
    title: "Plans",
    nav: "admin",
  },
  { path: "/admin", redirect: "/admin/users" },

  { path: "/", redirect: () => boardPath("board") },
];

/** Build `/board/<id>` for the default board, or fall back to a landing state. */
function boardPath(prefix) {
  const board = defaultBoard();
  return board ? `/${prefix}/${board.id}` : `/${prefix}/none`;
}

/* ==========================================================================
   Matching
   ========================================================================== */

/**
 * Split `#/board/abc?segment=upstream` into its path and query parts.
 * @param {string} hash
 */
export function parseHash(hash) {
  const raw = String(hash || "").replace(/^#/, "") || "/";
  const [path, search = ""] = raw.split("?");
  return {
    path: path.startsWith("/") ? path : `/${path}`,
    search: new URLSearchParams(search),
  };
}

function segments(path) {
  return path.split("/").filter(Boolean);
}

/**
 * Match a path against the route table.
 * @returns {{route: object, params: object}|null}
 */
export function matchRoute(path) {
  const parts = segments(path);

  for (const route of ROUTES) {
    const pattern = segments(route.path);
    if (pattern.length !== parts.length) continue;

    const params = {};
    let matched = true;

    for (let i = 0; i < pattern.length; i += 1) {
      const token = pattern[i];
      if (token.startsWith(":")) params[token.slice(1)] = decodeURIComponent(parts[i]);
      else if (token !== parts[i]) { matched = false; break; }
    }

    if (matched) return { route, params: { ...route.params, ...params } };
  }

  // The root route has zero segments and needs its own check.
  if (parts.length === 0) {
    const root = ROUTES.find((r) => r.path === "/");
    if (root) return { route: root, params: {} };
  }

  return null;
}

/* ==========================================================================
   Registered views (for view modules the shell owns, e.g. login)
   ========================================================================== */

const registry = new Map();

/**
 * Register a view module by name so the router can mount it without an import.
 * app.js uses this for `#/login`, which lives in the shell.
 *
 * @param {string} name
 * @param {{mount: Function, unmount?: Function, onQuery?: Function}} view
 */
export function register(name, view) {
  registry.set(name, view);
}

/* ==========================================================================
   Lifecycle
   ========================================================================== */

let mountEl = null;
let current = null;      // { module, route, params, path }
let generation = 0;      // guards against a slow import resolving after a newer nav
let started = false;
const moduleCache = new Map();

/** Show the indeterminate bar while a view module is in flight. */
function setBusy(busy) {
  if (!mountEl) return;
  const existing = mountEl.querySelector(":scope > .route-progress");
  if (busy && !existing) {
    mountEl.prepend(el("div", { className: "route-progress", role: "presentation" }));
  } else if (!busy && existing) {
    existing.remove();
  }
}

async function loadModule(specifier) {
  if (moduleCache.has(specifier)) return moduleCache.get(specifier);
  const mod = await import(specifier);
  moduleCache.set(specifier, mod);
  return mod;
}

function teardown() {
  if (current?.module?.unmount) {
    try {
      current.module.unmount();
    } catch (error) {
      // A view that throws on the way out must not block the next one.
      console.error("[router] unmount failed", error);
    }
  }
  current = null;
  if (mountEl) clear(mountEl);
}

/**
 * Render a non-view outcome (gate, 404, error) into the view container.
 * These bypass the module lifecycle entirely.
 */
function renderState(node, title) {
  teardown();
  render(mountEl, node);
  if (title) document.title = `${title} · Nagare`;
}

/* ==========================================================================
   Navigation
   ========================================================================== */

async function handleRoute() {
  if (!mountEl) return;

  const { path, search } = parseHash(window.location.hash);
  const match = matchRoute(path);
  const generationAtStart = (generation += 1);

  // ---- unknown route -----------------------------------------------------
  if (!match) {
    store.set({ route: { path, name: "404" } });
    renderState(
      emptyState({
        icon: "search",
        title: "Page not found",
        body: `No view is registered for ${path}.`,
        actions: [
          el("a", { className: "btn btn-primary", href: "#/" }, icon("arrow-right"), "Go to my board"),
        ],
      }),
      "Not found"
    );
    return;
  }

  const { route, params } = match;

  // ---- redirects ---------------------------------------------------------
  if (route.redirect) {
    const target = typeof route.redirect === "function" ? route.redirect() : route.redirect;
    // replace, not assign: a redirect should not add a history entry the user
    // has to press Back through twice.
    window.location.replace(`${window.location.pathname}${window.location.search}#${target}`);
    return;
  }

  // ---- filters come from the URL on every navigation ----------------------
  readFiltersFromUrl(search);

  const state = store.get();

  // ---- authentication ----------------------------------------------------
  if (!route.anonymous && !state.user && state.status !== "loading") {
    try {
      sessionStorage.setItem("nagare.returnTo", `${path}${search.toString() ? `?${search}` : ""}`);
    } catch { /* non-critical */ }
    window.location.hash = "#/login";
    return;
  }

  // ---- chrome ------------------------------------------------------------
  // The login route hides the rail and topbar with a body class rather than
  // hiding #app — #view lives *inside* #app, so hiding the shell would hide
  // the very container the login view mounts into.
  document.body.classList.toggle("is-bare", route.chrome === false);

  store.set({ route: { path, name: route.nav || route.view, params, chrome: route.chrome !== false } });

  // ---- feature-flag gate -------------------------------------------------
  if (route.flag && !hasFlag(route.flag)) {
    const plan = FLAG_MIN_PLAN[route.flag];
    renderState(
      upsellState({
        title: `${route.title} is not enabled`,
        body: `The ${humanFlag(route.flag)} capability is part of a higher plan, or has been switched off for this workspace. An administrator can enable it under Admin › Feature flags.`,
        plan: PLAN_LABEL[plan] || plan,
        actions: can("manage_plans")
          ? [el("a", { className: "btn btn-primary", href: "#/admin/flags" }, icon("flag"), "Open feature flags")]
          : [],
      }),
      route.title
    );
    return;
  }

  // ---- role gate ---------------------------------------------------------
  if (route.capability && !can(route.capability)) {
    renderState(
      emptyState({
        icon: "lock",
        title: "You do not have access to this area",
        body: `${route.title} requires a role with the "${route.capability.replace(/_/g, " ")}" capability. Your role is ${state.user?.role || "unknown"}.`,
        actions: [el("a", { className: "btn btn-secondary", href: "#/" }, "Back to my board")],
      }),
      route.title
    );
    return;
  }

  // ---- same view, query-only change --------------------------------------
  const sameView = current
    && current.route === route
    && shallowParamsEqual(current.params, params);

  if (sameView) {
    if (typeof current.module.onQuery === "function") {
      try {
        current.module.onQuery({ ...params, search });
      } catch (error) {
        console.error("[router] onQuery failed", error);
      }
    }
    return;
  }

  // ---- mount -------------------------------------------------------------
  teardown();

  let module = registry.get(route.view);

  if (!module && route.module) {
    setBusy(true);
    try {
      module = await loadModule(route.module);
    } catch (error) {
      if (generationAtStart !== generation) return; // superseded
      setBusy(false);
      // warn, not error: a view module that is not on disk yet is a degraded
      // state the shell is designed to survive, not a shell defect.
      console.warn(`[router] failed to load ${route.module}`, error);
      renderState(
        errorState(
          {
            message: `The ${route.title} view could not be loaded. The module ${route.module.replace("./", "js/")} is missing or failed to parse.`,
            code: "view_module_unavailable",
          },
          () => handleRoute(),
          { title: `${route.title} is unavailable` }
        ),
        route.title
      );
      return;
    }
    setBusy(false);
  }

  if (generationAtStart !== generation) return; // a newer navigation won

  if (!module || typeof module.mount !== "function") {
    renderState(
      errorState(
        {
          message: `The ${route.title} view does not export a mount() function.`,
          code: "view_contract_violation",
        },
        null,
        { title: `${route.title} is unavailable` }
      ),
      route.title
    );
    return;
  }

  current = { module, route, params, path };
  document.title = `${route.title} · Nagare`;

  try {
    await module.mount(mountEl, { ...params, search });
  } catch (error) {
    if (generationAtStart !== generation) return;
    console.error(`[router] mount failed for ${route.path}`, error);
    render(mountEl, errorState(error, () => handleRoute(), { title: `${route.title} failed to render` }));
  }

  // Move focus to the view region so keyboard and screen-reader users land in
  // the new content rather than at the top of the rail.
  if (route.chrome !== false) {
    mountEl.focus({ preventScroll: true });
    announce(`${route.title} loaded`);
  }
}

function humanFlag(key) {
  return key.replace(/_/g, " ");
}

function shallowParamsEqual(a = {}, b = {}) {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((key) => a[key] === b[key]);
}

/* ==========================================================================
   Public API
   ========================================================================== */

/**
 * Start routing.
 * @param {HTMLElement} container The `<main id="view">` element.
 */
export function start(container) {
  mountEl = container;
  if (!started) {
    started = true;
    window.addEventListener("hashchange", handleRoute);
  }
  return handleRoute();
}

/** Re-run the current route — used after sign-in and after flags change. */
export function refresh() {
  current = null; // force a real remount rather than an onQuery
  return handleRoute();
}

/**
 * Navigate.
 * @param {string} path e.g. `/board/abc`
 * @param {{replace?: boolean, query?: URLSearchParams|object}} [opts]
 */
export function navigate(path, opts = {}) {
  let target = path.startsWith("#") ? path.slice(1) : path;

  if (opts.query) {
    const params = opts.query instanceof URLSearchParams
      ? opts.query
      : new URLSearchParams(Object.entries(opts.query).filter(([, v]) => v != null && v !== ""));
    const qs = params.toString();
    if (qs) target += `${target.includes("?") ? "&" : "?"}${qs}`;
  }

  const hash = `#${target.startsWith("/") ? target : `/${target}`}`;
  if (hash === window.location.hash) {
    handleRoute();
    return;
  }

  if (opts.replace) {
    window.location.replace(`${window.location.pathname}${window.location.search}${hash}`);
  } else {
    window.location.hash = hash;
  }
}

/** The currently mounted route descriptor, or null. */
export function currentRoute() {
  return current ? { path: current.path, params: current.params, name: current.route.nav } : null;
}

/**
 * Is a nav destination reachable for this user right now?
 * The rail uses this to decide between an active link and a locked one.
 *
 * @param {"board"|"projects"|"metrics"|"simulator"|"admin"} nav
 * @returns {{allowed: boolean, reason: "flag"|"role"|null, plan: string|null}}
 */
export function navAccess(nav) {
  const route = ROUTES.find((r) => r.nav === nav && !r.redirect);
  if (!route) return { allowed: true, reason: null, plan: null };

  if (route.flag && !hasFlag(route.flag)) {
    return { allowed: false, reason: "flag", plan: PLAN_LABEL[FLAG_MIN_PLAN[route.flag]] || null };
  }
  if (route.capability && !can(route.capability)) {
    return { allowed: false, reason: "role", plan: null };
  }
  return { allowed: true, reason: null, plan: null };
}

export const router = { start, refresh, navigate, register, currentRoute, navAccess, matchRoute, parseHash };

export default router;
