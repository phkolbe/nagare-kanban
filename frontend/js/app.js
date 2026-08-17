/**
 * Nagare — application bootstrap.
 *
 * Loads the session, workspace, effective flags and boards; renders the shell
 * chrome (rail + top bar); owns the theme, the global keyboard shortcuts and
 * the login view; then hands navigation to the router.
 *
 * Degradation is a first-class concern here. The backend may not be up, the
 * token may be stale, and view modules may not exist yet. Every one of those
 * paths ends in a rendered state with a way forward — never a blank screen and
 * never an unhandled rejection.
 */

import api, { getToken, setToken, clearToken, ApiError } from "./api.js";
import store, {
  hasFlag, can, findBoard, defaultBoard, setSegment, filtersToSearchParams,
} from "./store.js";
import router, { register, navigate, navAccess } from "./router.js";
import {
  el, icon, render, clear, toast, modal, menu, avatar, skeleton,
  errorState, announce, debounce, humanize,
} from "./ui.js";

/* ==========================================================================
   Theme
   ========================================================================== */

const THEME_KEY = "nagare.theme";
const THEMES = ["light", "dark", "system"];

/** The user's stored preference — not necessarily the theme being displayed. */
function storedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return THEMES.includes(value) ? value : "system";
  } catch {
    return "system";
  }
}

/** The theme actually on screen, resolving "system" against the OS. */
function resolvedTheme(preference = store.get().theme) {
  if (preference !== "system") return preference;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Apply and persist a theme preference.
 * "system" removes the attribute entirely so the media query in tokens.css
 * takes over — that is what makes the OS preference live-update.
 */
function setTheme(preference) {
  const next = THEMES.includes(preference) ? preference : "system";

  if (next === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", next);

  try {
    if (next === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch { /* private mode — the choice lasts for this session only */ }

  store.set({ theme: next });
}

const THEME_ICON = { light: "sun", dark: "moon", system: "monitor" };

/* ==========================================================================
   Chrome — left rail
   ========================================================================== */

const NAV_ROUTES = {
  board: () => `#/board/${currentBoardId() || "none"}`,
  projects: () => "#/projects",
  metrics: () => `#/metrics/${currentBoardId() || "none"}`,
  simulator: () => "#/simulator",
  admin: () => "#/admin/users",
};

/** The board the chrome should point at: the routed one, else the default. */
function currentBoardId() {
  const routed = store.get().route?.params?.boardId;
  if (routed && routed !== "none" && findBoard(routed)) return routed;
  return defaultBoard()?.id || null;
}

function currentBoard() {
  return findBoard(currentBoardId());
}

function renderRail() {
  const activeNav = store.get().route?.name;

  for (const [nav, href] of Object.entries(NAV_ROUTES)) {
    const item = document.querySelector(`.rail-item[data-nav="${nav}"]`);
    if (!item) continue;

    const access = navAccess(nav);
    item.href = href();
    item.classList.toggle("is-locked", !access.allowed);

    if (nav === activeNav) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");

    const lock = item.querySelector(".rail-lock");
    if (lock) {
      lock.hidden = access.allowed;
      clear(lock);
      if (!access.allowed) {
        lock.append(icon("lock"));
        // A native `title`, not the CSS tooltip: .rail-nav scrolls, which
        // establishes a clipping context that would cut a ::after tooltip off
        // exactly where the collapsed rail needs it most.
        item.title = access.reason === "flag"
          ? `Included in the ${access.plan} plan`
          : "Your role does not have access";
      } else {
        item.removeAttribute("title");
      }
    }
  }

  renderRailFoot();
}

function renderRailFoot() {
  const foot = document.getElementById("rail-foot");
  if (!foot) return;

  const { workspace, plan, status } = store.get();

  if (status === "loading" && !workspace) {
    render(foot, el("div", { className: "stack stack-2", style: { padding: "var(--space-1)" } },
      skeleton({ width: "70%", height: 10 }),
      skeleton({ width: "45%", height: 8 })
    ));
    return;
  }

  if (!workspace) { clear(foot); return; }

  render(
    foot,
    el(
      "div",
      { className: "stack stack-1", style: { padding: "var(--space-1) var(--space-2)" } },
      el("div", { className: "t-xs t-semibold truncate" }, workspace.name),
      el(
        "div",
        { className: "row row-1" },
        el("span", { className: "badge", "data-tone": "accent" }, plan?.name || plan?.slug || "Workspace")
      )
    )
  );
}

/* ==========================================================================
   Chrome — top bar
   ========================================================================== */

function renderTopbar() {
  renderBoardSwitcher();
  renderSegmentControl();
  renderTopbarActions();
}

function renderBoardSwitcher() {
  const slot = document.getElementById("board-switcher-slot");
  if (!slot) return;

  const { boards, status, route } = store.get();
  const showsBoard = ["board", "metrics", "simulator"].includes(route?.name);

  if (!showsBoard) { clear(slot); return; }

  if (status === "loading" && !boards.length) {
    render(slot, skeleton({ width: 150, height: 20, radius: "var(--radius-md)" }));
    return;
  }

  const board = currentBoard();

  const button = el(
    "button",
    {
      className: "board-switcher",
      "aria-label": `Switch board — currently ${board?.name || "no board selected"}`,
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      onClick: (event) => openBoardMenu(event.currentTarget),
    },
    icon(board?.kind === "simulator" ? "flask" : "board"),
    el("span", { className: "truncate" }, board?.name || "No board"),
    icon("chevrons-up-down")
  );

  render(slot, button);
}

function openBoardMenu(anchor) {
  const { boards, route } = store.get();
  const prefix = route?.name === "metrics" ? "metrics" : route?.name === "simulator" ? "simulator" : "board";
  const activeId = currentBoardId();

  const operational = boards.filter((b) => b.kind !== "simulator" && !b.archived);
  const simulators = boards.filter((b) => b.kind === "simulator" && !b.archived);

  const items = [];

  const section = (label, list, iconName) => {
    if (!list.length) return;
    items.push(el("div", { className: "menu-label" }, label));
    for (const board of list) {
      items.push(
        el(
          "button",
          {
            className: "menu-item",
            role: "menuitemradio",
            "aria-checked": board.id === activeId ? "true" : "false",
            onClick: () => navigate(`/${prefix}/${board.id}`),
          },
          icon(board.id === activeId ? "check" : iconName),
          el("span", { className: "truncate" }, board.name)
        )
      );
    }
  };

  section("Operational", operational, "board");
  section("Simulator", simulators, "flask");

  if (!items.length) {
    items.push(el("div", { className: "menu-label" }, "No boards available"));
  }

  menu(anchor, items, { minWidth: 240 });
}

/** Segment switcher — Req. 1. Only the segments the plan enables are offered. */
function renderSegmentControl() {
  const slot = document.getElementById("segment-slot");
  if (!slot) return;

  const { segment, route } = store.get();
  const relevant = ["board", "metrics"].includes(route?.name);

  if (!relevant || !currentBoard()) { clear(slot); return; }

  const options = [
    { id: "upstream", label: "Upstream", icon: "queue", flag: "upstream_board" },
    { id: "downstream", label: "Downstream", icon: "activity", flag: "downstream_board" },
    { id: "unified", label: "Unified", icon: "layers", flag: "unified_flow" },
  ].filter((option) => hasFlag(option.flag));

  if (options.length < 2) { clear(slot); return; }

  // If the active segment was gated off, fall back to one that is available
  // rather than rendering a control with nothing selected.
  if (!options.some((o) => o.id === segment)) {
    setSegment(options[options.length - 1].id);
    return;
  }

  render(
    slot,
    el(
      "div",
      { className: "segmented", role: "tablist", "aria-label": "Board segment" },
      options.map((option) =>
        el(
          "button",
          {
            role: "tab",
            "aria-selected": option.id === segment ? "true" : "false",
            onClick: () => setSegment(option.id),
          },
          icon(option.icon),
          option.label
        )
      )
    )
  );
}

function renderTopbarActions() {
  const slot = document.getElementById("topbar-actions");
  if (!slot) return;

  const { user, theme, status } = store.get();

  const themeButton = el(
    "button",
    {
      className: "btn btn-ghost btn-icon",
      "aria-label": `Theme: ${theme}`,
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      "data-tooltip": `Theme — ${theme}`,
      "data-tooltip-pos": "bottom",
      onClick: (event) => openThemeMenu(event.currentTarget),
    },
    icon(THEME_ICON[theme] || "monitor")
  );

  const helpButton = el(
    "button",
    {
      className: "btn btn-ghost btn-icon",
      "aria-label": "Keyboard shortcuts",
      "data-tooltip": "Keyboard shortcuts — ?",
      "data-tooltip-pos": "bottom",
      onClick: () => openShortcutHelp(),
    },
    icon("keyboard")
  );

  const userButton = status === "loading" && !user
    ? skeleton({ width: 24, height: 24, radius: "999px" })
    : el(
        "button",
        {
          className: "btn btn-ghost btn-icon",
          "aria-label": `Account menu for ${user?.name || "user"}`,
          "aria-haspopup": "menu",
          "aria-expanded": "false",
          onClick: (event) => openUserMenu(event.currentTarget),
        },
        avatar(user, { size: "sm", title: false })
      );

  render(slot, themeButton, helpButton, userButton);
}

function openThemeMenu(anchor) {
  const active = store.get().theme;
  menu(
    anchor,
    THEMES.map((value) =>
      el(
        "button",
        {
          className: "menu-item",
          role: "menuitemradio",
          "aria-checked": value === active ? "true" : "false",
          onClick: () => setTheme(value),
        },
        icon(THEME_ICON[value]),
        humanize(value),
        value === active ? el("span", { className: "menu-shortcut" }, icon("check")) : null
      )
    ),
    { align: "right", minWidth: 170 }
  );
}

function openUserMenu(anchor) {
  const { user, plan, workspace } = store.get();

  const items = [
    el(
      "div",
      { className: "menu-label", style: { textTransform: "none", letterSpacing: "0", paddingBottom: "var(--space-2)" } },
      el("div", { className: "t-sm t-semibold", style: { color: "var(--fg)" } }, user?.name || "Signed in"),
      el("div", { className: "t-xs subtle truncate" }, user?.email || ""),
      el(
        "div",
        { className: "row row-1", style: { marginTop: "var(--space-2)" } },
        el("span", { className: "badge" }, humanize(user?.role || "")),
        plan ? el("span", { className: "badge", "data-tone": "accent" }, plan.name || plan.slug) : null
      )
    ),
    el("div", { className: "menu-separator" }),
  ];

  if (can("manage_users")) {
    items.push(
      el("a", { className: "menu-item", href: "#/admin/users" }, icon("users"), "Manage users")
    );
  }
  if (can("manage_plans")) {
    items.push(
      el("a", { className: "menu-item", href: "#/admin/flags" }, icon("flag"), "Feature flags"),
      el("a", { className: "menu-item", href: "#/admin/plans" }, icon("sparkle"), "Plans")
    );
  }

  items.push(
    el("button", { className: "menu-item", onClick: () => openShortcutHelp() },
      icon("keyboard"), "Keyboard shortcuts", el("span", { className: "menu-shortcut" }, "?")),
    el("div", { className: "menu-separator" }),
    el("button", { className: "menu-item is-danger", onClick: () => signOut() },
      icon("logout"), "Sign out")
  );

  if (workspace?.timezone) {
    items.splice(items.length - 2, 0,
      el("div", { className: "menu-label" }, `Timezone · ${workspace.timezone}`));
  }

  menu(anchor, items, { align: "right", minWidth: 230 });
}

/* ==========================================================================
   Global search
   ========================================================================== */

function wireSearch() {
  const input = document.getElementById("global-search");
  if (!input) return;

  const apply = debounce((value) => {
    store.setFilters({ q: value });
    // Text search only means something on a board; take the user there.
    if (value && store.get().route?.name !== "board") {
      const id = currentBoardId();
      if (id) navigate(`/board/${id}`, { query: filtersToSearchParams() });
    }
  }, 260);

  input.addEventListener("input", (event) => apply(event.target.value));

  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      input.value = "";
      store.setFilters({ q: "" });
      input.blur();
    }
  });

  // Keep the box in step with filters restored from the URL or cleared elsewhere.
  store.select(
    (state) => state.filters.q,
    (value) => { if (document.activeElement !== input && input.value !== value) input.value = value; }
  );
}

/* ==========================================================================
   Keyboard shortcuts
   ========================================================================== */

const SHORTCUTS = [
  { group: "Navigation", keys: ["g", "b"], label: "Go to boards" },
  { group: "Navigation", keys: ["g", "p"], label: "Go to projects" },
  { group: "Navigation", keys: ["g", "m"], label: "Go to metrics" },
  { group: "Navigation", keys: ["g", "s"], label: "Go to simulator" },
  { group: "Navigation", keys: ["g", "a"], label: "Go to admin" },
  { group: "General", keys: ["/"], label: "Focus search" },
  { group: "General", keys: ["?"], label: "Show this help" },
  { group: "General", keys: ["Esc"], label: "Close overlay or clear search" },
  { group: "Board", keys: ["Enter"], label: "Open the focused card" },
  { group: "Board", keys: ["m"], label: "Move the focused card" },
  { group: "Board", keys: ["←", "→"], label: "Move focus between columns" },
  { group: "Board", keys: ["↑", "↓"], label: "Move focus between cards" },
];

const GOTO = {
  b: () => NAV_ROUTES.board(),
  p: () => NAV_ROUTES.projects(),
  m: () => NAV_ROUTES.metrics(),
  s: () => NAV_ROUTES.simulator(),
  a: () => NAV_ROUTES.admin(),
};

/** True when the user is typing and shortcuts must stay out of the way. */
function isTyping(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

let gotoPending = false;
let gotoTimer = null;

function wireShortcuts() {
  document.addEventListener("keydown", (event) => {
    // Escape always works, including from inside a field.
    if (event.key === "Escape") {
      const overlay = document.querySelector(".modal-root, .drawer-root");
      if (overlay) return; // the overlay's own focus trap handles it
      if (isTyping(event.target)) event.target.blur();
      return;
    }

    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isTyping(event.target)) return;

    // `g` then a destination — the two-key chord used by Linear, Gmail, GitHub.
    if (gotoPending) {
      const destination = GOTO[event.key.toLowerCase()];
      clearGoto();
      if (destination) {
        event.preventDefault();
        window.location.hash = destination();
      }
      return;
    }

    if (event.key === "g") {
      gotoPending = true;
      gotoTimer = window.setTimeout(clearGoto, 1400);
      return;
    }

    if (event.key === "/") {
      const search = document.getElementById("global-search");
      // Below 900px the search box is hidden to keep the top bar dense. Only
      // swallow the keystroke if there is actually something to focus,
      // otherwise "/" would silently do nothing on a narrow screen.
      if (search && search.offsetParent !== null) {
        event.preventDefault();
        search.focus();
        search.select();
      }
      return;
    }

    if (event.key === "?") {
      event.preventDefault();
      openShortcutHelp();
    }
  });
}

function clearGoto() {
  gotoPending = false;
  if (gotoTimer) window.clearTimeout(gotoTimer);
  gotoTimer = null;
}

let shortcutModal = null;

function openShortcutHelp() {
  if (shortcutModal) { shortcutModal.close(); shortcutModal = null; return; }

  const groups = new Map();
  for (const item of SHORTCUTS) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }

  shortcutModal = modal({
    title: "Keyboard shortcuts",
    size: "lg",
    body: el(
      "div",
      { className: "shortcuts-grid" },
      Array.from(groups, ([name, items]) =>
        el(
          "div",
          { className: "stack stack-1" },
          el("div", { className: "eyebrow", style: { marginBottom: "var(--space-2)" } }, name),
          items.map((item) =>
            el(
              "div",
              { className: "shortcut-row" },
              el("span", { className: "muted" }, item.label),
              el(
                "span",
                { className: "keys" },
                item.keys.flatMap((key, i) => [
                  i > 0 && item.keys.length === 2 && name === "Navigation"
                    ? el("span", { className: "sep" }, "then")
                    : i > 0
                      ? el("span", { className: "sep" }, "/")
                      : null,
                  el("kbd", { className: "kbd" }, key),
                ])
              )
            )
          )
        )
      )
    ),
    onClose: () => { shortcutModal = null; },
  });
}

/* ==========================================================================
   Login view (owned by the shell)
   ========================================================================== */

const loginView = {
  mount(container) {
    let submitting = false;

    const errorSlot = el("div");
    const emailInput = el("input", {
      className: "input", id: "login-email", type: "email", name: "email",
      autocomplete: "username", required: true, placeholder: "you@example.com",
      value: "master@nagare.app", "data-autofocus": "",
    });
    const passwordInput = el("input", {
      className: "input", id: "login-password", type: "password", name: "password",
      autocomplete: "current-password", required: true, placeholder: "Your password",
    });
    const submitButton = el("button", { className: "btn btn-primary btn-lg btn-block", type: "submit" }, "Sign in");

    function showError(message) {
      render(errorSlot, el("div", { className: "alert", "data-tone": "danger" }, icon("alert"), message));
    }

    async function onSubmit(event) {
      event.preventDefault();
      if (submitting) return;

      const email = emailInput.value.trim();
      const password = passwordInput.value;

      if (!email || !password) {
        showError("Enter your email and password to continue.");
        return;
      }

      submitting = true;
      submitButton.classList.add("is-loading");
      clear(errorSlot);

      try {
        // `silent` because a wrong password belongs inline in the form, not in
        // a toast in the corner of the screen.
        const result = await api.post("/auth/login", { email, password }, { silent: true });
        const token = result?.access_token;
        if (!token) throw new ApiError("The server did not return an access token.", { code: "invalid_response" });

        setToken(token);
        store.set({ user: result.user || null, status: "ready", error: null });

        await loadWorkspaceData();

        let target = "/";
        try {
          target = sessionStorage.getItem("nagare.returnTo") || "/";
          sessionStorage.removeItem("nagare.returnTo");
        } catch { /* non-critical */ }

        toast(`Welcome back, ${result.user?.name?.split(" ")[0] || "there"}.`, { tone: "success" });
        // navigate() re-runs the route itself, either via hashchange or
        // directly when the hash is unchanged — calling refresh() as well
        // would mount the destination view twice.
        navigate(target, { replace: true });
      } catch (error) {
        submitting = false;
        submitButton.classList.remove("is-loading");
        showError(
          error?.code === "invalid_credentials" || error?.status === 401
            ? "That email and password combination was not recognised."
            : error?.message || "Sign-in failed. Please try again."
        );
        passwordInput.focus();
        passwordInput.select();
      }
    }

    render(
      container,
      el(
        "div",
        { className: "login-shell" },
        el(
          "div",
          { className: "login-card" },
          el(
            "div",
            { className: "login-brand" },
            el("div", { className: "wordmark-glyph", "aria-hidden": "true" }, "流"),
            el(
              "div",
              { className: "stack stack-1" },
              el("h1", { className: "t-2xl" }, "Nagare"),
              el("p", { className: "t-sm subtle" }, "Kanban flow management")
            )
          ),
          el(
            "form",
            { className: "stack stack-4", onSubmit, novalidate: true },
            errorSlot,
            el("div", { className: "field" },
              el("label", { className: "label", for: "login-email" }, "Email"), emailInput),
            el("div", { className: "field" },
              el("label", { className: "label", for: "login-password" }, "Password"), passwordInput),
            submitButton
          ),
          el(
            "div",
            { className: "login-hint" },
            el("strong", null, "Demo workspace. "),
            "Sign in as ",
            el("code", null, "master@nagare.app"),
            " with the password ",
            el("code", null, "nagare123"),
            ". Every seeded role shares that password."
          )
        )
      )
    );

    window.setTimeout(() => passwordInput.focus(), 0);
  },

  unmount() {
    document.body.classList.remove("is-bare");
  },
};

function signOut() {
  clearToken();
  store.reset();
  store.set({ status: "anonymous" });
  navigate("/login", { replace: true });
  toast("Signed out.", { tone: "neutral" });
}

/* ==========================================================================
   Data bootstrap
   ========================================================================== */

/**
 * Load everything the chrome needs. Each request is independent and failure of
 * one must not blank the others: a workspace without a readable plan is still
 * a usable workspace.
 */
async function loadWorkspaceData() {
  const [workspace, flags, boards, plans] = await Promise.all([
    api.get("/workspace", { silent: true, cache: 15000 }).catch(() => null),
    api.get("/flags/effective", { silent: true, cache: 15000 }).catch(() => ({})),
    api.get("/boards", { silent: true }).catch(() => null),
    api.get("/plans", { silent: true, cache: 60000 }).catch(() => null),
  ]);

  const boardList = Array.isArray(boards?.items) ? boards.items : Array.isArray(boards) ? boards : [];
  const planList = Array.isArray(plans?.items) ? plans.items : [];
  const activePlan = workspace?.active_plan_id
    ? planList.find((p) => p.id === workspace.active_plan_id) || null
    : null;

  store.set({
    workspace: workspace || null,
    plan: activePlan,
    flags: flags && typeof flags === "object" ? flags : {},
    boards: boardList,
  });

  return { workspace, flags, boardList };
}

/** Render the chrome in its loading shape so the first paint is never empty. */
function renderChromeSkeleton() {
  renderRail();
  renderTopbar();
}

async function boot() {
  setTheme(storedTheme());
  wireShortcuts();
  wireSearch();
  register("login", loginView);

  // Chrome reacts to state; register before any fetch so the skeleton shows.
  store.subscribe((state, changed) => {
    if (["route", "boards", "board", "flags", "user", "status"].some((k) => changed.has(k))) {
      renderRail();
    }
    if (["route", "boards", "segment", "flags", "user", "theme", "status", "plan", "workspace"]
      .some((k) => changed.has(k))) {
      renderTopbar();
    }
    if (["workspace", "plan", "status"].some((k) => changed.has(k))) renderRailFoot();
  });

  renderChromeSkeleton();

  const viewEl = document.getElementById("view");

  // ---- no token: straight to the login view -------------------------------
  if (!getToken()) {
    store.set({ status: "anonymous" });
    if (!window.location.hash.startsWith("#/login")) {
      window.history.replaceState(null, "", `${window.location.pathname}#/login`);
    }
    await router.start(viewEl);
    return;
  }

  // ---- validate the session ----------------------------------------------
  try {
    const me = await api.get("/auth/me", { silent: true });
    // The endpoint may return the user directly or wrapped; accept both.
    store.set({ user: me?.user || me || null, status: "ready" });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      clearToken();
      store.set({ status: "anonymous", user: null });
      window.history.replaceState(null, "", `${window.location.pathname}#/login`);
      await router.start(viewEl);
      return;
    }

    // The server is unreachable or broken. Show a real error with a retry
    // rather than an infinite skeleton or a redirect loop.
    store.set({ status: "error", error });
    render(
      viewEl,
      errorState(
        error,
        async () => { render(viewEl, buildBootSkeleton()); await boot(); },
        {
          // Distinguish "the server is not there" from "the server answered
          // badly" — they need completely different things from the reader.
          title: error?.status
            ? "The Nagare API returned an error"
            : "Cannot reach the Nagare API",
        }
      )
    );
    return;
  }

  await loadWorkspaceData();
  await router.start(viewEl);
}

/** A calm loading shape for the very first paint and for boot retries. */
function buildBootSkeleton() {
  return el(
    "div",
    { className: "view-pad stack stack-6" },
    el(
      "div",
      { className: "stack stack-2" },
      skeleton({ width: 220, height: 22, radius: "var(--radius-md)" }),
      skeleton({ width: 320, height: 12 })
    ),
    el(
      "div",
      { className: "grid grid-auto" },
      Array.from({ length: 4 }, () => skeleton({ height: 96, radius: "var(--radius-lg)" }))
    ),
    skeleton({ height: 260, radius: "var(--radius-lg)" })
  );
}

/* ==========================================================================
   Global error handling
   ========================================================================== */

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  // ApiError has already produced a toast on its way through api.js; toasting
  // again would double-report every failure.
  if (reason instanceof ApiError) {
    event.preventDefault();
    return;
  }
  console.error("[nagare] unhandled rejection", reason);
  toast(reason?.message || "An unexpected error occurred.", { tone: "danger", title: "Unexpected error" });
  event.preventDefault();
});

window.addEventListener("error", (event) => {
  // Resource load failures (a missing stylesheet) surface here with no message.
  if (event.message) console.error("[nagare] uncaught error", event.error || event.message);
});

/* ==========================================================================
   Go
   ========================================================================== */

const viewContainer = document.getElementById("view");
if (viewContainer) render(viewContainer, buildBootSkeleton());

boot().catch((error) => {
  console.error("[nagare] bootstrap failed", error);
  const target = document.getElementById("view");
  if (target) {
    render(target, errorState(error, () => window.location.reload(), { title: "Nagare failed to start" }));
  }
});

// Expose a small handle for other view modules and for debugging in the console.
// Not an API surface — the modules import what they need directly.
window.nagare = { store, api, router, setTheme, resolvedTheme, announce };
