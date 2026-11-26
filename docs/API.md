# Nagare Kanban — REST API Reference

> **Documentation status:** checked against the code on 25/11/2026 (commit `26b2e6c`):
> every route of `app.openapi()`, every router, the error codes raised in `backend/app`
> and the capability and flag catalogues in `config.py`.

This describes the API **as implemented**: it was written from the OpenAPI document the
app generates (`app.openapi()`) and checked against every router in
`backend/app/routers/` plus `deps.py`, `tenancy.py`, `settings.py`, `pampufa.py`,
`integration.py`, `reports/` and `errors.py`. The live server (`python run.py`,
`nagare.master@grupo-sr.com.br` / `teste12345`) was used to capture the examples.

- **Base path.** Every endpoint below is mounted under `/api/v1` (e.g.
  `POST /api/v1/auth/login`). Standalone: `http://127.0.0.1:8770/api/v1` (also
  `http://127.0.0.1:8770/nagare/api/v1`, where the standalone screens call it). Inside the
  Pampufa the screens reach the same API through the platform's gateway at
  `/nagare/api/v1`.
- **Discovery.** Swagger UI at `/docs`, ReDoc at `/redoc`, the raw OpenAPI document at
  `/openapi.json` (none of these under `/api/v1`). The OpenAPI document is the ground
  truth for the route table: `curl -s :8770/openapi.json | jq '.paths | keys'`.
- **Size.** 114 operations: 1 meta route (`GET /health`) and the fourteen routers of
  `ROUTER_MODULES` — `auth` (5), `users` (6), `flags` (9), `boards` (18), `projects` (7),
  `cards` (16), `card_types` (5), `movements` (4), `metrics` (12), `simulator` (8),
  `integration` (13), `horas` (4, tag `hours`), `notifications` (3), `reports` (3). Every
  one is listed below. Outside the OpenAPI document there is also the Prometheus
  `GET /metrics` (not under `/api/v1`, §17).
- **Two modes, one API.** Most routes work in both modes. **Standalone only** (a Pampufa
  token answers `403 standalone_only`): users (§5), flags, plans and workspace (§6), the
  simulator (§12), Reports (§15) and the bell (§16).
- **Lists** answer `{"items": [...], "total": n}` uniformly; ids are UUID v7 as dashed
  text, instants ISO-8601 `Z` strings.

The binding v1 contract in `docs/SPEC.md` predates the platform integration; where the
code goes beyond it (products, Pampufa roles, the flow lock, the integration API), this
document follows the code. The decisions behind that work are in the Obsidian vault,
`grupo-sr/plataforma/decisoes/integracao-nagare.md` (NG1–NG17).

---

## Table of contents

1. [Authentication, products and roles](#1-authentication-products-and-roles)
2. [Error handling](#2-error-handling)
3. [RBAC & feature flags, mechanically](#3-rbac--feature-flags-mechanically)
4. [Auth & identity (and the person's language)](#4-auth--identity-and-the-persons-language)
5. [Users (ADMIN)](#5-users-admin)
6. [Flags, plans & workspace (ADMIN)](#6-flags-plans--workspace-admin)
7. [Boards, columns, groups, WIP (BOARDS)](#7-boards-columns-groups-wip-boards)
8. [Projects & demands (BOARDS)](#8-projects--demands-boards)
9. [Cards, tasks, blockers, comments (CARDS)](#9-cards-tasks-blockers-comments-cards)
10. [Movement & history (CARDS)](#10-movement--history-cards)
11. [Metrics & KMM (METRICS)](#11-metrics--kmm-metrics)
12. [Simulator (SIM)](#12-simulator-sim)
13. [Integrating with Nagare (MCP)](#13-integrating-with-nagare-mcp)
14. [Integration with the Pampufa platform](#14-integration-with-the-pampufa-platform)
15. [Reports (standalone)](#15-reports-standalone)
16. [Notifications: the standalone bell](#16-notifications-the-standalone-bell)
17. [Appendix: implementation notes](#17-appendix-implementation-notes)

---

## 1. Authentication, products and roles

Every request except `POST /auth/login` and `GET /health` carries
`Authorization: Bearer <token>`. `deps.get_current_user` accepts **two kinds of token**
(NG5) and tells them apart by the JWT header: `alg: EdDSA` is a Pampufa token, anything
else is treated as a local one.

### 1.1 Local login (standalone Nagare)

A hand-rolled **HS256 JWT** (`backend/app/security.py`, stdlib only), minted by
`POST /auth/login`:

```
POST /api/v1/auth/login
{"email": "nagare.master@grupo-sr.com.br", "password": "teste12345"}
```

```json
200
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9....",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": {
    "id": "59888dd1-780e-44d1-958c-12e1de458f5a",
    "email": "nagare.master@grupo-sr.com.br", "name": "Yuki Tanaka", "role": "master",
    "is_active": true, "created_at": "2025-07-12T08:00:00Z",
    "last_login_at": "2026-08-16T13:04:46Z"
  }
}
```

- **Claims**: `sub` (user id), `role` (a hint only — the role is re-read from `users` on
  every request, so a role change or deactivation lands immediately), `iat`, `exp`,
  `iss: "nagare-kanban"`, `jti`.
- **Lifetime**: `NAGARE_TOKEN_TTL` seconds, default `43200` (12h). No refresh token:
  `POST /auth/refresh` re-mints from the *current* database row.
- **Signature**: only `HS256` is accepted (no `alg: none`), compared with
  `hmac.compare_digest`. `NAGARE_JWT_SECRET` must be set outside development; it falls
  back to the published `nagare-dev-secret-do-not-use-in-production`.
- **Passwords**: PBKDF2-HMAC-SHA256, 260,000 iterations, per-user salt, stored as
  `pbkdf2$<iterations>$<salt_b64>$<hash_b64>`; a weaker stored hash is re-hashed on the
  next successful login.
- **`system` cannot sign in interactively.** `POST /auth/login` answers
  `403 interactive_login_denied` *after* verifying the password, so the check cannot be
  used to enumerate accounts. Account-state failures (`account_disabled`) are likewise
  reported only after the password matched.
- **Brute-force throttle** (in memory, per email **and client address**, so someone
  typing the master's e-mail elsewhere cannot lock the master out of their own machine;
  behind a proxy that hides the client it degrades to per email): 5 failures inside 15
  minutes lock that pair for 30s, doubling per further failure up to 15 minutes →
  `429 too_many_login_attempts` with `details.retry_after_seconds`. Unknown emails, and
  accounts that sign in only through the Pampufa (no password hash), pay for one real
  PBKDF2 comparison and answer the same `401 invalid_credentials`, so timing does not
  leak which addresses exist.

### 1.2 Pampufa login (the Tarefas module of each product)

Inside the platform the screens send the person's **Pampufa access token** unchanged
(EdDSA, Ed25519). `backend/app/pampufa.py` validates it offline with the `pampufa-sdk`
validator against the platform's JWKS:

| Check | Value |
|---|---|
| JWKS | `NAGARE_PAMPUFA_JWKS_URL`, default `${NAGARE_PAMPUFA_URL}/.well-known/jwks.json` |
| Issuer | `NAGARE_PAMPUFA_EMISSOR` (default `pampufa`) |
| Audience | `nagare` (fixed) |

- With `NAGARE_PAMPUFA_URL` empty the Nagare runs standalone and refuses Pampufa tokens
  with `401 pampufa_not_configured`.
- An expired Pampufa token answers `401` with `error.code = "token_expired"` and
  `codigo = "auth.token_expirado"` — the platform's client renews the session on that
  code. Any other validation failure is `401 invalid_token`.
- **First sight of a person.** The token carries roles but no name or e-mail, so the
  Nagare calls the Pampufa's `GET /api/v1/auth/eu` with the same token and creates — or
  links by e-mail — the local user (no password: it signs in only through the Pampufa).
  Failures: `401 pampufa_profile_unavailable`, `401 pampufa_link_conflict` (the e-mail is
  linked to another Pampufa account). Service identities (`servico:<name>`) map to one
  `system` user each, without a profile call.

### 1.3 The product: `X-Produto`

The Nagare serves several products at once (NG4): `nagare` for the standalone use and
one product per Pampufa product (`pampufa`, `sr-adv`, `sr-gj`, `batoom`). The product of
a request travels in the **`X-Produto`** header (validated against
`^[a-z0-9][a-z0-9-]{1,39}$`, else `400 invalid_product`) and scopes every query that
follows (`backend/app/tenancy.py`):

| Way in | No header | Header present |
|---|---|---|
| Local token | product `nagare` | `master` works as `master`; `administrator` and `viewer` read the product as `viewer`; `editor` and `simulator` → `403 product_forbidden` (`tenancy.LOCAL_ROLES_IN_PRODUCTS`) |
| Pampufa token | `400 product_required` | the role comes from the token's roles **in that product** |

`GET /integration/products` (§14.2) lists the products the caller can open and the role
held in each — the product picker of the standalone screens.

**Pampufa roles → Nagare role in the product** (NG6, strongest first):

| Pampufa role (in `papeis[<product>]`) | Nagare role |
|---|---|
| `system` (the platform's service token, e.g. `servico:pampufa`) | `system` |
| `nagare.admin` | `administrator` |
| `nagare.editor` | `editor` |
| `nagare.leitor` | `viewer` |
| `pampufa.gestor` (in `papeis["pampufa"]`) | `viewer` in every product (the consolidated view) |
| none of these | `403 product_forbidden` |

`master` exists only through the local login. Boards, projects and cards carry a
`product_id`; human keys (board slug, project key, card key) are unique per product, and
a board, project or card of another product simply does not exist for the request
(`404`).

---

## 2. Error handling

Every failure — raised by a router, by FastAPI's validator, or by an unhandled
exception — is rendered by `backend/app/errors.py` in one body that is both the Nagare's
own envelope and readable as **problem+json** by the platform's client (`@pampufa/ui`):

```json
409
{
  "title": "The column WIP limit on “Build” is full: 8 of 8 — finish something before starting something.",
  "status": 409,
  "codigo": "wip_limit_exceeded",
  "error": {
    "code": "wip_limit_exceeded",
    "message": "The column WIP limit on “Build” is full: 8 of 8 — finish something before starting something.",
    "details": {"policy": "column", "name": "Build", "current": 8, "max": 8, "projected": 9,
                "column_id": "b9ee78d3-...", "service_class": "standard"}
  }
}
```

- `error.code` is the stable machine string the React screens, the MCP server and the
  tests switch on; `error.details` carries the structured context.
- `title`, `status` and `codigo` are the problem+json side. `codigo` equals `error.code`,
  except `token_expired`, which becomes `auth.token_expirado` (the platform's vocabulary).
- Messages are English; the screens translate **by code**. Error texts are never
  localised by the API (see §4.1).
- A `401` also carries `WWW-Authenticate: Bearer`.

### 2.1 Error codes raised

Found by searching every `code=` / `Conflict(...)` / `ApiError(...)` in `backend/app`.

| HTTP | Code | Where | Meaning |
|---|---|---|---|
| 400 | `invalid_product` / `product_required` | `tenancy.py`, `deps.py` | Bad `X-Produto` value; Pampufa token without `X-Produto` |
| 400 | `board_has_no_columns` | `cards.py`, `simulator.py` | Board with no column (simulator: fewer than two) |
| 400 | `board_id_required` | `projects.py` | Product has more than one operational board; name one |
| 400 | `column_board_mismatch` / `card_column_mismatch` | `boards.py`, `cards.py`, `movements.py` | A column of another board |
| 400 | `column_not_on_board` / `duplicate_column_in_order` / `incomplete_column_order` | `boards.py` | Reorder or group span names a foreign column, repeats one, or leaves one out |
| 400 | `invalid_position` | `boards.py` | Column position out of range |
| 400 | `field_not_editable` | `cards.py` | `PATCH /cards/{id}` touched a non-editable field (e.g. `column_id`) |
| 400 | `group_not_on_board` | `boards.py` | Group of another board |
| 400 | `invalid_filter` | `cards.py` | Unknown enum value in a filter |
| 400 | `invalid_wip_range` | `boards.py` | `wip_min > wip_max` |
| 400 | `no_envelope_column` / `sealed_requires_envelope_column` | `cards.py` | Sealed card without an envelope column |
| 400 | `owner_not_found` | `projects.py` | `owner_id` does not exist |
| 400 | `unknown_flag` / `plan_inactive` | `flags.py` | Plan names an unknown flag; activating a retired plan |
| 400 | `unknown_role` | `users.py` | `?role=` is not one of the six roles |
| 400 | `invalid_service_class` | `integration.py` | `classe_servico` of a providência is not a class of service |
| 400 | `refresh_through_pampufa` | `auth.py` | `POST /auth/refresh` with a Pampufa token: the Pampufa renews it |
| 400 | `not_a_simulator_board` | `simulator.py` | Write to a non-simulator board through `/simulator/*` |
| 400 | `invalid_simulation_params` / `simulation_failed` / `compare_failed` | `simulator.py` | Missing/invalid run parameter; engine refused the input; comparison impossible |
| 400 | `simulation_params_out_of_range` | `simulator.py` | A size parameter outside its range (`trials` 1–100,000, `backlog_size` 1–10,000, `days` 1–3,650, `weeks` 1–104, `sample_weeks` 1–520, …; `details.param`, `min`, `max`) |
| 400 | `forecast_failed` | `metrics.py` | The board's history cannot feed `GET /boards/{id}/metrics/forecast` |
| 400 | `invalid_card_type` / `card_type_name_required` / `card_type_order_mismatch` | `card_types.py` | Card type not active in the product; empty name; reorder without every key once (§9.1) |
| 400 | `case_link_unavailable` | `cases.py` | A card linked to an SR Adv case in the standalone Nagare (§9.2) |
| 400 | `report_unknown` / `report_bad_period` / `report_bad_date` / `report_board_required` / `report_project_required` | `reports.py`, `reports/modelos.py` | Reports: unknown template, bad period or date, missing scope (§15) |
| 401 | `missing_token` | `deps.py` | No `Authorization: Bearer` |
| 401 | `invalid_token` / `token_expired` | `security.py`, `deps.py` | Untrustworthy or expired token (local or Pampufa) |
| 401 | `unknown_subject` | `deps.py`, `auth.py` | Token's `sub` no longer exists |
| 401 | `invalid_credentials` | `auth.py` | Wrong email or password (same body for both) |
| 401 | `pampufa_not_configured` / `pampufa_profile_unavailable` / `pampufa_link_conflict` | `deps.py`, `pampufa.py` | See §1.2 |
| 401 / 403 | `metrics_token` / `metrics_local_only` | `observability.py` | Prometheus `GET /metrics`: wrong `NAGARE_METRICS_TOKEN`; no token configured and the caller is not the loopback (§17) |
| 403 | `account_disabled` | `auth.py`, `deps.py` | Deactivated account |
| 403 | `interactive_login_denied` | `auth.py` | `system` tried to sign in |
| 403 | `product_forbidden` | `tenancy.py`, `deps.py` | No Nagare role in that product, or a local `editor`/`simulator` with `X-Produto` (§1.3) |
| 403 | `insufficient_role` | `deps.py`, `boards.py`, `projects.py` | Role not allowed for this action |
| 403 | `board_write_forbidden` | `deps.py` | Role cannot write this board's content |
| 403 | `feature_disabled` / `plan_upgrade_required` | `deps.require_flag` | Flag off; active plan below the flag's `min_plan` |
| 403 | `master_account_protected` / `master_role_forbidden` | `users.py` | Non-master touching a master / granting master |
| 403 | `plan_change_forbidden` | `flags.py` | Non-master switching the active plan |
| 403 | `comment_not_yours` | `cards.py` | Editing someone else's comment (non-admin) |
| 403 | `not_yours` | `horas.py` | Removing someone else's hour entry (non-admin, §9.3) |
| 403 | `system_has_no_preferences` | `auth.py` | `PATCH /auth/me` by a service identity |
| 403 | `standalone_only` | `deps.require_standalone` | Users, flags, plans, workspace, simulator, Reports or the bell called with a Pampufa token (§5, §6, §12, §15, §16) |
| 404 | `board_not_found`, `column_not_found`, `group_not_found`, `card_not_found`, `project_not_found`, `user_not_found`, `task_not_found`, `comment_not_found`, `flag_not_found`, `plan_not_found`, `run_not_found`, `providencia_not_found`, `card_type_not_found`, `time_not_found`, `notification_not_found` | throughout | Lookups; boards, cards and projects also accept their human key (slug, `NGR-104`, `NGR`) |
| 404 | `not_found` | `main.py` catch-all | No API route for that method and path |
| 405 | `method_not_allowed` | Starlette | Wrong verb on a real path |
| 409 | `backward_move_blocked` | `movements.py` | Move against the flow without an allowed `force` |
| 409 | `wip_limit_exceeded` | `cards.assert_wip_room` (moves, new cards, demands, opened envelopes, unarchiving) | Column/group/flow maximum would break (`details.policy`) |
| 409 | `envelope_sealed` / `envelope_already_opened` / `envelope_column_missing` | `movements.py`, `projects.py` | Envelope state conflicts |
| 409 | `card_archived` / `card_moved_elsewhere` / `same_column_move` | `movements.py`, `boards.py`, `horas.py` | Move preconditions; hours on an archived card |
| 409 | `card_type_name_taken` / `card_type_last_active` / `card_type_in_use` | `card_types.py` | Card type guardrails (§9.1) |
| 409 | `board_linked_to_product` / `board_has_link_history` | `boards.py` | Archiving the board that receives a product's providências (link another flow first, §14.4); hard-deleting a board that ever received them (archive it instead) |
| 409 | `project_managed_elsewhere` | `projects.py` | `PATCH /projects/{id}` touching name, status, dates or `archived` of a project kept by another service (§14.2, GJ-1); `details.url` opens it there |
| 409 | `card_already_blocked` / `card_not_blocked` | `cards.py` | Blocker state |
| 409 | `flow_locked` | `boards.py` | Structural change on a product board that holds work (§7.1) |
| 409 | `board_read_only` | `deps.py` | Board swapped out of its product (§14.4): history only |
| 409 | `column_not_empty` | `boards.py` | Deleting a column that holds cards (`details.drain_endpoint`) |
| 409 | `envelope_column_exists` / `envelope_must_be_upstream` / `envelope_must_be_first` | `boards.py` | Envelope-column invariants |
| 409 | `done_must_be_downstream` / `segment_order_violation` / `group_not_contiguous` / `invalid_structure` | `boards.py` | Ordering invariants; a change that would remove the delivery point |
| 409 | `commitment_point_is_derived` / `delivery_point_is_derived` | `boards.py` | Derived flags set directly |
| 409 | `no_open_column` / `no_operational_board` / `board_has_no_columns` | `projects.py`, `integration.py` | Structural precondition missing |
| 409 | `board_not_linkable` / `already_linked` | `integration.py` | Flow swap preconditions (§14.4) |
| 409 | `providencia_in_other_product` | `integration.py` | The `providencia_id` already has a card in another product (§14.2) |
| 409 | `email_taken`, `last_master_protected`, `self_role_change_blocked`, `self_deactivation_blocked` | `users.py` | User guardrails |
| 409 | `project_key_taken` | `projects.py` | Project key in use in this product |
| 409 | `plan_slug_taken` / `active_plan_locked` | `flags.py` | Plan conflicts |
| 422 | `validation_error` | FastAPI validator | `details.fields` = `[{field, message, type}]` |
| 429 | `too_many_login_attempts` | `auth.py` | Login throttle (§1.1) |
| 500 | `internal_error` | unhandled exception | Logged with traceback; the client gets `details.type` only |

### 2.2 Examples, captured live

Shown without the three problem+json fields, which every body carries as in §2.

```json
409 {"error": {"code": "envelope_sealed",
  "message": "NGR-192 is still a sealed envelope — open it before moving it.",
  "details": {"card_id": "576147cc-...", "envelope_state": "sealed"}}}

409 {"error": {"code": "backward_move_blocked",
  "message": "Cards flow forward only: “Analysis Done” sits after “Ideas” in this value stream.",
  "details": {"card_id": "d0d15afb-...", "from_column_id": "883c0c25-...",
              "from_column": "Analysis Done", "to_column_id": "35cf42bd-...",
              "to_column": "Ideas", "from_position": 6, "to_position": 1,
              "force_allowed_for": ["administrator", "master"], "forced": false}}}

409 {"error": {"code": "flow_locked",
  "message": "This board's flow is locked: it already holds work. WIP limits, the SLE and column names and colours can still change; for a different flow, link a new project to the product.",
  "details": {"board_id": "01a14b6d-...", "product_id": "sr-adv", "change": "create_column"}}}

403 {"error": {"code": "product_forbidden",
  "message": "Your local role cannot work in a Pampufa product.",
  "details": {"product": "sr-adv"}}}

403 {"error": {"code": "plan_upgrade_required",
  "message": "“CFD, flow efficiency, aging, SLE” requires the Business plan.",
  "details": {"flag": "metrics_advanced", "min_plan": "business", "plan": "free"}}}

422 {"error": {"code": "validation_error",
  "message": "The request body or query string failed validation.",
  "details": {"fields": [{"field": "password", "message": "Field required", "type": "missing"}]}}}

400 {"error": {"code": "invalid_filter",
  "message": "Unsupported value for “service_class”: bogus.",
  "details": {"filter": "service_class", "invalid": ["bogus"],
              "allowed": ["expedite", "fixed_date", "intangible", "standard"]}}}
```

---

## 3. RBAC & feature flags, mechanically

**Roles.** The capability matrix lives once, in `config.ROLE_CAPABILITIES`, and is
consumed by the API guards, `GET /users/roles` and `GET /auth/me`:

| Capability | Roles |
|---|---|
| `manage_plans_flags` | master |
| `manage_users` | master, administrator |
| `manage_boards` | master, administrator |
| `manage_cards` | master, administrator, editor, system |
| `read_boards_metrics` | every role |
| `simulator_boards` | master, administrator, simulator |
| `api_tokens` | master, administrator, system |
| `read_reports` | every role (whoever reads metrics; decision R21, 24/11/2026) |

Guards come in three shapes:

- `require_roles(...)` and `require_capability(...)` — FastAPI dependencies that answer
  `403 insufficient_role` before the body runs.
- **Board writes** (`require_board_write`, from the board's `kind` and the caller's
  role): `master`/`administrator` any board; `editor`/`system` operational boards only;
  `simulator` simulator boards only; `viewer` never → `403 board_write_forbidden`. A
  board swapped out of its product refuses every write with `409 board_read_only`.
- **Board structure** (`require_structure_write`): `master`/`administrator`, plus
  `simulator` on simulator boards. Column and group routes declare
  `require_roles("master", "administrator", "simulator")` and then check the board;
  `POST /boards`, `PATCH`/`DELETE /boards/{id}` and `/clone` declare the permissive
  `read_boards_metrics` and do the real check in the body — the effect for the caller is
  the same `403`, it just happens after the body was parsed.

Inside a product, the role is the one §1.3 resolved from the Pampufa token, so the same
matrix applies per product.

**Feature flags.** A flag is *effective* when `feature_flags.is_enabled` **and**
`tier(active_plan) ≥ tier(flag.min_plan)`. `require_flag(key)` answers
`403 feature_disabled` (off) or `403 plan_upgrade_required` (plan too low); an unknown
flag key never gates anything. `GET /flags/effective` returns exactly the map
`require_flag` enforces.

**Settings by product (D46, `ng_0005`, `backend/app/settings.py`).** The workspace row is
the default and the standalone Nagare's own configuration. A Pampufa product may keep its
own **active plan**, **time zone** and **flag switches** in `product_settings`; whatever it
leaves empty falls back to the workspace. A flag's `min_plan` is global: per product a flag
is only switched on or off. The effective map, `require_flag`, the time zone of the
metrics and the period of the Reports all read the product of the request. The local
master edits a product's settings through the admin routes of §6 with `X-Produto`.

---

## 4. Auth & identity (and the person's language)

| Method & path | RBAC | Notes |
|---|---|---|
| `POST /auth/login` | public | `{email, password}` → `TokenResponse` (§1.1) |
| `POST /auth/refresh` | local token | Re-mints a **local** token from the database row; refuses `system`; a Pampufa token answers `400 refresh_through_pampufa` (the platform renews its own) |
| `GET /auth/me` | any valid token | Profile, capability matrix, effective flags, locale, preferences, way in, product |
| `PATCH /auth/me` | any valid token except `system` (`403 system_has_no_preferences`) | `{locale?: "pt-BR" \| "en" \| null, preferences?: {board_toolbar?: "bar" \| "panel" \| null}}` — only the fields sent change; `locale: null` goes back to the default of the way in, a preference set to `null` goes back to its default. Stored on the user, so it follows the person to every device |
| `GET /auth/capabilities` | any valid token | `{capability: bool}` for the caller's role |

`GET /auth/me` (and the `PATCH` answer):

```json
{
  "id": "59888dd1-...", "email": "nagare.master@grupo-sr.com.br", "name": "Yuki Tanaka",
  "role": "master", "is_active": true, "created_at": "...", "last_login_at": "...",
  "role_label": "Master",
  "role_description": "Owns the workspace: billing plan, feature flags, users and every board.",
  "capabilities": {"manage_plans_flags": true, "manage_users": true, "...": true},
  "granted": ["manage_plans_flags", "manage_users", "..."],
  "flags": {"api_access": true, "card_history": true, "...": "(21 keys)"},
  "expires_in": 43200,
  "locale": "pt-BR",
  "locale_chosen": "pt-BR",
  "preferences": {"board_toolbar": "bar"},
  "via": "local",
  "product_id": "nagare"
}
```

Through the Pampufa, `role` is the role **in the product** of `X-Produto`, `via` is
`pampufa` and `product_id` that product. `preferences.board_toolbar` is where the board
tools live: `bar` (a collapsible bar above the board, the default) or `panel` (a floating
panel).

### 4.1 Language

The texts the API writes for people — metric labels and interpretations, KMM `why`
sentences, Monte Carlo and simulator verdicts, run names — come in **pt-BR or English**
(`backend/app/texts.py`):

- `locale` is the person's own choice (`PATCH /auth/me`, stored in `users.locale`, the
  same on every device); `locale_chosen: null` means "follow the default".
- The default follows the way in: **Portuguese through the Pampufa, English on the local
  login**.
- Machine keys, codes, enum values and error messages are never translated. A saved
  simulator run keeps the language of whoever ran it.

---

## 5. Users (ADMIN)

**Standalone Nagare only:** with a Pampufa token every route here answers
`403 standalone_only` (inside the platform, people and roles are the Pampufa's, and the
users table is not split by product; decision N3-c). Writes need `manage_users`
(`master`, `administrator`); reads are open to every local identity because assignee
pickers resolve names.

| Method & path | Query / body | RBAC | Notes |
|---|---|---|---|
| `GET /users` | `?role=&q=&is_active=&limit=(≤500, default 200)&offset=` | any | Unknown `role` → `400 unknown_role` |
| `GET /users/roles` | — | `manage_users` + flag `rbac_advanced` | Roles with capabilities, `matrix`, `user_counts`, `protected_roles: ["master"]`, `non_interactive_roles: ["system"]` |
| `GET /users/{user_id}` | — | any | |
| `POST /users` | `{email, name, password, role, is_active}` | `manage_users` | Only a master may grant `master` |
| `PATCH /users/{user_id}` | any subset | `manage_users` | Guardrails below |
| `DELETE /users/{user_id}` | — | `manage_users` | Soft delete (`is_active = false`); history keeps its author |

Guardrails on every write: only `master` creates or edits a master; nobody changes their
own role or deactivates themselves; the last active master can never be demoted or
deactivated; e-mail uniqueness is case-insensitive.

Users are global (the `users` table has no `product_id`): people who come in through the
Pampufa are created here on first sight (§1.2), without a password.

---

## 6. Flags, plans & workspace (ADMIN)

**Standalone Nagare only**, like §5: a Pampufa token answers `403 standalone_only`. Inside
the platform the shell reads the effective flags from `GET /auth/me`.

| Method & path | Query / body | RBAC | Notes |
|---|---|---|---|
| `GET /flags` | `?category=` | master | Switchboard grouped by category, with enabled/effective counts and the active plan |
| `GET /flags/effective` | — | any | `{key: bool}` — what `require_flag` enforces |
| `GET /flags/catalogue` | — | any | The static flag table from `config.py` (name, description, `min_plan`, category) |
| `PATCH /flags/{key}` | `{is_enabled?, min_plan?}` | master | `404 flag_not_found`. With `X-Produto` of a Pampufa product, `is_enabled` is that product's own switch (§3); `min_plan` is always global |
| `GET /plans` | — | master | Cheapest tier first |
| `POST /plans` | `{name, slug, price_month, price_year, currency, tier_order, description, flags[], is_active}` | master | `409 plan_slug_taken`, `400 unknown_flag` |
| `PATCH /plans/{plan_id}` | any subset | master | Cannot deactivate the active plan (`409 active_plan_locked`) |
| `GET /workspace` | — | any | Workspace, active plan, effective flags, plan list |
| `PATCH /workspace` | `{name?, active_plan_id?, timezone?}` | `manage_users`; switching `active_plan_id` is master-only (`403 plan_change_forbidden`), a retired plan → `400 plan_inactive` | Re-gates every flag in the same response. With `X-Produto` of a Pampufa product, the plan and the time zone are stored as that product's own (§3); the name stays the workspace's |

`GET /workspace` (and the `PATCH` answer) reports the settings in force for the product of
the request: its own plan and time zone over the workspace's.

21 flags in 7 categories, 4 plans `free`(0) → `team`(1) → `business`(2) →
`enterprise`(3), each cumulative with the tier below. Since 24/11/2026: `reports`
(metrics, business) and `reports_portfolio` (metrics, enterprise), §15. The seeded
workspace runs on **Enterprise** with every flag on (NG15).

---

## 7. Boards, columns, groups, WIP (BOARDS)

| Method & path | Query / body | RBAC (effective) | Flag |
|---|---|---|---|
| `GET /boards` | `?kind=operational\|simulator&q=&archived=` | any | — |
| `GET /boards/templates` | — | any | — |
| `POST /boards` | `{name, description?, kind, template?, count_envelope_in_metrics?, confirm_moves?, wip_min?, wip_max?, sle_days?, sle_percentile?}` | master/administrator; `kind=simulator` also `simulator` | `simulator` for `kind=simulator` |
| `GET /boards/{id}` | — | any | — |
| `PATCH /boards/{id}` | any subset of `{name, description, count_envelope_in_metrics, confirm_moves, wip_min, wip_max, sle_days, sle_percentile, archived}` | structure role | — |
| `DELETE /boards/{id}` | `?hard=true` | archive: structure role; hard delete: master only. The board that receives a product's providências cannot be archived (here or by `PATCH archived: true`) → `409 board_linked_to_product`; one that ever received them cannot be hard-deleted → `409 board_has_link_history` | — |
| `POST /boards/{id}/clone` | `{name, kind (default simulator), copy_cards}` | as `POST /boards` | `simulator` for `kind=simulator` |
| `GET /boards/{id}/view` | `?segment=upstream\|downstream\|unified` + `project_id, service_class, assignee_id, column_id, tag, q, is_blocked, demand_type, value_area, size, due_before, envelope_state, case_id, archived` | any | `unified_flow` for `segment=unified` |
| `GET /boards/{id}/wip` | — | any | — |
| `GET /boards/{id}/version` | — | any | — |
| `POST /boards/{id}/columns` | `{name, segment, kind, position?, wip_min?, wip_max?, group_id?, color?, description?}` | structure role | — |
| `PATCH /columns/{id}` | any subset | structure role | — |
| `DELETE /columns/{id}` | — | structure role | — |
| `POST /boards/{id}/columns/reorder` | `{ordered_ids: [...]}` — **every** column of the board | structure role | — |
| `POST /columns/{id}/drain` | `{to_column_id}` | structure role | — |
| `POST /boards/{id}/groups` | `{name, position?, wip_min?, wip_max?, color?, column_ids?}` | structure role | `wip_group` |
| `PATCH /groups/{id}` | any subset + `column_ids?` (replaces the span) | structure role | `wip_group` |
| `DELETE /groups/{id}` | — | structure role | `wip_group`; columns are ungrouped, not deleted |

*Structure role = `master`/`administrator`, or `simulator` on a simulator board (§3).*
A board created inside a product (`X-Produto`) belongs to that product.

`BoardOut` carries, besides the policies, `product_id`, `product_board` (an operational
board of a Pampufa product), `flow_locked` (§7.1), `read_only` (§14.4), `card_count`, and
— on `GET /boards/{id}` — its `groups` and `columns`. `GET /boards/{id}/view` returns
`{board, segment, groups, columns, cards, wip, commitment_column_id, delivery_column_id,
total_cards, generated_at}`. `GET /boards/{id}/wip`:

```json
{"board_id": "eea60bad-...",
 "columns": {"489c731b-...": {"count": 4, "min": 5, "max": 8, "state": "under", "label": "4/8"},
             "b9ee78d3-...": {"count": 8, "min": 3, "max": 8, "state": "at_limit", "label": "8/8"}},
 "groups": {"05203746-...": {"count": 20, "min": 6, "max": 24, "state": "ok"}},
 "flow": {"count": 35, "min": 10, "max": 45, "state": "ok", "label": "Whole flow"}}
```

`GET /boards/{id}/version` returns `{version, checked_at}`: a fingerprint of everything the board
screens show (board, groups, columns, every card row, archived ones too, and the counts of events,
movements, comments, tasks, blockers and hours). The screens poll it every ~10 s while the tab is
visible and reload `/view` and the open card only when `version` changes (23/11/2026), so a move
made by someone else, another tab or an integration shows up without a page reload.

**Templates** (`GET /boards/templates`, the `template` of `POST /boards`):
`upstream_downstream` (12 columns, the seeded shape; aliases `default`, `full`,
`unified` — the default), `simple_delivery` (aliases `simple`, `delivery`),
`discovery_only` (aliases `discovery`, `upstream`), `providencias` (the board every
Pampufa product gets: Entrada → A fazer → Fazendo (WIP 3) → Aguardando terceiro →
Entregue, NG12) and `blank` (aliases `empty`, `none`).

**Structural invariants**, enforced by `normalize_structure()` on every change: exactly
one commitment point (the first `downstream` column) and one delivery point (the last
`done` column), both derived; an `envelope` column only as the first upstream column, at
most one; `done` only downstream; upstream before downstream; groups contiguous.

**Drain.** `DELETE /columns/{id}` on a column that holds cards answers
`409 column_not_empty` with `details.card_count` and `details.drain_endpoint`.
`POST /columns/{id}/drain {to_column_id}` empties it into another column of the same
board in one transaction, writing a real movement and event per card; unlike a move, it
may go backward (recorded `is_backward`), ignores WIP maxima, opens sealed demands, and
stamps `committed_at`/`delivered_at` when it crosses those points. It answers
`{ok, moved, from_column_id, to_column_id, is_backward, committed, delivered,
opened_envelopes, message}`.

### 7.1 The flow lock (product boards, NG13)

On a **product board** (operational, `product_id` other than `nagare`) the structure is
editable **only until the first card**. Once the board holds — or ever held — a card,
`flow_locked` is `true` and these answer `409 flow_locked` (`details.change` names the
attempt):

- `POST /boards/{id}/columns`, `DELETE /columns/{id}`, `POST /boards/{id}/columns/reorder`,
  `POST /columns/{id}/drain`;
- `PATCH /columns/{id}` touching `segment`, `kind`, `group_id`, `position` or the
  commitment/delivery flags;
- `POST /boards/{id}/groups`, `DELETE /groups/{id}`, `PATCH /groups/{id}` with
  `column_ids`.

Still free on a locked board: WIP limits (column, group, flow), the SLE, and column and
group names, colours and descriptions (`PATCH /boards/{id}`, `PATCH /columns/{id}`,
`PATCH /groups/{id}` restricted to those fields). A different flow is a **new project**
linked to the product (`PUT /integration/product`, §14.4). Standalone and simulator
boards are never locked.

---

## 8. Projects & demands (BOARDS)

| Method & path | Query / body | RBAC | Flag |
|---|---|---|---|
| `GET /projects` | `?status=&q=&archived=` | any | — |
| `POST /projects` | `{name, key, color?, description?, status?, owner_id?, started_at?, target_date?}` | master/administrator | `multi_project` |
| `GET /projects/{id}` | — | any | — |
| `PATCH /projects/{id}` | any subset | master/administrator | `multi_project` |
| `DELETE /projects/{id}` | — | master/administrator | `multi_project` (archives) |
| `POST /projects/{id}/demands` | `{title, board_id?, description?, requester?, service_class?, value_area?, demand_type?, size?, due_date?, tags?, custom_fields?, sealed? (default true)}` | `manage_cards` | `envelope` |
| `POST /cards/{id}/open-envelope` | — | `manage_cards` | `envelope` |

`{id}` accepts the id or the project key (`NGR`). `GET /projects/{id}` adds a `demands`
breakdown by `envelope_state` and by flow position, plus the project's `cards[]`
(sealed first). `POST /projects/{id}/demands` without `board_id` uses the product's only
operational board (`400 board_id_required` when there are several). Opening an envelope
is a real move into the first upstream non-envelope column and returns the same
`MoveResult` as `POST /cards/{id}/move` (§10). A new demand and an opened envelope obey
the WIP maxima like a move (`409 wip_limit_exceeded`).

`ProjectOut` also carries `client_name`, `external_url` and `managed_elsewhere`: a project
kept by another service (the SR GJ's Projeto, created through
`PUT /integration/projects/{source}/{external_id}`, §14.2) keeps its name, status, dates
and archiving there, and `PATCH /projects/{id}` touching them answers
`409 project_managed_elsewhere` (`details.url` opens it); colour, description and owner
still change here.

---

## 9. Cards, tasks, blockers, comments (CARDS)

Reads need any valid token; writes go through the board-write rule (§3), and a
read-only board refuses them (`409 board_read_only`).

| Method & path | Query / body | Notes |
|---|---|---|
| `GET /cards` | `?board_id=&project_id=&service_class=&assignee_id=&segment=&column_id=&tag=&q=&is_blocked=&demand_type=&value_area=&size=&case_id=&due_before=&archived=&envelope_state=&in_flight=&sort=&order=&limit=(≤1000, default 200)&offset=` | `project_id=none`, `assignee_id=unassigned`; `service_class`, `column_id`, `tag` take comma lists; `sort ∈ {created_at, aging, due_date, title, key, position}`, `order ∈ {asc, desc}`; `total` is the filtered count |
| `POST /cards` | `{board_id, title, column_id?, project_id?, description?, service_class?, assignee_id?, requester?, size?, value_area?, demand_type?, due_date?, envelope_state?, tags?, custom_fields?, case_id?, case_label?, client_id?, client_name?}` | Writes the creation movement (`from_column_id: null`) and event; created at/after the commitment column → `committed_at` at once. Without a project the key is the product's (`SRADV-`, the key of the project that receives the providências; the product's initials before there is one); the standalone Nagare keeps the board's three letters. `value_area` empty → the product's first card type. The target column's WIP maxima apply as on a move (`409 wip_limit_exceeded`; `expedite` bypasses the column maximum only) |
| `GET /cards/{card_id}` | — | Id or key (`NGR-104`). Card + `tasks[]`, `blockers[]`, `comments[]`, `movements[]`, `intervals[]`, `flow{}` (touch/wait/efficiency), `can_edit` |
| `PATCH /cards/{card_id}` | subset of `title, description, project_id, service_class, assignee_id, requester, size, value_area, demand_type, due_date, position, tags, custom_fields, archived, case_id, case_label, client_id, client_name` | One `card_events` row per changed field (the case and its client: one `case` event; `case_id: null` unlinks); `column_id` → `400 field_not_editable` (use `/move`). `archived: true` on a providência card discards it (webhook, §14.3) |
| `DELETE /cards/{card_id}` | — | Archives (soft, idempotent); same webhook rule |
| `GET /cards/{card_id}/tasks` | — | Checklist in display order (`{items, total}`) |
| `POST /cards/{card_id}/tasks` | `{title, assignee_id?, position?, is_done?}` | |
| `PATCH /tasks/{task_id}` / `DELETE /tasks/{task_id}` | `{title?, assignee_id?, position?, is_done?}` | Completing records `task_completed` |
| `POST /cards/{card_id}/block` | `{reason, category}` | `category ∈ {dependency, external_vendor, waiting_on_customer, technical, people, compliance, environment, other}`; `409 card_already_blocked` |
| `POST /cards/{card_id}/unblock` | — | Closes every open blocker; `409 card_not_blocked` |
| `GET /cards/{card_id}/blockers` | — | History with `duration_seconds` / `is_open` |
| `GET /cards/{card_id}/comments` / `POST /cards/{card_id}/comments` | `{body}` | |
| `PATCH /comments/{comment_id}` / `DELETE /comments/{comment_id}` | `{body}` | Author or master/administrator (`403 comment_not_yours`) |

Inside a product, assignees come from `GET /integration/people` (§14.2). Unarchiving a
card (`PATCH archived: false`) checks the WIP maxima of its column again.

**The card's own page (24/11/2026).** Every card has a page addressed by its key, which is
born with the card and never changes: `/nagare/card/<KEY>` on the standalone Nagare
(`/nagare/<product>/card/<KEY>` for a Pampufa product's card, which switches the product
picker), and `/grupo-sr/<product>/tarefas/cartao/<KEY>` in the Tarefas module. The page
reads `GET /cards/{key}` like the drawer does; the board's old drawer links
(`/board/<board id>?cartao=<card id>`) still open. This is the `card_url` the API hands out
(§14.3).

### 9.1 Card types (PPLAT-312)

`value_area` is a key of the product's list. A product without a
list of its own uses the software one (`feature, defect, risk, debt, compliance`, labelled by
the screens); SR Adv and SR GJ start with `prazo, peticao, audiencia, diligencia,
administrativo` (`ng_0007`). A new card takes an active type (`400 invalid_card_type`
otherwise); a card keeps the type it has even after it is archived.

| Method & path | Body | Notes |
|---|---|---|
| `GET /card-types` | — | `{product_id, items: [{key, name, position, archived, builtin}], can_edit}`; `name: null` → the screens' label for the key |
| `POST /card-types` | `{name}` | `manage_boards`. The key comes from the name (no accents, `_`), never changes; `409 card_type_name_taken` |
| `PATCH /card-types/{key}` | `{name?, archived?}` | `manage_boards`; `409 card_type_last_active` |
| `POST /card-types/order` | `{keys: [...]}` (every key once) | `manage_boards`; `400 card_type_order_mismatch` |
| `DELETE /card-types/{key}` | — | `manage_boards`; a type some card has → `409 card_type_in_use` (archive it instead) |

The first edit of a product on the default list copies it into the product's own rows.
`can_edit` is `true` for the roles with `manage_boards`; an unknown key answers
`404 card_type_not_found`, an empty name `400 card_type_name_required`.

### 9.2 The SR Adv case (PPLAT-310)

`case_id` is the case's id in the `sr-adv` service;
`case_label`, `client_id` and `client_name` are what the case picker showed (the client comes
from the case); `case_url` is set by the server (`<NAGARE_URL_TELAS>/<product>/processos/<id>`).
Only inside a Pampufa product (`400 case_link_unavailable` in the standalone Nagare). The
board's "Tarefas deste caso" link is `/grupo-sr/<product>/tarefas/quadro?caso=<case id>`: the
screen opens the product's board with `GET /boards/{id}/view?case_id=…`. The screens find the
cases in the `sr-adv` API through the gateway with the person's own token
(`GET /api/v1/p/<product>/sr-adv/casos?busca=&limite=20`, `GET …/casos/{id}`).

### 9.3 Hours on a card (GJ-4)

Router `backend/app/routers/horas.py` (tag `hours`), table `time_entries` (`ng_0006`). Both
modes; no feature flag. Whoever moves cards logs **their own** hours; the entry keeps the
card's project, so the hours of a project can be read without the card.

| Method & path | Body / query | RBAC | Notes |
|---|---|---|---|
| `GET /cards/{id}/time` | — | `read_boards_metrics` | `{items: [{id, card_id, card_key, card_title, project_id, user_id, user_name, worked_on, minutes, description, created_at}], total_minutes}`, newest day first |
| `POST /cards/{id}/time` | `{worked_on: "YYYY-MM-DD", minutes: 1–1440, description?: ≤2000 chars}` | `manage_cards` + the board-write rule (§3) | `201` with the entry. A read-only board → `409 board_read_only`; an archived card → `409 card_archived` |
| `DELETE /time/{id}` | — | `manage_cards` | `{ok: true}`. Only the author, or a master/administrator (`403 not_yours`); `404 time_not_found` |

`{id}` is the card id or its key (`NGR-104`), like the other card routes; neither
found → `404 card_not_found`. The hours count in `GET /metrics/people` (§11.2), in the
billing routes of the integration (`GET /integration/time`, `/time/by-external`, §14.2),
in `GET /boards/{id}/version` (§7) and in the Hours and Project status reports (§15).

---

## 10. Movement & history (CARDS)

`POST /cards/{card_id}/move` is the endpoint every metric replays. SPEC §6 still holds:

| Rule | Behaviour |
|---|---|
| Forward only, in the unified order (all upstream before all downstream) | `409 backward_move_blocked` |
| `force: true` | Only `master`/`administrator`; records `is_backward` and a `backward_override` event |
| Entering the commitment column | Stamps `committed_at` once |
| Entering the delivery column | Stamps `delivered_at` once; a providência card queues the delivery webhook in the same transaction (§14.3) |
| Sealed envelope | `409 envelope_sealed` |
| Column / group / flow WIP maximum | Checked inside the write transaction → `409 wip_limit_exceeded` (`details.policy`) |
| `expedite` | Bypasses the **column** maximum only; reported as `wip_override: true` |
| WIP minimum | Never blocks; `warnings: ["... is starving ..."]` |
| `seconds_in_from_column` | From the previous movement (or creation) |

| Method & path | Query / body | Flag |
|---|---|---|
| `POST /cards/{card_id}/move` | `{to_column_id, note?, force?, source?}` → `MoveResult` | — |
| `GET /cards/{card_id}/history` | `?order=asc\|desc` — merged movements + events, `timeline[]` with readable labels, per-column `dwell[]`, `totals` | `card_history` |
| `GET /cards/{card_id}/movements` | raw movement rows | — |
| `GET /cards/{card_id}/events` | raw audit events | `card_history` |

`source ∈ {ui, api, mcp, seed, simulation}` labels the row (default `ui`, or `api` for
the `system` role). `MoveResult`:

```json
{"card": {"id": "0ef930cf-...", "column_id": "3913fdaf-...", "key": "PAY-146", "...": "..."},
 "movement": {"id": "b409dbf1-...", "from_column_id": "883c0c25-...", "to_column_id": "3913fdaf-...",
   "moved_at": "2026-08-16T13:06:49Z", "seconds_in_from_column": 435097, "note": "analysis complete",
   "is_backward": false, "source": "ui", "from_column_name": "Analysis Done", "to_column_name": "Build Done"},
 "wip_override": false, "warnings": [],
 "wip": {"board_id": "eea60bad-...", "columns": {"...": "..."}, "groups": {"...": "..."}, "flow": {"...": "..."}},
 "committed": false, "delivered": false,
 "from_column": {"id": "883c0c25-...", "name": "Analysis Done", "kind": "queue"},
 "to_column": {"id": "3913fdaf-...", "name": "Build Done", "kind": "queue"},
 "seconds_in_from_column": 435097, "duration_in_from_column": "5d", "is_backward": false}
```

---

## 11. Metrics & KMM (METRICS)

Read-only for every role (`read_boards_metrics`), in both modes. Every route but the last
two of the table takes the common filters
`?from=&to=&project_id=&service_class=&segment=` (`segment` defaults to `unified`),
resolved into one snapshot per request. Labels and interpretations come in the caller's
language (§4.1).

| Method & path | Extra params | Flag |
|---|---|---|
| `GET /boards/{id}/metrics/summary` | — | `metrics_basic` |
| `GET /boards/{id}/metrics/throughput` | `?period=day\|week\|month&rolling=1..26` | `metrics_basic` |
| `GET /boards/{id}/metrics/distribution` | `?metric=customer\|system\|discovery&bin_days=` | `metrics_basic` |
| `GET /boards/{id}/metrics/cfd` | `?interval=auto\|day\|week` | `metrics_advanced` |
| `GET /boards/{id}/metrics/scatter` | `?metric=customer\|system\|discovery` | `metrics_advanced` |
| `GET /boards/{id}/metrics/aging` | — | `metrics_advanced` |
| `GET /boards/{id}/metrics/flow-efficiency` | — | `metrics_advanced` |
| `GET /boards/{id}/metrics/blockers` | — | `metrics_advanced` |
| `GET /boards/{id}/metrics/demand` | — | `metrics_advanced` |
| `GET /boards/{id}/metrics/littles-law` | — | `metrics_advanced` |
| `GET /boards/{id}/metrics/kmm` | — | `kmm_dashboard` |
| `GET /boards/{id}/metrics/forecast` | `?question=when\|how_many&backlog_size=&days=&sample_weeks=` (does **not** take the common filters) | `monte_carlo` (§11.1) |
| `GET /metrics/people` | `?from=&to=` only (§11.2) | — |

`summary` returns `{board_id, from, to, metrics: {<kpi>: {value, unit, trend_pct,
sample_size, previous_value, low_confidence, label, higher_is_better}}, percentiles:
{p50, p70, p85, p95}, filters}`; any sample under 8 is flagged `low_confidence`.
`littles-law` returns `avg_wip`, `throughput_per_week`, the average lead times,
`predicted_wip`, `coherence_ratio`, arrivals/departures and a plain-language `verdict`.
`demand` returns the class-of-service mix over deliveries and over arrivals, demand type
and value area mixes, `failure_demand_pct`, `lead_time_by_class`, `flow_debt`,
`per_project` and `due_date_performance`.

**KMM.** `GET /boards/{id}/metrics/kmm` returns `current_level`,
`overall_readiness_pct` and the seven levels ML0–ML6, each
`{level, name, focus, metrics: [{key, label, available, value, why}], readiness_pct}`
(SPEC §7.2). Inside the Pampufa (`via: pampufa`) the ML5 practices that rely on the
simulator — "WIP-experiment outcomes" and "capability comparison across boards" — point
to **the Nagare simulator** instead of a screen of the Tarefas module, which has none
(A9-d); they still count toward the level.

### 11.1 The Monte Carlo panel (read only)

`GET /boards/{id}/metrics/forecast` asks the simulator's two Monte Carlo questions on one
board **without saving a run**, so every role that reads metrics can ask it (in both
modes), not only the `simulator_boards` roles of §12. It samples the board's own
delivered-per-week history (non-archived cards) with the same trials (10,000) and seed
(`20260816`) as `POST /simulator/runs`, so the panel and the simulator give the same answer.

| Query | Default | Range |
|---|---|---|
| `question` | `when` | `when` ("how long for `backlog_size` items?") or `how_many` ("how many items in `days` days?") |
| `backlog_size` | `10` | 1–10,000 (`when`) |
| `days` | `28` | 1–3,650 (`how_many`) |
| `sample_weeks` | all history | 1–520 weeks to sample |

The answer is the engine's own result: `{kind: monte_carlo_when | monte_carlo_how_many,
question, trials, seed, unit (weeks | items), start_date, samples: {weeks, values, mean,
stdev, cv, min, max, median, distinct}, percentiles, levels, histogram}`, plus
`backlog_size` and `dates` for `when`, or `days`, `weeks`, `target_date` and
`target_date_label` for `how_many`. A history the engine cannot use answers
`400 forecast_failed`; a value out of range, `422 validation_error`.

### 11.2 People, projects and clients (D45)

`GET /metrics/people?from=&to=` (router `horas.py`; `read_boards_metrics`, both modes, no
flag) reads the current product in a period — `from` and `to` inclusive, in the product's
time zone; default the last 30 days ending today:

```json
{"from": "2026-10-27", "to": "2026-11-25",
 "people":   [{"user_id", "name", "delivered", "on_time", "late", "wip", "arrivals", "minutes"}],
 "projects": [{"project_id", "name", "key", "client_name", "delivered", "...": "same counters"}],
 "clients":  [{"client_name", "projects", "delivered", "...": "same counters"}],
 "total_minutes": 5400}
```

`delivered` and `arrivals` are cards delivered and created in the period; `on_time` /
`late` split the delivered **fixed-date** cards by their due day (end of day in the
product's zone); `wip` is committed and not delivered now; `minutes` are the hours logged
in the period. Rows without any of these are left out; a card without assignee, project or
client lands in a "Sem responsável" / "Sem projeto" / "Sem cliente" row (`user_id` or
`project_id` `null`). Each list is sorted by `minutes`, most first. The gestor's
cross-product version is `GET /integration/overview/metrics` (§14.2).

---

## 12. Simulator (SIM)

Router `backend/app/routers/simulator.py`, the HTTP face of
`backend/app/flow/forecast.py`. **Standalone Nagare only** (decision A9-c): a Pampufa
token answers `403 standalone_only`; runs are also read only from boards of the current
product. Every route needs the `simulator` flag (business) and
the `simulator_boards` capability (`master`, `administrator`, `simulator`); the two
Monte Carlo kinds also need `monte_carlo`. The screens offer the simulator only on the
**standalone Nagare** (:8770); the Tarefas module inside the Pampufa has no simulator
screen (A9-c).

| Method & path | Body / query | Notes |
|---|---|---|
| `GET /simulator/boards` | `?q=&archived=` | Simulator boards of the product, newest first, with `card_count` |
| `POST /simulator/boards` | `{name, source_board_id?, template? (default\|blank\|simple), copy_cards?}` | With `source_board_id`: deep clone (structure, policies, optionally cards and history); else from a template. `201 BoardOut` |
| `POST /simulator/boards/{id}/reset` | — | Deletes the board's cards and their history; structure untouched |
| `POST /simulator/boards/{id}/generate` | `{weeks (1–104, default 12), arrival_rate (default 8/week), class_mix, seed (default 20260816), wip_limits, reset (default true)}` | Runs the deterministic pull simulator and writes real cards and movements (`source = simulation`), anchored so the last simulated day is now |
| `POST /simulator/runs` | `{board_id, kind, name?, params}` | Computes and stores `{params, results}`; `board_id` may be an operational board (runs never write cards). `201 SimRunOut` |
| `GET /simulator/runs` | `?board_id=&kind=` | Saved runs, newest first |
| `GET /simulator/runs/{id}` | — | One run |
| `POST /simulator/compare` | `{run_ids: [2–8 ids], baseline_run_id?}` | `{runs, metrics, rows, deltas, verdict, baseline_run_id}`; deltas are measured against `baseline_run_id` (default: the first id), which must be one of `run_ids` (`400 compare_failed` otherwise) |

Write paths verify the board is `kind = simulator` (`400 not_a_simulator_board`), so an
operational board's history is never touched.

**Run kinds and their `params`** (all deterministic given `seed`, default `20260816`):

| `kind` | Main params |
|---|---|
| `monte_carlo_when` | `backlog_size` (required), `trials` (default 10,000), `throughput_samples` or `sample_weeks` (default: the board's own weekly throughput), `start_date` |
| `monte_carlo_how_many` | `days` (required), `trials`, `throughput_samples` / `sample_weeks`, `start_date` |
| `wip_experiment` | `weeks` (16), `arrival_rate` (8), `class_mix`, `size_mix`, `configs`, `baseline_flow_wip_max` (omitted: the board's `wip_max`; `null`: no flow cap), `target_utilization` (0.85), `variability` (0.6), `warmup_weeks` |
| `class_of_service_mix` | `weeks`, `arrival_rate`, `expedite_shares`, `class_mix`, `size_mix`, `wip_limits`, `flow_wip_max`, `target_utilization`, `variability`, `warmup_weeks`, `expedite_preemption` (true) |
| `replay` | `policies`, `weeks`, `use_observed_service_times` (true), `target_utilization`, `baseline_flow_wip_max`, `class_mix`, `warmup_weeks` |

A missing required parameter answers `400 invalid_simulation_params`; a size parameter out
of range (`trials` 1–100,000, `backlog_size` 1–10,000, `days` 1–3,650, `weeks` 1–104,
`warmup_weeks` 0–104, `sample_weeks` 1–520, `arrival_rate` 0.01–200, at most 520
`throughput_samples`), `400 simulation_params_out_of_range` before any work; an input the
engine refuses, `400 simulation_failed`. The WIP experiment's verdict compares against
the "no WIP limits" row (or, without one, the row with the highest average WIP) and says
so when no limit shortens lead time without costing throughput (A9-b).

---

## 13. Integrating with Nagare (MCP)

`mcp/nagare_mcp.py` is a dependency-free (stdlib `json`/`sys`/`os`/`urllib.request`)
**Model Context Protocol** server: JSON-RPC 2.0 over stdio, protocol `2024-11-05`. It is
a client of the REST API above, so every policy (WIP, forward-only, RBAC, flags) applies
to the agent exactly as to a person. It authenticates with a static token or with an
e-mail/password it exchanges lazily (and once more, automatically, on a `401`). It sends
no `X-Produto`, so it works in the standalone product (`nagare`).

| Tool | Calls | Notes |
|---|---|---|
| `list_boards` | `GET /boards` | `kind`, `include_archived` |
| `get_board` | `GET /boards/{id}` + `GET /boards/{id}/wip` | Structure and live WIP in one object |
| `list_cards` | `GET /cards` | Full filter set, `limit` ≤ 500 |
| `get_card` | `GET /cards/{id}` (+ `/history` with `include_history`) | |
| `create_card` | `POST /cards` | |
| `move_card` | `POST /cards/{id}/move` | Surfaces `wip_override` / `committed` / `delivered` / `warnings` |
| `open_envelope` | `POST /cards/{id}/open-envelope` | |
| `list_projects` | `GET /projects` | |
| `create_demand` | `POST /projects/{id}/demands` | |
| `get_metrics_summary` | `GET /boards/{id}/metrics/summary` | |
| `get_kmm_status` | `GET /boards/{id}/metrics/kmm` | |
| `run_forecast` | `POST /simulator/runs` | Works: `kind = monte_carlo_when` with `items` (sent as `backlog_size`), or `monte_carlo_how_many` with `days`, `weeks` (× 7) or `target_date` (days from `start_date`, default today), all sent as `days`. Needs a role with `simulator_boards` (master, administrator, simulator) and the `simulator` + `monte_carlo` flags. See §17 for the `weeks` / `target_date` horizons |

REST errors become tool errors prefixed with the code (`[wip_limit_exceeded] ...`) plus a
canned `Hint:` for the common ones (`_HINTS` in the file). Card writes are attributed to
the authenticated user with `card_movements.source = "mcp"`.

Start the app first, then register the server:

```bash
python run.py                          # http://127.0.0.1:8770

claude mcp add nagare \
    --env NAGARE_URL=http://127.0.0.1:8770 \
    --env NAGARE_EMAIL=nagare.master@grupo-sr.com.br \
    --env NAGARE_PASSWORD=teste12345 \
    -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py
```

Or a `.mcp.json` (the same shape works in Claude Desktop):

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

Environment: `NAGARE_URL` (default `http://127.0.0.1:8770`), `NAGARE_TOKEN` (wins over
e-mail/password), `NAGARE_EMAIL` / `NAGARE_PASSWORD`, `NAGARE_TIMEOUT` (seconds,
default 30).

```bash
python mcp/nagare_mcp.py --selftest       # full round trip against a running server; exit 0 on success
python mcp/nagare_mcp.py --print-tools    # dump the tool schemas, no server needed
```

`--selftest` exercises every tool, `run_forecast` included, and passes against the seeded
demo. The `mcp_server` flag (business) describes the integration commercially; the MCP
process itself only needs a token whose role allows each call.

---

## 14. Integration with the Pampufa platform

Phases N1–N3 (decisions NG1–NG17 in the vault). The Nagare is a separate system that the
platform reaches **only by API** (NG1): **the Pampufa calls, the Nagare answers back**
(NG10). Router `backend/app/routers/integration.py`, logic in
`backend/app/integration.py`, network access in `backend/app/pampufa.py`.

### 14.1 Who may call

| Routes | Caller |
|---|---|
| `POST /integration/providencias`, `GET /integration/providencias/{id}`, `POST /integration/providencias/{id}/resolved-at-origin`, `PUT /integration/projects/{source}/{external_id}`, `GET /integration/time`, `GET /integration/time/by-external` | `system` only: a platform **service token** (`sub: servico:<name>`, role `system` in the product) with `X-Produto`. Anyone else → `403 insufficient_role` |
| `GET /integration/overview`, `GET /integration/overview/metrics`, `GET /integration/people`, `GET /integration/product` | any role in the product (`read_boards_metrics`) |
| `GET /integration/products` | any valid token |
| `POST /integration/product/board` | `manage_cards` (master, administrator, editor, system) |
| `PUT /integration/product` | `master` / `administrator` (in practice `nagare.admin` of the product) |

All of them work in both modes; the product is the one of the request (§1.3).

### 14.2 Routes

| Method & path | Body | Answer |
|---|---|---|
| `POST /integration/providencias` | `{providencia_id, servico, tipo, titulo?, motivo?, url?, produto_nome?, responsavel?: {sub?, email?, nome?}, prazo?, classe_servico?, entidade?, entidade_id?}` | A `url` that is a case page of the product (`/grupo-sr/<product>/processos/<id>`), or `entidade: sa_caso` with `entidade_id`, links the card to that case. `201 {created: true, card}`; the same `providencia_id` again → `200 {created: false, card}` (idempotent, never a second card) |
| `GET /integration/providencias/{providencia_id}` | — | `{state, card}`; `state ∈ {open, resolved_at_origin, delivered, discarded}` |
| `POST /integration/providencias/{providencia_id}/resolved-at-origin` | `{em?, origem?}` | `{changed, state, card}` |
| `GET /integration/overview` | — | `{stages, delivered_window_days, products: [...]}` |
| `GET /integration/overview/metrics` | `?from=&to=` (days, inclusive; default the last 30 days) | `{products: [{product_id, delivered, on_time, late, wip, minutes, from, to}]}` — one row per linked product the caller can read, each the sum of its `GET /metrics/people` (§11.2) |
| `GET /integration/products` | — | `{items: [{id, role, project: {id, key, name} \| null, board: {id, name} \| null}]}` — the products the caller can open and the role held in each: on the local login `nagare` first, then every linked product the local role reaches (§1.3); through the Pampufa, the linked products where the token holds a role |
| `GET /integration/people` | — | `{source, people: [{id, name, email, sub, roles}]}` |
| `GET /integration/product` | — | `{product_id, link, history, projects_new_url}` |
| `POST /integration/product/board` | `{produto_nome?}` | Creates the product's project and `providencias` board now, without waiting for a providência (SR GJ 4.3: the team writes tasks first) — the same board the providências will land on. `201` with the `GET /integration/product` shape; `200` when it already existed (idempotent) |
| `PUT /integration/product` | `{project_id, board_id, motivo}` | `{product_id, link, history, projects_new_url, moved_cards}` |
| `PUT /integration/projects/{source}/{external_id}` | `{name, key (2–8 letters/digits), color?, status? (discovery\|active\|on_hold\|done\|cancelled), client_name?, description?, started_at?, target_date?, url?, novo_url?}` | GJ-1: creates or updates the project another service keeps (the SR GJ's Projeto), found by `(source, external_id)` in the product; `cancelled` archives it. `url` is where a person opens it (`external_url`); `novo_url` is where "Novo projeto" goes in this product (`projects_new_url`). `{project_id, key}`; a key used by another project → `409 project_key_taken`. Retries are serialised by an advisory lock |
| `GET /integration/time` | `?from=&to=` (required) `&project_id=` (repeatable) | GJ-4 billing: the hour entries of those projects in the period, with the card (`{items: [entry as in §9.3], total_minutes}`) |
| `GET /integration/time/by-external` | `?source=` (required) `&from=&to=` | GJ-6, for the Financeiro reports: `{items: [{external_id, minutes}]}` — minutes logged on each project kept by `source`, keyed by the source's id |

`card` in the providência answers is a summary: `{id, key, title, board_id, column,
column_kind, delivered_at, archived, tags, assignee: {id, name, email, sub} | null,
url}`. A providência of another product answers `404 providencia_not_found`.

**A providência becomes a card** (NG8, NG12). The first providência of a product creates
its project (key from the product id: `sr-adv` → `SRADV`) and board (template
`providencias`) and links them to the product. The card lands in the board's first
column (Entrada) with `title` = `titulo` (else `motivo`, else `tipo`), the description
from `motivo` plus "Resolver em: `url`", tags `providencia`, `<servico>`, `<tipo>`,
`requester = servico`, `due_date = prazo`, `service_class = classe_servico` (default
`standard`), the assignee from `responsavel` (by Pampufa `sub`, then e-mail; created
without a password when new) and `custom_fields.pampufa = {providencia_id, servico,
tipo, url}`.

**Resolved at origin** (NG11). The cause vanished in the owning service: the card gets a
comment ("Resolvido na origem em <date> (<origem>). Confira e entregue o cartão...") and
the tag `resolvido-na-origem`, and the reference goes to `resolved_at_origin`. **The card
never moves by itself**: whoever owns it confirms and delivers it. Repeating the call, or
calling after the card closed, answers `changed: false`.

**Overview** — the PO's consolidated board. Every linked product's board read in five
stages — `entrada` (upstream), `a_fazer` (downstream queues before the first activity),
`fazendo` (activities), `aguardando` (queues after an activity), `entregue` (done) — one
lane per product with `counts`, `wip` per stage and its open cards plus those delivered
in the last 14 days, each card once. Sealed envelopes keep their content hidden. Read
only. `pampufa.gestor` (and the local `master`) see every product; anyone else, the
products where they hold a `nagare.*` role.

**People** — the assignee picker. In a platform product, the people with a `nagare.*`
role there, straight from the Pampufa (`GET {NAGARE_PAMPUFA_URL}/api/v1/integracao/
nagare/pessoas?produto=` with the Nagare's service credential; mirrored for 60 seconds),
each given a Nagare user so they can be assigned before ever opening the Nagare
(`source: "pampufa"`). On the standalone Nagare, its own active users
(`source: "local"`).

### 14.3 The webhook back to the Pampufa

A card that stands for a providência tells the Pampufa when it closes. The notification
is written to `webhook_outbox` **in the same transaction** as the change (it exists if
and only if the change does) and a background task posts it with the Nagare's service
credential (`NAGARE_PAMPUFA_CLIENTE_ID` / `NAGARE_PAMPUFA_CLIENTE_SEGREDO`) to:

```
POST {NAGARE_PAMPUFA_URL}/api/v1/integracao/nagare/eventos
```

| `evento` | When | Payload (besides `evento` and `product_id`) |
|---|---|---|
| `cartao.entregue` | The card enters the delivery column (`POST /cards/{id}/move`) | `providencia_id, cartao_id, cartao_chave, resolvido_na_origem, em` |
| `cartao.arquivado` | The card is archived (`DELETE /cards/{id}` or `PATCH archived: true`): the providência is discarded | same as above |
| `cartao.movido` | A flow swap recreated the card on the new board (§14.4) | `providencia_id, cartao_id, cartao_chave, cartao_url, cartao_anterior_chave, em` |

Delivery rounds run every **`NAGARE_WEBHOOK_INTERVALO`** seconds (default 5), up to 50
notifications per round. A notification the Pampufa does not accept is retried with
backoff (5s, doubling, at most 10 minutes) until it is confirmed; `attempts` and
`last_error` stay on the row. The loop runs only when `NAGARE_PAMPUFA_URL` is set.

**`card_url`** — the link the Pampufa shows for a card (`card.url` above, and
`cartao_url` in `cartao.movido`): the card's own page, by its key (24/11/2026, §9):

- a platform product's card, with `NAGARE_URL_TELAS` set:
  `{NAGARE_URL_TELAS}/{product}/tarefas/cartao/{key}` (the Tarefas module);
- a platform product's card without it: `{NAGARE_URL_PUBLICA}/{product}/card/{key}`;
- a standalone card: `{NAGARE_URL_PUBLICA}/card/{key}`.

Links sent before in the old form (`.../quadro/{board_id}?cartao={card_id}`,
`.../board/{board_id}?cartao={card_id}`) still open the card's drawer.

### 14.4 A new flow is a new project (NG14)

`PUT /integration/product {project_id, board_id, motivo}` links another project and
board of the same product (`404` otherwise) in place of the current ones. The board must
be an active operational board (`409 board_not_linkable`) and not the one already linked
(`409 already_linked`). Every open card (not delivered, not archived) of the old board is
**recreated in the new board's first column**, keeping its providência (and queuing a
`cartao.movido`); the old card gets a comment with the reason and is archived; the old
board becomes **read-only** (`read_only: true`, every write `409 board_read_only`) while
its history and metrics stay. Each link and unlink is kept in `history`
(`{action: linked | unlinked, reason, moved_cards, at, by}`).

### 14.5 Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NAGARE_PAMPUFA_URL` | empty (standalone) | Base URL of the `pampufa` service; turns the integration on |
| `NAGARE_PAMPUFA_EMISSOR` | `pampufa` | Expected token issuer |
| `NAGARE_PAMPUFA_JWKS_URL` | `${NAGARE_PAMPUFA_URL}/.well-known/jwks.json` | Where the public keys are |
| `NAGARE_PAMPUFA_CLIENTE_ID` / `NAGARE_PAMPUFA_CLIENTE_SEGREDO` | `nagare` / empty | The Nagare's service credential at the Pampufa (webhook, people) |
| `NAGARE_WEBHOOK_INTERVALO` | `5` | Seconds between webhook rounds |
| `NAGARE_URL_TELAS` | empty | The platform's screens (`.../grupo-sr`), for `card_url` |
| `NAGARE_URL_PUBLICA` | `http://127.0.0.1:8770/nagare` | The standalone Nagare, `/nagare` included, for `card_url` |

`.env.example` has the development values.

---

## 15. Reports (standalone)

Router `backend/app/routers/reports.py`, templates in `backend/app/reports/modelos.py`, the
Document in `reports/documento.py`, the spreadsheet in `reports/planilha.py` (wave 1 of the
Reports plan, 24/11/2026). **Standalone Nagare only** (decision R4): a Pampufa token
answers `403 standalone_only`. Every route needs the `reports` flag (Business) and the
`read_reports` capability (every role, R21); the portfolio also needs `reports_portfolio`
(Enterprise). A local master with `X-Produto` reads that product's reports. The screen is
`/nagare/reports` (`/nagare/reports/<template>`).

| Method & path | Query | Answer |
|---|---|---|
| `GET /reports/templates` | — | `{items: [{id, code, title, description, scope, flag, available}]}` — the catalogue in the caller's language; `available` is whether the template's flag is effective |
| `GET /reports/{template}` | the common query below | The report, live, as a **Document** |
| `GET /reports/{template}/export` | `format=xlsx\|csv` (default `xlsx`) + the common query | The same Document as a file, `Content-Disposition: attachment; filename="rep-01-<scope>-<first day>.xlsx"` |

**Common query.** `period` = `week` (default), `month` or `custom`; `date` = a day inside
the week or month (default: the last **complete** week or month); `from` / `to` for
`custom` (default: the 30 days ending today; at most 400 days); and the scope:
`board_id`, `project_id`, or `project_ids` (comma-separated). Weeks run Monday to Sunday
and months are calendar months, in the product's time zone (R22). Every report compares
with the **previous period**: the week or month before, or an interval of the same
length.

**Templates** (`TEMPLATES`):

| `id` | Code | Scope | Flag | What it reads |
|---|---|---|---|---|
| `delivery_review` | REP-01 | `board_id` (required) | `reports` | A board's week or month against the previous one: deliveries and pace, due dates and aging, blockers, classes of service, Little's Law |
| `project_status` | REP-02 | `project_id` (required, the id) | `reports` | Delivered and remaining, risks (blocked and aging cards), hours logged |
| `hours` | REP-03 | `project_ids` (optional; none = every project) | `reports` | Hours by client and by project against the previous period — the basis for billing |
| `portfolio` | REP-04 | none | `reports_portfolio` | Every product the caller opens (`GET /integration/products`), side by side: throughput and trend, lead time, WIP, aging, blocked |
| `risks` | REP-05 | `board_id` (required) | `reports` | Blocker causes by weight, blocked time, cards blocked now, recurring causes |
| `waiting` | REP-11 | `board_id` (required) | `reports` | Time in each column, flow efficiency, the column that holds the flow most |
| `policy_exceptions` | REP-13 | `board_id` (required) | `reports` | Expedite cards that passed a WIP limit, WIP limit changes, class-of-service changes — only what happened after `ng_0008` (R14) |

**The Document** (`FORMAT_VERSION` 1) is the one JSON the screen draws and the
spreadsheet reads, so the numbers are the same in both:

```json
{"template": "delivery_review", "code": "REP-01", "title": "...", "subtitle": "...",
 "format_version": 1, "product_id": "nagare",
 "params": {"period": "week", "from": "2026-11-16", "to": "2026-11-22",
            "board_id": "...", "project_id": null, "project_ids": null},
 "period": {"kind": "week", "from": "2026-11-16", "to": "2026-11-22", "label": "...", "timezone": "UTC"},
 "previous": {"from": "2026-11-09", "to": "2026-11-15", "label": "..."},
 "generated_at": "2026-11-25T12:00:00Z",
 "scope": {"board": {"id": "...", "name": "...", "sle_days": 14, "sle_percentile": 85}},
 "sections": [{"id": "...", "title": "...", "description": null, "blocks": ["..."]}]}
```

Blocks: `kpis` (`items: [{key, label, value, unit, previous, trend_pct, higher_is_better,
low_confidence, hint}]`), `table` (`title, columns: [{key, label, unit}], rows, empty`),
`bars` (`title, unit, items: [{label, value, note?}], empty`) and `note` (`tone, text`).
Units are machine keys: `days`, `percent`, `count`, `cards/week`, `hours`, `ratio`,
`date`, `datetime`, `text`, `card` (a `card` cell is `{key, title}`). Labels and texts come
in the caller's language (§4.1).

**Export** (decision R17, `openpyxl`): `xlsx` has one sheet per section (the header —
title, period, previous period, generation time — on top, numbers kept as numbers);
`csv` is UTF-8 with BOM and follows the caller's language — in pt-BR `;` between fields
and `,` decimals, as a Brazilian Excel expects. An unknown `format` is
`422 validation_error`.

**Errors.** Unknown template → `400 report_unknown`; bad `period` → `400 report_bad_period`
(also an interval over 400 days); a date that is not `YYYY-MM-DD` → `400 report_bad_date`;
a missing scope → `400 report_board_required` / `report_project_required`; a project of
another product → `404 project_not_found` (also when none of the `project_ids` is
known); a board of another product → `404 board_not_found`; `reports_portfolio` off for the portfolio →
`403 feature_disabled` / `plan_upgrade_required`.

---

## 16. Notifications: the standalone bell

Router `backend/app/routers/notifications.py`, table `notifications` (`ng_0008`; decision
R23 of the Reports plan, 24/11/2026). **Standalone Nagare only**: inside the Pampufa the
platform's own bell does the job, and a Pampufa token answers `403 standalone_only`. Any
local identity reads and marks **its own** notices; no flag.

| Method & path | Query | Answer |
|---|---|---|
| `GET /notifications` | `limit` (1–100, default 30) | `{items: [{id, product_id, kind, title, body, link, created_at, read_at}], unread}` — newest first; `unread` counts every unread notice, not only the page |
| `POST /notifications/{id}/read` | — | The notice, with `read_at` set (idempotent); another person's or unknown id → `404 notification_not_found` |
| `POST /notifications/read-all` | — | `{ok: true, marked}` |

Notices are written in the caller's transaction by `create_notification()`. Nothing
creates one yet: the first kind (a scheduled report was issued) arrives with wave 3 of the
Reports, so today the list is empty.

---

## 17. Appendix: implementation notes

Things an integrator should know, found while writing this document from the code.

1. **Fixed on 17/10/2026** (found while writing this document): the dead codes
   `feature_not_available` and `interactive_login_forbidden` are gone; the catch-all
   404 carries `title`/`status`/`codigo` like every other error; MCP `run_forecast`
   turns `weeks` and `target_date` into the `days` the API reads; users, flags, plans,
   the workspace and the simulator answer `403 standalone_only` to a Pampufa token
   (those rows have no `product_id`); a `providencia_id` already carded in another
   product answers `409 providencia_in_other_product`; `POST /auth/refresh` no longer
   trades a Pampufa token for a local one.
2. **Additive routes beyond SPEC §5**: `GET /boards/templates`, `GET /auth/capabilities`,
   `PATCH /auth/me`, `GET /flags/catalogue`, `GET /cards/{id}/tasks`,
   `GET /cards/{id}/movements`, `GET /cards/{id}/events`, `PATCH`/`DELETE
   /comments/{id}`, `POST /columns/{id}/drain`, `DELETE /boards/{id}?hard=`, the
   `sort`/`order`/`in_flight`/`envelope_state` card filters, `baseline_run_id` on
   compare, `GET /boards/{id}/version`, `GET /boards/{id}/metrics/forecast`, and the
   whole `/integration/*`, `/card-types`, hours (`/cards/{id}/time`, `/time/{id}`,
   `/metrics/people`), `/reports/*` and `/notifications` routers.
3. **Observability** (`backend/app/observability.py`, outside the OpenAPI document). With
   `OTEL_EXPORTER_OTLP_ENDPOINT` set, traces (FastAPI, SQLAlchemy, httpx; the webhooks
   carry `traceparent`) and logs go to the platform's collector as `service.name=nagare`.
   `GET /metrics` (not under `/api/v1`) answers the Prometheus format — HTTP requests and
   latency by route template and product, plus the webhook outbox backlog — to
   `Authorization: Bearer $NAGARE_METRICS_TOKEN`, or, without that variable, only to the
   loopback (`401 metrics_token` / `403 metrics_local_only`).
4. **Found while checking on 25/11/2026, fixed the same day** (RM-12 and RM-13): the hour
   routes now take the card key as well as the id, and the Reports answer a project of
   another product with `404 project_not_found`, like the rest of the API.
