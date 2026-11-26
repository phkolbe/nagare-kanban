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

It runs two ways from the same code:

- **Standalone** ("Nagare sozinho"), under `/nagare` (<http://127.0.0.1:8770/nagare/>,
  with English URL segments: `/nagare/sign-in`, `/nagare/board`, `/nagare/projects`,
  `/nagare/metrics`, `/nagare/reports`, `/nagare/simulator`,
  `/nagare/admin/users|roles|flags|plans`, and each card's own page at
  `/nagare/card/<KEY>`), with its local login, a product picker (the local master can work
  in every Pampufa product), the **Reports** area, its own notification bell, the
  simulator and the administration of users, flags and plans. The root and the old
  Portuguese paths (`/quadro/...`, `/cartao/...`) redirect.
- **Inside the Pampufa platform**, as the **Tarefas** module of every product
  (`/grupo-sr/<produto>/tarefas/...`, card pages at `.../tarefas/cartao/<KEY>`), with the
  platform's single sign-on (its EdDSA token, the product in the `X-Produto` header),
  data isolated per product, and the platform's *providências* arriving as cards that
  answer back when they are delivered.

Built on the same stack as the products of the Pampufa platform: **FastAPI + Pydantic**
on Python ≥ 3.13, **SQLAlchemy 2 (async) + Alembic** on **PostgreSQL** (SQLite for the
tests and the zero-setup demo), **uv** for dependencies and **ruff** for lint. The local
login is hand-rolled **PBKDF2 + HS256**; the platform login is the Pampufa's **Ed25519**
token, validated against its JWKS by the `pampufa-sdk`. Reports export to Excel with
**openpyxl**; traces and logs go out by **OpenTelemetry** and `/metrics` speaks
**Prometheus**. The screens are **React 18 + TypeScript + Vite + TanStack Query** with
hand-written SVG charts (`web/`), in Portuguese and English.

The decisions behind the platform integration are in the Obsidian vault,
`grupo-sr/plataforma/decisoes/integracao-nagare.md` (NG1–NG17); the build history and
what is still open, in `grupo-sr/andamento.md`.

---

## Quickstart

Requires **[uv](https://docs.astral.sh/uv/)** (it brings Python 3.13), **Node.js 22** for
the screens, and read access to the private `pampufa-sdk` repository.

```bash
uv sync --all-extras
npm --prefix web ci && npm --prefix web run build
uv run python run.py
```

Then open **<http://127.0.0.1:8770/nagare/>**.

The database is `NAGARE_DATABASE_URL`. In the Pampufa development setup it is the
PostgreSQL of the platform's compose
(`postgresql+asyncpg://nagare:nagare@127.0.0.1:5432/nagare` — use `127.0.0.1`, not
`localhost`, on Windows); without it, a SQLite file in `data/` keeps a cold clone runnable.
The first run applies the Alembic migrations (`ng_0001`–`ng_0008`) and, in `dev` and
`teste` (`NAGARE_AMBIENTE`), seeds a complete
demo workspace: 8 users, 4 projects, 2 boards, 260+ cards with twelve months of
back-dated movement history, blockers, sealed envelopes and cards in flight — enough
that every chart in the product is populated. Seeding is **deterministic**
(`random.Random(20260816)`) and **idempotent** (skipped when the `users` table is
non-empty).

```bash
uv run python run.py --reset          # empty the database and reseed from scratch
uv run python run.py --port 9000      # serve somewhere else
uv run python run.py --seed-only      # migrate + seed, print a summary, exit
uv run python run.py --reload         # uvicorn auto-reload for development
uv run nagare migrar                  # migrate + seed (what the platform's dev-completo runs)
uv run nagare criar-admin --email ... --nome ...   # a master account (homologation, prod)
uv run nagare servir                  # serve (--reload for development)
uv run nagare demo                    # SQLite in data/demo.db, wired to the Pampufa demo
```

`nagare demo` is what the platform's `demo-completo` starts: its own SQLite file, the
Pampufa at `http://127.0.0.1:8030` with the demo service credential, and card links
through the gateway (`http://127.0.0.1:8080/nagare`).

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
> Outside `dev`/`teste` (`NAGARE_AMBIENTE`), `nagare migrar` and `nagare servir` refuse to
> start with the development secret or with the demo seed on; start such an environment
> with `nagare criar-admin` (password from `NAGARE_SENHA_ADMIN` or typed twice).

### Sign in

All demo accounts share the password **`teste12345`**.

| Email | Role | What it can do |
|---|---|---|
| `nagare.master@grupo-sr.com.br` | master | Everything, including plans and feature flags |
| `nagare.admin@grupo-sr.com.br` | administrator | Users, boards, columns, policies, cards |
| `nagare.editor@grupo-sr.com.br` | editor | Create, edit and move cards |
| `nagare.leitor@grupo-sr.com.br` | viewer | Read boards and metrics |
| `nagare.simulador@grupo-sr.com.br` | simulator | Read operational boards, full control of simulator boards |
| `master@grupo-sr.com.br` | master | The Grupo SR "everything" account (also administers every product on the Pampufa) |
| `admin@grupo-sr.com.br` | administrator | The Grupo SR "everything" administrator (also on the Pampufa) |
| `system@nagare.app` | system | API/MCP identity — **cannot sign in to the UI** |

Inside the Pampufa nobody signs in here: people arrive with the platform's token and
their role in each product comes from the `nagare.*` roles (see *Inside the Pampufa*
below).

Interactive API docs live at **<http://127.0.0.1:8770/docs>**, the OpenAPI document at
`/openapi.json`, and a health probe at `/api/v1/health`.

### Configuration

Environment only (`.env.example` lists the development values; the real environment
wins over `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `NAGARE_DATABASE_URL` | SQLite in `data/nagare.db` | The database |
| `NAGARE_HOST` / `NAGARE_PORT` | `127.0.0.1` / `8770` | Where `nagare servir` listens |
| `NAGARE_DEV` | `true` | No-cache headers on the screens |
| `NAGARE_AUTO_MIGRATE` | `true` | Upgrade the schema on boot |
| `NAGARE_JWT_SECRET` / `NAGARE_TOKEN_TTL` | dev value / `43200` | Local login signing key and token lifetime (seconds) |
| `NAGARE_PAMPUFA_URL` | empty | The `pampufa` service; empty means standalone (Pampufa tokens refused) |
| `NAGARE_PAMPUFA_EMISSOR` / `NAGARE_PAMPUFA_JWKS_URL` | `pampufa` / `<url>/.well-known/jwks.json` | How Pampufa tokens are validated |
| `NAGARE_PAMPUFA_CLIENTE_ID` / `NAGARE_PAMPUFA_CLIENTE_SEGREDO` | `nagare` / empty | The Nagare's service credential at the Pampufa |
| `NAGARE_WEBHOOK_INTERVALO` | `5` | Seconds between webhook delivery rounds |
| `NAGARE_URL_TELAS` | empty | The platform's screens (`.../grupo-sr`): a product's card links to its Tarefas module |
| `NAGARE_URL_PUBLICA` | `http://127.0.0.1:8770/nagare` | The standalone Nagare, `/nagare` included, for the other card links |
| `NAGARE_AMBIENTE` | `dev` | `dev`, `teste` or anything else (homologation, production): outside `dev`/`teste` the development secret and the demo seed are refused |
| `NAGARE_SEMENTE_DEMO` | on in `dev`/`teste` | Seed the demo workspace into an empty database |
| `NAGARE_METRICS_TOKEN` | empty | Bearer token of the Prometheus `/metrics`; empty answers only the loopback |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | The OpenTelemetry collector (OTLP/HTTP); empty turns tracing and OTLP logs off |

---

## Features, mapped to the thirteen requirements

| # | Requirement | What ships |
|---|---|---|
| **1** | **Upstream & downstream board** | Every column declares a `segment`. The board renders discovery and delivery as tinted regions either side of an explicit **commitment point** rail; the segment switcher toggles Upstream / Downstream / Unified. Crossing the commitment point stamps `committed_at` once, immutably. |
| **2** | **Queue vs activity columns** | Every column declares a `kind` — `queue` accrues **wait time**, `activity` accrues **touch time**, `envelope` is sealed pre-discovery, `done` is terminal. This is what makes **flow efficiency** computable rather than guessed. The structure editor adds, re-kinds, reorders and removes columns (draining an occupied column first). |
| **3** | **WIP limits at three scopes** | Minimum/maximum limits per **column**, per **column group** (e.g. all of Discovery) and across the **whole flow**. Maxima are enforced server-side at move time (`409 wip_limit_exceeded`, naming the policy that broke). Minima are advisory and surface a *starving* warning. |
| **4** | **Flow metrics dashboard** | Twelve board metric endpoints and a dashboard of hand-written SVG: CFD, lead-time scatter with percentile lines, throughput with rolling average, aging WIP against percentile bands, flow-efficiency split, lead-time histogram, blocker Pareto, class-of-service donut, Little's Law coherence, demand mix, KMM ladder and a read-only Monte Carlo panel (`/metrics/forecast`); plus `GET /metrics/people` (people, projects and clients in a period). Every distribution reports 50/70/85/95; any sample under 8 is flagged `low_confidence`. |
| **5** | **Compact cards, deep drawer** | Cards stay readable at a glance — title, project chip, service-class stripe, assignee, aging dot, blocked flag, task progress. Everything else (description, checklist tasks, blockers, comments, movement trail) lives in the detail drawer. |
| **6** | **Classes of service** | `expedite`, `standard`, `fixed_date`, `intangible` as *policies*, not labels: expedite bypasses column WIP and is reported as `wip_override`, fixed-date drives due-date performance, and the class mix is a first-class metric. Optional lanes by class (`?raias=classe`). |
| **7** | **Movement history & audit** | Every move writes a `card_movements` row including `seconds_in_from_column`; every field change writes a `card_events` row. The metrics are computed from that trail, which is why the trail is never rewritten. |
| **8** | **Sealed-envelope demands** | Demand can be captured sealed: counted, but unopened, unassigned and unmovable until someone deliberately opens it. `opened_at` makes intake latency measurable, and a per-board toggle decides whether sealed time counts in customer lead time. |
| **9** | **Forward-only movement** | Backward moves are rejected (`409 backward_move_blocked`) unless a master/administrator passes `force`, which records `is_backward` and an audit entry. Invalid drop targets are disabled during a drag, and `board.confirm_moves` drives a confirmation modal. On a product board the **flow itself locks after the first card** (NG13). |
| **10** | **Feature flags & plans** | 21 canonical flags across 7 categories, 4 plan tiers (free → enterprise). A flag is *effective* only when it is enabled **and** the active plan's tier reaches its `min_plan`; `GET /flags/effective` gates the UI and `require_flag()` gates the API. A Pampufa product may keep its own plan, time zone and flag switches over the workspace's (D46). |
| **11** | **Role-based access control** | Six roles — master, administrator, editor, viewer, simulator, system — enforced by a single matrix of ten capabilities (`issue_reports` and `share_reports` the newest) shared by the API guard, `GET /users/roles` and the admin UI. Inside the Pampufa the role comes per product from `nagare.admin` / `nagare.editor` / `nagare.leitor` (and `pampufa.gestor` reads every product). |
| **12** | **MCP server** | `mcp/nagare_mcp.py`: a dependency-free stdio JSON-RPC 2.0 MCP server exposing 12 tools over the REST API, so Claude can read the board, create and move work under the same policies, and pull metrics and forecasts. |
| **13** | **Simulator & forecasting** | Simulator boards cloned from an operational board, a deterministic synthetic demand generator, Monte Carlo forecasts (≥10,000 trials) for *when* and *how many*, WIP experiments that demonstrate Little's Law, class-of-service mix experiments, and side-by-side run comparison against a chosen baseline with a plain-language verdict. A screen of the standalone Nagare only. |

> The build contract tags requirements 1–3 and 5–13 explicitly against features. Number 4
> is the one pillar it leaves untagged; the flow-metrics dashboard (SPEC §7 and §9.3) is
> the feature that has no other number, so it is mapped there.

### Beyond the thirteen

Added since the v1 contract, mostly at the request of the products of the platform:

| Feature | What ships |
|---|---|
| **Two modes, one build** | The standalone Nagare under `/nagare` (local HS256 login, English URL segments, product picker) and the Tarefas module of each Pampufa product (EdDSA token, `X-Produto`). Users, flags, plans, workspace, the simulator, Reports and the bell are standalone only (`403 standalone_only` to a Pampufa token). |
| **Reports** (standalone) | `/nagare/reports`: eight templates — delivery review, project status, hours, product portfolio, risks and blockers, where the work waits, policy exceptions, forecast against actual — each a week, a month or a free interval against the previous one, on screen and exported to `.xlsx` or `.csv`. Wave 2: issue a frozen copy (`/nagare/reports/issued`), share REP-02 and REP-03 with a client through a link that expires (7 days by default) and can be revoked (`/nagare/r/<token>`, no sign-in), and REP-09 forecast against actual, fed by the **Register forecast** button of the Monte Carlo panel. Flags `reports` (Business) and `reports_portfolio` (Enterprise), capabilities `read_reports`, `issue_reports` and `share_reports`. |
| **Hours on cards** | Whoever moves cards logs their own hours on a card (`/cards/{id}/time`); the metrics read people, projects and clients in a period, and the integration hands the hours of linked projects to the SR GJ for billing. |
| **Card types per product** | `/card-types`: each product keeps its own list of card types (SR Adv and SR GJ start with *prazo, petição, audiência, diligência, administrativo*); a card can be linked to an SR Adv case. |
| **The card's own page** | Every card has a page by its key — `/nagare/card/<KEY>`, `/nagare/<produto>/card/<KEY>`, `/grupo-sr/<produto>/tarefas/cartao/<KEY>` — which is also the link the Pampufa receives. |
| **A board that keeps itself current** | The screens poll `GET /boards/{id}/version` (~10 s) and reload only when something changed. |
| **The standalone bell** | `/notifications`: a person's notices, unread count, mark read. Empty until the first kind (scheduled reports) arrives. |

### Inside the Pampufa

- **Single sign-on and roles per product.** The Nagare accepts the Pampufa's access
  token (validated offline by JWKS) next to its own login; the product travels in the
  `X-Produto` header and the role comes from the token's `nagare.*` roles in that
  product. `master` exists only through the local login.
- **Products isolated.** Boards, projects and cards carry a `product_id`; keys are unique
  per product, and nothing of one product is visible from another. The PO's
  consolidated board reads every product's board by stage without duplicating a card.
- **Providências become cards.** The Pampufa creates the card through the integration
  API (`/api/v1/integration/*`, idempotent per providência) on its product's board — a
  `providencias` flow created with the first one. A cause that vanished at the origin
  comments and tags the card; it never moves by itself.
- **The Nagare answers back.** Delivering or archiving the card, or recreating it on a
  new flow, queues a webhook (`cartao.entregue`, `cartao.arquivado`, `cartao.movido`) in
  the same transaction; it is posted every `NAGARE_WEBHOOK_INTERVALO` seconds and retried
  until the Pampufa confirms.
- **The flow locks after the first card** (NG13). Structure is editable until a product
  board receives work; after that only WIP limits, the SLE and column names and colours
  change. A new flow is a new project linked to the product: open cards are recreated on
  it and the old board becomes read-only, history and metrics kept (NG14).
- **Projects kept elsewhere, hours for billing.** The SR GJ keeps its projects through
  `PUT /integration/projects/{source}/{id}` (name, status and dates then change only
  there) and reads the hours logged on them (`/integration/time`,
  `/integration/time/by-external`); a product's board can be created before its first
  providência (`POST /integration/product/board`).

Every route, payload and error code is in **[docs/API.md](docs/API.md)**.

---

## Architecture

```
Browser  ──  React 18 + TypeScript (web/, built by Vite) · hand-written SVG charts
   │            two modes: standalone (/nagare) · Pampufa Tarefas module (/grupo-sr/<produto>/tarefas)
   │  same origin
   ▼
uvicorn ── FastAPI app  (backend/app/main.py)
   │            routers/  auth · users · flags · boards · projects · cards · card_types
   │                      movements · metrics · simulator · integration · horas
   │                      notifications · reports
   │            deps.py   auth (local or Pampufa) → product → RBAC guard → feature-flag guard
   │            settings.py  plan, time zone and flags of the product over the workspace's
   │            flow/     intervals · metrics · kmm · forecast
   │            reports/  templates → Document → screen · xlsx · csv
   │            pampufa.py  JWKS validation, profiles, webhook outbox ──► Pampufa
   │            observability.py  OpenTelemetry traces/logs ──► collector · /metrics (Prometheus)
   ▼
SQLAlchemy 2 async  ── PostgreSQL (asyncpg) · SQLite (aiosqlite) in tests/demo
                          schema from Alembic: backend/migrations (ng_*)
```

Deliberate decisions:

- **One origin.** `run.py` serves the API under `/api/v1` and the React build (`web/dist`)
  under `/nagare` (where the API also answers at `/nagare/api/v1`; other screen paths
  redirect there) and under `/grupo-sr/` for the platform, behind its gateway. CORS is
  only open to localhost development servers.
- **SQLAlchemy models, a stable API.** `backend/app/models.py` is the schema of record,
  with real types on PostgreSQL (`uuid`, `timestamptz`, `jsonb`, `boolean`) and UUID v7
  ids. `backend/app/sqltypes.py` keeps the wire contract: ids travel as dashed text and
  instants as ISO-8601 `Z` strings. The flow engine (`flow/`) stays synchronous and
  pure; routers build its context through `AsyncSession.run_sync`.
- **Product isolation** (`tenancy.py`). The product of each request is fixed once, from
  `X-Produto` and the token, and every query scopes itself by it (`nagare` for the
  standalone use). The plan, time zone and flag switches can be the product's own
  (`settings.py`, D46), the workspace's otherwise.
- **Two ways in.** Local tokens are stdlib crypto: PBKDF2-SHA256 password hashing
  (iteration count stored with the hash) and hand-assembled HS256 JWTs with
  constant-time verification, no `alg: none`. Pampufa tokens (EdDSA) are validated by
  the `pampufa-sdk` against the platform's JWKS; the first visit creates or links the
  local user.
- **The movement log is the source of truth for time.** Metrics are derived from
  `card_movements` intervals, never from mutable "status changed" fields.
- **Outbox, not fire-and-forget.** Notifications to the Pampufa are rows in
  `webhook_outbox`, written in the transaction of the change and delivered by a
  background loop with backoff.
- **Routers import defensively.** A missing router logs a warning instead of preventing
  the app from booting.
- **One React app, two modes.** Standalone at `/nagare` (local login at `/nagare/sign-in`,
  light/dark/system theme, its own shell with product picker, board search, shortcuts
  and bell): board, card drawer and card page (`/nagare/card/<KEY>`), structure editor,
  projects, metrics with the KMM, the Monte Carlo panel and the people panel, the
  **Reports**, the **simulator**, and the admin of users, roles, flags and plans
  (`/nagare/admin/users|roles|flags|plans`). Inside the Pampufa, the Tarefas module of
  each product (`/grupo-sr/<produto>/tarefas/...`) with the platform's shell, bell and
  single sign-on from `@pampufa/ui`, talking to `/nagare/api/v1` with the Pampufa token
  and `X-Produto`: board, card drawer and card page (`.../tarefas/cartao/<KEY>`),
  structure editor, projects and the flow swap, metrics with the KMM, task rules and card
  types, and the PO's consolidated board. **The simulator, the Reports and the
  administration are not part of the platform mode**; there the KMM points to the
  Nagare's simulator.
- **The person's language, end to end.** The screens and the texts the API writes
  (metric labels, verdicts, KMM, forecasts) come in pt-BR or English: the person's
  choice (`PATCH /auth/me`, which also keeps the person's screen preferences), else
  Portuguese through the Pampufa and English locally (`backend/app/texts.py`).

### Project layout

```
meu-kanban/
├─ run.py                    entry point: migrate, seed, serve
├─ pyproject.toml  uv.lock   uv project (pampufa-sdk pinned by tag, openpyxl), ruff, pytest
├─ .env.example              the environment variables, with development values
├─ README.md                 this file
├─ docs/
│  ├─ API.md                 complete REST reference (incl. the Pampufa integration)
│  ├─ KANBAN.md              the method rationale (Anderson / Yoshima)
│  ├─ SPEC.md                the v1 build contract (historical)
│  └─ SPEC-v2.md             increment 2 of the v1 contract (historical)
├─ mcp/
│  └─ nagare_mcp.py          stdio JSON-RPC MCP server (stdlib only)
├─ backend/app/
│  ├─ config.py              settings, flag & plan catalogues, role matrix
│  ├─ db.py  sqltypes.py     async engine/sessions, migrations, wire-preserving types
│  ├─ models.py  schemas.py  SQLAlchemy models + pydantic contracts
│  ├─ cli.py                 nagare migrar | criar-admin | servir | demo
│  ├─ security.py  deps.py   PBKDF2/JWT, auth + product + RBAC + flag dependencies
│  ├─ tenancy.py             the product of the request, Pampufa role mapping
│  ├─ settings.py            plan, time zone and flags by product (D46)
│  ├─ pampufa.py             Pampufa gateway: JWKS, profiles, people, webhook outbox
│  ├─ integration.py         providências → cards, consolidated view, flow swap, card_url
│  ├─ card_types.py  cases.py  card types by product, the SR Adv case link
│  ├─ texts.py               pt-BR / English texts of the API
│  ├─ errors.py              the error envelope (also problem+json)
│  ├─ observability.py       OpenTelemetry traces and logs, Prometheus /metrics
│  ├─ seed.py                deterministic demo workspace
│  ├─ main.py                app factory, router mounting, static serving
│  ├─ flow/                  intervals · metrics · kmm · forecast
│  ├─ reports/               report templates · the Document · xlsx/csv (openpyxl)
│  └─ routers/               the fourteen HTTP routers (routers/__init__.py: ROUTER_MODULES)
├─ backend/migrations/       Alembic (ng_0001–ng_0008)
├─ web/                      the React screens (both modes)
│  └─ src/                   app · api · i18n (pt-BR, en) · componentes · graficos ·
│                            sozinho (standalone shell, login, theme, product picker, bell) ·
│                            features/{quadro, cartao, estrutura, projetos, metricas,
│                            relatorios, simulador, admin, regras, consolidado} · checks
├─ tools/                    Playwright QA scripts from v1 (optional `qa` extra)
└─ tests/                    pytest + FastAPI TestClient (sqlhelper: raw SQL on both databases)
```

---

## Tests

```bash
uv run pytest -q                       # everything, on a temp SQLite database
uv run pytest tests/test_metrics.py -q  # one suite
uv run pytest -q -k wip                 # by keyword
uv run ruff check && uv run ruff format --check

# the same suite on PostgreSQL (the database of record)
TEST_DATABASE_URL=postgresql+asyncpg://nagare:nagare@127.0.0.1:5432/nagare_teste uv run pytest -q
```

387 tests in 18 suites (`uv run pytest --collect-only -q`). They cover authentication and
the RBAC matrix, board/column/group CRUD and WIP policy evaluation, card movement rules
(forward-only, commitment/delivery stamping, envelope sealing, WIP breaches, expedite
override), the flow metric formulas, the simulator and Monte Carlo engine (and the MCP
forecast horizons), the Pampufa integration (tokens, products, providências, webhook,
flow lock and swap, with an in-memory Pampufa), settings by product, card types and the
SR Adv case, hours, the board version, the Reports (Documents and exports) and the bell,
observability, the production guard on the demo seed, the 09/11/2026 audit fixes, the API
texts in both languages, and an end-to-end API smoke test. `tests/conftest.py` builds a
freshly migrated and seeded database per session (a temp SQLite file, or the
`TEST_DATABASE_URL` schema, reset first), so the suite never touches your data. CI runs it
on both databases.

The React screens (in `web/`, with the platform's SDK from its git tag):

```bash
npm ci && npm run typecheck && npm run check && npm run build   # web/dist, served by the Nagare
npm run dev                                                       # :5177, API through the gateway (:8080)
```

---

## Plans and feature flags

Two independent switches gate every optional capability:

1. **The flag** — `feature_flags.is_enabled`, toggled by the master role.
2. **The plan** — a flag also declares a `min_plan`, and the workspace has one active
   plan. A flag is **effective** only when it is enabled *and*
   `tier(active_plan) ≥ tier(flag.min_plan)`.

| Plan | Tier | Unlocks |
|---|---|---|
| **Free** | 0 | Upstream & downstream boards, column WIP limits, classes of service, card history, basic metrics (lead time, throughput, WIP), multiple projects |
| **Team** | 1 | + Unified flow view, group and whole-flow WIP limits, sealed envelopes, custom fields, REST API tokens |
| **Business** | 2 | + Advanced metrics (CFD, flow efficiency, aging, SLE), Monte Carlo forecasting, KMM dashboard, simulator boards, MCP integration, full role matrix, Reports (`reports`) |
| **Enterprise** | 3 | + The portfolio report (`reports_portfolio`); the plan also advertises portfolio roll-up and SSO-ready RBAC |

21 flags in 7 categories (`config.FEATURE_FLAGS`; `GET /flags/catalogue` serves the
table).

The frontend hides or locks UI from `GET /api/v1/flags/effective` and the API refuses
the call independently, so a hidden button is never the only thing standing between a
user and a gated feature. The workspace holds the default plan and switches; a Pampufa
product may keep its own plan, time zone and flag switches over them (D46, `ng_0005`),
while each flag's `min_plan` stays global. The seeded demo runs on **Enterprise** with
every flag on (NG15).

---

## MCP: driving Nagare from Claude

`mcp/nagare_mcp.py` is a Model Context Protocol server (JSON-RPC 2.0 over stdio,
stdlib only) that talks to the running REST API, so every policy the product enforces
applies identically to the agent. Twelve tools: `list_boards`, `get_board`,
`list_cards`, `get_card`, `create_card`, `move_card`, `open_envelope`, `list_projects`,
`create_demand`, `get_metrics_summary`, `get_kmm_status`, `run_forecast`.

```bash
claude mcp add nagare \
    --env NAGARE_URL=http://127.0.0.1:8770 \
    --env NAGARE_EMAIL=nagare.master@grupo-sr.com.br \
    --env NAGARE_PASSWORD=teste12345 \
    -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py

python mcp/nagare_mcp.py --selftest      # live round trip; needs the app running
```

Configuration, tool table and `.mcp.json`:
[docs/API.md § Integrating with Nagare (MCP)](docs/API.md#13-integrating-with-nagare-mcp).

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

- **[docs/API.md](docs/API.md)** — the REST reference, written from the code and last
  checked against it on 25/11/2026: authentication (local and Pampufa, `X-Produto`,
  roles), the error envelope and every code, the person's language and preferences,
  settings by product, every endpoint (114 operations) with its RBAC requirement, feature
  flag and mode, hours and card types, the Monte Carlo panel, the simulator, the MCP
  server, the integration with the Pampufa (routes, webhook, configuration), the Reports
  (templates, the Document, exports) and the standalone bell.
- **[docs/KANBAN.md](docs/KANBAN.md)** — why the product is shaped this way: the six
  practices, upstream/downstream, options thinking, classes of service, WIP and Little's
  Law, flow efficiency, SLE and forecasting, the Kanban Maturity Model, and the
  anti-patterns Nagare guards against.
- **[docs/SPEC.md](docs/SPEC.md)** and **[docs/SPEC-v2.md](docs/SPEC-v2.md)** — the v1
  build contract (tag `v1`) and its second increment, kept as written. Their movement
  rules, metric formulas, simulator, seed and structure invariants still hold; each
  says at the top what changed since.
- **Obsidian vault** — the integration decisions
  (`grupo-sr/plataforma/decisoes/integracao-nagare.md`), the product vision
  (`grupo-sr/produtos/nagare/visao/nagare.md`) and the build history
  (`grupo-sr/andamento.md`).
