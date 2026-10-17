/**
 * Nagare — HTTP client.
 *
 * Thin wrapper over fetch that owns four concerns and no more:
 *   1. the `/api/v1` base and the Bearer token
 *   2. unwrapping the server's `{"error": {code, message, details}}` envelope
 *      into a typed ApiError
 *   3. one automatic error toast per failure (suppressible per call)
 *   4. de-duplicating concurrent identical GETs
 *
 * It depends only on ui.js (for toasts) so it can be imported from anywhere,
 * including modules that load before the router exists.
 */

import { toast } from "./ui.js";

/**
 * Relative to the page, so the same build answers at the root (`:8770/`) and behind
 * the platform gateway (`/nagare/`, prefix stripped by Caddy).
 */
export const API_BASE = new URL("api/v1", document.baseURI).pathname;
const TOKEN_KEY = "nagare.token";

/* ==========================================================================
   Token storage
   ========================================================================== */

let memoryToken = null; // fallback when localStorage is unavailable

/** @returns {string|null} the stored access token */
export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || memoryToken;
  } catch {
    return memoryToken;
  }
}

/** Persist the access token (or clear it when passed a falsy value). */
export function setToken(token) {
  memoryToken = token || null;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode — the in-memory copy carries the session */
  }
}

/** Drop the token and every cached response tied to it. */
export function clearToken() {
  setToken(null);
  invalidate();
}

/* ==========================================================================
   Errors
   ========================================================================== */

/**
 * A failed API call.
 *
 * `code` is the server's machine-readable identifier (`wip_limit_exceeded`,
 * `backward_move_blocked`, ...) which callers switch on; `details` carries the
 * structured payload, e.g. which WIP policy broke and its current/max.
 */
export class ApiError extends Error {
  /**
   * @param {string} message
   * @param {{code?: string, details?: object, status?: number, path?: string}} [info]
   */
  constructor(message, info = {}) {
    super(message);
    this.name = "ApiError";
    this.code = info.code || "unknown_error";
    this.details = info.details || null;
    this.status = info.status ?? 0;
    this.path = info.path || null;
  }

  /** True when the failure is worth a retry rather than a fix. */
  get isTransient() {
    return this.status === 0 || this.status === 408 || this.status === 429 || this.status >= 500;
  }
}

/* Human phrasing for the codes the UI surfaces most. Anything not listed falls
   back to the server's own message, which is already user-facing. */
const FRIENDLY = {
  network_error: "Cannot reach the Nagare server.",
  timeout: "The server took too long to respond.",
  unauthorized: "Your session has expired. Please sign in again.",
  forbidden: "You do not have permission to do that.",
  not_found: "That item no longer exists.",
  invalid_credentials: "Incorrect email or password.",
  feature_disabled: "That feature is not enabled on your plan.",
};

/* ==========================================================================
   GET de-duplication
   ========================================================================== */

/** path -> { promise, expires } */
const inflight = new Map();
/** path -> { value, expires } for opt-in short-lived caching */
const cache = new Map();

/**
 * Drop cached and in-flight GETs.
 * @param {string} [prefix] Only entries whose path starts with this are dropped.
 */
export function invalidate(prefix) {
  for (const map of [inflight, cache]) {
    if (!prefix) { map.clear(); continue; }
    for (const key of map.keys()) {
      if (key.startsWith(prefix)) map.delete(key);
    }
  }
}

/* ==========================================================================
   Request
   ========================================================================== */

/**
 * Serialise a query object. Arrays repeat the key (`service_class=a&service_class=b`),
 * which is what FastAPI expects for `List[str]` params. Null/empty are dropped.
 */
export function buildQuery(params) {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== null && item !== undefined && item !== "") search.append(key, item);
      }
    } else if (typeof value === "boolean") {
      search.append(key, value ? "true" : "false");
    } else {
      search.append(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

/**
 * Whether a 401 on this path should bounce the user to the login view.
 * A 401 from the login endpoint itself means "wrong password", not "logged out".
 */
function shouldRedirectOn401(path) {
  return !path.startsWith("/auth/login");
}

let redirectingToLogin = false;

function handleUnauthorized() {
  if (redirectingToLogin) return;
  redirectingToLogin = true;
  clearToken();

  // Remember where the user was so app.js can return them after signing in.
  const current = window.location.hash.replace(/^#/, "");
  if (current && !current.startsWith("/login")) {
    try { sessionStorage.setItem("nagare.returnTo", current); } catch { /* non-critical */ }
  }

  window.location.hash = "#/login";
  window.setTimeout(() => { redirectingToLogin = false; }, 500);
}

const DEFAULT_TIMEOUT = 20000;

/**
 * Perform a request.
 *
 * @param {string} method
 * @param {string} path Relative to `/api/v1`, e.g. `/boards/123/view`.
 * @param {object} [body] JSON body for write methods.
 * @param {object} [opts]
 * @param {object} [opts.query] Query params.
 * @param {boolean} [opts.silent=false] Suppress the automatic error toast.
 * @param {AbortSignal} [opts.signal]
 * @param {number} [opts.timeout=20000]
 * @param {number} [opts.cache=0] For GETs: reuse a response this many ms old.
 * @returns {Promise<any>} Parsed JSON, or null for 204.
 * @throws {ApiError}
 */
async function request(method, path, body, opts = {}) {
  const { query, silent = false, signal, timeout = DEFAULT_TIMEOUT } = opts;
  const url = `${API_BASE}${path}${buildQuery(query)}`;

  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined && body !== null) headers["Content-Type"] = "application/json";

  // Own timeout, merged with any caller-supplied signal.
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort("timeout"), timeout);
  if (signal) {
    if (signal.aborted) controller.abort(signal.reason);
    else signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body !== undefined && body !== null ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      credentials: "same-origin",
    });
  } catch (cause) {
    window.clearTimeout(timer);

    // A caller-initiated abort is not an error worth reporting.
    if (signal?.aborted) throw new ApiError("Request cancelled", { code: "aborted", status: 0, path });

    const timedOut = controller.signal.reason === "timeout";
    const error = new ApiError(
      FRIENDLY[timedOut ? "timeout" : "network_error"],
      { code: timedOut ? "timeout" : "network_error", status: 0, path }
    );
    if (!silent) reportError(error);
    throw error;
  }
  window.clearTimeout(timer);

  if (response.status === 204 || response.status === 205) return null;

  const raw = await response.text();
  let payload = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      // A non-JSON body on a failing response is almost always an HTML error
      // page from a proxy, a dev server, or an unhandled traceback. Showing it
      // to the user is noise, so it goes to the console and they get a
      // sentence that actually tells them what to do.
      if (!response.ok) {
        console.warn(`[api] non-JSON ${response.status} response from ${path}:`, raw.slice(0, 300));
        const error = new ApiError(
          response.status === 404
            ? "That API endpoint is not available. The server may still be starting up."
            : `The server returned an unexpected ${response.status} response.`,
          { code: "invalid_response", status: response.status, path }
        );
        if (!silent) reportError(error);
        throw error;
      }
      return null;
    }
  }

  if (response.ok) return payload;

  const envelope = payload?.error || {};
  const code = envelope.code
    || (response.status === 401 ? "unauthorized"
      : response.status === 403 ? "forbidden"
      : response.status === 404 ? "not_found"
      : "request_failed");

  // The server's message wins when it has one: it is already user-facing and
  // it is far more specific than anything a generic table can say ("Build is
  // at its WIP limit of 4" beats "That could not be done"). FRIENDLY is the
  // fallback for bare envelopes and for codes the client raises itself.
  const message = envelope.message
    || payload?.detail
    || FRIENDLY[code]
    || `Request failed with status ${response.status}`;

  const error = new ApiError(message, {
    code,
    details: envelope.details || null,
    status: response.status,
    path,
  });

  if (response.status === 401 && shouldRedirectOn401(path)) {
    if (!silent) reportError(error);
    handleUnauthorized();
    throw error;
  }

  if (!silent) reportError(error);
  throw error;
}

/** Surface a failure as a toast. Kept separate so callers can reuse it. */
export function reportError(error) {
  if (error?.code === "aborted") return;
  toast(error?.message || "Something went wrong", {
    tone: "danger",
    title: error?.code && error.code !== "request_failed" ? titleFor(error.code) : null,
  });
}

function titleFor(code) {
  return code
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/* ==========================================================================
   Public surface
   ========================================================================== */

/**
 * GET with in-flight de-duplication.
 *
 * Two views mounting at once and both asking for `/flags/effective` produce a
 * single network call. Pass `cache: ms` to also reuse a recently completed
 * response — used for the flag and workspace lookups that every view needs.
 */
function get(path, opts = {}) {
  const key = `${path}${buildQuery(opts.query)}`;
  const now = Date.now();

  if (opts.cache) {
    const hit = cache.get(key);
    if (hit && hit.expires > now) return Promise.resolve(hit.value);
  }

  const pending = inflight.get(key);
  if (pending) return pending;

  const promise = request("GET", path, undefined, opts)
    .then((value) => {
      if (opts.cache) cache.set(key, { value, expires: Date.now() + opts.cache });
      return value;
    })
    .finally(() => {
      inflight.delete(key);
    });

  inflight.set(key, promise);
  return promise;
}

/** POST. Invalidates the GET cache — any write may have changed anything. */
function post(path, body, opts = {}) {
  return request("POST", path, body, opts).then(afterWrite);
}

/** PATCH. */
function patch(path, body, opts = {}) {
  return request("PATCH", path, body, opts).then(afterWrite);
}

/** PUT. */
function put(path, body, opts = {}) {
  return request("PUT", path, body, opts).then(afterWrite);
}

/** DELETE. Named `del` because `delete` is reserved. */
function del(path, opts = {}) {
  return request("DELETE", path, undefined, opts).then(afterWrite);
}

function afterWrite(result) {
  cache.clear();
  return result;
}

export const api = {
  get,
  post,
  patch,
  put,
  del,
  request,
  invalidate,
  buildQuery,
  getToken,
  setToken,
  clearToken,
};

export default api;
