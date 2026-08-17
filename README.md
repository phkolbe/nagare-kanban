# Nagare Kanban

**流れ *nagare* — "flow".** A professional flow-management instrument for knowledge work:
an upstream/downstream Kanban board with an explicit commitment point, WIP limits that
are actually enforced, a full movement history, real flow metrics, probabilistic
forecasting, a scenario simulator, and an MCP server so an AI agent can work the board
with you.

It is not a to-do list with columns. Every constraint in it — forward-only movement,
server-enforced WIP maxima, sealed-envelope demand, immutable commitment timestamps —
exists because the Kanban Method says a system that lies about itself cannot be
improved. The reasoning is written down in **[docs/KANBAN.md](docs/KANBAN.md)**.

Built with a deliberately small dependency surface: **FastAPI + uvicorn + Pydantic** on
Python 3.14, **stdlib `sqlite3`** (no ORM), hand-rolled **PBKDF2 + HS256** auth, and a
frontend of **native ES modules, plain CSS and hand-written inline SVG charts** — no
npm, no bundler, no CDN, no external fonts.

---

## Screenshots

| | |
|---|---|
| ![Board — unified flow, light theme](docs/screenshots/board-light.png)<br>**The board.** Upstream and downstream either side of the commitment rail. | ![Board — dark theme](docs/screenshots/board-dark.png)<br>**Dark theme.** Same information, WCAG AA in both. |
| ![Metrics dashboard](docs/screenshots/metrics.png)<br>**Metrics.** CFD, scatter with percentile lines, aging WIP, flow efficiency. | ![KMM ladder](docs/screenshots/kmm.png)<br>**KMM readiness.** ML0→ML6 with the metrics each level needs. |
| ![Card detail drawer](docs/screenshots/card-drawer.png)<br>**Card drawer.** Tasks, blockers, comments and the movement trail. | ![Projects and sealed envelopes](docs/screenshots/projects.png)<br>**Sealed envelopes.** Demand captured without being started. |
| ![Simulator](docs/screenshots/simulator.png)<br>**Simulator.** Replay the same demand under different WIP limits. | ![Admin — flags and plans](docs/screenshots/admin-flags.png)<br>**Admin.** Feature-flag switchboard and the plan matrix. |

*Screenshots are captured with Playwright (chromium) against the seeded demo workspace
and stored in `docs/screenshots/`. Until they are generated, the links above are
placeholders — run the app and see the real thing at <http://127.0.0.1:8770>.*

---

## Quickstart

Requires **Python 3.14** and nothing else. Node.js is not used anywhere.

```bash
python -m pip install -r requirements.txt
python run.py
```

Then open **<http://127.0.0.1:8770>**.

The first run creates `data/nagare.db`, applies the schema and seeds a complete demo
workspace: 6 users, 4 projects, 2 boards, 260+ cards with twelve months of back-dated
movement history, blockers, sealed envelopes and cards in flight — enough that every
chart in the product is populated. Seeding is **deterministic** (`random.Random(20260816)`)
and **idempotent** (skipped when the `users` table is non-empty).

```bash
python run.py --reset          # delete the database and reseed from scratch
python run.py --port 9000      # serve somewhere else
python run.py --seed-only      # migrate + seed, print a summary, exit
python run.py --reload         # uvicorn auto-reload for development
```

> [!WARNING]
> **This ships configured as a demo, not as a deployment.** Every seeded account shares
> a published password, and `NAGARE_JWT_SECRET` falls back to a well-known development
> value that anyone reading this repository can see — meaning anyone could mint a valid
> token for any role. Before running this anywhere reachable by other people:
>
> ```bash
> export NAGARE_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
> ```
>
> then change every seeded password (or seed your own users), and serve it behind TLS.

### Sign in

All demo accounts share the password **`nagare123`**.

| Email | Role | What it can do |
|---|---|---|
| `master@nagare.app` | master | Everything, including plans and feature flags |
| `admin@nagare.app` | administrator | Users, boards, columns, policies, cards |
| `editor@nagare.app` | editor | Create, edit and move cards |
| `viewer@nagare.app` | viewer | Read boards and metrics |
| `simulator@nagare.app` | simulator | Read operational boards, full control of simulator boards |
| `system@nagare.app` | system | API/MCP identity — **cannot sign in to the UI** |

Interactive API docs live at **<http://127.0.0.1:8770/docs>**, the OpenAPI document at
`/openapi.json`, and a health probe at `/api/v1/health`.

---

## Features, mapped to the thirteen requirements

| # | Requirement | What ships |
|---|---|---|
| **1** | **Upstream & downstream board** | Every column declares a `segment`. The board renders discovery and delivery as tinted regions either side of an explicit **commitment point** rail; the segment switcher toggles Upstream / Downstream / Unified. Crossing the commitment point stamps `committed_at` once, immutably. |
| **2** | **Queue vs activity columns** | Every column declares a `kind` — `queue` accrues **wait time**, `activity` accrues **touch time**, `envelope` is sealed pre-discovery, `done` is terminal. This is what makes **flow efficiency** computable rather than guessed. |
| **3** | **WIP limits at three scopes** | Minimum/maximum limits per **column**, per **column group** (e.g. all of Discovery) and across the **whole flow**. Maxima are enforced server-side at move time (`409 wip_limit_exceeded`, naming the policy that broke). Minima are advisory and surface a *starving* warning. |
| **4** | **Flow metrics dashboard** | Eleven metric endpoints and a dashboard of hand-written SVG: CFD, lead-time scatter with percentile lines, throughput with rolling average, aging WIP against percentile bands, flow-efficiency split, lead-time histogram, blocker Pareto, class-of-service donut, Little's Law coherence, demand mix, KMM ladder. Every distribution reports 50/70/85/95; any sample under 8 is flagged `low_confidence`. |
| **5** | **Compact cards, deep drawer** | Cards stay readable at a glance — title, project chip, service-class stripe, assignee, aging dot, blocked flag, task progress. Everything else (description, checklist tasks, blockers, comments, movement trail) lives in the detail drawer. |
| **6** | **Classes of service** | `expedite`, `standard`, `fixed_date`, `intangible` as *policies*, not labels: expedite bypasses column WIP and is reported as `wip_override`, fixed-date drives due-date performance, and the class mix is a first-class metric. |
| **7** | **Movement history & audit** | Every move writes a `card_movements` row including `seconds_in_from_column`; every field change writes a `card_events` row. The metrics are computed from that trail, which is why the trail is never rewritten. |
| **8** | **Sealed-envelope demands** | Demand can be captured sealed: counted, but unopened, unassigned and unmovable until someone deliberately opens it. `opened_at` makes intake latency measurable, and a per-board toggle decides whether sealed time counts in customer lead time. |
| **9** | **Forward-only movement** | Backward moves are rejected (`409 backward_move_blocked`) unless a master/administrator passes `force`, which records `is_backward` and an audit entry. Invalid drop targets are visually disabled during a drag, and `board.confirm_moves` drives a confirmation modal with "don't ask again on this board". |
| **10** | **Feature flags & plans** | 19 canonical flags across 7 categories, 4 plan tiers (free → enterprise). A flag is *effective* only when it is enabled **and** the active plan's tier reaches its `min_plan`; `GET /flags/effective` gates the UI and `require_flag()` gates the API. |
| **11** | **Role-based access control** | Six roles — master, administrator, editor, viewer, simulator, system — enforced by a single capability matrix shared by the API guard, `GET /users/roles` and the admin UI. `simulator` may only write simulator boards; `system` may never sign in interactively. |
| **12** | **MCP server** | `mcp/nagare_mcp.py`: a dependency-free stdio JSON-RPC 2.0 MCP server exposing 12 tools over the REST API, so Claude can read the board, create and move work under the same policies, and pull metrics and forecasts. |
| **13** | **Simulator & forecasting** | Simulator boards cloned from an operational board, a deterministic synthetic demand generator, Monte Carlo forecasts (≥10,000 trials) for *when* and *how many*, WIP experiments that demonstrate Little's Law, class-of-service mix experiments, and side-by-side run comparison with a plain-language verdict. |

> The build contract tags requirements 1–3 and 5–13 explicitly against features. Number 4
> is the one pillar it leaves untagged; the flow-metrics dashboard (SPEC §7 and §9.3) is
> the feature that has no other number, so it is mapped there.

---

## Architecture

```
Browser  ──  native ES modules + plain CSS + inline SVG (no build step)
   │            hash router · pub/sub store · fetch wrapper
   │  same origin, no CORS in production
   ▼
uvicorn ── FastAPI app  (backend/app/main.py)
   │            routers/  auth · users · flags · boards · projects · cards
   │                      movements · metrics · simulator
   │            deps.py   auth → RBAC guard → feature-flag guard
   │            flow/     intervals · metrics · kmm · forecast
   ▼
sqlite3 (stdlib)  data/nagare.db
```

Deliberate decisions:

- **One origin.** `run.py` serves the API under `/api/v1` and the static frontend from
  `/`. No CORS to configure, no second server, no proxy.
- **No ORM.** `backend/app/db.py` is a thin helper over `sqlite3` with dict rows,
  parameterised queries and a `migrate()` that applies `schema.sql`. The schema *is* the
  model.
- **Stdlib crypto.** PBKDF2-SHA256 password hashing (iteration count stored with the
  hash) and hand-assembled HS256 JWTs with constant-time verification. No third-party
  crypto dependency, no `alg: none` acceptance.
- **The movement log is the source of truth for time.** Metrics are derived from
  `card_movements` intervals, never from mutable "status changed" fields.
- **Routers import defensively.** A missing router logs a warning instead of preventing
  the app from booting — the codebase is built by several agents in parallel.
- **No build step, ever.** The frontend is served as written. What you read in
  `frontend/js/*.js` is exactly what the browser runs.

### Project layout

```
meu-kanban/
├─ run.py                    entry point: migrate, seed, serve
├─ requirements.txt          fastapi, uvicorn, pydantic (+ pytest, httpx, playwright)
├─ README.md                 this file
├─ docs/
│  ├─ SPEC.md                the binding build contract
│  ├─ API.md                 complete REST reference
│  └─ KANBAN.md              the method rationale (Anderson / Yoshima)
├─ mcp/
│  └─ nagare_mcp.py          stdio JSON-RPC MCP server (stdlib only)
├─ backend/app/
│  ├─ config.py              settings, flag & plan catalogues, role matrix
│  ├─ db.py  schema.sql      sqlite helper + full DDL
│  ├─ models.py  schemas.py  row mappers + pydantic contracts
│  ├─ security.py  deps.py   PBKDF2/JWT, auth + RBAC + flag dependencies
│  ├─ errors.py              the single error envelope
│  ├─ seed.py                deterministic demo workspace
│  ├─ main.py                app factory, router mounting, static serving
│  ├─ flow/                  intervals · metrics · kmm · forecast
│  └─ routers/               the nine HTTP routers
├─ frontend/
│  ├─ index.html
│  ├─ css/                   tokens · base · components · board · metrics · views
│  └─ js/                    api · store · router · ui · app · board · dnd · card
│                            filters · charts · metrics · projects · simulator · admin
└─ tests/                    pytest + FastAPI TestClient
```

---

## Tests

```bash
python -m pytest -q                       # everything
python -m pytest tests/test_metrics.py -q  # one suite
python -m pytest -q -k wip                 # by keyword
```

The suites cover authentication and the RBAC matrix, board/column/group CRUD and WIP
policy evaluation, card movement rules (forward-only, commitment/delivery stamping,
envelope sealing, WIP breaches, expedite override), the flow metric formulas, the
simulator and Monte Carlo engine, and an end-to-end API smoke test. `tests/conftest.py`
builds a fresh, seeded temporary database per session, so the suite never touches
`data/nagare.db`.

The MCP server has its own live round-trip check:

```bash
python mcp/nagare_mcp.py --selftest      # needs the app running on NAGARE_URL
```

---

## Plans and feature flags

Nagare ships the commercial model as a first-class part of the domain rather than a
bolt-on. Two independent switches gate every optional capability:

1. **The flag** — `feature_flags.is_enabled`, toggled by the master role.
2. **The plan** — a flag also declares a `min_plan`, and the workspace has one active
   plan. A flag is **effective** only when it is enabled *and*
   `tier(active_plan) ≥ tier(flag.min_plan)`.

| Plan | Tier | Unlocks |
|---|---|---|
| **Free** | 0 | Upstream & downstream boards, column WIP limits, classes of service, card history, basic metrics (lead time, throughput, WIP), multiple projects |
| **Team** | 1 | + Unified flow view, group and whole-flow WIP limits, sealed envelopes, custom fields, REST API tokens |
| **Business** | 2 | + Advanced metrics (CFD, flow efficiency, aging, SLE), Monte Carlo forecasting, KMM dashboard, simulator boards, MCP integration, full role matrix |
| **Enterprise** | 3 | + Everything above with portfolio roll-up and SSO-ready RBAC |

`GET /api/v1/flags/effective` returns `{key: bool}`; the frontend hides or locks UI
accordingly and the API refuses the call independently, so a hidden button is never the
only thing standing between a user and a gated feature. The seeded demo workspace runs
on **Business** with every flag on.

---

## MCP: driving Nagare from Claude

`mcp/nagare_mcp.py` is a complete Model Context Protocol server — JSON-RPC 2.0 over
stdio, protocol version `2024-11-05`, implemented with `json`, `sys` and
`urllib.request` only. It talks to the running REST API, so every policy the product
enforces (WIP limits, forward-only movement, RBAC, feature flags) applies identically to
the agent.

```bash
python run.py                                  # in one terminal

claude mcp add nagare \
    --env NAGARE_URL=http://127.0.0.1:8770 \
    --env NAGARE_EMAIL=master@nagare.app \
    --env NAGARE_PASSWORD=nagare123 \
    -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py
```

Or commit a `.mcp.json` next to the repo (same shape works in Claude Desktop):

```json
{
  "mcpServers": {
    "nagare": {
      "command": "python",
      "args": ["C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py"],
      "env": {
        "NAGARE_URL": "http://127.0.0.1:8770",
        "NAGARE_TOKEN": "<jwt from POST /api/v1/auth/login>"
      }
    }
  }
}
```

**Tools:** `list_boards`, `get_board`, `list_cards`, `get_card`, `create_card`,
`move_card`, `open_envelope`, `list_projects`, `create_demand`, `get_metrics_summary`,
`get_kmm_status`, `run_forecast`.

Verify the wiring end to end with `python mcp/nagare_mcp.py --selftest`, or dump the tool
schemas with `--print-tools`. Full details in
[docs/API.md § Integrating with Nagare](docs/API.md#integrating-with-nagare).

---

## Concepts primer

Five ideas explain most of the product. The full treatment — with the anti-patterns the
tool deliberately refuses to support — is in **[docs/KANBAN.md](docs/KANBAN.md)**.

- **The commitment point.** The boundary where an option becomes a promise. Upstream of
  it, abandoning work is a success; downstream, it is a failure. Nagare measures the two
  halves separately: *discovery lead time* before it, *system lead time* after it.
- **Options thinking.** An idea is an option, not an obligation — exercise it at the last
  responsible moment. The sealed envelope is that principle made mechanical: demand is
  captured without being started.
- **WIP limits and Little's Law.** `Lead time = WIP ÷ Throughput`. For a given
  throughput, the only lever on lead time is WIP. That is why the limits are enforced by
  the server and not merely displayed.
- **Wait vs touch time.** Marking each column as a queue or an activity yields **flow
  efficiency** — usually a shocking number, and the one that redirects improvement from
  "work harder" to "drain the queues".
- **Probabilistic forecasting.** An SLE is a time *and* a probability ("85% within 14
  days"), measured from your own history. Monte Carlo answers "when" and "how many" with
  a distribution. Report the 85th percentile; never the average.

---

## Documentation

- **[docs/API.md](docs/API.md)** — the complete REST reference: authentication, the error
  code table, every endpoint with its RBAC requirement and feature flag, request and
  response examples, and the MCP integration guide.
- **[docs/KANBAN.md](docs/KANBAN.md)** — why the product is shaped this way: the six
  practices, upstream/downstream, options thinking, classes of service, WIP and Little's
  Law, flow efficiency, SLE and forecasting, the Kanban Maturity Model, and the
  anti-patterns Nagare guards against.
- **[docs/SPEC.md](docs/SPEC.md)** — the build contract: stack, DDL, routes, RBAC matrix,
  flag catalogue, metric formulas and design system.
