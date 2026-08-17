# Nagare Kanban — REST API Reference

This is a description of the API **as implemented**, verified by reading every router in
`backend/app/routers/` and by running the live server (`python run.py`,
`master@nagare.app` / `nagare123`) and capturing real requests with `httpx`/`urllib`.
Where the running app disagrees with `docs/SPEC.md`, that is called out explicitly with
**⚠ differs from SPEC** so the integrator can decide whether to fix the code or the spec.

Base URL: `http://127.0.0.1:8770`. Every endpoint below is mounted under `/api/v1`
(e.g. `POST /api/v1/auth/login`). Interactive docs live at `/docs` (Swagger UI) and
`/redoc`; the raw OpenAPI document is at `/openapi.json` (note: **not** under `/api/v1`).

At the time of writing, seven of the nine routers named in `docs/SPEC.md` §1 are mounted:
`auth`, `users`, `flags`, `boards`, `projects`, `cards`, `movements`, **and** `metrics`.
The ninth, **`simulator`**, does not exist yet (`backend/app/routers/simulator.py` is
absent) — `backend/app/main.py` mounts routers defensively, so the app boots fine and
every `/api/v1/simulator/*` path currently answers `404 not_found`. See
[§12 Simulator](#12-simulator-sim--not-yet-mounted) for what SPEC §8 promises and what the
underlying engine (`backend/app/flow/forecast.py`) already implements, ready to be wired
up.

---

## Table of contents

1. [Authentication & the token model](#1-authentication--the-token-model)
2. [Error handling](#2-error-handling)
3. [RBAC & feature flags, mechanically](#3-rbac--feature-flags-mechanically)
4. [Auth & identity](#4-auth--identity)
5. [Users (ADMIN)](#5-users-admin)
6. [Flags, plans & workspace (ADMIN)](#6-flags-plans--workspace-admin)
7. [Boards, columns, groups, WIP (BOARDS)](#7-boards-columns-groups-wip-boards)
8. [Projects & demands (BOARDS)](#8-projects--demands-boards)
9. [Cards, tasks, blockers, comments (CARDS)](#9-cards-tasks-blockers-comments-cards)
10. [Movement & history (CARDS)](#10-movement--history-cards)
11. [Metrics (METRICS)](#11-metrics-metrics)
12. [Simulator (SIM) — not yet mounted](#12-simulator-sim--not-yet-mounted)
13. [Integrating with Nagare](#13-integrating-with-nagare)
14. [Appendix: SPEC discrepancies found](#14-appendix-spec-discrepancies-found)

---

## 1. Authentication & the token model

Nagare uses a hand-rolled **HS256 JWT** (`backend/app/security.py`) — no third-party
crypto, no session store. There is exactly one way in: `POST /auth/login`.

```
POST /api/v1/auth/login
{"email": "master@nagare.app", "password": "nagare123"}
```

```json
200
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9....eRxwX7GJkhon6PLSZvCPHzKhjrIpVDWoG-oUqlFg4dg",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": {
    "id": "59888dd1-780e-44d1-958c-12e1de458f5a",
    "email": "master@nagare.app",
    "name": "Yuki Tanaka",
    "role": "master",
    "is_active": true,
    "created_at": "2025-07-12T08:00:00Z",
    "last_login_at": "2026-08-16T13:04:46Z"
  }
}
```

Every other request authenticates with `Authorization: Bearer <access_token>`.

**Claims.** The token payload (base64url JSON, unverified example decoded for
illustration) is:

```json
{
  "sub": "59888dd1-780e-44d1-958c-12e1de458f5a",
  "role": "master",
  "iat": 1786885486,
  "exp": 1786928686,
  "iss": "nagare-kanban",
  "jti": "0e774bf392ee96b3"
}
```

- `sub` — user id. `role` — a **hint only**: `deps.get_current_user` re-reads the role
  from the `users` table on every request, so a role change or deactivation takes effect
  immediately, without waiting for the token to expire or be refreshed.
- `exp` — `NAGARE_TOKEN_TTL` seconds from issue, default `12h` (`43200`). There is no
  refresh-token concept; `POST /auth/refresh` just re-mints a token from the *current*
  database row for whoever's still-valid access token you present.
- Algorithm confusion is refused outright: `decode_token` rejects any `alg` other than
  `HS256` (no `"alg": "none"` acceptance), and the signature check uses
  `hmac.compare_digest`.
- `NAGARE_JWT_SECRET` must be set in production; it defaults to a well-known dev string
  (`nagare-dev-secret-do-not-use-in-production`) so a cold clone still runs.

**`system` cannot log in interactively.** `role = system` is the non-human integration
identity (SPEC §4). `POST /auth/login` rejects it with `403 interactive_login_denied`
*after* verifying the password (so the check can't be used to enumerate which accounts
are human) — the only way to get a token for `system` is to mint one directly (tests,
an operator script) or to reuse a token minted before the role was set to `system`.

**Brute-force throttling.** In-memory, per-email, not persisted (`routers/auth.py`):
5 failed attempts in a 15-minute window lock that address for 30s; every further
failure inside the window doubles the lockout up to 15 minutes. A successful login (or
process restart) clears it. Locked-out logins get `429 too_many_login_attempts` with a
`retry_after_seconds` in `details`. Unknown emails pay for one real PBKDF2 hash
comparison against a dummy hash so timing doesn't leak which addresses exist.

**Password hashing.** PBKDF2-HMAC-SHA256, 260,000 iterations, per-user salt, stored as
`pbkdf2$<iterations>$<salt_b64>$<hash_b64>`. A login with a hash below the current
iteration count is transparently re-hashed on success.

```
POST /api/v1/auth/refresh        Authorization: Bearer <still-valid token>
                                  -> same TokenResponse shape, freshly signed
GET  /api/v1/auth/me              -> user + role_label/description + capability
                                     matrix + granted[] + effective flags{} + expires_in
GET  /api/v1/auth/capabilities    -> {capability: bool} for the caller's own role only
                                     (a cheap poll; not documented in SPEC §5, additive)
```

Real `GET /auth/me` response (trimmed):

```json
{
  "id": "59888dd1-780e-44d1-958c-12e1de458f5a",
  "email": "master@nagare.app", "name": "Yuki Tanaka", "role": "master",
  "role_label": "Master",
  "role_description": "Owns the workspace: billing plan, feature flags, users and every board.",
  "capabilities": {
    "manage_plans_flags": true, "manage_users": true, "manage_boards": true,
    "manage_cards": true, "read_boards_metrics": true, "simulator_boards": true,
    "api_tokens": true
  },
  "granted": ["manage_plans_flags", "manage_users", "manage_boards", "manage_cards",
              "read_boards_metrics", "simulator_boards", "api_tokens"],
  "flags": { "api_access": true, "card_history": true, "...": "...(19 keys total)" },
  "expires_in": 43200
}
```

---

## 2. Error handling

Every failure — ours, FastAPI's validator, or an unhandled exception — is rendered by
`backend/app/errors.py` as the single envelope:

```json
{"error": {"code": "wip_limit_exceeded", "message": "human-readable text", "details": {}}}
```

`code` is the stable, machine-readable string the frontend (`ui.js` → `api.js`) and the
MCP server both switch on. `details` carries whatever structured context the specific
failure has (ids, current/max counts, allowed values…). List endpoints return
`{"items": [...], "total": n}` uniformly.

### 2.1 Error codes actually raised

Grouped by HTTP status, exactly as found in the source (`grep -r 'code='` across every
router plus `deps.py`, `security.py`, `errors.py`). Codes marked **(default)** are the
class's fallback when no router bothers to override it.

| HTTP | Code | Raised where | Meaning |
|---|---|---|---|
| 400 | `bad_request` **(default)** | `errors.BadRequest` fallback | Generic malformed request; every real caller below supplies a specific code instead |
| 400 | `board_has_no_columns` | `cards.py`, `projects.py` create-demand | Board has zero columns, cannot hold a card |
| 400 | `board_id_required` | `projects.py` create-demand | Workspace has >1 operational board; caller must name one |
| 400 | `column_board_mismatch` | `cards.py`, `movements.py` | `column_id` belongs to a different board than the card/board |
| 400 | `column_not_on_board` | `boards.py` (reorder, group assignment) | A referenced column id isn't on this board |
| 400 | `duplicate_column_in_order` | `boards.py` reorder | `ordered_ids` repeats a column |
| 400 | `field_not_editable` | `cards.py` PATCH | Payload touches a field not in the editable set (e.g. `column_id` — moves are a separate endpoint) |
| 400 | `group_not_on_board` | `boards.py` | Group referenced in a column create/update belongs to another board |
| 400 | `invalid_filter` | `cards.py` list | Unknown enum value in a filter query param (real example below) |
| 400 | `invalid_wip_range` | `boards.py` (board/column/group) | `wip_min > wip_max` |
| 400 | `owner_not_found` | `projects.py` | `owner_id` doesn't exist |
| 400 | `sealed_requires_envelope_column` | `cards.py` create | `envelope_state=sealed` but target column isn't `kind=envelope` |
| 400 | `unknown_flag` | `flags.py` plan create/update | Plan references a feature-flag key that doesn't exist |
| 400 | `unknown_role` | `users.py` list | `?role=` filter value isn't one of the six roles |
| 401 | `missing_token` | `deps.get_current_user` | No `Authorization: Bearer` header |
| 401 | `invalid_token` **(default)** | `security.TokenError` | Malformed JWT, bad signature, unsupported `alg` |
| 401 | `token_expired` | `security.decode_token` | `exp` claim in the past |
| 401 | `unknown_subject` | `deps._load_user` | Token's `sub` no longer exists in `users` |
| 401 | `invalid_credentials` | `auth.py` login | Wrong email/password (identical message for both, on purpose) |
| 401 | `unauthorized` **(default)** | `errors.Unauthorized` fallback | Generic auth failure; not used directly by any router — every real 401 above has its own code |
| 403 | `account_disabled` | `auth.py`, `deps.py` | `is_active=0`; checked *after* password verification |
| 403 | `interactive_login_denied` | `auth.py` login | `role=system` tried to sign in |
| 403 | `interactive_login_forbidden` | `deps.assert_can_login` | Same rule, **dead code** — this helper is never called; the reachable path always produces `interactive_login_denied` instead (see [§14](#14-appendix-spec-discrepancies-found)) |
| 403 | `insufficient_role` | `deps.require_roles/require_capability`, `boards.py`, `projects.py` | Role isn't in the allowed set for this action |
| 403 | `board_write_forbidden` | `deps.require_board_write` | Role can't write this board's cards (e.g. `viewer`, or `simulator` on an operational board) |
| 403 | `feature_disabled` | `deps.require_flag` | Flag exists and is switched off in the workspace |
| 403 | `plan_upgrade_required` | `deps.require_flag` | Flag is on but the active plan's tier is below `min_plan` |
| 403 | `master_account_protected` | `users.py` | Non-master tried to edit a master account |
| 403 | `master_role_forbidden` | `users.py` | Non-master tried to grant the master role |
| 403 | `plan_change_forbidden` | `flags.py` workspace update | Non-master tried to switch the active plan |
| 403 | `comment_not_yours` | `cards.py` | Editing/deleting someone else's comment (non-admin) |
| 403 | `forbidden` **(default)** | `errors.Forbidden` fallback | Never raised directly by any router in this codebase — reachable only if Starlette itself raises a bare 403 `HTTPException` (it doesn't, currently) |
| 404 | `board_not_found`, `column_not_found`, `group_not_found`, `card_not_found`, `project_not_found`, `user_not_found`, `task_not_found`, `comment_not_found`, `flag_not_found`, `plan_not_found` | throughout | Resource lookups; each accepts an id **or** the resource's human key/slug where one exists (board slug, card key like `NGR-104`, project key like `NGR`) |
| 404 | `not_found` **(default)** | `errors.NotFound` fallback, and `main.py`'s SPA catch-all | Every explicit `NotFound(...)` call in the routers supplies its own `*_not_found` code above; the bare `not_found` code you'll actually see comes from hitting an **unmounted** API path (e.g. any `/api/v1/simulator/*` route right now) |
| 405 | `method_not_allowed` **(default)** | Starlette → `errors.py` HTTP map | Wrong verb on a real path |
| 409 | `backward_move_blocked` | `movements.py` move | Target column's flow position is behind the origin's, and the caller isn't `master`/`administrator` with `force: true` |
| 409 | `wip_limit_exceeded` | `movements.py`, `projects.py` open-envelope | Column/group/flow WIP maximum would be breached (`details.policy` names which one) |
| 409 | `envelope_sealed` | `movements.py` move | Card is still `envelope_state=sealed`; call `open-envelope` first |
| 409 | `envelope_already_opened` | `projects.py` open-envelope | Card is already `opened` |
| 409 | `card_archived` | `movements.py` | Trying to move an archived card |
| 409 | `card_already_blocked` / `card_not_blocked` | `cards.py` block/unblock | Blocker-interval state conflict |
| 409 | `card_moved_elsewhere` | `movements.py` | Optimistic-concurrency guard: the card's column changed between read and write inside the same request |
| 409 | `same_column_move` | `movements.py` | Target column equals the current column |
| 409 | `column_not_empty` | `boards.py` delete column | Column still has cards |
| 409 | `no_open_column` / `no_envelope_column` / `no_operational_board` | `boards.py`, `projects.py` | Structural precondition missing (e.g. board with no non-envelope column to unseal into) |
| 409 | `envelope_column_exists` / `envelope_must_be_upstream` / `envelope_must_be_first` / `envelope_column_missing` | `boards.py`, `projects.py` | Envelope-column invariants (at most one, always upstream, always first) |
| 409 | `done_must_be_downstream` / `segment_order_violation` / `group_not_contiguous` | `boards.py` | Column-ordering invariants |
| 409 | `commitment_point_is_derived` / `delivery_point_is_derived` | `boards.py` | Client tried to set a derived flag directly instead of changing segment/kind/position |
| 409 | `email_taken` | `users.py` | Email already registered |
| 409 | `last_master_protected` | `users.py` | Would leave the workspace with zero active masters |
| 409 | `self_role_change_blocked` / `self_deactivation_blocked` | `users.py` | Acting on your own account in a way that's disallowed (real example below) |
| 409 | `project_key_taken` | `projects.py` | Project key already in use |
| 409 | `plan_slug_taken` / `active_plan_locked` / `plan_inactive` | `flags.py` | Plan CRUD conflicts |
| 409 | `too_many_login_attempts` | `auth.py` | Brute-force lockout active (429 in practice — see below) |
| 409 | `conflict` **(default)** | Starlette → HTTP map | Generic fallback; unused directly |
| 413/415 | `payload_too_large` / `unsupported_media_type` **(default)** | Starlette → HTTP map | Framework-level, not raised by application code |
| 422 | `validation_error` | FastAPI `RequestValidationError` handler | Pydantic body/query validation failed; `details.fields` is a list of `{field, message, type}` |
| 429 | `too_many_login_attempts` | `auth.py` login throttle | *(Note: `errors.Conflict` sets HTTP 409 by construction, but this specific raise uses the bare `ApiError` class with `status=429` explicitly — it really does answer 429, not 409.)* |
| 500 | `internal_error` **(default)** | Unhandled-exception handler | Traceback is logged server-side; the client only gets `{"type": "<ExceptionClassName>"}` |
| 501/503 | `not_implemented` / `service_unavailable` **(default)** | Starlette → HTTP map | Framework-level, unused by application code |

Two codes named in the original task brief are **not** present anywhere in the running
backend: **`feature_not_available`** and, functionally, a distinct `forbidden` you'd
ever actually see. See [§14](#14-appendix-spec-discrepancies-found).

### 2.2 Real examples, captured live

**`envelope_sealed`** — moving a sealed demand:

```json
409
{"error": {"code": "envelope_sealed",
  "message": "NGR-192 is still a sealed envelope — open it before moving it.",
  "details": {"card_id": "576147cc-...", "envelope_state": "sealed"}}}
```

**`wip_limit_exceeded`** — moving into a full column:

```json
409
{"error": {"code": "wip_limit_exceeded",
  "message": "The column WIP limit on “Build” is full: 8 of 8 — finish something before starting something.",
  "details": {"policy": "column", "name": "Build", "current": 8, "max": 8, "projected": 9,
              "column_id": "b9ee78d3-...", "service_class": "standard"}}}
```

**`backward_move_blocked`**:

```json
409
{"error": {"code": "backward_move_blocked",
  "message": "Cards flow forward only: “Analysis Done” sits after “Ideas” in this value stream.",
  "details": {"card_id": "d0d15afb-...", "from_column_id": "883c0c25-...",
              "from_column": "Analysis Done", "to_column_id": "35cf42bd-...",
              "to_column": "Ideas", "from_position": 6, "to_position": 1,
              "force_allowed_for": ["administrator", "master"], "forced": false}}}
```

**`board_write_forbidden`** — a `viewer` tries to move a card:

```json
403
{"error": {"code": "board_write_forbidden",
  "message": "Your role cannot modify this board.",
  "details": {"role": "viewer", "board_kind": "operational"}}}
```

**`interactive_login_denied`** — the `system` identity tries to sign in:

```json
403
{"error": {"code": "interactive_login_denied",
  "message": "The system identity is for API and MCP access only and cannot sign in.",
  "details": {"role": "system"}}}
```

**`feature_disabled`** — a master switches `kmm_dashboard` off, then calls `/metrics/kmm`:

```json
403
{"error": {"code": "feature_disabled",
  "message": "The feature “Kanban Maturity Model dashboard” is switched off for this workspace.",
  "details": {"flag": "kmm_dashboard", "min_plan": "business", "plan": "business"}}}
```

**`plan_upgrade_required`** — workspace on Free, calling an advanced-metrics endpoint:

```json
403
{"error": {"code": "plan_upgrade_required",
  "message": "“CFD, flow efficiency, aging, SLE” requires the Business plan.",
  "details": {"flag": "metrics_advanced", "min_plan": "business", "plan": "free"}}}
```

**`validation_error`** — missing required field:

```json
422
{"error": {"code": "validation_error",
  "message": "The request body or query string failed validation.",
  "details": {"fields": [{"field": "password", "message": "Field required", "type": "missing"}]}}}
```

**`card_not_found`** — real 404 (not the bare `not_found` fallback):

```json
404
{"error": {"code": "card_not_found", "message": "Card not found.",
  "details": {"resource": "Card", "id": "00000000-0000-0000-0000-000000000000"}}}
```

**`self_deactivation_blocked`** — master tries to deactivate itself:

```json
409
{"error": {"code": "self_deactivation_blocked",
  "message": "You cannot deactivate your own account.",
  "details": {"user_id": "59888dd1-..."}}}
```

**`invalid_filter`**:

```json
400
{"error": {"code": "invalid_filter",
  "message": "Unsupported value for “service_class”: bogus.",
  "details": {"filter": "service_class", "invalid": ["bogus"],
              "allowed": ["expedite", "fixed_date", "intangible", "standard"]}}}
```

---

## 3. RBAC & feature flags, mechanically

The SPEC §4 capability matrix (`master`/`administrator`/`editor`/`viewer`/`simulator`/
`system`) is implemented once, in `config.ROLE_CAPABILITIES`, and consumed two ways:

- **`require_roles("master", "administrator")`** — an explicit allow-list.
- **`require_capability("manage_cards")`** — looks up the allow-list for a named
  capability, so the matrix stays the single source of truth. `GET /users/roles`
  renders the same table for the admin UI.

⚠ **Implementation nuance, not a behavioural bug:** several structural endpoints in
`boards.py` (`POST /boards`, `POST /boards/{id}/columns`, `PATCH /columns/{id}`, group
CRUD, …) declare their FastAPI dependency as the very permissive
`require_capability("read_boards_metrics")` — which every role including `viewer` holds —
and only *then*, inside the function body, call `require_structure_write(user, board)` or
an inline role check that actually raises `insufficient_role`/`board_write_forbidden`.
The net result for a caller is identical (a `viewer` still gets a 403), but it means the
role check happens after Pydantic has already parsed the body, not as a FastAPI
dependency gate. Documented here because it explains why you can't infer the true
required role from the route's `Depends(...)` signature alone for board-structure
endpoints — read the function body, or trust this document.

**Feature flags** (`deps.require_flag`, `deps.is_flag_effective`, `deps.effective_flags`):
a flag is *effective* when `feature_flags.is_enabled = 1` **and**
`plan_tier(active_plan) >= plan_tier(flag.min_plan)`. `require_flag(key)` is a FastAPI
dependency that 403s with `feature_disabled` (flag off) or `plan_upgrade_required` (plan
too low) — **never** a generic "not available" code. An **unknown flag key** is treated
as always-effective (`is_flag_effective` returns `True` when the row doesn't exist) so a
router that ships before its flag row is seeded never breaks.

`GET /flags/effective` returns exactly the `{key: bool}` map that `require_flag` enforces
— the frontend can gate its own UI from the same source of truth the server checks.

---

## 4. Auth & identity

| Method & path | RBAC | Flag | Notes |
|---|---|---|---|
| `POST /auth/login` | none (public) | none | Body: `{email, password}`. See §1. |
| `POST /auth/refresh` | any valid token | none | Re-mints from the DB row, not the old token's claims |
| `GET /auth/me` | any valid token | none | Profile + capability matrix + effective flags |
| `GET /auth/capabilities` | any valid token | none | `{capability: bool}` for the caller's own role — not in SPEC §5, additive |

---

## 5. Users (ADMIN)

Guarded by the `manage_users` capability (`master`, `administrator`) for writes; reads
are open to any authenticated identity (`read_boards_metrics` capability — held by every
role) because assignee pickers and filter bars need to resolve names.

| Method & path | Query / body | RBAC | Notes |
|---|---|---|---|
| `GET /users` | `?role=&q=&is_active=&limit=&offset=` | any signed-in identity | `role` must be one of the six; unknown value → `400 unknown_role` |
| `GET /users/roles` | — | `manage_users` **+** flag `rbac_advanced` (business) | Full capability matrix as data (see below) |
| `GET /users/{id}` | — | any signed-in identity | |
| `POST /users` | `{email, name, password, role, is_active}` | `manage_users` | Only a `master` may set `role: master` (`403 master_role_forbidden` otherwise) |
| `PATCH /users/{id}` | any subset of the above | `manage_users` | Five guardrails apply (below) |
| `DELETE /users/{id}` | — | `manage_users` | **Soft** delete (`is_active=0`); history keeps its author |

**Guardrails enforced on every write** (SPEC-consistent, verified in code):
1. Only `master` may create/edit a `master` account.
2. Nobody may change **their own** role.
3. Nobody may deactivate/delete **themselves**.
4. The **last active master** can never be demoted, deactivated or deleted.
5. Email uniqueness is case-insensitive.

Real `GET /users?limit=1`:

```json
{"items": [{"id": "59888dd1-...", "email": "master@nagare.app", "name": "Yuki Tanaka",
  "role": "master", "is_active": true, "created_at": "2025-07-12T08:00:00Z",
  "last_login_at": "2026-08-16T13:05:01Z"}], "total": 6}
```

`GET /users/roles` returns, per role, `{key, label, description, capabilities}` plus
workspace-level context: `matrix` (capability → roles that hold it), `user_counts`,
`protected_roles: ["master"]`, `non_interactive_roles: ["system"]`.

The six seeded accounts (all password `nagare123`): `master@nagare.app`,
`admin@nagare.app`, `editor@nagare.app`, `viewer@nagare.app`, `simulator@nagare.app`,
`system@nagare.app` (cannot sign in — see §1).

---

## 6. Flags, plans & workspace (ADMIN)

Everything here is `master`-only (`manage_plans_flags` capability) **except** two reads
that any signed-in identity needs to render the shell: `GET /flags/effective` and
`GET /workspace`.

| Method & path | Query / body | RBAC | Notes |
|---|---|---|---|
| `GET /flags` | `?category=` | `master` (`manage_plans_flags`) | Raw switchboard, grouped by category with per-category enabled/effective counts |
| `GET /flags/effective` | — | any signed-in identity | `{key: bool}` — the exact map `require_flag` enforces |
| `GET /flags/catalogue` | — | any signed-in identity | Static SPEC §3 table as data (name/description/min_plan/category per flag) — used for upgrade-prompt copy; reads `config.py`, not the DB, so it answers "what the product offers" rather than "what's on right now" |
| `PATCH /flags/{key}` | `{is_enabled?, min_plan?}` | `master` | 404 `flag_not_found` for an unknown key |
| `GET /plans` | — | `master` | |
| `POST /plans` | `{name, slug, price_month, price_year, currency, tier_order, description, flags[], is_active}` | `master` | `409 plan_slug_taken`; unknown flag keys → `400 unknown_flag` |
| `PATCH /plans/{id}` | any subset | `master` | Can't deactivate the currently-active plan (`409 active_plan_locked`) |
| `GET /workspace` | — | any signed-in identity | Workspace + active plan + effective flags + full plan list, all in one call |
| `PATCH /workspace` | `{name?, active_plan_id?, timezone?}` | `manage_users` capability for name/timezone; **switching `active_plan_id` requires `master` specifically** (`403 plan_change_forbidden` otherwise) | Re-gates every flag in the same response |

Real `GET /flags/effective` (Business plan, all flags on — the seeded default):

```json
{"api_access": true, "card_history": true, "custom_fields": true, "downstream_board": true,
 "envelope": true, "kmm_dashboard": true, "mcp_server": true, "metrics_advanced": true,
 "metrics_basic": true, "monte_carlo": true, "multi_project": true, "rbac_advanced": true,
 "service_classes": true, "simulator": true, "unified_flow": true, "upstream_board": true,
 "wip_column": true, "wip_flow": true, "wip_group": true}
```

The four plans, exactly as seeded (`config.PLANS`): `free`(0) → `team`(1) →
`business`(2) → `enterprise`(3), each `flags_for_plan(slug)` cumulative with the tier
below it.

---

## 7. Boards, columns, groups, WIP (BOARDS)

| Method & path | Query / body | RBAC (effective) | Flag |
|---|---|---|---|
| `GET /boards` | `?kind=&q=&archived=` | any signed-in identity | — |
| `GET /boards/templates` | — | any signed-in identity | — (additive; not in SPEC §5) |
| `POST /boards` | `{name, description?, kind, template?, count_envelope_in_metrics?, confirm_moves?, wip_min?, wip_max?, sle_days?, sle_percentile?}` | `master`/`administrator`; `simulator` may create `kind=simulator` boards only | `simulator` flag gates `kind=simulator` |
| `GET /boards/{id}` | — | any signed-in identity | — |
| `PATCH /boards/{id}` | any subset of `BoardUpdate` | `master`/`administrator` (or `simulator` on its own simulator boards) | — |
| `DELETE /boards/{id}` | `?hard=true` | archive: same as PATCH; **hard delete: `master` only** | — |
| `POST /boards/{id}/clone` | `{name, kind, copy_cards}` | same as create | `simulator` flag if `kind=simulator` |
| `GET /boards/{id}/view` | `?segment=upstream\|downstream\|unified` + the full card filter set (`project_id`, `service_class`, `assignee_id`, `column_id`, `tag`, `q`, `is_blocked`, `demand_type`, `value_area`, `size`, `due_before`, `envelope_state`, `archived`) | any signed-in identity | `segment=unified` needs `unified_flow` |
| `GET /boards/{id}/wip` | — | any signed-in identity | — |
| `POST /boards/{id}/columns` | `{name, segment, kind, position?, wip_min?, wip_max?, group_id?, color?, description?}` | structural role | — |
| `PATCH /columns/{id}` | any subset | structural role | — |
| `DELETE /columns/{id}` | — | structural role | Column must be empty (`409 column_not_empty`) |
| `POST /boards/{id}/columns/reorder` | `{ordered_ids: []}` | structural role | Partial lists are accepted; omitted columns keep relative order |
| `POST /boards/{id}/groups` | `{name, position?, wip_min?, wip_max?, color?, column_ids?}` | structural role | `wip_group` (team) |
| `PATCH /groups/{id}` | any subset + `column_ids?` | structural role | `wip_group` |
| `DELETE /groups/{id}` | — | structural role | `wip_group`; columns are ungrouped, not deleted |

*"Structural role" = `master`/`administrator` always; `simulator` only on boards where
`kind=simulator`. See the RBAC nuance in §3 about how this is actually enforced.*

Real `GET /boards`:

```json
{"items": [
  {"id": "eea60bad-...", "name": "Nagare Flow", "slug": "nagare-flow", "kind": "operational",
   "source_board_id": null, "count_envelope_in_metrics": false, "confirm_moves": true,
   "wip_min": 10, "wip_max": 45, "sle_days": 25, "sle_percentile": 85,
   "created_at": "2025-07-17T08:00:00Z", "archived": false, "card_count": 266},
  {"id": "637da8dc-...", "name": "Scenario Lab", "kind": "simulator",
   "source_board_id": "eea60bad-...", "wip_min": 6, "wip_max": 24, "card_count": 0}
], "total": 2}
```

Real `GET /boards/{id}/wip` (one column of each state, from the seeded board):

```json
{"board_id": "eea60bad-...",
 "columns": {
   "489c731b-...": {"count": 4, "min": 5, "max": 8, "state": "under", "label": "4/8"},
   "b9ee78d3-...": {"count": 8, "min": 3, "max": 8, "state": "at_limit", "label": "8/8"},
   "cc852667-...": {"count": 6, "min": 2, "max": 5, "state": "over", "label": "6/5"}
 },
 "groups": {"05203746-...": {"count": 20, "min": 6, "max": 24, "state": "ok"}},
 "flow": {"count": 35, "min": 10, "max": 45, "state": "ok", "label": "Whole flow"}}
```

`GET /boards/templates` lists the four board templates a `POST /boards` `template`
value can select: `upstream_downstream` (12 columns, the seeded shape; aliases:
`default`, `full`, `unified`), `simple_delivery` (4 columns; aliases: `simple`,
`delivery`), `discovery_only` (6 columns; aliases: `discovery`, `upstream`), and `blank`
(0 columns; aliases: `empty`, `none`).

---

## 8. Projects & demands (BOARDS)

| Method & path | Query / body | RBAC | Flag |
|---|---|---|---|
| `GET /projects` | `?status=&q=&archived=` | any signed-in identity | — |
| `POST /projects` | `{name, key, color?, description?, status?, owner_id?, started_at?, target_date?}` | `master`/`administrator` | `multi_project` |
| `GET /projects/{id}` | — | any signed-in identity | — |
| `PATCH /projects/{id}` | any subset | `master`/`administrator` | `multi_project` |
| `DELETE /projects/{id}` | — | `master`/`administrator` | `multi_project` (archives; cards keep their key) |
| `POST /projects/{id}/demands` | `{title, board_id?, description?, requester?, service_class?, value_area?, demand_type?, size?, due_date?, tags?, custom_fields?, sealed?}` (default `sealed: true`) | `manage_cards` capability (`master`/`administrator`/`editor`/`system`) | `envelope` (team) |
| `POST /cards/{id}/open-envelope` | — | `manage_cards` capability | `envelope` (team) |

`GET /projects/{id}` returns the project plus a `demands` breakdown counted **both**
ways: by `envelope_state` (`sealed`/`opened`) and by flow position (`upstream`/
`downstream`/`delivered`/`in_flight`/`blocked`), plus the project's `cards[]` sorted
sealed-first.

`POST /projects/{id}/demands` writes a genuine `card_movements` row
(`from_column_id: null`, matching card creation) even though the card is deliberately
bare (no assignee, no size). Real response (sealed demand created against `NGR`):

```json
201
{"id": "f6f5b5d3-...", "board_id": "eea60bad-...", "column_id": "28acf6e2-...",
 "project_id": "3240b816-...", "key": "NGR-202", "title": "Evaluate SSO for enterprise tier",
 "service_class": "standard", "requester": "Sales Engineering", "value_area": "feature",
 "demand_type": "value", "position": 25, "envelope_state": "sealed",
 "created_at": "2026-08-16T13:08:30Z", "opened_at": null, "committed_at": null,
 "delivered_at": null, "project_key": "NGR"}
```

`POST /cards/{id}/open-envelope` returns the **same `MoveResult` shape** as
`POST /cards/{id}/move` — `{card, movement, wip_override, warnings, wip, committed,
delivered}` — because opening an envelope really is a move (into the first upstream
non-envelope column), not a flag flip: it writes a `card_movements` row carrying the
seconds the demand spent sealed.

---

## 9. Cards, tasks, blockers, comments (CARDS)

Writes require `require_board_write` — resolved from the **board's** kind and the
caller's role (`master`/`administrator`: any board; `editor`/`system`: operational
boards only; `simulator`: simulator boards only; `viewer`: never). Reads require only a
valid token (`get_current_user`), no capability gate.

| Method & path | Query / body | Notes |
|---|---|---|
| `GET /cards` | `?board_id=&project_id=&service_class=&assignee_id=&segment=&column_id=&tag=&q=&is_blocked=&demand_type=&value_area=&size=&due_before=&archived=&envelope_state=&in_flight=&sort=&order=&limit=&offset=` | `project_id`/`assignee_id` accept `none`/`unassigned` for "no value"; `service_class`, `column_id`, `tag` accept comma-separated lists. `sort ∈ {created_at, aging, due_date, title, key, position}`. `envelope_state`, `in_flight`, `sort`, `order` are additive beyond SPEC §5's documented query set. |
| `POST /cards` | `{board_id, title, column_id?, project_id?, description?, service_class?, assignee_id?, requester?, size?, value_area?, demand_type?, due_date?, envelope_state?, tags?, custom_fields?}` | Writes the creation `card_movements` row (`from_column_id: null`); a card created at/past the commitment column is stamped `committed_at` immediately |
| `GET /cards/{id}` | — | Accepts id **or** card key (`NGR-104`). Returns card + `tasks[]` + `blockers[]` + `comments[]` + `movements[]` + `intervals[]` + `flow{}` (touch/wait/efficiency) + `can_edit` |
| `PATCH /cards/{id}` | subset of editable fields (title, description, project_id, service_class, assignee_id, requester, size, value_area, demand_type, due_date, position, tags, custom_fields, archived) | **`column_id` is deliberately not editable here** — `400 field_not_editable`; use `POST /cards/{id}/move`. One `card_events` row per changed field. |
| `DELETE /cards/{id}` | — | Soft (archives); idempotent |
| `POST /cards/{id}/tasks` / `PATCH /tasks/{id}` / `DELETE /tasks/{id}` | `{title, assignee_id?, position?, is_done?}` | Completing a task records `task_completed` |
| `POST /cards/{id}/block` | `{reason, category}` | `category ∈ {dependency, external_vendor, waiting_on_customer, technical, people, compliance, environment, other}`; `409 card_already_blocked` if one's already open |
| `POST /cards/{id}/unblock` | — | Closes every open blocker; `409 card_not_blocked` if none |
| `GET /cards/{id}/blockers` | — | Full blocker history, each with `duration_seconds`/`is_open` |
| `GET /cards/{id}/comments` / `POST /cards/{id}/comments` | `{body}` | — |
| `PATCH /comments/{id}` / `DELETE /comments/{id}` | `{body}` | Author or `master`/`administrator` only (`403 comment_not_yours`) |

Real `GET /cards?limit=2` (trimmed to one item):

```json
{"items": [{"id": "576147cc-...", "board_id": "eea60bad-...", "column_id": "28acf6e2-...",
  "key": "NGR-192", "title": "Backward drop target stays highlighted while dragging (phase 2)",
  "service_class": "standard", "size": "xl", "value_area": "defect", "demand_type": "failure",
  "envelope_state": "sealed", "is_blocked": false, "tags": ["regression"],
  "project_key": "NGR", "assignee_name": "Yuki Tanaka", "task_total": 0, "task_done": 0,
  "age_days": 1.79, "column_name": "Envelope", "column_kind": "envelope",
  "segment": "upstream", "is_in_flight": false}], "total": 266}
```

---

## 10. Movement & history (CARDS)

`POST /cards/{id}/move` is the single most important endpoint in the product — every
metric downstream replays what it writes. Every rule in SPEC §6 was verified against the
running code:

| Rule | Verified |
|---|---|
| Forward-only, against the **unified** flow order (all upstream before all downstream) | ✅ `409 backward_move_blocked` otherwise |
| `force: true` bypass restricted to `master`/`administrator` | ✅ (`FORCE_ROLES` in `movements.py`), records `is_backward=1` + a `backward_override` audit event |
| Entering the commitment column stamps `committed_at` once, immutably | ✅ `COALESCE(committed_at, ...)` in the same `UPDATE` |
| Entering the delivery column stamps `delivered_at` once | ✅ same mechanism |
| Sealed envelope can't move | ✅ `409 envelope_sealed` |
| Column/group/flow WIP maxima enforced inside the write transaction | ✅ re-reads the card row inside `db.tx()` before checking, so two concurrent moves can't both win the last slot |
| `expedite` bypasses the **column** maximum only (not group, not flow) | ✅ confirmed reading the code — group and flow checks have no expedite carve-out |
| WIP minima never block, only warn | ✅ surfaced as `warnings: ["... is starving ..."]` |
| `seconds_in_from_column` measured from the previous movement (or creation) | ✅ |

```
POST /cards/{id}/move             {to_column_id, note?, force?, source?}
                                   -> MoveResult (see below)
GET  /cards/{id}/history           ?order=asc|desc
                                   -> merged movements+events timeline, per-column
                                      dwell, running touch/wait totals   [flag: card_history]
GET  /cards/{id}/movements         -> raw movement rows only (no flag gate)
GET  /cards/{id}/events            -> raw audit events only             [flag: card_history]
```

`source` in the move body (`ui|api|mcp|seed|simulation`) is optional and exists so the
MCP server and a future simulator replay can label their own rows; the browser UI never
sends it (defaults to `"ui"`, or `"api"` automatically for the `system` role).

Real successful move (`Analysis Done` → `Build Done`, both queue columns, no WIP issue):

```json
200
{"card": {"id": "0ef930cf-...", "column_id": "3913fdaf-...", "key": "PAY-146", "...": "..."},
 "movement": {"id": "b409dbf1-...", "from_column_id": "883c0c25-...",
   "to_column_id": "3913fdaf-...", "moved_at": "2026-08-16T13:06:49Z",
   "seconds_in_from_column": 435097, "note": "analysis complete", "is_backward": false,
   "source": "ui", "from_column_name": "Analysis Done", "to_column_name": "Build Done"},
 "wip_override": false, "warnings": [],
 "wip": {"board_id": "eea60bad-...", "columns": {"...": "every column's live cell"},
         "groups": {"...": "..."}, "flow": {"count": 35, "min": 10, "max": 45, "state": "ok"}},
 "committed": false, "delivered": false,
 "from_column": {"id": "883c0c25-...", "name": "Analysis Done", "kind": "queue"},
 "to_column": {"id": "3913fdaf-...", "name": "Build Done", "kind": "queue"},
 "seconds_in_from_column": 435097, "duration_in_from_column": "5d", "is_backward": false}
```

`GET /cards/{id}/history` additionally returns a human-readable `timeline[]` (movements
and events merged and sorted, each entry carrying a plain-English `label` like
`"Moved from “Ideas” to “Discovery”"` and running `touch_seconds_total` /
`wait_seconds_total` / `flow_efficiency_pct`), a `dwell[]` per-column aggregate, and
`totals` (moves, backward_moves, events, columns_visited, plus the same touch/wait/
efficiency figures `flow_totals` computes for the card detail view).

---

## 11. Metrics (METRICS)

Read-only for every role (`read_boards_metrics` capability — everyone including
`viewer`/`simulator`/`system`). Every endpoint accepts the five common filters
(`?from=&to=&project_id=&service_class=&segment=`, `segment` defaulting to `unified`)
via a shared `common_filters` dependency, resolved into one `MetricsContext` snapshot per
request so every metric on a page reads the same window.

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

Real `GET /boards/{id}/metrics/summary` (Nagare Flow, unified segment, default 90-day
window; trimmed to a representative slice of the 22 KPIs actually returned):

```json
{"board_id": "eea60bad-...", "from": "2026-05-18T13:10:24Z", "to": "2026-08-16T13:10:24Z",
 "metrics": {
   "customer_lead_time": {"value": 30.92, "unit": "days", "trend_pct": -6.5,
     "sample_size": 51, "previous_value": 33.06, "low_confidence": false,
     "label": "Customer lead time", "higher_is_better": false},
   "flow_efficiency": {"value": 26.07, "unit": "percent", "trend_pct": -14.0,
     "sample_size": 51, "higher_is_better": true},
   "throughput": {"value": 3.97, "unit": "cards/week", "sample_size": 51},
   "wip": {"...": "..."}, "sle_attainment": {"...": "..."},
   "failure_demand": {"...": "..."}, "littles_law_coherence": {"...": "..."}
 },
 "percentiles": {"p50": 27.9778, "p70": 33.1424, "p85": 39.5045, "p95": 53.9083},
 "filters": {"from": "...", "to": "...", "as_of": "...", "project_id": null,
   "service_class": null, "segment": "unified", "include_envelope": false,
   "window_days": 90.0}}
```

⚠ **Field additive to `MetricValue` beyond `schemas.py`'s declared shape:** every metric
in `summary` also carries `higher_is_better: bool | null` (a chart-rendering hint —
`null` for touch time, where neither direction is unambiguously "better"). `SummaryOut`
and `MetricValue` in `schemas.py` use `extra="allow"`, so this is accepted, just not
declared in the pydantic model — worth adding to `schemas.py` if you want it in the
OpenAPI document.

`GET /boards/{id}/metrics/littles-law` real `meta` block (the coherence check SPEC §7.1
asks for, plus a plain-language verdict the SPEC doesn't explicitly require but the
implementation adds):

```json
{"avg_wip": 13.48, "wip_now": 35, "throughput_per_week": 3.97,
 "avg_system_lead_time_days": 16.93, "avg_customer_lead_time_days": 30.92,
 "predicted_wip": 9.59, "implied_lead_time_days": 23.79, "coherence_ratio": 1.405,
 "arrivals": 111, "departures": 51, "conservation_gap": 60, "stable": false,
 "verdict": "Observed WIP is well above throughput × cycle time — work is entering faster than it leaves, so lead times will keep growing."}
```

`GET /boards/{id}/metrics/kmm` returns the 7-level ladder exactly as SPEC §7.2 specifies
— `{level, name, focus, metrics: [{key, label, available, value, why}], readiness_pct}`
for ML0 through ML6 — plus `current_level` and `overall_readiness_pct` at the top. Each
metric's `why` is a full sentence naming which of Anderson's or Yoshima's practices it
evidences, e.g. *"Reading the number of items currently in progress is Anderson's ML1
threshold metric — the first number a team can state without any movement history at
all."*

`GET /boards/{id}/metrics/demand` real response includes `service_class_mix`,
`arrival_service_class_mix` (a second mix computed over **arrivals**, not just
deliveries — additive beyond SPEC), `demand_type_mix`, `value_area_mix`,
`failure_demand_pct`, `lead_time_by_class` (mean/p50/p85 per service class, each with a
`color_role` naming the design token to use), a `flow_debt` block comparing
expedite vs. standard p85 lead time, `per_project` breakdown, and `due_date_performance`.

---

## 12. Simulator (SIM) — not yet mounted

`docs/SPEC.md` §5/§8 specifies a `simulator` router with these routes:

```
GET  /simulator/boards
POST /simulator/boards                  {name, source_board_id?, template?}
POST /simulator/boards/{id}/reset
POST /simulator/boards/{id}/generate    {weeks, arrival_rate, class_mix, seed}
POST /simulator/runs                    {board_id, kind, params}
GET  /simulator/runs?board_id=
GET  /simulator/runs/{id}
POST /simulator/compare                 {run_ids: []}
```

**As of this writing, `backend/app/routers/simulator.py` does not exist.** Every
`/api/v1/simulator/*` request currently gets the SPA catch-all's `404 not_found` (see the
[error table](#21-error-codes-actually-raised)), and `GET /openapi.json` confirms no
`simulator` path is registered. `main.py` mounts routers defensively for exactly this
reason (concurrent agent development) — the rest of the app is unaffected.

What **does** already exist, ready to wire up: `backend/app/flow/forecast.py` is a
complete, tested-looking engine implementing every piece of SPEC §8 — deterministic
demand generation (`build_demand_stream`, seeded with `random.Random`), the WIP-limited
flow simulator itself (`simulate_flow`), both Monte Carlo questions
(`monte_carlo_when`, `monte_carlo_how_many`, each with a plain-language `_verdict`
helper), the WIP experiment (`wip_experiment`), the class-of-service mix experiment
(`class_of_service_experiment`), a historical replay (`replay`, `board_history`), and
run comparison (`compare_runs`). `schemas.py` also already declares the full contract
(`SimBoardCreate`, `SimGenerateRequest`, `SimRunCreate`, `SimRunOut`,
`SimCompareRequest`, `SimCompareOut`) and `simulation_runs` exists in the DDL. The
`simulator` feature flag is on in the seeded workspace. What's missing is purely the
`APIRouter` that turns these into the eight HTTP routes above — the SIM owner's next
step, not a redesign.

The MCP server (`mcp/nagare_mcp.py`) already has a `run_forecast` tool that calls
`POST /simulator/runs` — it will start working the moment the router is mounted, with no
changes needed on the MCP side.

---

## 13. Integrating with Nagare

`mcp/nagare_mcp.py` is a dependency-free (stdlib `json`/`sys`/`os`/`urllib.request` only)
**Model Context Protocol** server: JSON-RPC 2.0 over stdio, protocol version
`2024-11-05`. It authenticates against the REST API above with either a static token or
a login/password pair it exchanges for one lazily (and re-exchanges once, automatically,
on a `401`).

### Tool list (12 tools, verified against `TOOLS`/`HANDLERS` in the source)

| Tool | Calls | Notes |
|---|---|---|
| `list_boards` | `GET /boards` | `kind`, `include_archived` filters |
| `get_board` | `GET /boards/{id}` + `GET /boards/{id}/wip` | Merges structure and live WIP into one compact object |
| `list_cards` | `GET /cards` | Full filter set, `limit` capped at 500 |
| `get_card` | `GET /cards/{id}` (+ `/history` if `include_history`) | |
| `create_card` | `POST /cards` | |
| `move_card` | `POST /cards/{id}/move` | Surfaces `wip_override`/`committed`/`delivered`/`warnings` |
| `open_envelope` | `POST /cards/{id}/open-envelope` | |
| `list_projects` | `GET /projects` | |
| `create_demand` | `POST /projects/{id}/demands` | |
| `get_metrics_summary` | `GET /boards/{id}/metrics/summary` | |
| `get_kmm_status` | `GET /boards/{id}/metrics/kmm` | |
| `run_forecast` | `POST /simulator/runs` | **Currently fails**: the simulator router isn't mounted yet (§12). The tool itself is fully implemented and will work unmodified once it is. |

Every REST error above is translated into a tool-call error with the original `code`
prefixed (`[wip_limit_exceeded] The column WIP limit on...`) plus a canned `Hint:` for
the codes an agent is most likely to hit (`backward_move_blocked`,
`wip_limit_exceeded`, `envelope_sealed`, `feature_disabled`, `plan_upgrade_required`,
`forbidden`, `insufficient_role`, `not_found`, `validation_error`, `unauthorized`,
`interactive_login_denied`/`interactive_login_forbidden`, …) — see `_HINTS` in the file.
Card writes are attributed to the authenticated user and recorded with
`card_movements.source = "mcp"`.

### Exact config to add

Start the app first — the MCP server is a client of the REST API, not a separate
process with its own database:

```bash
python run.py                          # http://127.0.0.1:8770
```

Then, with a static bearer token (mint one via `POST /auth/login`):

```bash
claude mcp add nagare \
    --env NAGARE_URL=http://127.0.0.1:8770 \
    --env NAGARE_TOKEN=<jwt from POST /api/v1/auth/login> \
    -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py
```

Or let the MCP server sign itself in with workspace credentials (it will re-authenticate
automatically if the token expires mid-session):

```bash
claude mcp add nagare \
    --env NAGARE_URL=http://127.0.0.1:8770 \
    --env NAGARE_EMAIL=master@nagare.app \
    --env NAGARE_PASSWORD=nagare123 \
    -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py
```

Or commit a `.mcp.json` (identical shape works in Claude Desktop's
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nagare": {
      "command": "python",
      "args": ["C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py"],
      "env": {
        "NAGARE_URL": "http://127.0.0.1:8770",
        "NAGARE_TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
      }
    }
  }
}
```

Environment variables the server reads: `NAGARE_URL` (default
`http://127.0.0.1:8770`), `NAGARE_TOKEN` (takes precedence over email/password),
`NAGARE_EMAIL` / `NAGARE_PASSWORD` (used to mint a token lazily if no static token is
set), `NAGARE_TIMEOUT` (per-request seconds, default `30`).

Verify the wiring end-to-end against a **running** server:

```bash
python mcp/nagare_mcp.py --selftest       # exit 0 iff the full round trip succeeds
python mcp/nagare_mcp.py --print-tools    # dump the tool schemas as JSON, no server needed
```

`--selftest` will currently report `run_forecast` failing (simulator router not mounted)
while every other tool passes — that's the expected state until §12 is closed out; the
self-test's own assertions do not treat this as fatal to the other 11 tools.

Product-side gate: the `mcp_server` feature flag (business plan) is meant to gate this
integration in the UI/marketing sense; the MCP process itself only needs a bearer token
whose **role** is allowed to perform each action — there is no separate flag check inside
`nagare_mcp.py` or its target endpoints beyond the normal `require_flag` gates each REST
call already goes through.

---

## 14. Appendix: SPEC discrepancies found

Accuracy-over-volume notes, so whoever owns `docs/SPEC.md` can decide which side to fix.

1. **⚠ `feature_not_available` does not exist.** SPEC's error examples and the original
   task brief for this document both name it. The real code only ever raises
   `feature_disabled` (flag off) or `plan_upgrade_required` (plan too low) from
   `deps.require_flag`. `mcp/nagare_mcp.py`'s `_HINTS` dict and
   `tests/test_auth_rbac.py`'s `FLAG_DENIED_CODES` both defensively list
   `feature_not_available` alongside the two real codes, but nothing in the backend ever
   produces it. Treat any integration code that switches on `feature_not_available` as
   dead.

2. **⚠ Two near-duplicate "system can't log in" codes; only one is reachable.**
   `deps.assert_can_login` raises `interactive_login_forbidden` but is never called from
   anywhere in the routers (`grep` confirms zero call sites). The actual login path
   (`routers/auth.py::_assert_interactive`) raises `interactive_login_denied` instead.
   Both codes are pre-emptively documented in the MCP server's `_HINTS` map, which is
   the only reason `interactive_login_forbidden` appears in this codebase at all. If
   `deps.assert_can_login` is truly unused, it's a candidate for deletion; otherwise
   whoever intended to call it should.

3. **`simulator.py` is entirely unmounted** (SPEC §5, §8, §1's router list). See
   [§12](#12-simulator-sim--not-yet-mounted) for the full detail — this is the biggest
   gap between SPEC and the running app, though the underlying engine
   (`flow/forecast.py`) and schemas are already complete.

4. **`forbidden` is effectively a dead default.** SPEC's example error envelope uses
   `wip_limit_exceeded` as its illustration, not `forbidden`, so there's no direct SPEC
   contradiction — but it's worth knowing that of the ~20 call sites that raise
   `errors.Forbidden(...)` in this codebase, every single one supplies its own specific
   `code=`. The bare `"forbidden"` you see in `errors.py`'s class default is only
   reachable if some future code raises `Forbidden()` with no code, or if Starlette
   itself throws a raw 403 (it currently never does inside this app).

5. **Board-structure endpoints gate on a very permissive capability, then check the real
   role manually.** Not a functional bug (see §3) — documented so nobody mistakes
   `Depends(require_capability("read_boards_metrics"))` on `POST /boards/{id}/columns`
   etc. as meaning "any role can do this."

6. **Additive, not contradictory:** several routes and query parameters exist beyond
   SPEC §5's minimal list — `GET /boards/templates`, `GET /auth/capabilities`,
   `GET /flags/catalogue`, `GET /cards/{id}/movements`, `GET /cards/{id}/events`,
   `PATCH`/`DELETE /comments/{id}`, `DELETE /boards/{id}?hard=`, and the `sort`/`order`/
   `in_flight`/`envelope_state` card filters. None of these conflict with SPEC; they
   simply weren't enumerated there. `GET /openapi.json` is the ground truth for the
   complete current route table at any time (`curl -s :8770/openapi.json | jq '.paths
   | keys'`).

7. **`GET /openapi.json` and `/docs` live at the bare path, not under `/api/v1`** — SPEC
   §5's "Meta" section lists them without a prefix, which matches; called out here only
   because every other path in this document does carry `/api/v1` and it's an easy copy-
   paste mistake.
