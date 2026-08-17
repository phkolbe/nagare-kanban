/**
 * Nagare — Admin (SPEC 9.6, Requirements 10 & 11).
 *
 * Three surfaces behind one module, selected by the route param `section`:
 *   #/admin/users   user directory + create/edit/activate + the SPEC §4
 *                   role × capability matrix
 *   #/admin/flags   the feature-flag switchboard, grouped by category
 *   #/admin/plans   the four plans as pricing cards, flag-set editor,
 *                   and the "which plan is active" switch
 *
 * Users and Flags/Plans sit behind different capabilities (`manage_users` vs.
 * the master-only `manage_plans`, SPEC §4), and the router already refuses to
 * mount this module for a role that cannot reach the section in the URL bar.
 * The tab strip goes one step further: a tab the signed-in role could never
 * open renders locked-with-an-explanation rather than as a dead link, so the
 * capability boundary is something a visitor can *see*, not just hit.
 */

import api from "./api.js";
import store, { can } from "./store.js";
import {
  el, icon, modal, toast, emptyState, errorState, upsellState, skeleton,
  confirmDialog, render, relativeTime, formatDate, humanize, avatar, debounce,
} from "./ui.js";

/* ==========================================================================
   Static catalogues
   --------------------------------------------------------------------------
   Mirrors backend/app/config.py so the UI has something to render even
   before (or when) a flag-gated lookup fails.
   ========================================================================== */

const ROLES = ["master", "administrator", "editor", "viewer", "simulator", "system"];

const ROLE_DESCRIPTIONS = {
  master: "Owns the workspace: billing plan, feature flags, users and every board.",
  administrator: "Runs the workspace day to day: users, boards, columns, policies and cards.",
  editor: "Works the board: creates, edits and moves cards.",
  viewer: "Read-only access to boards and metrics.",
  simulator: "Read-only on operational boards, full control of simulator boards.",
  system: "Non-human integration identity for the REST API and MCP server. Cannot sign in to the UI.",
};

const PLAN_LABEL = { free: "Free", team: "Team", business: "Business", enterprise: "Enterprise" };

const SECTIONS = [
  { key: "users", label: "Users", icon: "users", capability: "manage_users" },
  { key: "flags", label: "Feature flags", icon: "flag", capability: "manage_plans" },
  { key: "plans", label: "Plans", icon: "sparkle", capability: "manage_plans" },
];

/* ==========================================================================
   Module state
   ========================================================================== */

let root = null;
let generation = 0;
let section = "users";

let userFilters = { role: "", q: "" };
let userSearchDebounced = null;

/* ==========================================================================
   Lifecycle
   ========================================================================== */

/**
 * Router entry point.
 * @param {HTMLElement} container
 * @param {{section?: "users"|"flags"|"plans"}} params
 */
export async function mount(container, params = {}) {
  root = container;
  generation += 1;
  const token = generation;
  section = SECTIONS.some((s) => s.key === params.section) ? params.section : "users";

  if (section === "users") await renderUsers(token);
  else if (section === "flags") await renderFlags(token);
  else await renderPlans(token);
}

/** Router teardown. */
export function unmount() {
  generation += 1;
  userSearchDebounced = null;
  root = null;
}

function stale(token) {
  return token !== generation || !root;
}

function paint(token, node) {
  if (stale(token)) return;
  render(root, node);
}

/* ==========================================================================
   Shared chrome
   ========================================================================== */

function formatMoney(value) {
  const n = Number(value) || 0;
  return Number.isInteger(n) ? String(n) : n.toFixed(2);
}

function tabStrip() {
  return el(
    "nav",
    { className: "admin-tabs tabs", role: "tablist", "aria-label": "Admin sections" },
    SECTIONS.map((s) => {
      const selected = s.key === section;
      const allowed = can(s.capability);
      if (!allowed) {
        return el(
          "span",
          {
            className: "tab",
            role: "tab",
            tabIndex: -1,
            "aria-disabled": "true",
            "aria-selected": "false",
            "data-tooltip": "Only the master role can open this.",
          },
          icon(s.icon),
          s.label,
          icon("lock", { className: "tab-lock" })
        );
      }
      return el(
        "a",
        {
          className: "tab",
          role: "tab",
          "aria-selected": String(selected),
          href: `#/admin/${s.key}`,
        },
        icon(s.icon),
        s.label
      );
    })
  );
}

function pageHeader(title, lede, actions) {
  return el(
    "div",
    { className: "view-header" },
    el(
      "div",
      { className: "stack stack-2" },
      el("div", { className: "view-title-row" }, el("h1", null, "Admin")),
      el("p", { className: "view-lede" }, lede)
    ),
    actions ? el("div", { className: "row row-2" }, actions) : null
  );
}

function shell(title, lede, actions, body) {
  return el("div", { className: "view-pad" }, pageHeader(title, lede, actions), tabStrip(), body);
}

/* Small form helpers, local to this module (mirrors the pattern in projects.js). */
function field(label, control, opts = {}) {
  const error = el("div", { className: "field-error", hidden: true });
  return {
    node: el(
      "label",
      { className: "field" },
      el("span", { className: "label" }, label, opts.required ? el("span", { className: "subtle" }, " *") : null),
      control,
      opts.hint ? el("span", { className: "hint" }, opts.hint) : null,
      error
    ),
    control,
    setError(message) {
      error.textContent = message || "";
      error.hidden = !message;
      control.setAttribute("aria-invalid", message ? "true" : "false");
    },
  };
}

function inputEl(props) {
  return el("input", { className: "input", ...props });
}

function selectEl(options, value, props = {}) {
  return el(
    "select",
    { className: "select", ...props },
    options.map(([v, label]) => el("option", { value: v, selected: v === value }, label))
  );
}

/* ==========================================================================
   USERS
   ========================================================================== */

/** Unwrap `{items, total}` or a bare array. */
function items(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.items) ? payload.items : [];
}

async function renderUsers(token) {
  paint(token, shell("Users", "Loading the user directory…", null, usersSkeleton()));

  const me = store.get().user;
  let users;
  let catalogue = null;
  let catalogueError = null;

  try {
    const query = {};
    if (userFilters.role) query.role = userFilters.role;
    if (userFilters.q) query.q = userFilters.q;
    users = await api.get("/users", { query }).then(items);
  } catch (error) {
    paint(
      token,
      shell("Users", "The user directory.", null, errorState(error, () => renderUsers(++generation)))
    );
    return;
  }

  // The role matrix needs its own flag (`rbac_advanced`, business plan) — a
  // failure here degrades to an upsell panel, never the whole tab.
  try {
    catalogue = await api.get("/users/roles", { silent: true });
  } catch (error) {
    catalogueError = error;
  }

  if (stale(token)) return;

  const reload = () => renderUsers(++generation);
  const ctx = { me, reload, roleCounts: catalogue?.user_counts || null };

  const toolbar = el(
    "div",
    { className: "view-toolbar" },
    (() => {
      if (!userSearchDebounced) {
        userSearchDebounced = debounce((value) => {
          userFilters.q = value;
          renderUsers(++generation);
        }, 300);
      }
      const search = el("input", {
        className: "input input-sm",
        type: "search",
        placeholder: "Search name or email",
        "aria-label": "Search users",
        value: userFilters.q,
        onInput: (e) => userSearchDebounced(e.target.value.trim()),
      });
      return el("div", { className: "input-group" }, icon("search"), search);
    })(),
    el(
      "select",
      {
        className: "select select-sm",
        "aria-label": "Filter by role",
        style: { width: "auto" },
        onChange: (e) => {
          userFilters.role = e.target.value;
          renderUsers(++generation);
        },
      },
      el("option", { value: "" }, "All roles"),
      ROLES.map((r) => el("option", { value: r, selected: userFilters.role === r }, humanize(r)))
    ),
    userFilters.role || userFilters.q
      ? el(
          "button",
          {
            className: "btn btn-ghost btn-sm",
            onClick: () => { userFilters = { role: "", q: "" }; renderUsers(++generation); },
          },
          icon("x"),
          "Clear"
        )
      : null,
    el("div", { className: "spacer" }),
    can("manage_users")
      ? el("button", { className: "btn btn-primary btn-sm", onClick: () => openUserForm(null, ctx) }, icon("plus"), "New user")
      : null
  );

  const table = users.length
    ? el(
        "div",
        { className: "table-wrap" },
        el(
          "table",
          { className: "table" },
          el(
            "thead",
            null,
            el(
              "tr",
              null,
              ["User", "Role", "Status", "Created", "Last login", ""].map((h) => el("th", null, h))
            )
          ),
          el("tbody", null, users.map((u) => userRow(u, ctx)))
        )
      )
    : emptyState({
        icon: "users",
        title: userFilters.role || userFilters.q ? "No users match this filter" : "No users yet",
        body: userFilters.role || userFilters.q
          ? "Try a broader search, or clear the role filter."
          : "Create the first account for this workspace.",
        actions: userFilters.role || userFilters.q
          ? [el("button", { className: "btn btn-secondary", onClick: () => { userFilters = { role: "", q: "" }; renderUsers(++generation); } }, "Clear filters")]
          : can("manage_users")
            ? [el("button", { className: "btn btn-primary", onClick: () => openUserForm(null, ctx) }, icon("plus"), "New user")]
            : [],
      });

  paint(
    token,
    shell(
      "Users",
      "Every account that can sign in to Nagare, its role, and the capability matrix that role holds.",
      null,
      el(
        "div",
        null,
        toolbar,
        table,
        catalogue
          ? roleMatrixPanel(catalogue, me?.role)
          : catalogueError
            ? el(
                "div",
                { style: { marginTop: "var(--space-6)" } },
                upsellState({
                  title: "Role × capability matrix is not available",
                  body: catalogueError.message || "This view requires the full role matrix feature.",
                  plan: PLAN_LABEL[catalogueError.details?.min_plan] || "Business",
                })
              )
            : null
      )
    )
  );
}

function userRow(user, ctx) {
  const isSelf = user.id === ctx.me?.id;
  const isMasterRow = user.role === "master";
  const masterLocked = isMasterRow && ctx.me?.role !== "master";
  const lastActiveMaster = isMasterRow && user.is_active && ctx.roleCounts && (ctx.roleCounts.master || 0) <= 1;

  let lockReason = "";
  if (isSelf) lockReason = "You cannot change your own role or deactivate yourself.";
  else if (masterLocked) lockReason = "Only a master can modify another master account.";
  else if (lastActiveMaster) lockReason = "This is the last active master; the workspace needs at least one.";

  const roleDisabled = Boolean(isSelf || masterLocked || lastActiveMaster);
  const activeDisabled = Boolean(isSelf || masterLocked || (lastActiveMaster && user.is_active));

  const roleOptions = ROLES.filter((r) => r !== "master" || ctx.me?.role === "master" || r === user.role);

  const roleSelect = el(
    "select",
    {
      className: "select select-sm role-select",
      disabled: roleDisabled,
      "aria-label": `Role for ${user.name}`,
      ...(roleDisabled && lockReason ? { "data-tooltip": lockReason } : {}),
      onChange: async (event) => {
        const next = event.target.value;
        const previous = user.role;
        event.target.disabled = true;
        try {
          await api.patch(`/users/${user.id}`, { role: next });
          toast(`${user.name} is now ${humanize(next)}.`, { tone: "success" });
          await ctx.reload();
        } catch {
          event.target.value = previous;
          event.target.disabled = roleDisabled;
        }
      },
    },
    roleOptions.map((r) => el("option", { value: r, selected: r === user.role }, humanize(r)))
  );

  const activeSwitch = el("input", {
    className: "switch",
    type: "checkbox",
    role: "switch",
    checked: user.is_active,
    disabled: activeDisabled,
    "aria-label": `${user.name} is ${user.is_active ? "active" : "inactive"}`,
    ...(activeDisabled && lockReason ? { "data-tooltip": lockReason } : {}),
    onChange: async (event) => {
      const next = event.target.checked;
      event.target.disabled = true;
      try {
        await api.patch(`/users/${user.id}`, { is_active: next });
        toast(`${user.name} ${next ? "activated" : "deactivated"}.`, { tone: "success" });
        await ctx.reload();
      } catch {
        event.target.checked = !next;
        event.target.disabled = activeDisabled;
      }
    },
  });

  return el(
    "tr",
    { className: user.is_active ? "" : "row-inactive" },
    el(
      "td",
      null,
      el(
        "div",
        { className: "user-cell" },
        avatar(user, { size: "md" }),
        el(
          "div",
          { className: "user-cell-text" },
          el(
            "span",
            { className: "row row-1" },
            el("span", { className: "user-name" }, user.name),
            isSelf ? el("span", { className: "self-badge" }, "You") : null
          ),
          el("span", { className: "user-email" }, user.email)
        )
      )
    ),
    el("td", null, roleSelect),
    el(
      "td",
      null,
      el(
        "label",
        { className: "row row-1", style: { cursor: activeDisabled ? "not-allowed" : "pointer" } },
        activeSwitch,
        el("span", { className: "t-xs subtle" }, user.is_active ? "Active" : "Inactive")
      )
    ),
    el("td", { className: "t-xs subtle" }, formatDate(user.created_at)),
    el("td", { className: "t-xs subtle" }, user.last_login_at ? relativeTime(user.last_login_at) : "Never"),
    el(
      "td",
      { className: "shrink" },
      can("manage_users")
        ? el(
            "button",
            {
              className: "btn btn-ghost btn-icon btn-sm",
              "aria-label": `Edit ${user.name}`,
              onClick: () => openUserForm(user, ctx),
            },
            icon("edit")
          )
        : null
    )
  );
}

function matrixMark(on) {
  return el("span", { className: "matrix-mark", dataset: { on: String(Boolean(on)) } }, icon(on ? "check" : "x"));
}

function roleMatrixPanel(catalogue, myRole) {
  const roles = catalogue.roles || [];
  const capabilities = catalogue.capabilities || [];
  const labels = catalogue.capability_labels || {};

  return el(
    "div",
    { className: "panel", style: { marginTop: "var(--space-6)" } },
    el(
      "div",
      { className: "panel-header" },
      el("span", { className: "panel-title" }, "Role × capability matrix"),
      el("span", { className: "badge" }, `${roles.length} roles · ${capabilities.length} capabilities`)
    ),
    el(
      "div",
      { className: "panel-body stack stack-5" },
      el(
        "div",
        { className: "table-wrap" },
        el(
          "table",
          { className: "role-matrix" },
          el(
            "thead",
            null,
            el(
              "tr",
              null,
              el("th", null, "Capability"),
              roles.map((r) => el("th", { dataset: r.key === myRole ? { mine: "true" } : {} }, r.label))
            )
          ),
          el(
            "tbody",
            null,
            capabilities.map((cap) =>
              el(
                "tr",
                null,
                el("th", { scope: "row" }, labels[cap] || humanize(cap)),
                roles.map((r) =>
                  el("td", { dataset: r.key === myRole ? { mine: "true" } : {} }, matrixMark(r.capabilities?.[cap]))
                )
              )
            )
          )
        )
      ),
      el(
        "div",
        { className: "role-legend" },
        roles.map((r) =>
          el(
            "div",
            { className: "role-legend-item" },
            el("span", { className: "role-legend-name" }, r.label),
            el("span", { className: "role-legend-desc" }, r.description || ROLE_DESCRIPTIONS[r.key] || "")
          )
        )
      )
    )
  );
}

function usersSkeleton() {
  return el(
    "div",
    null,
    el("div", { className: "view-toolbar" }, skeleton({ width: 220, height: 26 }), skeleton({ width: 140, height: 26 })),
    el(
      "div",
      { className: "table-wrap" },
      el(
        "table",
        { className: "table" },
        el("tbody", null, Array.from({ length: 6 }, () =>
          el(
            "tr",
            null,
            el("td", null, el("div", { className: "row row-3" }, skeleton({ width: 28, height: 28, radius: "50%" }), skeleton({ width: 160, height: 12 }))),
            el("td", null, skeleton({ width: 100, height: 22 })),
            el("td", null, skeleton({ width: 60, height: 18 })),
            el("td", null, skeleton({ width: 70, height: 10 })),
            el("td", null, skeleton({ width: 70, height: 10 })),
            el("td", null, skeleton({ width: 24, height: 24 }))
          )
        ))
      )
    )
  );
}

/**
 * Create or edit a user.
 * @param {object|null} existing
 * @param {{me: object, reload: Function}} ctx
 */
function openUserForm(existing, ctx) {
  const editing = Boolean(existing);
  const isSelf = editing && existing.id === ctx.me?.id;
  const masterLocked = editing && existing.role === "master" && ctx.me?.role !== "master";
  const roleOptions = ROLES.filter((r) => r !== "master" || ctx.me?.role === "master" || (editing && r === existing.role));

  // Guardrail 2 (SPEC §4/users.py): only a master may touch ANY field of
  // another master's account, not just role and activation.
  const name = field("Name", inputEl({ value: existing?.name || "", maxLength: 120, "data-autofocus": "", disabled: masterLocked }), { required: true });
  const email = field(
    "Email",
    inputEl({ type: "email", value: existing?.email || "", maxLength: 254, disabled: masterLocked }),
    { required: true }
  );
  const role = field(
    "Role",
    selectEl(roleOptions.map((r) => [r, humanize(r)]), existing?.role || "viewer", { disabled: isSelf || masterLocked }),
    { hint: isSelf ? "You cannot change your own role." : masterLocked ? "Only a master can change a master's role." : "" }
  );
  const active = el("input", {
    className: "switch",
    type: "checkbox",
    role: "switch",
    checked: existing ? Boolean(existing.is_active) : true,
    disabled: isSelf || masterLocked,
  });
  const password = field(
    editing ? "New password" : "Password",
    inputEl({ type: "password", minLength: 6, autocomplete: "new-password", disabled: masterLocked }),
    { required: !editing, hint: editing ? "Leave blank to keep the current password." : "At least 6 characters." }
  );

  const submit = el(
    "button",
    { className: "btn btn-primary", type: "submit", disabled: masterLocked },
    editing ? "Save user" : "Create user"
  );

  const form = el(
    "form",
    {
      className: "stack stack-4",
      noValidate: true,
      onSubmit: async (event) => {
        event.preventDefault();
        for (const f of [name, email, password]) f.setError("");

        const nameValue = name.control.value.trim();
        const emailValue = email.control.value.trim();
        const passwordValue = password.control.value;

        let ok = true;
        if (!nameValue) { name.setError("A name is required."); ok = false; }
        if (!emailValue || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailValue)) { email.setError("Enter a valid email address."); ok = false; }
        if (!editing && passwordValue.length < 6) { password.setError("At least 6 characters."); ok = false; }
        if (editing && passwordValue && passwordValue.length < 6) { password.setError("At least 6 characters."); ok = false; }
        if (!ok) return;

        submit.classList.add("is-loading");
        submit.disabled = true;
        try {
          if (editing) {
            const payload = {};
            if (nameValue !== existing.name) payload.name = nameValue;
            if (emailValue !== existing.email) payload.email = emailValue;
            if (!isSelf && !masterLocked && role.control.value !== existing.role) payload.role = role.control.value;
            if (!isSelf && !masterLocked && active.checked !== Boolean(existing.is_active)) payload.is_active = active.checked;
            if (passwordValue) payload.password = passwordValue;
            await api.patch(`/users/${existing.id}`, payload);
            toast(`${nameValue} saved.`, { tone: "success" });
          } else {
            await api.post("/users", {
              name: nameValue,
              email: emailValue,
              password: passwordValue,
              role: role.control.value,
              is_active: active.checked,
            });
            toast(`${nameValue} created.`, { tone: "success" });
          }
          handle.close();
          await ctx.reload();
        } catch (error) {
          if (error?.code === "email_taken") email.setError(error.message);
          submit.classList.remove("is-loading");
          submit.disabled = false;
        }
      },
    },
    masterLocked
      ? el(
          "div",
          { className: "note-strip" },
          icon("lock"),
          el("span", null, "Only a master can view or change another master's account. Nothing here is editable.")
        )
      : null,
    name.node,
    email.node,
    el("div", { className: "grid grid-2" }, role.node, el(
      "label",
      { className: "field" },
      el("span", { className: "label" }, "Active"),
      el("label", { className: "switch-row" }, el("span", { className: "t-sm" }, "Can sign in"), active)
    )),
    password.node
  );

  const handle = modal({
    title: editing ? `Edit ${existing.name}` : "New user",
    size: "lg",
    body: form,
    footer: [el("button", { className: "btn btn-secondary", onClick: () => handle.close() }, "Cancel"), submit],
  });

  submit.addEventListener("click", (event) => {
    event.preventDefault();
    form.requestSubmit();
  });
}

/* ==========================================================================
   FEATURE FLAGS
   ========================================================================== */

async function renderFlags(token) {
  paint(token, shell("Feature flags", "Loading the switchboard…", null, flagsSkeleton()));

  let data;
  try {
    data = await api.get("/flags");
  } catch (error) {
    paint(token, shell("Feature flags", "The commercial switchboard.", null, errorState(error, () => renderFlags(++generation))));
    return;
  }
  if (stale(token)) return;

  const reload = () => renderFlags(++generation);

  const groups = el(
    "div",
    { className: "flag-groups" },
    (data.categories || []).map((cat) => flagGroup(cat, reload))
  );

  paint(
    token,
    shell(
      "Feature flags",
      "Every capability Nagare can offer, grouped by area, with the plan that unlocks it and whether it is switched on for this workspace.",
      el(
        "span",
        { className: "badge", dataset: { tone: "accent" } },
        `${data.effective_count} of ${data.total} effective`
      ),
      (data.categories || []).length ? groups : emptyState({ icon: "flag", title: "No feature flags configured" })
    )
  );
}

function flagGroup(category, reload) {
  return el(
    "section",
    { style: { marginBottom: "var(--space-2)" } },
    el(
      "div",
      { className: "flag-group-head" },
      el("span", { className: "flag-group-name" }, category.label),
      el("span", { className: "flag-group-rule" }),
      el("span", { className: "t-xs subtle" }, `${category.effective_count} of ${category.flags.length} effective`)
    ),
    el("div", { className: "panel" }, category.flags.map((flag) => flagRow(flag, reload)))
  );
}

function flagRow(flag, reload) {
  const state = flag.effective ? "on" : flag.is_enabled ? "locked" : "off";
  const toggleDisabled = !flag.unlocked_by_plan;

  const input = el("input", {
    className: "switch",
    type: "checkbox",
    role: "switch",
    checked: flag.is_enabled,
    disabled: toggleDisabled,
    "aria-label": `${flag.name} switch`,
    ...(toggleDisabled ? { "data-tooltip": `Requires the ${PLAN_LABEL[flag.min_plan] || flag.min_plan} plan.` } : {}),
    onChange: async (event) => {
      const next = event.target.checked;
      event.target.disabled = true;
      try {
        const updated = await api.patch(`/flags/${flag.key}`, { is_enabled: next });
        toast(`${flag.name} ${next ? "enabled" : "disabled"}.`, { tone: "success" });
        const current = store.get().flags || {};
        store.set({ flags: { ...current, [flag.key]: Boolean(updated.effective) } });
        await reload();
      } catch {
        event.target.checked = !next;
        event.target.disabled = toggleDisabled;
      }
    },
  });

  return el(
    "div",
    { className: "flag-row" },
    el(
      "div",
      { className: "flag-text" },
      el("div", { className: "flag-name" }, flag.name, el("span", { className: "flag-key" }, flag.key)),
      flag.description ? el("div", { className: "flag-desc" }, flag.description) : null,
      toggleDisabled
        ? el("div", { className: "flag-locked-note" }, icon("lock"), `Needs the ${PLAN_LABEL[flag.min_plan] || flag.min_plan} plan or higher — switch it on the Plans tab.`)
        : null
    ),
    el("span", { className: "plan-chip", dataset: { plan: flag.min_plan } }, PLAN_LABEL[flag.min_plan] || flag.min_plan),
    el(
      "div",
      { className: "flag-controls" },
      el(
        "span",
        { className: "flag-state", dataset: { state } },
        state === "on" ? "Effective" : state === "locked" ? "Plan-locked" : "Off"
      ),
      input
    )
  );
}

function flagsSkeleton() {
  return el(
    "div",
    { className: "flag-groups" },
    Array.from({ length: 3 }, () =>
      el(
        "section",
        null,
        skeleton({ width: 140, height: 12 }),
        el(
          "div",
          { className: "panel", style: { marginTop: "var(--space-2)" } },
          Array.from({ length: 3 }, () =>
            el(
              "div",
              { className: "flag-row" },
              el("div", { className: "stack stack-1" }, skeleton({ width: 160, height: 12 }), skeleton({ width: 260, height: 10 })),
              skeleton({ width: 60, height: 18 }),
              skeleton({ width: 80, height: 18 })
            )
          )
        )
      )
    )
  );
}

/* ==========================================================================
   PLANS
   ========================================================================== */

async function renderPlans(token) {
  paint(token, shell("Plans", "Loading the plan catalogue…", null, plansSkeleton()));

  let workspace;
  let flagCategories;
  try {
    [workspace, flagCategories] = await Promise.all([
      api.get("/workspace"),
      api.get("/flags", { silent: true }).then((d) => d.categories || []).catch(() => []),
    ]);
  } catch (error) {
    paint(token, shell("Plans", "Pricing and the flag set each tier unlocks.", null, errorState(error, () => renderPlans(++generation))));
    return;
  }
  if (stale(token)) return;

  const allFlags = flagCategories.flatMap((c) => c.flags);
  const plans = (workspace.plans || []).slice().sort((a, b) => a.tier_order - b.tier_order);
  const reload = () => renderPlans(++generation);
  const effectiveCount = Object.values(workspace.flags || {}).filter(Boolean).length;

  const grid = plans.length
    ? el("div", { className: "plan-grid" }, plans.map((plan) => planCard(plan, workspace, allFlags, flagCategories, reload)))
    : emptyState({ icon: "sparkle", title: "No plans configured" });

  paint(
    token,
    shell(
      "Plans",
      "The four commercial tiers. Switching the active plan re-gates every feature flag for the whole workspace immediately.",
      el(
        "span",
        { className: "badge", dataset: { tone: "accent" } },
        `${effectiveCount} of ${allFlags.length || "…"} flags effective now`
      ),
      grid
    )
  );
}

function planCard(plan, workspace, allFlags, flagCategories, reload) {
  const isActive = plan.id === workspace.active_plan_id;
  const included = new Set(plan.flags);

  return el(
    "article",
    { className: "plan-card", dataset: { active: String(isActive) } },
    isActive ? el("span", { className: "plan-active-flag" }, "Active plan") : null,
    el(
      "div",
      { className: "stack stack-1" },
      el(
        "div",
        { className: "row row-2" },
        el("span", { className: "plan-tier" }, `Tier ${plan.tier_order}`),
        !plan.is_active ? el("span", { className: "badge", dataset: { tone: "warning" } }, "Retired") : null
      ),
      el("h3", { className: "plan-name" }, plan.name)
    ),
    el(
      "div",
      { className: "plan-price" },
      el("span", { className: "plan-price-value" }, plan.price_month > 0 ? `$${formatMoney(plan.price_month)}` : "Free"),
      plan.price_month > 0 ? el("span", { className: "plan-price-unit" }, `${plan.currency} / month`) : null
    ),
    plan.price_month > 0 && plan.price_year > 0
      ? el("div", { className: "plan-price-year" }, `or $${formatMoney(plan.price_year)}/year`)
      : null,
    plan.description ? el("p", { className: "plan-desc" }, plan.description) : null,
    el(
      "div",
      { className: "plan-flags" },
      el("div", { className: "plan-flag-count" }, `${plan.flags.length} of ${allFlags.length} flags included`),
      el(
        "div",
        { className: "plan-flag-list" },
        allFlags.map((f) =>
          el(
            "div",
            { className: "plan-flag-item", dataset: { on: String(included.has(f.key)) } },
            icon(included.has(f.key) ? "check" : "x"),
            // Wrapped so the excluded state can strike the name through — a
            // colour-independent cue — without also striking the icon.
            el("span", { className: "plan-flag-name" }, f.name)
          )
        )
      )
    ),
    el(
      "div",
      { className: "plan-actions" },
      el(
        "button",
        { className: "btn btn-secondary btn-sm", onClick: () => openPlanFlagsEditor(plan, flagCategories, reload) },
        icon("edit"),
        "Edit flag set"
      ),
      isActive
        ? el("button", { className: "btn btn-secondary btn-sm", disabled: true }, icon("check"), "Current plan")
        : el(
            "button",
            {
              className: "btn btn-primary btn-sm",
              disabled: !plan.is_active,
              ...(!plan.is_active ? { "data-tooltip": "This plan is retired and cannot be activated." } : {}),
              onClick: () => setActivePlan(plan, reload),
            },
            icon("sparkle"),
            "Set active"
          )
    )
  );
}

async function setActivePlan(plan, reload) {
  const confirmed = await confirmDialog({
    title: "Switch the active plan",
    message: `Switch the workspace to ${plan.name}? Every feature flag re-evaluates immediately for every signed-in user.`,
    confirmLabel: "Switch plan",
  });
  if (!confirmed) return;

  try {
    const workspace = await api.patch("/workspace", { active_plan_id: plan.id });
    store.set({ flags: workspace.flags || {} });
    toast(`Workspace switched to ${plan.name}.`, { tone: "success", title: "Plan changed" });
    await reload();
  } catch {
    /* the toast from api.js already explained why */
  }
}

function openPlanFlagsEditor(plan, flagCategories, reload) {
  const selected = new Set(plan.flags);

  const body = el(
    "div",
    { className: "flag-picker" },
    flagCategories.length
      ? flagCategories.map((cat) =>
          el(
            "div",
            { className: "flag-picker-group" },
            el(
              "div",
              { className: "flag-group-head" },
              el("span", { className: "flag-group-name" }, cat.label),
              el("span", { className: "flag-group-rule" })
            ),
            cat.flags.map((f) =>
              el(
                "label",
                { className: "flag-picker-row" },
                el("input", {
                  type: "checkbox",
                  className: "checkbox",
                  checked: selected.has(f.key),
                  onChange: (event) => {
                    if (event.target.checked) selected.add(f.key);
                    else selected.delete(f.key);
                  },
                }),
                el("span", null, f.name),
                el("span", { className: "flag-desc" }, f.description || "")
              )
            )
          )
        )
      : el("p", { className: "muted t-sm" }, "No flag catalogue is available right now.")
  );

  const submit = el("button", { className: "btn btn-primary" }, "Save flag set");

  const handle = modal({
    title: `Edit flags · ${plan.name}`,
    size: "lg",
    body,
    footer: [el("button", { className: "btn btn-secondary", onClick: () => handle.close() }, "Cancel"), submit],
  });

  submit.addEventListener("click", async () => {
    submit.classList.add("is-loading");
    submit.disabled = true;
    try {
      await api.patch(`/plans/${plan.id}`, { flags: Array.from(selected) });
      handle.close();
      toast(`${plan.name}'s flag set was updated.`, { tone: "success" });
      await reload();
    } catch {
      submit.classList.remove("is-loading");
      submit.disabled = false;
    }
  });
}

function plansSkeleton() {
  return el(
    "div",
    { className: "plan-grid" },
    Array.from({ length: 4 }, () =>
      el(
        "div",
        { className: "plan-card" },
        skeleton({ width: 70, height: 12 }),
        skeleton({ width: 120, height: 22 }),
        skeleton({ width: 90, height: 30 }),
        skeleton({ width: "100%", height: 12 }),
        skeleton({ height: 120, radius: "var(--radius-md)" }),
        skeleton({ height: 30 })
      )
    )
  );
}
