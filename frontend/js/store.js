/**
 * Nagare — application store.
 *
 * A deliberately small observable: a single state object, shallow `set`,
 * subscribers, and `select` for listeners that only care about one slice.
 * No framework, no proxies, no immutability library.
 *
 * The store also owns filter <-> URL serialisation. Filters live in the hash
 * query string so that a filtered board is a shareable link and the browser's
 * back button restores the previous filter set.
 */

/* ==========================================================================
   Shape
   ========================================================================== */

/** The empty filter set. Also the reference for "is anything filtered?". */
export const EMPTY_FILTERS = Object.freeze({
  project_id: null,
  service_class: [],
  assignee_id: null,
  tag: null,
  q: "",
  is_blocked: null,
  demand_type: null,
});

const INITIAL = {
  /** @type {{id, name, email, role}|null} */
  user: null,
  /** @type {{id, name, active_plan_id, timezone}|null} */
  workspace: null,
  /** @type {{name, slug, tier_order}|null} */
  plan: null,
  /** @type {Object<string, boolean>} effective feature flags */
  flags: {},
  /** @type {Array<object>} every board the user can see */
  boards: [],
  /** @type {object|null} the board currently in view */
  board: null,
  /** @type {"upstream"|"downstream"|"unified"} */
  segment: "unified",
  /** @type {typeof EMPTY_FILTERS} */
  filters: { ...EMPTY_FILTERS },

  /** Bootstrap lifecycle: the shell renders skeletons until this is "ready". */
  status: "loading", // loading | ready | error | anonymous
  error: null,
  theme: "system", // light | dark | system
  route: null,
};

let state = { ...INITIAL };

/* ==========================================================================
   Pub/sub
   ========================================================================== */

const subscribers = new Set();
let notifyScheduled = false;
let pendingKeys = new Set();

/** @returns {object} the current state. Treat it as read-only. */
export function get() {
  return state;
}

/**
 * Shallow-merge a patch into state and notify subscribers.
 *
 * Notification is coalesced to a microtask so a burst of `set` calls during
 * bootstrap produces one render, not six.
 *
 * @param {object} patch
 */
export function set(patch) {
  if (!patch) return state;

  let changed = false;
  for (const [key, value] of Object.entries(patch)) {
    if (!Object.is(state[key], value)) {
      changed = true;
      pendingKeys.add(key);
    }
  }
  if (!changed) return state;

  state = { ...state, ...patch };
  scheduleNotify();
  return state;
}

function scheduleNotify() {
  if (notifyScheduled) return;
  notifyScheduled = true;
  queueMicrotask(() => {
    notifyScheduled = false;
    const keys = pendingKeys;
    pendingKeys = new Set();
    for (const fn of Array.from(subscribers)) {
      try {
        fn(state, keys);
      } catch (error) {
        // One broken subscriber must not stop the others from updating.
        console.error("[store] subscriber failed", error);
      }
    }
  });
}

/**
 * Subscribe to every change.
 * @param {(state: object, changedKeys: Set<string>) => void} fn
 * @param {{immediate?: boolean}} [opts]
 * @returns {() => void} unsubscribe
 */
export function subscribe(fn, opts = {}) {
  subscribers.add(fn);
  if (opts.immediate) fn(state, new Set(Object.keys(state)));
  return () => subscribers.delete(fn);
}

/**
 * Subscribe to a derived slice, firing only when that slice actually changes.
 *
 * @param {(state: object) => any} selector
 * @param {(value: any, previous: any) => void} listener
 * @param {{immediate?: boolean, equals?: (a, b) => boolean}} [opts]
 * @returns {() => void} unsubscribe
 */
export function select(selector, listener, opts = {}) {
  const equals = opts.equals || shallowEqual;
  let previous = selector(state);
  if (opts.immediate) listener(previous, undefined);

  return subscribe(() => {
    const next = selector(state);
    if (equals(next, previous)) return;
    const prior = previous;
    previous = next;
    listener(next, prior);
  });
}

/** Structural equality good enough for state slices (arrays, flat objects). */
export function shallowEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;

  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;

  return aKeys.every((key) => {
    const av = a[key];
    const bv = b[key];
    if (Array.isArray(av) && Array.isArray(bv)) {
      return av.length === bv.length && av.every((item, i) => Object.is(item, bv[i]));
    }
    return Object.is(av, bv);
  });
}

/** Reset to the initial state, keeping the chosen theme. Used on sign-out. */
export function reset() {
  const { theme } = state;
  state = { ...INITIAL, filters: { ...EMPTY_FILTERS }, theme };
  pendingKeys = new Set(Object.keys(state));
  scheduleNotify();
}

/* ==========================================================================
   Feature flags & RBAC (SPEC 3 and 4)
   ========================================================================== */

/**
 * Is a feature effective for this workspace?
 * Unknown keys are treated as OFF: a UI that fails closed cannot leak a
 * capability the plan has not paid for.
 *
 * @param {string} key
 */
export function hasFlag(key) {
  return state.flags?.[key] === true;
}

/** Capability -> roles allowed, transcribed from SPEC section 4. */
export const CAPABILITIES = Object.freeze({
  manage_plans: ["master"],
  manage_users: ["master", "administrator"],
  manage_boards: ["master", "administrator"],
  manage_cards: ["master", "administrator", "editor", "system"],
  read: ["master", "administrator", "editor", "viewer", "simulator", "system"],
  simulator: ["master", "administrator", "simulator"],
  api_tokens: ["master", "administrator", "system"],
});

/**
 * Can the signed-in user do `capability`?
 * @param {keyof typeof CAPABILITIES} capability
 */
export function can(capability) {
  const role = state.user?.role;
  if (!role) return false;
  const allowed = CAPABILITIES[capability];
  return Array.isArray(allowed) && allowed.includes(role);
}

/** Convenience for the many places that ask "is this a read-only user?". */
export function isReadOnly() {
  return Boolean(state.user) && !can("manage_cards");
}

/* ==========================================================================
   Filters
   ========================================================================== */

/** Current filters. */
export function getFilters() {
  return state.filters;
}

/**
 * Merge a patch into the filter set and push it to the URL.
 * Passing `null`/`""` for a key clears it.
 *
 * @param {Partial<typeof EMPTY_FILTERS>} patch
 * @param {{silent?: boolean}} [opts] `silent` skips the URL write.
 */
export function setFilters(patch, opts = {}) {
  const next = normalizeFilters({ ...state.filters, ...patch });
  if (shallowEqual(next, state.filters)) return state.filters;

  set({ filters: next });
  if (!opts.silent) writeFiltersToUrl();
  return next;
}

/** Clear every filter. */
export function clearFilters(opts = {}) {
  if (isFilterEmpty(state.filters)) return;
  set({ filters: { ...EMPTY_FILTERS } });
  if (!opts.silent) writeFiltersToUrl();
}

/** How many filters are active — drives the "Filters (3)" badge. */
export function activeFilterCount(filters = state.filters) {
  let count = 0;
  for (const [key, value] of Object.entries(EMPTY_FILTERS)) {
    const current = filters[key];
    if (Array.isArray(value)) {
      if (Array.isArray(current) && current.length) count += current.length;
    } else if (current !== null && current !== undefined && current !== "" && current !== value) {
      count += 1;
    }
  }
  return count;
}

/** @param {object} filters */
export function isFilterEmpty(filters = state.filters) {
  return activeFilterCount(filters) === 0;
}

function normalizeFilters(raw) {
  const out = { ...EMPTY_FILTERS };

  out.project_id = emptyToNull(raw.project_id);
  out.assignee_id = emptyToNull(raw.assignee_id);
  out.tag = emptyToNull(raw.tag);
  out.demand_type = emptyToNull(raw.demand_type);
  out.q = typeof raw.q === "string" ? raw.q : "";

  const sc = raw.service_class;
  out.service_class = Array.isArray(sc)
    ? Array.from(new Set(sc.filter(Boolean)))
    : (sc ? [sc] : []);

  // Tri-state: null = "either", true = blocked only, false = unblocked only.
  if (raw.is_blocked === true || raw.is_blocked === "true") out.is_blocked = true;
  else if (raw.is_blocked === false || raw.is_blocked === "false") out.is_blocked = false;
  else out.is_blocked = null;

  return out;
}

function emptyToNull(value) {
  return value === undefined || value === null || value === "" ? null : String(value);
}

/**
 * Filters shaped for `api.get(..., {query})`. Drops empties so the request URL
 * stays readable and the backend never sees `project_id=`.
 */
export function filtersToQuery(filters = state.filters) {
  const query = {};
  if (filters.project_id) query.project_id = filters.project_id;
  if (filters.assignee_id) query.assignee_id = filters.assignee_id;
  if (filters.tag) query.tag = filters.tag;
  if (filters.demand_type) query.demand_type = filters.demand_type;
  if (filters.q) query.q = filters.q;
  if (filters.is_blocked !== null) query.is_blocked = filters.is_blocked;
  if (filters.service_class?.length) query.service_class = filters.service_class;
  return query;
}

/* ---- URL serialisation ---------------------------------------------------- */

/**
 * Read filters (and segment) out of a hash query string and put them in state.
 * Called by the router on every navigation so a pasted link lands filtered.
 *
 * @param {URLSearchParams|string} search
 */
export function readFiltersFromUrl(search) {
  const params = search instanceof URLSearchParams
    ? search
    : new URLSearchParams(String(search || "").replace(/^\?/, ""));

  const next = normalizeFilters({
    project_id: params.get("project_id"),
    assignee_id: params.get("assignee_id"),
    tag: params.get("tag"),
    demand_type: params.get("demand_type"),
    q: params.get("q") || "",
    is_blocked: params.get("is_blocked"),
    // Comma-joined in the URL because a hash full of repeated keys is unreadable.
    service_class: (params.get("service_class") || "").split(",").filter(Boolean),
  });

  const patch = {};
  if (!shallowEqual(next, state.filters)) patch.filters = next;

  const segment = params.get("segment");
  if (segment && ["upstream", "downstream", "unified"].includes(segment) && segment !== state.segment) {
    patch.segment = segment;
  }

  if (Object.keys(patch).length) set(patch);
  return next;
}

/** Serialise the current filters and segment into a query string. */
export function filtersToSearchParams(filters = state.filters, segment = state.segment) {
  const params = new URLSearchParams();
  if (segment && segment !== "unified") params.set("segment", segment);
  if (filters.project_id) params.set("project_id", filters.project_id);
  if (filters.assignee_id) params.set("assignee_id", filters.assignee_id);
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.demand_type) params.set("demand_type", filters.demand_type);
  if (filters.q) params.set("q", filters.q);
  if (filters.is_blocked !== null) params.set("is_blocked", String(filters.is_blocked));
  if (filters.service_class?.length) params.set("service_class", filters.service_class.join(","));
  return params;
}

/**
 * Write the current filters into the address bar.
 *
 * Uses `replaceState` on purpose: adjusting a filter is not a navigation, and
 * pushing every keystroke would bury the previous *view* under fifty history
 * entries. It also means no `hashchange` fires, so the router never remounts
 * the view underneath the user.
 */
export function writeFiltersToUrl() {
  const hash = window.location.hash.replace(/^#/, "") || "/";
  const path = hash.split("?")[0];
  const params = filtersToSearchParams();
  const qs = params.toString();
  const next = `#${path}${qs ? `?${qs}` : ""}`;

  if (next === window.location.hash) return;
  window.history.replaceState(window.history.state, "", next);
}

/** Set the board segment (Req. 1) and reflect it in the URL. */
export function setSegment(segment) {
  if (!["upstream", "downstream", "unified"].includes(segment)) return;
  if (segment === state.segment) return;
  set({ segment });
  writeFiltersToUrl();
}

/* ==========================================================================
   Convenience selectors
   ========================================================================== */

/** Find a board by id or slug. */
export function findBoard(idOrSlug) {
  if (!idOrSlug) return null;
  return state.boards.find((b) => b.id === idOrSlug || b.slug === idOrSlug) || null;
}

/** The board to land on when no id is in the URL. */
export function defaultBoard() {
  return state.boards.find((b) => b.kind === "operational" && !b.archived)
    || state.boards.find((b) => !b.archived)
    || state.boards[0]
    || null;
}

export const store = {
  get,
  set,
  subscribe,
  select,
  reset,
  hasFlag,
  can,
  isReadOnly,
  getFilters,
  setFilters,
  clearFilters,
  activeFilterCount,
  isFilterEmpty,
  filtersToQuery,
  filtersToSearchParams,
  readFiltersFromUrl,
  writeFiltersToUrl,
  setSegment,
  findBoard,
  defaultBoard,
  shallowEqual,
  EMPTY_FILTERS,
  CAPABILITIES,
  get filters() { return state.filters; },
  get state() { return state; },
};

export default store;
