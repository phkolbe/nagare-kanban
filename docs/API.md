# Nagare Kanban — REST API Reference

This describes the API **as implemented**: it was written from the OpenAPI document the
app generates (`app.openapi()`) and checked against every router in
`backend/app/routers/` plus `deps.py`, `tenancy.py`, `pampufa.py`, `integration.py` and
`errors.py`. The live server (`python run.py`, `master@nagare.app` / `nagare123`) was used
to capture the examples.

- **Base path.** Every endpoint below is mounted under `/api/v1` (e.g.
  `POST /api/v1/auth/login`). Standalone: `http://127.0.0.1:8770/api/v1`. Inside the
  Pampufa the screens reach the same API through the platform's gateway at
  `/nagare/api/v1`.
- **Discovery.** Swagger UI at `/docs`, ReDoc at `/redoc`, the raw OpenAPI document at
  `/openapi.json` (none of these under `/api/v1`). The OpenAPI document is the ground
  truth for the route table: `curl -s :8770/openapi.json | jq '.paths | keys'`.
- **Size.** 91 operations: 1 meta route (`GET /health`) and ten routers — `auth` (5),
  `users` (6), `flags` (9), `boards` (17), `projects` (7), `cards` (16), `movements` (4),
  `metrics` (11), `simulator` (8), `integration` (7). Every one is listed below.
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
15. [Appendix: implementation notes](#15-appendix-implementation-notes)

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
{"email": "master@nagare.app", "password": "nagare123"}
```

```json
200
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9....",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": {
    "id": "59888dd1-780e-44d1-958c-12e1de458f5a",
    "email": "master@nagare.app", "name": "Yuki Tanaka", "role": "master",
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
- **Brute-force throttle** (in memory, per email): 5 failures inside 15 minutes lock the
  address for 30s, doubling per further failure up to 15 minutes →
  `429 too_many_login_attempts` with `details.retry_after_seconds`. Unknown emails pay
  for one real PBKDF2 comparison so timing does not leak which addresses exist.

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
| Local token | product `nagare` | allowed for `master` only, else `403 product_forbidden` |
| Pampufa token | `400 product_required` | the role comes from the token's roles **in that product** |

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
| 401 | `missing_token` | `deps.py` | No `Authorization: Bearer` |
| 401 | `invalid_token` / `token_expired` | `security.py`, `deps.py` | Untrustworthy or expired token (local or Pampufa) |
| 401 | `unknown_subject` | `deps.py`, `auth.py` | Token's `sub` no longer exists |
| 401 | `invalid_credentials` | `auth.py` | Wrong email or password (same body for both) |
| 401 | `pampufa_not_configured` / `pampufa_profile_unavailable` / `pampufa_link_conflict` | `deps.py`, `pampufa.py` | See §1.2 |
| 403 | `account_disabled` | `auth.py`, `deps.py` | Deactivated account |
| 403 | `interactive_login_denied` | `auth.py` | `system` tried to sign in |
| 403 | `product_forbidden` | `tenancy.py`, `deps.py` | No Nagare role in that product, or a non-master local login with `X-Produto` |
| 403 | `insufficient_role` | `deps.py`, `boards.py`, `projects.py` | Role not allowed for this action |
| 403 | `board_write_forbidden` | `deps.py` | Role cannot write this board's content |
| 403 | `feature_disabled` / `plan_upgrade_required` | `deps.require_flag` | Flag off; active plan below the flag's `min_plan` |
| 403 | `master_account_protected` / `master_role_forbidden` | `users.py` | Non-master touching a master / granting master |
| 403 | `plan_change_forbidden` | `flags.py` | Non-master switching the active plan |
| 403 | `comment_not_yours` | `cards.py` | Editing someone else's comment (non-admin) |
| 403 | `system_has_no_preferences` | `auth.py` | `PATCH /auth/me` by a service identity |
| 403 | `standalone_only` | `deps.require_standalone` | Users, flags, plans, workspace or simulator called with a Pampufa token (§5, §6, §12) |
| 404 | `board_not_found`, `column_not_found`, `group_not_found`, `card_not_found`, `project_not_found`, `user_not_found`, `task_not_found`, `comment_not_found`, `flag_not_found`, `plan_not_found`, `run_not_found`, `providencia_not_found` | throughout | Lookups; boards, cards and projects also accept their human key (slug, `NGR-104`, `NGR`) |
| 404 | `not_found` | `main.py` catch-all | No API route for that method and path |
| 405 | `method_not_allowed` | Starlette | Wrong verb on a real path |
| 409 | `backward_move_blocked` | `movements.py` | Move against the flow without an allowed `force` |
| 409 | `wip_limit_exceeded` | `movements.py`, `projects.py` | Column/group/flow maximum would break (`details.policy`) |
| 409 | `envelope_sealed` / `envelope_already_opened` / `envelope_column_missing` | `movements.py`, `projects.py` | Envelope state conflicts |
| 409 | `card_archived` / `card_moved_elsewhere` / `same_column_move` | `movements.py`, `boards.py` | Move preconditions |
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
  "message": "Only the master account can work in a Pampufa product with the local login.",
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
flag key never gates anything. Flags and the plan are **workspace-wide**, the same for
every product (NG15: the Grupo SR runs on enterprise with everything on).
`GET /flags/effective` returns exactly the map `require_flag` enforces.

---

## 4. Auth & identity (and the person's language)

| Method & path | RBAC | Notes |
|---|---|---|
| `POST /auth/login` | public | `{email, password}` → `TokenResponse` (§1.1) |
| `POST /auth/refresh` | local token | Re-mints a **local** token from the database row; refuses `system`; a Pampufa token answers `400 refresh_through_pampufa` (the platform renews its own) |
| `GET /auth/me` | any valid token | Profile, capability matrix, effective flags, locale, way in, product |
| `PATCH /auth/me` | any valid token except `system` | `{locale: "pt-BR" \| "en" \| null}` — the person's language |
| `GET /auth/capabilities` | any valid token | `{capability: bool}` for the caller's role |

`GET /auth/me` (and the `PATCH` answer):

```json
{
  "id": "59888dd1-...", "email": "master@nagare.app", "name": "Yuki Tanaka",
  "role": "master", "is_active": true, "created_at": "...", "last_login_at": "...",
  "role_label": "Master",
  "role_description": "Owns the workspace: billing plan, feature flags, users and every board.",
  "capabilities": {"manage_plans_flags": true, "manage_users": true, "...": true},
  "granted": ["manage_plans_flags", "manage_users", "..."],
  "flags": {"api_access": true, "card_history": true, "...": "(19 keys)"},
  "expires_in": 43200,
  "locale": "pt-BR",
  "locale_chosen": "pt-BR",
  "via": "local",
  "product_id": "nagare"
}
```

Through the Pampufa, `role` is the role **in the product** of `X-Produto`, `via` is
`pampufa` and `product_id` that product.

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
| `PATCH /flags/{key}` | `{is_enabled?, min_plan?}` | master | `404 flag_not_found` |
| `GET /plans` | — | master | Cheapest tier first |
| `POST /plans` | `{name, slug, price_month, price_year, currency, tier_order, description, flags[], is_active}` | master | `409 plan_slug_taken`, `400 unknown_flag` |
| `PATCH /plans/{plan_id}` | any subset | master | Cannot deactivate the active plan (`409 active_plan_locked`) |
| `GET /workspace` | — | any | Workspace, active plan, effective flags, plan list |
| `PATCH /workspace` | `{name?, active_plan_id?, timezone?}` | `manage_users`; switching `active_plan_id` is master-only (`403 plan_change_forbidden`), a retired plan → `400 plan_inactive` | Re-gates every flag in the same response |

19 flags in 7 categories, 4 plans `free`(0) → `team`(1) → `business`(2) →
`enterprise`(3), each cumulative with the tier below. The seeded workspace runs on
Business with every flag on.

---

## 7. Boards, columns, groups, WIP (BOARDS)

| Method & path | Query / body | RBAC (effective) | Flag |
|---|---|---|---|
| `GET /boards` | `?kind=operational\|simulator&q=&archived=` | any | — |
| `GET /boards/templates` | — | any | — |
| `POST /boards` | `{name, description?, kind, template?, count_envelope_in_metrics?, confirm_moves?, wip_min?, wip_max?, sle_days?, sle_percentile?}` | master/administrator; `kind=simulator` also `simulator` | `simulator` for `kind=simulator` |
| `GET /boards/{id}` | — | any | — |
| `PATCH /boards/{id}` | any subset of `{name, description, count_envelope_in_metrics, confirm_moves, wip_min, wip_max, sle_days, sle_percentile, archived}` | structure role | — |
| `DELETE /boards/{id}` | `?hard=true` | archive: structure role; hard delete: master only | — |
| `POST /boards/{id}/clone` | `{name, kind (default simulator), copy_cards}` | as `POST /boards` | `simulator` for `kind=simulator` |
| `GET /boards/{id}/view` | `?segment=upstream\|downstream\|unified` + `project_id, service_class, assignee_id, column_id, tag, q, is_blocked, demand_type, value_area, size, due_before, envelope_state, archived` | any | `unified_flow` for `segment=unified` |
| `GET /boards/{id}/wip` | — | any | — |
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
`MoveResult` as `POST /cards/{id}/move` (§10).

---

## 9. Cards, tasks, blockers, comments (CARDS)

Reads need any valid token; writes go through the board-write rule (§3), and a
read-only board refuses them (`409 board_read_only`).

| Method & path | Query / body | Notes |
|---|---|---|
| `GET /cards` | `?board_id=&project_id=&service_class=&assignee_id=&segment=&column_id=&tag=&q=&is_blocked=&demand_type=&value_area=&size=&due_before=&archived=&envelope_state=&in_flight=&sort=&order=&limit=(≤1000, default 200)&offset=` | `project_id=none`, `assignee_id=unassigned`; `service_class`, `column_id`, `tag` take comma lists; `sort ∈ {created_at, aging, due_date, title, key, position}`, `order ∈ {asc, desc}`; `total` is the filtered count |
| `POST /cards` | `{board_id, title, column_id?, project_id?, description?, service_class?, assignee_id?, requester?, size?, value_area?, demand_type?, due_date?, envelope_state?, tags?, custom_fields?}` | Writes the creation movement (`from_column_id: null`) and event; created at/after the commitment column → `committed_at` at once |
| `GET /cards/{card_id}` | — | Id or key (`NGR-104`). Card + `tasks[]`, `blockers[]`, `comments[]`, `movements[]`, `intervals[]`, `flow{}` (touch/wait/efficiency), `can_edit` |
| `PATCH /cards/{card_id}` | subset of `title, description, project_id, service_class, assignee_id, requester, size, value_area, demand_type, due_date, position, tags, custom_fields, archived` | One `card_events` row per changed field; `column_id` → `400 field_not_editable` (use `/move`). `archived: true` on a providência card discards it (webhook, §14.3) |
| `DELETE /cards/{card_id}` | — | Archives (soft, idempotent); same webhook rule |
| `GET /cards/{card_id}/tasks` | — | Checklist in display order (`{items, total}`) |
| `POST /cards/{card_id}/tasks` | `{title, assignee_id?, position?, is_done?}` | |
| `PATCH /tasks/{task_id}` / `DELETE /tasks/{task_id}` | `{title?, assignee_id?, position?, is_done?}` | Completing records `task_completed` |
| `POST /cards/{card_id}/block` | `{reason, category}` | `category ∈ {dependency, external_vendor, waiting_on_customer, technical, people, compliance, environment, other}`; `409 card_already_blocked` |
| `POST /cards/{card_id}/unblock` | — | Closes every open blocker; `409 card_not_blocked` |
| `GET /cards/{card_id}/blockers` | — | History with `duration_seconds` / `is_open` |
| `GET /cards/{card_id}/comments` / `POST /cards/{card_id}/comments` | `{body}` | |
| `PATCH /comments/{comment_id}` / `DELETE /comments/{comment_id}` | `{body}` | Author or master/administrator (`403 comment_not_yours`) |

Inside a product, assignees come from `GET /integration/people` (§14.2).

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

Read-only for every role (`read_boards_metrics`). Every route takes the common filters
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

A missing required parameter answers `400 invalid_simulation_params`; an input the
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
| `run_forecast` | `POST /simulator/runs` | Works: `kind = monte_carlo_when` with `items` (sent as `backlog_size`), or `monte_carlo_how_many` with `days`, `weeks` (× 7) or `target_date` (days from `start_date`, default today), all sent as `days`. Needs a role with `simulator_boards` (master, administrator, simulator) and the `simulator` + `monte_carlo` flags. See §15 for the `weeks` / `target_date` horizons |

REST errors become tool errors prefixed with the code (`[wip_limit_exceeded] ...`) plus a
canned `Hint:` for the common ones (`_HINTS` in the file). Card writes are attributed to
the authenticated user with `card_movements.source = "mcp"`.

Start the app first, then register the server:

```bash
python run.py                          # http://127.0.0.1:8770

claude mcp add nagare \
    --env NAGARE_URL=http://127.0.0.1:8770 \
    --env NAGARE_EMAIL=master@nagare.app \
    --env NAGARE_PASSWORD=nagare123 \
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
| `POST /integration/providencias`, `GET /integration/providencias/{id}`, `POST /integration/providencias/{id}/resolved-at-origin` | `system` only: the Pampufa's **service token** (`sub: servico:pampufa`, role `system` in the product) with `X-Produto`. Anyone else → `403 insufficient_role` |
| `GET /integration/overview`, `GET /integration/people`, `GET /integration/product` | any role in the product (`read_boards_metrics`) |
| `PUT /integration/product` | `master` / `administrator` (in practice `nagare.admin` of the product) |

### 14.2 Routes

| Method & path | Body | Answer |
|---|---|---|
| `POST /integration/providencias` | `{providencia_id, servico, tipo, titulo?, motivo?, url?, produto_nome?, responsavel?: {sub?, email?, nome?}, prazo?, classe_servico?}` | `201 {created: true, card}`; the same `providencia_id` again → `200 {created: false, card}` (idempotent, never a second card) |
| `GET /integration/providencias/{providencia_id}` | — | `{state, card}`; `state ∈ {open, resolved_at_origin, delivered, discarded}` |
| `POST /integration/providencias/{providencia_id}/resolved-at-origin` | `{em?, origem?}` | `{changed, state, card}` |
| `GET /integration/overview` | — | `{stages, delivered_window_days, products: [...]}` |
| `GET /integration/people` | — | `{source, people: [{id, name, email, sub, roles}]}` |
| `GET /integration/product` | — | `{product_id, link, history}` |
| `PUT /integration/product` | `{project_id, board_id, motivo}` | `{product_id, link, history, moved_cards}` |

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
`cartao_url` in `cartao.movido`):

- a platform product's card, with `NAGARE_URL_TELAS` set:
  `{NAGARE_URL_TELAS}/{product}/tarefas/quadro/{board_id}?cartao={card_id}` (the Tarefas
  module);
- otherwise: `{NAGARE_URL_PUBLICA}/quadro/{board_id}?cartao={card_id}` (the standalone
  Nagare's board).

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
| `NAGARE_URL_PUBLICA` | `http://127.0.0.1:8770` | The standalone Nagare, for `card_url` |

`.env.example` has the development values.

---

## 15. Appendix: implementation notes

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
   compare, and the whole `/integration/*` router.
