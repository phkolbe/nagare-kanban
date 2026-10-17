> **Historical document — the v1 build contract** (tag `v1`, August 2026). The code has
> moved on since: PostgreSQL through async SQLAlchemy and Alembic (SQLite only for tests
> and the demo), one React app in `web/` instead of the vanilla frontend, data isolated
> per product (`product_id`, `X-Produto`), the Pampufa's `nagare.*` roles next to the
> local login, and the platform integration of phases N1–N3. What still holds: §6
> (movement rules), §7 (metric formulas), §8 (simulator) and §11 (seed data). The current
> API is in [API.md](API.md); the decisions since v1 are in the Obsidian vault,
> `grupo-sr/plataforma/decisoes/integracao-nagare.md`. The text below is kept as written.

# Nagare Kanban — Build Contract (SPEC)

> 流れ *nagare* = "flow". This document is the **single source of truth** for every agent
> working on this codebase. Do not invent alternative names, routes, or schemas.
> If something is missing, follow the closest existing convention — never redesign.

---

## 0. Stack & hard constraints

| Concern | Decision |
|---|---|
| Language | **Python 3.14** (backend), vanilla **ES modules** (frontend) |
| Web framework | **FastAPI** + **uvicorn** (only 3rd-party runtime deps allowed) |
| Database | **sqlite3 (stdlib)** via a thin helper in `backend/app/db.py`. **No SQLAlchemy.** |
| Auth | Hand-rolled HS256 JWT + PBKDF2-SHA256 using **stdlib only** (`hmac`, `hashlib`, `base64`) |
| Frontend build | **None.** No npm, no bundler, no CDN. Native ES modules + plain CSS. |
| Charts | **Hand-written inline SVG**. No chart library, no external requests. |
| Tests | `pytest` + FastAPI `TestClient` |
| Screenshots | `playwright` (Python, chromium) |
| Node.js | **Not available on this machine.** Never emit a solution requiring it. |

Everything must run from a cold clone with:

```bash
python -m pip install -r requirements.txt && python run.py
```

`run.py` seeds the DB on first run and serves the API **and** the static frontend
from a single origin at `http://127.0.0.1:8770`.

---

## 1. Repository layout & file ownership

Each file has exactly **one** owning agent. Never write outside your ownership list.

```
meu-kanban/
├─ run.py                          # CORE
├─ requirements.txt                # CORE
├─ README.md                       # DOCS
├─ docs/SPEC.md                    # (this file — read-only)
├─ docs/API.md                     # DOCS
├─ docs/KANBAN.md                  # DOCS  (method rationale: Anderson / Yoshima)
├─ backend/
│  ├─ __init__.py                  # CORE
│  └─ app/
│     ├─ __init__.py               # CORE
│     ├─ config.py                 # CORE   settings, feature-flag defaults, plans
│     ├─ db.py                     # CORE   connection, migrate(), query helpers
│     ├─ schema.sql                # CORE   full DDL
│     ├─ models.py                 # CORE   dataclasses + row<->dict mappers
│     ├─ schemas.py                # CORE   pydantic request/response models
│     ├─ security.py               # CORE   pbkdf2, jwt encode/decode
│     ├─ deps.py                   # CORE   auth deps, RBAC guard, flag guard
│     ├─ errors.py                 # CORE   ApiError + handlers
│     ├─ seed.py                   # CORE   demo workspace generator
│     ├─ main.py                   # CORE   app factory, router includes, static mount
│     ├─ flow/
│     │  ├─ __init__.py            # METRICS
│     │  ├─ intervals.py           # METRICS  movement -> per-column intervals
│     │  ├─ metrics.py             # METRICS  all flow metric computations
│     │  ├─ kmm.py                 # METRICS  KMM level readiness mapping
│     │  └─ forecast.py            # SIM      Monte Carlo + scenario engine
│     └─ routers/
│        ├─ __init__.py            # CORE
│        ├─ auth.py                # ADMIN
│        ├─ users.py               # ADMIN
│        ├─ flags.py               # ADMIN   feature flags + plans
│        ├─ boards.py              # BOARDS  boards, columns, groups, WIP policies
│        ├─ projects.py            # BOARDS  projects + demands (envelope)
│        ├─ cards.py               # CARDS   cards, tasks, blockers, comments
│        ├─ movements.py           # CARDS   move + history + audit
│        ├─ metrics.py             # METRICS
│        └─ simulator.py           # SIM     simulator boards + runs
├─ mcp/
│  └─ nagare_mcp.py                # DOCS    stdio JSON-RPC MCP server
├─ frontend/
│  ├─ index.html                   # SHELL
│  ├─ css/
│  │  ├─ tokens.css                # SHELL   design tokens (light + dark)
│  │  ├─ base.css                  # SHELL   reset, typography, layout primitives
│  │  ├─ components.css            # SHELL   buttons, chips, inputs, drawer, modal, toast
│  │  ├─ board.css                 # BOARD
│  │  ├─ metrics.css               # CHARTS
│  │  └─ views.css                 # VIEWS   projects, simulator, admin
│  └─ js/
│     ├─ api.js                    # SHELL   fetch wrapper, auth token, error toasts
│     ├─ store.js                  # SHELL   app state, pub/sub, filters
│     ├─ router.js                 # SHELL   hash router
│     ├─ ui.js                     # SHELL   el(), modal, drawer, toast, confirm, icons
│     ├─ app.js                    # SHELL   bootstrap, nav, theme toggle
│     ├─ board.js                  # BOARD   board render + WIP + commitment point
│     ├─ dnd.js                    # BOARD   forward-only drag & drop
│     ├─ card.js                   # BOARD   card detail drawer
│     ├─ filters.js                # BOARD   filter bar
│     ├─ charts.js                 # CHARTS  SVG primitives (axes, scales, legend)
│     ├─ metrics.js                # CHARTS  metrics dashboard view
│     ├─ projects.js               # VIEWS   projects + envelope view
│     ├─ simulator.js              # VIEWS   simulator view
│     └─ admin.js                  # VIEWS   users, roles, flags, plans
└─ tests/
   ├─ conftest.py                  # CORE
   ├─ test_auth_rbac.py            # ADMIN
   ├─ test_boards.py               # BOARDS
   ├─ test_cards_movement.py       # CARDS
   ├─ test_metrics.py              # METRICS
   ├─ test_simulator.py            # SIM
   └─ test_smoke_api.py            # CORE
```

---

## 2. Domain model (authoritative DDL semantics)

All ids are `TEXT` UUID4 strings. All timestamps are **ISO-8601 UTC strings**
(`2026-08-16T13:45:00Z`). Booleans stored as `INTEGER 0/1`.

### 2.1 `users`
`id, email UNIQUE, name, password_hash, role, is_active, created_at, last_login_at`

`role ∈ {master, administrator, editor, viewer, simulator, system}` — see §4.

### 2.2 `boards`
`id, name, slug UNIQUE, description, kind, source_board_id, count_envelope_in_metrics,
 confirm_moves, wip_min, wip_max, sle_days, sle_percentile, created_at, archived`

- `kind ∈ {operational, simulator}`.
- `source_board_id` — for simulator boards cloned from an operational board (nullable).
- `wip_min/wip_max` — **whole-flow** limits (Req. 3).
- `sle_days`/`sle_percentile` — Service Level Expectation (e.g. 85% in 14 days).
- `count_envelope_in_metrics` — Req. 8 toggle.
- `confirm_moves` — Req. 9 toggle.

### 2.3 `column_groups`
`id, board_id, name, position, wip_min, wip_max, color`
Group-level WIP limits (Req. 3), e.g. "Discovery" spanning 3 columns.

### 2.4 `columns`
`id, board_id, group_id, name, position, segment, kind, wip_min, wip_max,
 is_commitment_point, is_delivery_point, color, description`

- `segment ∈ {upstream, downstream}` (Req. 1)
- `kind ∈ {envelope, queue, activity, done}` (Req. 2 — `queue` accrues **wait time**,
  `activity` accrues **touch time**, `envelope` is the sealed pre-discovery stage,
  `done` is terminal)
- Exactly **one** column per board has `is_commitment_point = 1`: the first downstream
  column. Exactly one has `is_delivery_point = 1`: the terminal `done` column.

### 2.5 `projects`
`id, name, key UNIQUE, color, description, status, owner_id, started_at, target_date,
 created_at, archived`  — `status ∈ {discovery, active, on_hold, done, cancelled}`

### 2.6 `cards`
`id, board_id, column_id, project_id, key, title, description, service_class,
 assignee_id, requester, size, value_area, demand_type, due_date, position,
 envelope_state, is_blocked, tags, custom_fields,
 created_at, opened_at, committed_at, delivered_at, archived, created_by`

- `service_class ∈ {expedite, standard, fixed_date, intangible}` (Req. 6)
- `demand_type ∈ {value, failure, maintenance}` (failure demand metric)
- `value_area ∈ {feature, defect, risk, debt, compliance}`
- `envelope_state ∈ {sealed, opened}` (Req. 8) — `opened_at` set when unsealed
- `size ∈ {xs, s, m, l, xl}` (optional)
- `tags`, `custom_fields` — JSON text
- `key` — human ref like `NGR-104`, derived from project key

### 2.7 `card_movements` (Req. 7 — the metrics backbone)
`id, card_id, board_id, from_column_id, to_column_id, from_segment, to_segment,
 moved_at, moved_by, seconds_in_from_column, note, is_backward, source`
`source ∈ {ui, api, mcp, seed, simulation}`. Card creation is recorded as a movement
with `from_column_id = NULL`.

### 2.8 `card_events` (audit trail)
`id, card_id, board_id, actor_id, event_type, field, old_value, new_value, created_at`
`event_type ∈ {created, moved, updated, blocked, unblocked, opened_envelope,
 archived, commented, task_added, task_completed}`

### 2.9 `card_tasks` (Req. 5)
`id, card_id, title, is_done, assignee_id, position, created_at, completed_at`

### 2.10 `blockers`
`id, card_id, reason, category, started_at, ended_at, created_by`

### 2.11 `comments`
`id, card_id, author_id, body, created_at`

### 2.12 `feature_flags` (Req. 10)
`key PRIMARY KEY, name, description, is_enabled, min_plan, category`

### 2.13 `plans` (Req. 10)
`id, name, slug UNIQUE, price_month, price_year, currency, tier_order, description,
 flags` (JSON array of flag keys), `is_active`

### 2.14 `workspace` (singleton row, id='workspace')
`id, name, active_plan_id, timezone, updated_at`

### 2.15 `simulation_runs` (Req. 13)
`id, board_id, name, kind, params, results, created_at, created_by`
`kind ∈ {monte_carlo_when, monte_carlo_how_many, wip_experiment, class_of_service_mix,
 replay}`

---

## 3. Canonical feature flags (Req. 10)

| key | name | min_plan |
|---|---|---|
| `upstream_board` | Upstream board | free |
| `downstream_board` | Downstream board | free |
| `unified_flow` | Unified end-to-end view | team |
| `wip_column` | Per-column WIP limits | free |
| `wip_group` | Per-group WIP limits | team |
| `wip_flow` | Whole-flow WIP limits | team |
| `envelope` | Sealed-envelope demands | team |
| `service_classes` | Classes of service | free |
| `custom_fields` | Custom card fields | team |
| `card_history` | Movement history & audit | free |
| `metrics_basic` | Lead time, throughput, WIP | free |
| `metrics_advanced` | CFD, flow efficiency, aging, SLE | business |
| `monte_carlo` | Probabilistic forecasting | business |
| `kmm_dashboard` | Kanban Maturity Model dashboard | business |
| `simulator` | Simulator boards | business |
| `api_access` | REST API tokens | team |
| `mcp_server` | MCP integration | business |
| `rbac_advanced` | Full role matrix | business |
| `multi_project` | Multiple projects | free |

Plans (tier_order): `free`(0), `team`(1), `business`(2), `enterprise`(3).
A flag is **effective** when `is_enabled = 1` **and** active plan tier ≥ `min_plan` tier.
`GET /api/v1/flags/effective` returns `{key: bool}` for the frontend to gate UI.

---

## 4. Roles & permissions (Req. 11)

| Capability | master | administrator | editor | viewer | simulator | system |
|---|---|---|---|---|---|---|
| Manage plans & flags | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Manage users/roles | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Create/edit boards & columns | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Create/edit/move cards | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Read boards & metrics | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Simulator boards (CRUD + run) | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| Integration/API token use | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |

`simulator` may fully manipulate boards with `kind='simulator'` only.
`system` is the non-human integration identity (API/MCP); it cannot log into the UI.
Guard with `require_roles(...)` and `require_flag(...)` from `deps.py`.

---

## 5. REST API — `/api/v1`

Auth: `Authorization: Bearer <jwt>`. Errors use
`{"error": {"code": "wip_limit_exceeded", "message": "...", "details": {...}}}` with
appropriate HTTP status. All list endpoints return `{"items": [...], "total": n}`.

### Auth & identity
```
POST   /auth/login                {email, password} -> {access_token, user}
POST   /auth/refresh
GET    /auth/me
```

### Users (ADMIN)
```
GET    /users                    ?role=&q=
POST   /users
PATCH  /users/{id}
DELETE /users/{id}               (soft: is_active=0)
GET    /users/roles              -> role catalogue + capability matrix
```

### Flags & plans (ADMIN)
```
GET    /flags                    -> raw flags
GET    /flags/effective          -> {key: bool}
PATCH  /flags/{key}              {is_enabled, min_plan}
GET    /plans
POST   /plans
PATCH  /plans/{id}
GET    /workspace
PATCH  /workspace                {name, active_plan_id}
```

### Boards, columns, groups (BOARDS)
```
GET    /boards                   ?kind=
POST   /boards                   {name, description, kind, template?}
GET    /boards/{id}              -> board + groups + columns + policies
PATCH  /boards/{id}
DELETE /boards/{id}
POST   /boards/{id}/clone        {name, kind} -> deep clone (used by simulator)
GET    /boards/{id}/view         ?segment=upstream|downstream|unified
                                 -> board + columns(filtered) + cards + wip status
POST   /boards/{id}/columns      {name, segment, kind, position, wip_min, wip_max, group_id}
PATCH  /columns/{id}
DELETE /columns/{id}
POST   /boards/{id}/columns/reorder  {ordered_ids: []}
POST   /boards/{id}/groups
PATCH  /groups/{id}
DELETE /groups/{id}
GET    /boards/{id}/wip          -> {columns:{id:{count,min,max,state}},
                                     groups:{...}, flow:{...}}
```
`state ∈ {under, ok, at_limit, over}`.

### Projects & demands (BOARDS)
```
GET    /projects                 ?status=&q=
POST   /projects
GET    /projects/{id}            -> project + demand counts by envelope_state
PATCH  /projects/{id}
DELETE /projects/{id}
POST   /projects/{id}/demands    {title, ...} -> card in envelope column, sealed
POST   /cards/{id}/open-envelope -> envelope_state=opened, opened_at=now,
                                    moves card to first upstream non-envelope column
```

### Cards (CARDS)
```
GET    /cards                    ?board_id=&project_id=&service_class=&assignee_id=
                                 &segment=&column_id=&tag=&q=&is_blocked=&demand_type=
                                 &value_area=&size=&due_before=&archived=
POST   /cards
GET    /cards/{id}               -> card + tasks + blockers + comments + movements
PATCH  /cards/{id}
DELETE /cards/{id}
POST   /cards/{id}/move          {to_column_id, note?, force?} -> see §6
GET    /cards/{id}/history       -> movements + events, chronological
POST   /cards/{id}/tasks         / PATCH /tasks/{id} / DELETE /tasks/{id}
POST   /cards/{id}/block         {reason, category}
POST   /cards/{id}/unblock
POST   /cards/{id}/comments
```

### Metrics (METRICS) — all accept `?from=&to=&project_id=&service_class=&segment=`
```
GET /boards/{id}/metrics/summary        -> headline KPI set (§7.1)
GET /boards/{id}/metrics/cfd            -> cumulative flow diagram series
GET /boards/{id}/metrics/scatter        -> lead-time scatter + percentiles
GET /boards/{id}/metrics/throughput     -> per-period counts + rolling avg
GET /boards/{id}/metrics/aging          -> in-flight cards vs percentile bands
GET /boards/{id}/metrics/flow-efficiency-> touch/wait split, per card & aggregate
GET /boards/{id}/metrics/distribution   -> lead-time histogram + percentiles
GET /boards/{id}/metrics/blockers       -> blocked time, count, pareto by category
GET /boards/{id}/metrics/demand         -> class-of-service mix, failure demand %
GET /boards/{id}/metrics/littles-law    -> WIP / throughput / lead time coherence
GET /boards/{id}/metrics/kmm            -> per-KMM-level readiness (§7.2)
```

### Simulator (SIM)
```
GET  /simulator/boards
POST /simulator/boards                  {name, source_board_id?, template?}
POST /simulator/boards/{id}/reset
POST /simulator/boards/{id}/generate    {weeks, arrival_rate, class_mix, seed}
POST /simulator/runs                    {board_id, kind, params}
GET  /simulator/runs?board_id=
GET  /simulator/runs/{id}
POST /simulator/compare                 {run_ids: []} -> side-by-side deltas
```

### Meta
```
GET /api/v1/health
GET /openapi.json  /docs           (FastAPI built-ins, keep enabled)
```

---

## 6. Movement rules (Req. 9) — CARDS owner implements, everyone respects

1. Cards move **forward only**: target column `position` must be **>** current position
   (within the unified ordering upstream→downstream).
2. A backward move is rejected with `409 {"error":{"code":"backward_move_blocked"}}`
   unless the caller is `master`/`administrator` **and** passes `force: true`
   — that records `is_backward = 1` and a `card_events` audit row.
3. Moving **into** the commitment-point column sets `committed_at` (once, immutably).
4. Moving into the delivery-point column sets `delivered_at`.
5. A sealed envelope card cannot be moved until `open-envelope` is called.
6. WIP `max` breach → `409 {"error":{"code":"wip_limit_exceeded"}}` naming which policy
   (column / group / flow) broke, with current and max. `expedite` service class
   bypasses column WIP but is reported in the response as `wip_override: true`.
7. WIP `min` is advisory: never blocks, surfaced as a `starving` warning.
8. Every accepted move writes a `card_movements` row **including
   `seconds_in_from_column`** computed from the previous movement's `moved_at`.
9. Confirmation is a **frontend** concern driven by `board.confirm_moves`; the API
   never asks.

---

## 7. Flow metric definitions (METRICS owner — be exact)

Let `E` = envelope interval, `U` = upstream intervals, `D` = downstream intervals.
`board.count_envelope_in_metrics` decides whether `E` is included in lead time.

### 7.1 Summary KPIs
- **Customer Lead Time** = `delivered_at − (created_at | opened_at)` (envelope toggle)
- **Discovery Lead Time** = `committed_at − created_at`
- **System Lead Time / Cycle Time** = `delivered_at − committed_at`
- **Touch time** = Σ seconds in `kind='activity'` columns
- **Wait time** = Σ seconds in `kind='queue'` columns
- **Flow Efficiency** = `touch / (touch + wait) × 100`
- **Throughput** = delivered cards per week (default period)
- **WIP** = cards with `committed_at != NULL AND delivered_at == NULL`
- **Aging** = `now − committed_at` for in-flight cards
- **Due Date Performance** = % of `fixed_date` cards delivered `≤ due_date`
- **Failure demand %** = `demand_type='failure'` / total delivered
- **SLE attainment** = % delivered within `board.sle_days`
- **Percentiles**: report 50 / 70 / 85 / 95 everywhere a distribution exists
- **Little's Law check**: `avg_wip ≈ throughput × avg_lead_time` — return all three plus
  a `coherence_ratio`

Return, for every KPI: `{value, unit, trend_pct, sample_size, previous_value}`.
`sample_size < 8` ⇒ include `"low_confidence": true` (the UI must show it).

### 7.2 KMM readiness (`/metrics/kmm`)
Return an array of 7 levels (ML0…ML6). Each: `{level, name, focus, metrics: [{key,
label, available: bool, value, why}], readiness_pct}`. Mapping:

- **ML0 Oblivious** — no measurement.
- **ML1 Emerging** — WIP count, board in use, cards visualised.
- **ML2 Defined** — lead time avg, throughput, WIP aging, blocker count, work-item types.
- **ML3 Managed** — lead-time distribution + percentiles, SLE + attainment, CFD,
  due-date performance, class-of-service mix, flow efficiency.
- **ML4 Quantitatively Managed** — Monte Carlo forecasts, throughput variability,
  failure demand, flow debt, upstream (discovery) lead time, Little's Law coherence.
- **ML5 Optimizing** — end-to-end (envelope→delivery) lead time, bottleneck/queue
  analysis, capability comparison across boards, WIP-experiment outcomes.
- **ML6 Congruent** — portfolio roll-up across projects, business-outcome alignment.

`readiness_pct` = available metrics / total for that level.

---

## 8. Simulator (Req. 13 — the crown jewel, SIM owner)

A simulator board is a first-class board with `kind='simulator'`, isolated from
operational metrics. Capabilities:

1. **Clone** an operational board's columns/policies (optionally its cards).
2. **Synthetic demand generator** — deterministic given `seed`; arrival process
   (Poisson-ish), configurable class-of-service mix, sizes, and per-column service-time
   distributions. Uses `random.Random(seed)` — reproducible, no wall-clock dependence.
3. **Monte Carlo forecasting** — `monte_carlo_when` (how long for N items) and
   `monte_carlo_how_many` (items by date), sampling historical/simulated weekly
   throughput with replacement, ≥ 10 000 trials, returning the full distribution plus
   50/70/85/95 percentiles.
4. **WIP experiment** — replay the same demand under different WIP limits and report
   the lead-time / throughput / flow-efficiency deltas. This is the headline demo:
   it must visibly show that *lowering WIP shortens lead time* (Little's Law).
5. **Class-of-service mix experiment** — vary expedite share, show the impact on
   standard-class lead time.
6. **Compare runs** side by side with deltas and a verdict sentence.

Results are stored in `simulation_runs.results` as JSON and rendered as charts.

---

## 9. Frontend architecture

### 9.1 Shell
`index.html` declares: a left rail nav, a top bar (workspace, board switcher, segment
switcher, filters, theme toggle, user menu), and one `<main id="view">` container.
Views are mounted by `router.js` on hash routes:

```
#/board/:boardId?segment=unified|upstream|downstream
#/projects            #/projects/:id
#/metrics/:boardId
#/simulator           #/simulator/:boardId
#/admin/users         #/admin/flags         #/admin/plans
#/login
```

Each view module exports `mount(el, params)` and `unmount()`.
`store.js` exposes `store.get()`, `store.set(patch)`, `store.subscribe(fn)`,
plus `store.filters` (project, service class, assignee, tag, text, blocked).
`api.js` exports `api.get/post/patch/del(path, body)` and raises `ApiError` with the
server's `error.code`, which `ui.toast()` renders.

### 9.2 Board view (Req. 1, 2, 3, 9)
- Horizontal scroll of columns grouped under **group headers** with their own WIP badge.
- A **commitment point** rendered as a distinct vertical marker/rail between the last
  upstream and first downstream column, labelled "Commitment Point", with the upstream
  and downstream regions tinted differently and labelled in the segment ribbon.
- Column header: name, `kind` badge (⏳ wait / ⚡ touch), count, `min–max` WIP badge
  coloured by state (`under` amber-dim, `ok` neutral, `at_limit` amber, `over` red with
  a pulsing outline).
- Cards: compact by default (Req. 5) — title, project chip, service-class stripe on the
  left edge, assignee avatar, small aging dot, blocked flag, task progress `2/5`.
  Everything else lives in the **detail drawer**.
- Drag & drop: HTML5 DnD. Invalid (backward) drop targets are visually disabled during
  the drag. Forward targets show an insertion affordance. Over-WIP targets show a
  warning tint but stay droppable (the API decides).
- `board.confirm_moves` → a confirmation modal naming source and target column;
  the modal has a "don't ask again on this board" checkbox that PATCHes the board.
- Segment switcher: Upstream / Downstream / **Unified**.

### 9.3 Metrics view (CHARTS)
Dashboard of KPI tiles + charts, all hand-built SVG, all responsive, all
theme-aware, all with accessible `<title>`/`aria-label` and a hover tooltip:
CFD (stacked areas), lead-time scatter with percentile lines, throughput bars with
rolling average, aging WIP (columns on X, cards as dots, percentile bands),
flow-efficiency bar, lead-time histogram, blocker pareto, class-of-service donut,
KMM ladder (ML0→ML6 with readiness rings).

**Before writing any chart code, the CHARTS agent must invoke the `dataviz` skill**
and follow its palette/mark/legend rules.

### 9.4 Projects & envelope view (VIEWS, Req. 8)
Project cards with demand counts; a demand list where sealed items render as a literal
**closed envelope** treatment (wax-seal accent, muted, no assignee shown) and an
"Open envelope" action animates the unsealing and moves the demand into upstream.
A per-board toggle: *"Count sealed-envelope time in metrics"*.

### 9.5 Simulator view (VIEWS + CHARTS, Req. 13)
Scenario builder form (weeks, arrival rate, class mix, WIP limits, seed) → run → results
with charts and a plain-language verdict. A comparison table across saved runs.

### 9.6 Admin (VIEWS)
Users + role matrix, feature-flag switchboard grouped by category showing which plan
unlocks each, plan editor with the flag set per plan.

---

## 10. Design system (SHELL owner defines; everyone consumes)

Aesthetic target: **calm, precise, editorial** — a professional flow-management
instrument, not a toy. Reference bar: Linear, Vercel dashboards, Stripe docs.
Japanese-influenced restraint: generous whitespace, one accent, hairline rules,
no gradients-as-decoration, no drop-shadow soup.

Required tokens in `tokens.css` (both `:root` light and `[data-theme="dark"]`, plus a
`prefers-color-scheme` fallback so system-dark works without a stored preference):

```
--bg, --bg-sunken, --bg-raised, --bg-overlay
--fg, --fg-muted, --fg-subtle, --fg-inverse
--border, --border-strong, --focus-ring
--accent, --accent-fg, --accent-soft
--success, --warning, --danger, --info  (+ each with -soft variant)
--sc-expedite, --sc-standard, --sc-fixed-date, --sc-intangible (+ -soft)
--upstream-tint, --downstream-tint, --commitment-line
--radius-sm/md/lg/xl, --shadow-sm/md/lg
--space-1..--space-10 (4px base scale)
--font-sans, --font-mono
--text-xs..--text-3xl with matching line heights
--dur-fast/base/slow, --ease-out, --ease-spring
```

Non-negotiables:
- **No external fonts/CDNs.** System font stack only.
- Every interactive element has a visible `:focus-visible` ring and a hover state.
- WCAG AA contrast in **both** themes.
- Full keyboard support: card focus with arrows, `Enter` opens the drawer, `m` opens
  move, `Esc` closes overlays; drawer and modal trap focus.
- `prefers-reduced-motion: reduce` disables transforms/animations.
- Responsive down to 768px (board becomes horizontally scrollable with sticky headers).
- Empty states, loading skeletons, and error states for **every** view. No blank screens.

---

## 11. Seed data (CORE) — must make screenshots look real

- Workspace "Nagare", plan `business`, all flags on.
- 6 users covering every role. Login: `master@nagare.app` / `nagare123` (all users share
  this password for the demo).
- Projects: `NGR` Nagare Platform, `PAY` Payments Revamp, `MOB` Mobile App,
  `INF` Infra Reliability.
- One operational board **"Nagare Flow"**:
  - upstream: `Envelope (envelope)`, `Ideas (queue)`, `Discovery (activity)`,
    `Options (queue)`, `Ready to Commit (queue)`
  - downstream: `Analysis (activity)`, `Analysis Done (queue)`, `Build (activity)`,
    `Build Done (queue)`, `Verify (activity)`, `Ready to Deploy (queue)`,
    `Delivered (done)`
  - `Analysis` is the commitment point; `Delivered` is the delivery point.
  - Groups: "Discovery" (Ideas→Ready to Commit), "Delivery" (Analysis→Ready to Deploy).
  - Column WIP maxima set so at least one column sits `at_limit` and one sits `over`.
- One simulator board **"Scenario Lab"**.
- **≥ 220 cards** with **12 months** of realistic, back-dated movement history so every
  chart is populated: varied service classes (≈8% expedite, 70% standard, 12% fixed
  date, 10% intangible), realistic wait/touch ratios (flow efficiency ~25–40%),
  a few blockers, some failure demand, ~35 cards currently in flight, ~25 sealed
  envelope demands. Seeded with a fixed `random.Random(20260816)` — **deterministic**.
- Seeding must be **idempotent**: `run.py` skips seeding if `users` is non-empty.

---

## 12. Definition of Done (every agent self-checks before returning)

1. `python -c "import backend.app.main"` succeeds — no syntax/import errors.
2. Your `pytest` file passes.
3. No `TODO`, no stub, no `pass  # implement later`, no lorem ipsum.
4. No file you don't own was modified.
5. Backend: every endpoint in your section exists, is RBAC-guarded, flag-guarded where
   applicable, and returns the documented shape.
6. Frontend: your view renders with real seeded data, in light **and** dark, at 1440px
   and 1024px, with empty/loading/error states, and zero console errors.
7. Comments explain *why*, not *what*. Docstrings on public functions.
