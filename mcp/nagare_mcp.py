#!/usr/bin/env python
"""Nagare Kanban — Model Context Protocol server (stdio, JSON-RPC 2.0).

Gives an AI agent first-class access to a running Nagare Kanban workspace: read
boards and their WIP state, search cards, create and move work respecting the
forward-only movement policy, open sealed-envelope demands, pull flow metrics and
Kanban Maturity Model readiness, and launch Monte Carlo forecasts.

Dependency-free by design: only ``json``, ``sys``, ``os`` and ``urllib.request``
from the standard library. No MCP SDK, no HTTP client library. It speaks
JSON-RPC 2.0 over newline-delimited stdio, protocol version ``2024-11-05``.

-------------------------------------------------------------------------------
Setup
-------------------------------------------------------------------------------

1. Start the app (it serves the API the MCP server talks to)::

       python run.py                      # http://127.0.0.1:8770

2. Register the server with Claude Code::

       claude mcp add nagare \
           --env NAGARE_URL=http://127.0.0.1:8770 \
           --env NAGARE_TOKEN=<jwt from POST /api/v1/auth/login> \
           -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py

   Or, letting the server sign in for itself with workspace credentials::

       claude mcp add nagare \
           --env NAGARE_URL=http://127.0.0.1:8770 \
           --env NAGARE_EMAIL=master@nagare.app \
           --env NAGARE_PASSWORD=nagare123 \
           -- python C:/MyWorkspace/claude-code/meu-kanban/mcp/nagare_mcp.py

3. Or check a ``.mcp.json`` into the repository so the whole team gets it::

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

   The same block works in Claude Desktop's ``claude_desktop_config.json``.

Environment
    ``NAGARE_URL``       base URL of the running app (default ``http://127.0.0.1:8770``)
    ``NAGARE_TOKEN``     bearer token; takes precedence over email/password
    ``NAGARE_EMAIL``     workspace login used to mint a token on demand
    ``NAGARE_PASSWORD``  password for ``NAGARE_EMAIL``
    ``NAGARE_TIMEOUT``   per-request timeout in seconds (default ``30``)

Command line
    ``--selftest``  drive a scripted initialize / tools/list / tools/call round
                    trip through the real dispatcher against a live server and
                    print every result (exit code 1 on any failure)
    ``--url``, ``--token``, ``--email``, ``--password``, ``--timeout``
                    override the environment
    ``--verbose``   echo every JSON-RPC frame to stderr

The ``mcp_server`` feature flag (Business plan) gates the integration in the
product; this process only needs a token whose role may perform the action.
Card writes are attributed to the authenticated user and recorded with
``source = "mcp"`` in ``card_movements``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

SERVER_NAME = "nagare-kanban"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"

DEFAULT_URL = "http://127.0.0.1:8770"
API_PREFIX = "/api/v1"

# JSON-RPC 2.0 reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "Nagare Kanban is a flow-management board split by a commitment point: an "
    "upstream (discovery) half where options are kept open, and a downstream "
    "(delivery) half that is a pull system with WIP limits. Cards move forward "
    "only; a backward move is rejected unless an administrator forces it. "
    "Before creating or moving work, read the board with get_board to learn the "
    "column ids, their kind (queue = wait time, activity = touch time) and the "
    "current WIP state — a move into a column that is already at its maximum "
    "will be refused with wip_limit_exceeded. Sealed envelope demands must be "
    "opened with open_envelope before they can move. Prefer the probabilistic "
    "answers from get_metrics_summary and run_forecast over estimates."
)


# ---------------------------------------------------------------------------
# transport-level helpers
# ---------------------------------------------------------------------------


def _log(message: str) -> None:
    """Diagnostics go to stderr; stdout carries protocol frames only."""
    print(message, file=sys.stderr, flush=True)


class ToolError(Exception):
    """A tool failed in a way the model should see and can act on."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------


class NagareClient:
    """Minimal JSON client for the Nagare REST API.

    Authentication is resolved lazily and re-resolved once on a 401, so a long
    running MCP session survives token expiry when credentials are configured.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token or None
        self.email = email or None
        self.password = password or None
        self.timeout = timeout
        self._static_token = bool(token)
        self.identity: dict[str, Any] | None = None

    # -- auth ---------------------------------------------------------------

    def _login(self) -> str:
        """Exchange configured credentials for a bearer token."""
        if not (self.email and self.password):
            raise ToolError(
                "No Nagare credentials configured. Set NAGARE_TOKEN, or set "
                "NAGARE_EMAIL and NAGARE_PASSWORD so the MCP server can sign in.",
                {"base_url": self.base_url},
            )
        payload = self._request(
            "POST",
            "/auth/login",
            body={"email": self.email, "password": self.password},
            authenticated=False,
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ToolError("The login response did not contain an access token.")
        self.token = token
        self.identity = payload.get("user")
        return token

    def ensure_token(self) -> str:
        """Return a usable bearer token, signing in if necessary."""
        if self.token:
            return self.token
        return self._login()

    def whoami(self) -> dict[str, Any]:
        """The authenticated user (``GET /auth/me``), cached for the session."""
        if self.identity is None:
            self.identity = self.get("/auth/me")
        return self.identity

    # -- plumbing -----------------------------------------------------------

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{API_PREFIX}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        if clean:
            encoded = {}
            for key, value in clean.items():
                if isinstance(value, bool):
                    encoded[key] = "true" if value else "false"
                else:
                    encoded[key] = str(value)
            url = f"{url}?{urllib.parse.urlencode(encoded)}"
        return url

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
        authenticated: bool = True,
        _retry: bool = True,
    ) -> Any:
        url = self._url(path, params)
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"{SERVER_NAME}-mcp/{SERVER_VERSION}",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = f"Bearer {self.ensure_token()}"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            detail = _decode_json(raw)
            if (
                exc.code == 401
                and authenticated
                and _retry
                and not self._static_token
                and self.email
                and self.password
            ):
                # The cached token expired or was revoked: mint a new one once.
                self.token = None
                self.identity = None
                return self._request(method, path, params, body, authenticated, _retry=False)
            raise _api_error(exc.code, method, url, detail) from None
        except urllib.error.URLError as exc:
            raise ToolError(
                f"Cannot reach the Nagare API at {self.base_url} ({exc.reason}). "
                "Start it with `python run.py` or set NAGARE_URL.",
                {"url": url},
            ) from None
        except TimeoutError:
            raise ToolError(
                f"The Nagare API did not answer within {self.timeout:g}s ({method} {path}).",
                {"url": url},
            ) from None

        if not raw:
            return {}
        parsed = _decode_json(raw)
        if parsed is None:
            raise ToolError(f"The API returned a non-JSON body for {method} {path}.")
        return parsed

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, params=params, body=body if body is not None else {})

    def patch(self, path: str, body: Any = None) -> Any:
        return self._request("PATCH", path, body=body if body is not None else {})


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _api_error(status: int, method: str, url: str, detail: Any) -> ToolError:
    """Turn the Nagare error envelope into a message a model can act on."""
    code = "http_error"
    message = f"{method} {url} failed with HTTP {status}."
    details: dict[str, Any] = {}
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        envelope = detail["error"]
        code = str(envelope.get("code") or code)
        message = str(envelope.get("message") or message)
        details = envelope.get("details") or {}
    elif isinstance(detail, dict) and detail.get("detail"):
        message = str(detail["detail"])

    hint = _HINTS.get(code)
    text = f"[{code}] {message}" if code != "http_error" else message
    if hint:
        text = f"{text}\nHint: {hint}"
    return ToolError(
        text, {"code": code, "status": status, **({"details": details} if details else {})}
    )


#: Extra guidance attached to the error codes an agent is most likely to hit.
_HINTS: dict[str, str] = {
    "backward_move_blocked": (
        "Nagare is a pull system: cards move forward only. Re-read the board to "
        "confirm the column order, or ask an administrator to force the move."
    ),
    "wip_limit_exceeded": (
        "The target column, its group or the whole flow is at its WIP maximum. "
        "Finish or unblock something already in progress instead of starting more."
    ),
    "envelope_sealed": "Call open_envelope on this card before moving it.",
    "feature_not_available": "The workspace plan or a feature flag does not include this capability.",
    "feature_disabled": "A workspace administrator has switched this feature off.",
    "plan_upgrade_required": "This capability belongs to a higher plan tier.",
    "forbidden": "The authenticated role may not do this. Check GET /users/roles for the matrix.",
    "insufficient_role": "The authenticated role may not do this. Check GET /users/roles for the matrix.",
    "not_found": "Check the id: list_boards, list_cards and list_projects return the canonical ids.",
    "validation_error": "One or more arguments were rejected; see details.fields.",
    "unauthorized": "The token is missing or expired. Set NAGARE_TOKEN or NAGARE_EMAIL/NAGARE_PASSWORD.",
    "invalid_token": "The token is malformed or signed with a different secret.",
    "token_expired": "The token expired; restart the MCP server or supply a fresh NAGARE_TOKEN.",
    "standalone_only": "This tool needs the standalone Nagare login, not a Pampufa token.",
    "interactive_login_denied": "The system identity cannot sign in interactively; use a human account.",
    "interactive_login_forbidden": "The system identity cannot sign in interactively; use a human account.",
}


# ---------------------------------------------------------------------------
# payload shaping — keep tool results small enough to reason about
# ---------------------------------------------------------------------------


def _pick(source: Any, *keys: str) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {k: source[k] for k in keys if k in source and source[k] is not None}


def _items(payload: Any) -> list[Any]:
    """Unwrap ``{"items": [...], "total": n}`` or accept a bare list."""
    if isinstance(payload, dict):
        value = payload.get("items")
        return list(value) if isinstance(value, list) else []
    return list(payload) if isinstance(payload, list) else []


def _compact_board(board: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        board,
        "id",
        "name",
        "slug",
        "kind",
        "description",
        "wip_min",
        "wip_max",
        "sle_days",
        "sle_percentile",
        "count_envelope_in_metrics",
        "confirm_moves",
        "archived",
        "card_count",
        "source_board_id",
    )


def _compact_card(card: dict[str, Any]) -> dict[str, Any]:
    out = _pick(
        card,
        "id",
        "key",
        "title",
        "board_id",
        "column_id",
        "project_id",
        "project_key",
        "service_class",
        "demand_type",
        "value_area",
        "size",
        "assignee_id",
        "assignee_name",
        "envelope_state",
        "due_date",
        "age_days",
        "committed_at",
        "delivered_at",
        "created_at",
    )
    if card.get("is_blocked"):
        out["is_blocked"] = True
    tags = card.get("tags")
    if tags:
        out["tags"] = tags
    if card.get("task_total"):
        out["tasks"] = f"{card.get('task_done', 0)}/{card['task_total']}"
    return out


def _compact_movement(movement: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        movement,
        "moved_at",
        "from_column_name",
        "to_column_name",
        "from_column_id",
        "to_column_id",
        "seconds_in_from_column",
        "moved_by_name",
        "is_backward",
        "source",
        "note",
    )


def _wip_line(cell: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cell, dict):
        return {}
    return _pick(cell, "count", "min", "max", "state")


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------


def tool_list_boards(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = client.get("/boards", {"kind": args.get("kind")})
    boards = [_compact_board(b) for b in _items(payload)]
    if not args.get("include_archived"):
        boards = [b for b in boards if not b.get("archived")]
    return {"boards": boards, "total": len(boards)}


def tool_get_board(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    board_id = _require(args, "board_id")
    detail = client.get(f"/boards/{urllib.parse.quote(board_id)}")
    board = detail.get("board") if isinstance(detail.get("board"), dict) else detail
    columns = detail.get("columns") or board.get("columns") or []
    groups = detail.get("groups") or board.get("groups") or []

    wip: dict[str, Any] = {}
    try:
        wip = client.get(f"/boards/{urllib.parse.quote(board_id)}/wip") or {}
    except ToolError:
        # WIP is a convenience overlay; a board is still worth returning without it.
        wip = {}
    column_wip = wip.get("columns") or {}
    group_wip = wip.get("groups") or {}

    out_columns = []
    for column in sorted(columns, key=lambda c: c.get("position", 0)):
        row = _pick(
            column,
            "id",
            "name",
            "position",
            "segment",
            "kind",
            "group_id",
            "wip_min",
            "wip_max",
            "description",
        )
        if column.get("is_commitment_point"):
            row["is_commitment_point"] = True
        if column.get("is_delivery_point"):
            row["is_delivery_point"] = True
        cell = _wip_line(column_wip.get(column.get("id")))
        if cell:
            row["wip"] = cell
        out_columns.append(row)

    out_groups = []
    for group in sorted(groups, key=lambda g: g.get("position", 0)):
        row = _pick(group, "id", "name", "position", "wip_min", "wip_max")
        cell = _wip_line(group_wip.get(group.get("id")))
        if cell:
            row["wip"] = cell
        out_groups.append(row)

    return {
        "board": _compact_board(board),
        "groups": out_groups,
        "columns": out_columns,
        "flow_wip": _wip_line(wip.get("flow")),
        "commitment_point": next(
            (c["name"] for c in out_columns if c.get("is_commitment_point")), None
        ),
        "delivery_point": next(
            (c["name"] for c in out_columns if c.get("is_delivery_point")), None
        ),
    }


_CARD_FILTERS = (
    "board_id",
    "project_id",
    "service_class",
    "assignee_id",
    "segment",
    "column_id",
    "tag",
    "q",
    "is_blocked",
    "demand_type",
    "value_area",
    "size",
    "due_before",
    "archived",
)


def tool_list_cards(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    params = {key: args.get(key) for key in _CARD_FILTERS}
    payload = client.get("/cards", params)
    cards = _items(payload)
    limit = int(args.get("limit") or 50)
    limit = max(1, min(limit, 500))
    total = payload.get("total") if isinstance(payload, dict) else len(cards)
    shown = cards[:limit]
    result: dict[str, Any] = {
        "cards": [_compact_card(c) for c in shown],
        "returned": len(shown),
        "total": total if isinstance(total, int) else len(cards),
    }
    if len(cards) > len(shown):
        result["truncated"] = True
    return result


def tool_get_card(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    card_id = _require(args, "card_id")
    card = client.get(f"/cards/{urllib.parse.quote(card_id)}")
    out = dict(card)
    if not out.get("custom_fields"):
        out.pop("custom_fields", None)
    movements = out.get("movements")
    if isinstance(movements, list):
        out["movements"] = [_compact_movement(m) for m in movements]
    if args.get("include_history"):
        history = client.get(f"/cards/{urllib.parse.quote(card_id)}/history")
        out["events"] = history.get("events", [])
        if isinstance(history.get("movements"), list):
            out["movements"] = [_compact_movement(m) for m in history["movements"]]
    return out


_CARD_CREATE_FIELDS = (
    "board_id",
    "title",
    "column_id",
    "project_id",
    "description",
    "service_class",
    "assignee_id",
    "requester",
    "size",
    "value_area",
    "demand_type",
    "due_date",
    "tags",
    "custom_fields",
)


def tool_create_card(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "board_id")
    _require(args, "title")
    body = {k: args[k] for k in _CARD_CREATE_FIELDS if args.get(k) is not None}
    card = client.post("/cards", body)
    return {"created": True, "card": _compact_card(card)}


def tool_move_card(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    card_id = _require(args, "card_id")
    body = {"to_column_id": _require(args, "to_column_id")}
    if args.get("note"):
        body["note"] = args["note"]
    if args.get("force"):
        body["force"] = True
    result = client.post(f"/cards/{urllib.parse.quote(card_id)}/move", body)
    out: dict[str, Any] = {
        "moved": True,
        "card": _compact_card(result.get("card") or {}),
        "movement": _compact_movement(result.get("movement") or {}),
    }
    for key in ("wip_override", "committed", "delivered"):
        if result.get(key):
            out[key] = True
    if result.get("warnings"):
        out["warnings"] = result["warnings"]
    flow = (result.get("wip") or {}).get("flow") if isinstance(result.get("wip"), dict) else None
    if flow:
        out["flow_wip"] = _wip_line(flow)
    return out


def tool_open_envelope(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    card_id = _require(args, "card_id")
    result = client.post(f"/cards/{urllib.parse.quote(card_id)}/open-envelope")
    card = result.get("card") if isinstance(result, dict) and "card" in result else result
    return {"opened": True, "card": _compact_card(card or {})}


def tool_list_projects(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    payload = client.get("/projects", {"status": args.get("status"), "q": args.get("q")})
    projects = [
        _pick(
            p,
            "id",
            "key",
            "name",
            "status",
            "description",
            "owner_name",
            "target_date",
            "card_count",
            "sealed_count",
            "opened_count",
            "delivered_count",
            "in_flight_count",
        )
        for p in _items(payload)
    ]
    return {"projects": projects, "total": len(projects)}


_DEMAND_FIELDS = (
    "title",
    "board_id",
    "description",
    "requester",
    "service_class",
    "value_area",
    "demand_type",
    "size",
    "due_date",
    "tags",
    "sealed",
)


def tool_create_demand(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    project_id = _require(args, "project_id")
    _require(args, "title")
    body = {k: args[k] for k in _DEMAND_FIELDS if args.get(k) is not None}
    card = client.post(f"/projects/{urllib.parse.quote(project_id)}/demands", body)
    card = card.get("card") if isinstance(card, dict) and "card" in card else card
    return {"created": True, "demand": _compact_card(card or {})}


_METRIC_FILTERS = ("from", "to", "project_id", "service_class", "segment")


def tool_get_metrics_summary(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    board_id = _require(args, "board_id")
    params = {key: args.get(key) for key in _METRIC_FILTERS}
    payload = client.get(f"/boards/{urllib.parse.quote(board_id)}/metrics/summary", params)
    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    if isinstance(metrics, dict):
        trimmed = {}
        for key, metric in metrics.items():
            if isinstance(metric, dict):
                trimmed[key] = _pick(
                    metric,
                    "value",
                    "unit",
                    "trend_pct",
                    "previous_value",
                    "sample_size",
                    "low_confidence",
                    "label",
                )
            else:
                trimmed[key] = metric
        payload = dict(payload)
        payload["metrics"] = trimmed
    return payload if isinstance(payload, dict) else {"summary": payload}


def tool_get_kmm_status(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    board_id = _require(args, "board_id")
    payload = client.get(f"/boards/{urllib.parse.quote(board_id)}/metrics/kmm")
    if not isinstance(payload, dict):
        return {"kmm": payload}
    levels = []
    for level in payload.get("levels", []):
        row = _pick(level, "level", "name", "focus", "readiness_pct")
        metrics = level.get("metrics") or []
        row["available"] = [m.get("key") for m in metrics if m.get("available")]
        row["missing"] = [
            {"key": m.get("key"), "why": m.get("why")} for m in metrics if not m.get("available")
        ]
        levels.append(row)
    return {
        "board_id": payload.get("board_id"),
        "current_level": payload.get("current_level"),
        "overall_readiness_pct": payload.get("overall_readiness_pct"),
        "levels": levels,
    }


_FORECAST_PARAM_KEYS = (
    "items",
    "target_date",
    "days",
    "weeks",
    "trials",
    "start_date",
    "throughput_weeks",
    "backlog_size",
    "split_rate",
)


def tool_run_forecast(client: NagareClient, args: dict[str, Any]) -> dict[str, Any]:
    board_id = _require(args, "board_id")
    kind = args.get("kind") or "monte_carlo_when"
    params: dict[str, Any] = {}
    extra = args.get("params")
    if isinstance(extra, dict):
        params.update(extra)
    for key in _FORECAST_PARAM_KEYS:
        if args.get(key) is not None:
            params[key] = args[key]
    if kind == "monte_carlo_when":
        # The tool exposes the friendlier `items`, but the forecast API's parameter is
        # `backlog_size` (SPEC §8). Translate here rather than leaking the engine's
        # vocabulary into the tool schema — and accept either spelling from the caller.
        if "backlog_size" not in params and "items" in params:
            params["backlog_size"] = params.pop("items")
        if "backlog_size" not in params:
            raise ToolError("monte_carlo_when needs `items`: how many work items to forecast.")
    if kind == "monte_carlo_how_many":
        if not ({"target_date", "days", "weeks"} & params.keys()):
            raise ToolError(
                "monte_carlo_how_many needs a horizon: pass `target_date`, `days` or `weeks`."
            )
        # The forecast API reads only `days`; turn the friendlier horizons into it.
        params["days"] = _horizon_days(params)
    body = {"board_id": board_id, "kind": kind, "params": params}
    if args.get("name"):
        body["name"] = args["name"]
    run = client.post("/simulator/runs", body)
    results = run.get("results") if isinstance(run, dict) else None
    out: dict[str, Any] = {
        "run_id": run.get("id") if isinstance(run, dict) else None,
        "kind": kind,
        "params": params,
    }
    if isinstance(results, dict):
        # The full trial histogram is large and rarely useful to a model; the
        # percentiles and the verdict are what a forecast conversation needs.
        out["percentiles"] = results.get("percentiles")
        for key in (
            "summary",
            "verdict",
            "unit",
            "sample_size",
            "trials",
            "forecast",
            "distribution_summary",
        ):
            if results.get(key) is not None:
                out[key] = results[key]
        if out.get("percentiles") is None:
            out["results"] = results
    elif results is not None:
        out["results"] = results
    return out


def _horizon_days(params: dict[str, Any]) -> int:
    """The `days` of a how-many forecast from `days`, `weeks` or `target_date`."""
    weeks = params.pop("weeks", None)
    target = params.pop("target_date", None)
    if params.get("days") is not None:
        days = int(params["days"])
    elif weeks is not None:
        days = round(float(weeks) * 7)
    else:
        try:
            start = date.fromisoformat(str(params.get("start_date") or date.today().isoformat()))
            days = (date.fromisoformat(str(target)) - start).days
        except ValueError as error:
            raise ToolError(
                "`target_date` and `start_date` must be ISO dates (YYYY-MM-DD)."
            ) from error
    if days < 1:
        raise ToolError("The forecast horizon must be at least one day after the start date.")
    return days


def _require(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolError(f"Missing required argument `{key}`.")
    return str(value).strip() if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# tool catalogue
# ---------------------------------------------------------------------------

_SERVICE_CLASSES = ["expedite", "standard", "fixed_date", "intangible"]
_DEMAND_TYPES = ["value", "failure", "maintenance"]
_VALUE_AREAS = ["feature", "defect", "risk", "debt", "compliance"]
_SIZES = ["xs", "s", "m", "l", "xl"]


def _schema(properties: dict[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_boards",
        "description": (
            "List the boards in the workspace with their whole-flow WIP limits and "
            "service level expectation. Start here to discover board ids; every "
            "other board-scoped tool takes the id (or the slug) returned by this one."
        ),
        "inputSchema": _schema(
            {
                "kind": {
                    "type": "string",
                    "enum": ["operational", "simulator"],
                    "description": "Restrict to operational boards (real work) or simulator boards (experiments).",
                },
                "include_archived": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include archived boards in the result.",
                },
            }
        ),
    },
    {
        "name": "get_board",
        "description": (
            "Full structure of one board: column groups, ordered columns with their "
            "segment (upstream/downstream) and kind (envelope, queue = wait time, "
            "activity = touch time, done), the commitment and delivery points, and "
            "the live WIP count/limit/state for every column, group and the flow as "
            "a whole. Read this before creating or moving a card so you use real "
            "column ids and respect the limits."
        ),
        "inputSchema": _schema(
            {
                "board_id": {
                    "type": "string",
                    "description": "Board id (UUID) or slug, e.g. 'nagare-flow'.",
                }
            },
            ["board_id"],
        ),
    },
    {
        "name": "list_cards",
        "description": (
            "Search cards with the full filter set. Combine filters freely: they are "
            "ANDed. Returns a compact card projection; call get_card for the detail "
            "of a single card."
        ),
        "inputSchema": _schema(
            {
                "board_id": {"type": "string", "description": "Restrict to one board."},
                "project_id": {"type": "string", "description": "Restrict to one project."},
                "column_id": {"type": "string", "description": "Restrict to one column."},
                "segment": {
                    "type": "string",
                    "enum": ["upstream", "downstream"],
                    "description": "Discovery half or delivery half of the value stream.",
                },
                "service_class": {
                    "type": "string",
                    "enum": _SERVICE_CLASSES,
                    "description": "Class of service policy applied to the card.",
                },
                "assignee_id": {
                    "type": "string",
                    "description": "User id the card is assigned to.",
                },
                "demand_type": {
                    "type": "string",
                    "enum": _DEMAND_TYPES,
                    "description": "value = new demand, failure = rework caused by a defect, maintenance = keeping the lights on.",
                },
                "value_area": {"type": "string", "enum": _VALUE_AREAS},
                "size": {"type": "string", "enum": _SIZES},
                "tag": {"type": "string", "description": "Cards carrying this tag."},
                "q": {
                    "type": "string",
                    "description": "Free-text search over key, title and description.",
                },
                "is_blocked": {
                    "type": "boolean",
                    "description": "Only blocked (true) or only unblocked (false) cards.",
                },
                "due_before": {
                    "type": "string",
                    "description": "ISO date/datetime; cards due strictly before it.",
                },
                "archived": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include archived cards.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                    "description": "Maximum cards to return in this response.",
                },
            }
        ),
    },
    {
        "name": "get_card",
        "description": (
            "One card in full: fields, checklist tasks, blockers, comments and its "
            "movement trail (which column, when, and how long it sat in the previous "
            "one). Set include_history to also get the field-level audit events."
        ),
        "inputSchema": _schema(
            {
                "card_id": {"type": "string", "description": "Card id (UUID)."},
                "include_history": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also return the audit event trail (created/moved/updated/blocked...).",
                },
            },
            ["card_id"],
        ),
    },
    {
        "name": "create_card",
        "description": (
            "Create a card on a board. Without column_id the card lands in the first "
            "column of the board. Choose service_class deliberately: 'expedite' "
            "bypasses column WIP limits and should stay rare, 'fixed_date' needs a "
            "due_date, 'intangible' is improvement work with no external deadline."
        ),
        "inputSchema": _schema(
            {
                "board_id": {"type": "string", "description": "Target board id or slug."},
                "title": {"type": "string", "description": "Short, outcome-shaped card title."},
                "column_id": {
                    "type": "string",
                    "description": "Column to create the card in; defaults to the first column.",
                },
                "project_id": {"type": "string", "description": "Project the demand belongs to."},
                "description": {
                    "type": "string",
                    "description": "Longer description, markdown accepted.",
                },
                "service_class": {
                    "type": "string",
                    "enum": _SERVICE_CLASSES,
                    "default": "standard",
                    "description": "Class of service policy.",
                },
                "assignee_id": {"type": "string", "description": "User id to assign."},
                "requester": {"type": "string", "description": "Who asked for this work."},
                "size": {
                    "type": "string",
                    "enum": _SIZES,
                    "description": "Optional relative size.",
                },
                "value_area": {"type": "string", "enum": _VALUE_AREAS, "default": "feature"},
                "demand_type": {"type": "string", "enum": _DEMAND_TYPES, "default": "value"},
                "due_date": {
                    "type": "string",
                    "description": "ISO date; required in practice for fixed_date cards.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Free-form tags.",
                },
                "custom_fields": {
                    "type": "object",
                    "description": "Workspace-defined structured fields.",
                    "additionalProperties": True,
                },
            },
            ["board_id", "title"],
        ),
    },
    {
        "name": "move_card",
        "description": (
            "Pull a card into another column. Movement is forward only: a target "
            "earlier in the flow is rejected with backward_move_blocked unless an "
            "administrator passes force. Moving into the commitment point stamps "
            "committed_at, moving into the delivery point stamps delivered_at, and a "
            "target at its WIP maximum is rejected with wip_limit_exceeded (expedite "
            "cards are allowed through and flagged wip_override)."
        ),
        "inputSchema": _schema(
            {
                "card_id": {"type": "string", "description": "Card to move."},
                "to_column_id": {
                    "type": "string",
                    "description": "Destination column id, from get_board.",
                },
                "note": {
                    "type": "string",
                    "description": "Why the card moved; stored on the movement record.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Master/administrator only: permit a backward move and audit it.",
                },
            },
            ["card_id", "to_column_id"],
        ),
    },
    {
        "name": "open_envelope",
        "description": (
            "Unseal a sealed-envelope demand: reveals it, stamps opened_at and pulls "
            "it into the first real upstream column. Sealed demands cannot be moved "
            "or worked until this is called — that is the point, it keeps unstarted "
            "demand from looking like committed work."
        ),
        "inputSchema": _schema(
            {"card_id": {"type": "string", "description": "Sealed demand card id."}},
            ["card_id"],
        ),
    },
    {
        "name": "list_projects",
        "description": (
            "List projects with their demand roll-up (sealed, opened, in flight, "
            "delivered). Use the returned project id when creating demands or "
            "filtering cards and metrics."
        ),
        "inputSchema": _schema(
            {
                "status": {
                    "type": "string",
                    "enum": ["discovery", "active", "on_hold", "done", "cancelled"],
                    "description": "Restrict to one project status.",
                },
                "q": {"type": "string", "description": "Free-text search over name and key."},
            }
        ),
    },
    {
        "name": "create_demand",
        "description": (
            "Register a new demand against a project as a sealed envelope in the "
            "board's envelope column. Sealed demand is captured but deliberately not "
            "opened, so the discovery queue stays honest; call open_envelope when the "
            "team is ready to look at it. Pass sealed=false to skip the envelope."
        ),
        "inputSchema": _schema(
            {
                "project_id": {"type": "string", "description": "Project to attach the demand to."},
                "title": {"type": "string", "description": "What is being asked for."},
                "board_id": {
                    "type": "string",
                    "description": "Board to place it on; defaults to the workspace's operational board.",
                },
                "description": {"type": "string", "description": "Context, acceptance, links."},
                "requester": {"type": "string", "description": "Who raised the demand."},
                "service_class": {
                    "type": "string",
                    "enum": _SERVICE_CLASSES,
                    "default": "standard",
                },
                "value_area": {"type": "string", "enum": _VALUE_AREAS, "default": "feature"},
                "demand_type": {"type": "string", "enum": _DEMAND_TYPES, "default": "value"},
                "size": {"type": "string", "enum": _SIZES},
                "due_date": {"type": "string", "description": "ISO date for fixed-date demands."},
                "tags": {"type": "array", "items": {"type": "string"}},
                "sealed": {
                    "type": "boolean",
                    "default": True,
                    "description": "Create it sealed (default) or already opened.",
                },
            },
            ["project_id", "title"],
        ),
    },
    {
        "name": "get_metrics_summary",
        "description": (
            "Headline flow metrics for a board: lead times (customer, discovery, "
            "system), throughput, WIP, flow efficiency, aging, SLE attainment, "
            "due-date performance, failure demand share, the 50/70/85/95 percentiles "
            "and a Little's Law coherence check. Each metric carries its sample size "
            "and a low_confidence flag — quote that when you report a number."
        ),
        "inputSchema": _schema(
            {
                "board_id": {"type": "string", "description": "Board id or slug."},
                "from": {"type": "string", "description": "ISO date lower bound of the window."},
                "to": {"type": "string", "description": "ISO date upper bound of the window."},
                "project_id": {"type": "string", "description": "Restrict to one project."},
                "service_class": {"type": "string", "enum": _SERVICE_CLASSES},
                "segment": {
                    "type": "string",
                    "enum": ["upstream", "downstream", "unified"],
                    "description": "Which half of the value stream to measure.",
                },
            },
            ["board_id"],
        ),
    },
    {
        "name": "get_kmm_status",
        "description": (
            "Kanban Maturity Model readiness for a board, ML0 to ML6. For each level "
            "it reports which metrics the board can actually produce today, which are "
            "missing and why, and a readiness percentage — useful for advising a team "
            "on the next concrete practice to adopt rather than a vague 'be more mature'."
        ),
        "inputSchema": _schema(
            {"board_id": {"type": "string", "description": "Board id or slug."}},
            ["board_id"],
        ),
    },
    {
        "name": "run_forecast",
        "description": (
            "Run a Monte Carlo forecast from the board's own throughput history and "
            "return the percentile answers. kind='monte_carlo_when' answers 'how long "
            "for N items' (pass items); kind='monte_carlo_how_many' answers 'how many "
            "by a date' (pass target_date, days or weeks). Report the 85th percentile "
            "as the commitment date, never the average."
        ),
        "inputSchema": _schema(
            {
                "board_id": {"type": "string", "description": "Board to forecast from."},
                "kind": {
                    "type": "string",
                    "enum": ["monte_carlo_when", "monte_carlo_how_many"],
                    "default": "monte_carlo_when",
                    "description": "Which question to answer.",
                },
                "items": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "monte_carlo_when: number of work items remaining.",
                },
                "target_date": {
                    "type": "string",
                    "description": "monte_carlo_how_many: ISO date to forecast up to.",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "monte_carlo_how_many: horizon in days.",
                },
                "weeks": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "monte_carlo_how_many: horizon in weeks.",
                },
                "trials": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 200000,
                    "description": "Number of simulation trials (default 10000).",
                },
                "name": {"type": "string", "description": "Label stored with the saved run."},
                "params": {
                    "type": "object",
                    "description": "Additional engine parameters passed through untouched.",
                    "additionalProperties": True,
                },
            },
            ["board_id"],
        ),
    },
]

HANDLERS: dict[str, Callable[[NagareClient, dict[str, Any]], Any]] = {
    "list_boards": tool_list_boards,
    "get_board": tool_get_board,
    "list_cards": tool_list_cards,
    "get_card": tool_get_card,
    "create_card": tool_create_card,
    "move_card": tool_move_card,
    "open_envelope": tool_open_envelope,
    "list_projects": tool_list_projects,
    "create_demand": tool_create_demand,
    "get_metrics_summary": tool_get_metrics_summary,
    "get_kmm_status": tool_get_kmm_status,
    "run_forecast": tool_run_forecast,
}

# Every tool named in the SPEC must be catalogued and implemented; a mismatch
# here is a programming error, not a runtime condition.
assert {t["name"] for t in TOOLS} == set(HANDLERS), "tool catalogue and handlers disagree"


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


class MCPServer:
    """Protocol layer: turns JSON-RPC messages into REST calls and back."""

    def __init__(self, client: NagareClient, verbose: bool = False) -> None:
        self.client = client
        self.verbose = verbose
        self.initialized = False

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    @staticmethod
    def _tool_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
        if is_error:
            result["isError"] = True
        elif isinstance(payload, dict):
            # Clients that understand structured output get the object as-is.
            result["structuredContent"] = payload
        return result

    # -- dispatch -----------------------------------------------------------

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Handle one decoded JSON-RPC message. ``None`` means 'send nothing'."""
        if isinstance(message, list):
            # JSON-RPC batches are legal; answer each member that is a request.
            responses = [r for r in (self.handle(m) for m in message) if r is not None]
            return responses or None  # type: ignore[return-value]
        if not isinstance(message, dict):
            return self._error(None, INVALID_REQUEST, "A JSON-RPC message must be an object.")

        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error(request_id, INVALID_PARAMS, "`params` must be an object.")
        if not isinstance(method, str):
            return self._error(request_id, INVALID_REQUEST, "`method` is required.")

        is_notification = "id" not in message

        try:
            if method == "initialize":
                result = self.on_initialize(params)
            elif method in {"notifications/initialized", "initialized"}:
                self.initialized = True
                return None
            elif method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.on_tools_call(params)
            elif method in {"resources/list", "prompts/list"}:
                # Declared unsupported in capabilities, but some clients probe
                # anyway; an empty list is friendlier than a protocol error.
                result = {"resources": []} if method.startswith("resources") else {"prompts": []}
            elif method == "shutdown":
                result = {}
            else:
                if is_notification:
                    return None
                return self._error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
        except ToolError as exc:
            if is_notification:
                return None
            return self._error(request_id, INVALID_PARAMS, exc.message, exc.details or None)
        except Exception as exc:  # pragma: no cover - last-resort guard
            if is_notification:
                return None
            return self._error(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")

        if is_notification:
            return None
        return self._result(request_id, result)

    def on_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Answer the handshake, echoing a protocol version we can speak."""
        requested = params.get("protocolVersion")
        version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
        if version > PROTOCOL_VERSION:
            # Never claim a version newer than the one this server implements.
            version = PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }

    def on_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run one tool, mapping API failures onto ``isError`` tool results."""
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or name not in HANDLERS:
            known = ", ".join(sorted(HANDLERS))
            return self._tool_result(
                f"Unknown tool `{name}`. Available tools: {known}.", is_error=True
            )
        if not isinstance(arguments, dict):
            return self._tool_result("`arguments` must be an object.", is_error=True)

        try:
            payload = HANDLERS[name](self.client, arguments)
        except ToolError as exc:
            if self.verbose:
                _log(f"[nagare-mcp] {name} failed: {exc.message}")
            return self._tool_result(exc.message, is_error=True)
        except Exception as exc:  # pragma: no cover - defensive
            if self.verbose:
                _log(f"[nagare-mcp] {name} crashed: {type(exc).__name__}: {exc}")
            return self._tool_result(
                f"The tool `{name}` failed unexpectedly: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        return self._tool_result(payload)

    # -- stdio loop ---------------------------------------------------------

    def serve_stdio(self) -> int:
        """Read newline-delimited JSON-RPC from stdin until EOF."""
        _log(
            f"[nagare-mcp] serving {self.client.base_url} over stdio (protocol {PROTOCOL_VERSION})"
        )
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if self.verbose:
                _log(f"[nagare-mcp] --> {line}")
            try:
                message = json.loads(line)
            except ValueError:
                self._write(self._error(None, PARSE_ERROR, "Invalid JSON."))
                continue
            response = self.handle(message)
            if response is not None:
                self._write(response)
        return 0

    def _write(self, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if self.verbose:
            _log(f"[nagare-mcp] <-- {text[:400]}")
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------


def selftest(server: MCPServer) -> int:
    """Drive a scripted session through the real dispatcher against a live app.

    Returns 0 when every step passes. Everything goes through
    :meth:`MCPServer.handle`, so this exercises the same code path a client does.
    """
    failures: list[str] = []
    step = 0

    def rpc(method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        nonlocal step
        step += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            message["id"] = step
        response = server.handle(message)
        label = method if method != "tools/call" else f"tools/call {params.get('name')}"  # type: ignore[union-attr]
        if notify:
            print(f"  {step:>2}. {label:<34} -> (notification, no response)")
            return None
        if response is None:
            failures.append(f"{label}: no response")
            print(f"  {step:>2}. {label:<34} -> FAIL (no response)")
            return None
        if "error" in response:
            failures.append(f"{label}: {response['error'].get('message')}")
            print(f"  {step:>2}. {label:<34} -> FAIL {response['error'].get('message')}")
            return None
        return response.get("result")

    print(f"Nagare MCP self-test against {server.client.base_url}")
    print("-" * 72)

    init = rpc(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nagare-selftest", "version": SERVER_VERSION},
        },
    )
    if init:
        info = init.get("serverInfo", {})
        print(
            f"      protocol={init.get('protocolVersion')} "
            f"server={info.get('name')} {info.get('version')} "
            f"tools={'tools' in init.get('capabilities', {})}"
        )
    rpc("notifications/initialized", {}, notify=True)

    listing = rpc("tools/list")
    if listing:
        names = [t["name"] for t in listing.get("tools", [])]
        print(f"      {len(names)} tools: {', '.join(names)}")
        missing = set(HANDLERS) - set(names)
        if missing:
            failures.append(f"tools/list omitted {sorted(missing)}")

    def call(name: str, arguments: dict[str, Any], expect_error: bool = False) -> Any:
        result = rpc("tools/call", {"name": name, "arguments": arguments})
        if result is None:
            return None
        is_error = bool(result.get("isError"))
        text = ""
        content = result.get("content") or []
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text", ""))
        if is_error != expect_error:
            verdict = "FAIL"
            failures.append(f"{name}: {'unexpected error' if is_error else 'expected an error'}")
        else:
            verdict = "ok"
        preview = " ".join(text.split())[:150]
        print(f"      {verdict}: {preview}{'…' if len(text) > 150 else ''}")
        if is_error:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    boards = call("list_boards", {"kind": "operational"})
    board_id = None
    if isinstance(boards, dict) and boards.get("boards"):
        board_id = boards["boards"][0].get("id")

    if board_id:
        board = call("get_board", {"board_id": board_id})
        column_id = None
        if isinstance(board, dict) and board.get("columns"):
            column_id = board["columns"][0].get("id")
        call("list_cards", {"board_id": board_id, "limit": 3})
        if column_id:
            call("list_cards", {"column_id": column_id, "limit": 2})
        call("get_metrics_summary", {"board_id": board_id})
        call("get_kmm_status", {"board_id": board_id})
        call("run_forecast", {"board_id": board_id, "kind": "monte_carlo_when", "items": 10})
    else:
        failures.append("list_boards returned no operational board to test against")

    call("list_projects", {})
    cards = call("list_cards", {"limit": 1})
    if isinstance(cards, dict) and cards.get("cards"):
        call("get_card", {"card_id": cards["cards"][0]["id"]})

    # Contract checks that must fail: an unknown tool and a missing argument.
    call("no_such_tool", {}, expect_error=True)
    call("get_card", {"card_id": "00000000-0000-0000-0000-000000000000"}, expect_error=True)

    print("-" * 72)
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for failure in failures:
            print(f"  · {failure}")
        return 1
    print("PASSED — initialize, tools/list and every tools/call round trip succeeded.")
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def build_client(args: argparse.Namespace) -> NagareClient:
    base = args.url or os.environ.get("NAGARE_URL") or DEFAULT_URL
    timeout_raw = args.timeout or os.environ.get("NAGARE_TIMEOUT") or "30"
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 30.0
    return NagareClient(
        base_url=base,
        token=args.token or os.environ.get("NAGARE_TOKEN"),
        email=args.email or os.environ.get("NAGARE_EMAIL"),
        password=args.password or os.environ.get("NAGARE_PASSWORD"),
        timeout=timeout,
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):  # pragma: no cover - exotic shells
            pass

    parser = argparse.ArgumentParser(
        prog="nagare_mcp.py",
        description="Model Context Protocol server for Nagare Kanban (stdio, JSON-RPC 2.0).",
    )
    parser.add_argument("--url", default=None, help="Nagare base URL (env NAGARE_URL)")
    parser.add_argument("--token", default=None, help="bearer token (env NAGARE_TOKEN)")
    parser.add_argument("--email", default=None, help="login email (env NAGARE_EMAIL)")
    parser.add_argument("--password", default=None, help="login password (env NAGARE_PASSWORD)")
    parser.add_argument(
        "--timeout", default=None, help="request timeout in seconds (env NAGARE_TIMEOUT)"
    )
    parser.add_argument("--verbose", action="store_true", help="log every frame to stderr")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run a scripted initialize/tools-list/tools-call session against a live server",
    )
    parser.add_argument(
        "--print-tools",
        action="store_true",
        help="print the tool catalogue as JSON and exit (no server needed)",
    )
    args = parser.parse_args(argv)

    if args.print_tools:
        print(json.dumps({"tools": TOOLS}, indent=2))
        return 0

    server = MCPServer(build_client(args), verbose=args.verbose)

    if args.selftest:
        try:
            return selftest(server)
        except ToolError as exc:
            print(f"Self-test could not start: {exc.message}")
            return 1

    try:
        return server.serve_stdio()
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        return 0
    except BrokenPipeError:  # pragma: no cover - client went away
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
