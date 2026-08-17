"""Simulator boards, synthetic demand and the scenario engine (SPEC §5, §8).

This router is the HTTP face of :mod:`backend.app.flow.forecast` — a pure,
deterministic computation module written independently of FastAPI. Nothing here
re-implements Monte Carlo sampling, the discrete-event pull simulator or the
experiment machinery; every number in a response was produced by
``forecast.py`` and this file only does three things:

1. **Board lifecycle.** A simulator board is a first-class ``kind='simulator'``
   board (SPEC §2.2). It is created by deep-cloning an operational board's
   columns/groups/policies — reusing :func:`backend.app.routers.boards.clone_board`
   and :func:`backend.app.routers.boards.create_board` directly rather than
   duplicating their structural-invariant logic — reset (wipe its cards) and
   populated with synthetic demand via :func:`generate_demand`.
2. **Isolation.** Every write path below only ever touches the board addressed
   by its own ``{id}`` path parameter, and that board is verified
   ``kind == 'simulator'`` first. Generated cards/movements carry
   ``source='simulation'`` (SPEC §2.7) so an operational board's history and
   metrics can never be contaminated by a scenario.
3. **Runs.** ``POST /simulator/runs`` dispatches ``kind`` onto the matching
   ``forecast`` function, persists ``{params, results}`` verbatim as JSON in
   ``simulation_runs`` and returns the stored row. Because ``forecast`` is
   deterministic given ``seed``, replaying the exact same ``params`` always
   reproduces the exact same ``results`` — that determinism is the whole point
   of storing runs rather than recomputing them on every read.

RBAC (SPEC §4): simulator boards are fully manipulable only by
``master`` / ``administrator`` / ``simulator`` — enforced here through the
existing ``simulator_boards`` capability rather than a new one. Every route is
also gated behind the ``simulator`` feature flag, and the two probabilistic
forecasts additionally require ``monte_carlo`` (SPEC §3).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from fastapi import APIRouter, Depends, Query, status

from ..db import Database, iso, new_id, utcnow
from ..deps import CurrentUser, get_board_or_404, get_db, require_capability, require_flag
from ..errors import BadRequest, NotFound
from ..flow import forecast
from ..models import Card, CardEvent, CardMovement, SimulationRun, insert, insert_many
from ..schemas import (
    BoardCloneRequest,
    BoardOut,
    ListResponse,
    OkResponse,
    SimBoardCreate,
    SimCompareOut,
    SimCompareRequest,
    SimGenerateRequest,
    SimRunCreate,
    SimRunOut,
)
from .boards import (
    BoardCreateRequest,
    KeyAllocator,
    canonical_columns,
    clone_board,
    create_board,
)

router = APIRouter(
    tags=["simulator"],
    dependencies=[Depends(require_flag("simulator"))],
)

#: The two run kinds SPEC §3 gates behind the (separately purchasable) Monte
#: Carlo flag; the other three kinds are "simulator" — already gated above.
_MONTE_CARLO_KINDS: frozenset[str] = frozenset({"monte_carlo_when", "monte_carlo_how_many"})


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _like(value: str) -> str:
    return f"%{value.strip()}%"


def _get_simulator_board(db: Database, board_id: str) -> dict[str, Any]:
    """A board row, guaranteed ``kind == 'simulator'`` (every write path, SPEC §8)."""
    board = get_board_or_404(db, board_id)
    if board["kind"] != "simulator":
        raise BadRequest(
            "That board is not a simulator board.",
            code="not_a_simulator_board",
            details={"board_id": board["id"], "kind": board["kind"]},
        )
    return board


def _clear_board_cards(db: Database, board_id: str) -> int:
    """Delete every card on a board. FK ``ON DELETE CASCADE`` takes the rest.

    ``card_movements``, ``card_events``, ``card_tasks``, ``blockers`` and
    ``comments`` all reference ``cards.id`` with ``ON DELETE CASCADE``, so one
    statement is enough — and, critically, it can never reach past the one
    board it was asked to clear.
    """
    count = int(db.scalar("SELECT COUNT(*) FROM cards WHERE board_id = ?", board_id) or 0)
    with db.tx():
        db.ex("DELETE FROM cards WHERE board_id = ?", board_id)
    return count


def _board_key_prefix(board: dict[str, Any]) -> str:
    """A short, human card-key prefix derived from the board's own name."""
    letters = "".join(ch for ch in str(board.get("name") or "") if ch.isalpha())
    return letters.upper()[:4] or "SIM"


def _run_row_out(row: dict[str, Any]) -> dict[str, Any]:
    """Decode one ``simulation_runs`` row (params/results JSON -> dict)."""
    created_by_name = row.pop("created_by_name", None)
    out = SimulationRun.from_row(row).to_dict()
    out["created_by_name"] = created_by_name
    return out


def _get_run_or_404(db: Database, run_id: str) -> dict[str, Any]:
    row = db.q1(
        "SELECT r.*, u.name AS created_by_name FROM simulation_runs r "
        "LEFT JOIN users u ON u.id = r.created_by WHERE r.id = ?",
        run_id,
    )
    if row is None:
        raise NotFound("Simulation run", run_id, code="run_not_found")
    return _run_row_out(row)


# ---------------------------------------------------------------------------
# board lifecycle (SPEC §8.1)
# ---------------------------------------------------------------------------


@router.get(
    "/simulator/boards",
    response_model=ListResponse[BoardOut],
    summary="List simulator boards",
)
def list_simulator_boards(
    q: str | None = Query(None, description="Match the board name or slug"),
    archived: bool = Query(False),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Every ``kind='simulator'`` board, newest first, with a live card count."""
    where = ["b.kind = 'simulator'", "b.archived = ?"]
    params: list[Any] = [1 if archived else 0]
    if q:
        where.append("(b.name LIKE ? OR b.slug LIKE ?)")
        params.extend([_like(q), _like(q)])
    rows = db.q(
        "SELECT b.*, (SELECT COUNT(*) FROM cards c WHERE c.board_id = b.id AND c.archived = 0) "
        f"AS card_count FROM boards b WHERE {' AND '.join(where)} ORDER BY b.created_at DESC",
        params,
    )
    for row in rows:
        row["count_envelope_in_metrics"] = bool(row["count_envelope_in_metrics"])
        row["confirm_moves"] = bool(row["confirm_moves"])
        row["archived"] = bool(row["archived"])
    return {"items": rows, "total": len(rows)}


@router.post(
    "/simulator/boards",
    response_model=BoardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a simulator board — blank/template, or a deep clone",
)
def create_simulator_board(
    payload: SimBoardCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Build an isolated ``kind='simulator'`` board (SPEC §8.1).

    Cloning a source board copies its columns, groups and WIP policies — and,
    when ``copy_cards`` is set, its cards and movement history too — so a
    scenario always starts from a shape the team actually runs. Both branches
    call straight into :mod:`backend.app.routers.boards` so the structural
    invariants (dense positions, one derived commitment/delivery point, group
    contiguity) are enforced in exactly one place.
    """
    if payload.source_board_id:
        clone_payload = BoardCloneRequest(
            name=payload.name, kind="simulator", copy_cards=payload.copy_cards
        )
        return clone_board(payload.source_board_id, clone_payload, db=db, user=user)

    create_payload = BoardCreateRequest(
        name=payload.name, kind="simulator", template=payload.template or "default"
    )
    return create_board(create_payload, db=db, user=user)


@router.post(
    "/simulator/boards/{id}/reset",
    response_model=OkResponse,
    summary="Wipe a simulator board's cards and movement history",
)
def reset_simulator_board(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Empty a simulator board so a fresh scenario can be generated onto it.

    The board's columns, groups and policies are untouched — only the content
    that ``generate`` or a clone-with-cards would have written is removed.
    """
    board = _get_simulator_board(db, id)
    removed = _clear_board_cards(db, board["id"])
    return {
        "ok": True,
        "id": board["id"],
        "message": f"Removed {removed} card(s) and their history.",
    }


@router.post(
    "/simulator/boards/{id}/generate",
    summary="Materialise a synthetic demand stream onto a simulator board",
)
def generate_demand(
    id: str,
    payload: SimGenerateRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Run the deterministic pull simulator and write real cards + movements.

    ``forecast.simulate_flow`` produces the entire scenario in memory — every
    card's arrival, its path through the columns and its committed/delivered
    timestamps as day offsets from a synthetic "day zero" — and this endpoint's
    only job is to translate that into real ``cards`` and ``card_movements``
    rows, anchored so the scenario's last simulated day lands on *now*. Every
    row is stamped ``source='simulation'`` (SPEC §2.7): the normal board view
    and every ``/metrics`` endpoint can inspect this board exactly like real
    work, while ``kind='simulator'`` (enforced by :func:`_get_simulator_board`)
    guarantees an operational board's own history is never touched.
    """
    board = _get_simulator_board(db, id)
    columns = canonical_columns(db, board["id"])
    if len(columns) < 2:
        raise BadRequest(
            "This board needs at least two columns before demand can flow through it.",
            code="board_has_no_columns",
            details={"board_id": board["id"]},
        )

    removed = _clear_board_cards(db, board["id"]) if payload.reset else 0

    try:
        result = forecast.simulate_flow(
            columns,
            weeks=float(payload.weeks),
            arrival_rate=float(payload.arrival_rate),
            class_mix=payload.class_mix or None,
            seed=payload.seed,
            wip_limits=payload.wip_limits or None,
            collect_series=False,
            label="generate",
        )
    except ValueError as exc:
        raise BadRequest(str(exc), code="simulation_failed") from exc

    start_dt = datetime.now(timezone.utc) - timedelta(days=result.horizon_days)
    specs = result.specs
    allocator = KeyAllocator(db)
    prefix = _board_key_prefix(board)

    def _at(day: float) -> str:
        return iso(start_dt + timedelta(days=day))

    cards: list[Card] = []
    events: list[CardEvent] = []
    id_by_index: dict[int, str] = {}
    for record in result.cards:
        card_id = new_id()
        id_by_index[record["index"]] = card_id
        created_at = _at(record["arrival_day"])
        cards.append(
            Card(
                id=card_id,
                board_id=board["id"],
                column_id=specs[record["final_index"]].key,
                project_id=None,
                key=allocator.take(prefix),
                title=f"Simulated {record['service_class']} demand #{record['index'] + 1}",
                description=None,
                service_class=record["service_class"],
                assignee_id=None,
                requester="Simulator",
                size=record["size"],
                value_area=record["value_area"],
                demand_type=record["demand_type"],
                due_date=_at(record["due_day"]) if record["due_day"] is not None else None,
                position=0,
                envelope_state="opened",
                is_blocked=False,
                tags=["simulated"],
                custom_fields={},
                created_at=created_at,
                opened_at=created_at,
                committed_at=(
                    _at(record["committed_day"]) if record["committed_day"] is not None else None
                ),
                delivered_at=(
                    _at(record["delivered_day"]) if record["delivered_day"] is not None else None
                ),
                archived=False,
                created_by=user.get("id"),
            )
        )
        events.append(
            CardEvent(
                id=new_id(),
                card_id=card_id,
                board_id=board["id"],
                actor_id=user.get("id"),
                event_type="created",
                field=None,
                old_value=None,
                new_value=None,
                created_at=created_at,
            )
        )

    # Dense, deterministic per-column ordering — cosmetic only, but it keeps a
    # freshly generated board's card list as legible as a real one's.
    counters: dict[str, int] = {}
    for card in sorted(
        cards, key=lambda c: (c.column_id, c.delivered_at or c.committed_at or c.created_at)
    ):
        card.position = counters.get(card.column_id, 0)
        counters[card.column_id] = card.position + 1

    spec_by_key = {spec.key: spec for spec in specs}
    movements: list[CardMovement] = []
    for m in result.movements:
        to_spec = spec_by_key[m["to_key"]]
        from_spec = spec_by_key.get(m["from_key"]) if m["from_key"] else None
        seconds = m["seconds_in_from_column"]
        movements.append(
            CardMovement(
                id=new_id(),
                card_id=id_by_index[m["card_index"]],
                board_id=board["id"],
                from_column_id=m["from_key"],
                to_column_id=m["to_key"],
                from_segment=from_spec.segment if from_spec else None,
                to_segment=to_spec.segment,
                moved_at=_at(m["at_day"]),
                moved_by=user.get("id"),
                seconds_in_from_column=int(round(seconds)) if seconds is not None else None,
                note=None,
                is_backward=False,
                source="simulation",
            )
        )

    with db.tx():
        insert_many(db, cards)
        insert_many(db, movements)
        insert_many(db, events)

    return {
        "board_id": board["id"],
        "reset": payload.reset,
        "removed_cards": removed,
        "cards_created": len(cards),
        "movements_created": len(movements),
        "weeks": payload.weeks,
        "arrival_rate": payload.arrival_rate,
        "seed": payload.seed,
        "class_mix": payload.class_mix,
        "start_date": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": result.metrics,
    }


# ---------------------------------------------------------------------------
# runs (SPEC §8.3–§8.6)
# ---------------------------------------------------------------------------


def _observed_weekly_throughput(
    db: Database, board_id: str, weeks: int | None
) -> list[int]:
    """A board's own delivered-per-week history, for a Monte Carlo sample pool."""
    rows = db.q(
        "SELECT delivered_at FROM cards WHERE board_id = ? AND delivered_at IS NOT NULL "
        "AND archived = 0",
        board_id,
    )
    return forecast.weekly_throughput_from_deliveries(
        (row["delivered_at"] for row in rows), weeks=weeks
    )


def _run_kind(db: Database, board: dict[str, Any], kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one ``simulation_runs.kind`` onto its ``forecast`` function.

    ``params`` is exactly the caller's request body — untouched — so the same
    dict is what gets persisted back onto the stored run.
    """
    if kind in _MONTE_CARLO_KINDS:
        samples = params.get("throughput_samples") or _observed_weekly_throughput(
            db, board["id"], params.get("sample_weeks")
        )
        trials = int(params.get("trials", forecast.DEFAULT_TRIALS))
        seed = int(params.get("seed", 20260816))
        if kind == "monte_carlo_when":
            return forecast.monte_carlo_when(
                samples,
                int(params["backlog_size"]),
                trials=trials,
                seed=seed,
                start_date=params.get("start_date"),
            )
        return forecast.monte_carlo_how_many(
            samples,
            int(params["days"]),
            trials=trials,
            seed=seed,
            start_date=params.get("start_date"),
        )

    if kind == "wip_experiment":
        # An explicit `null` disables the whole-flow cap entirely (every row
        # relies only on its per-column buffer scale); omitting the key falls
        # back to the board's own configured policy, as SPEC §8.4 intends.
        baseline_flow = (
            params["baseline_flow_wip_max"]
            if "baseline_flow_wip_max" in params
            else board.get("wip_max")
        )
        return forecast.wip_experiment(
            canonical_columns(db, board["id"]),
            weeks=float(params.get("weeks", 16.0)),
            arrival_rate=float(params.get("arrival_rate", 8.0)),
            class_mix=params.get("class_mix"),
            size_mix=params.get("size_mix"),
            seed=int(params.get("seed", 20260816)),
            configs=params.get("configs"),
            baseline_flow_wip_max=baseline_flow,
            target_utilization=float(params.get("target_utilization", 0.85)),
            variability=float(params.get("variability", 0.6)),
            warmup_weeks=params.get("warmup_weeks"),
        )

    if kind == "class_of_service_mix":
        return forecast.class_of_service_experiment(
            canonical_columns(db, board["id"]),
            weeks=float(params.get("weeks", 16.0)),
            arrival_rate=float(params.get("arrival_rate", 8.0)),
            seed=int(params.get("seed", 20260816)),
            expedite_shares=params.get("expedite_shares") or forecast.DEFAULT_EXPEDITE_SHARES,
            class_mix=params.get("class_mix"),
            size_mix=params.get("size_mix"),
            wip_limits=params.get("wip_limits"),
            flow_wip_max=params.get("flow_wip_max"),
            target_utilization=float(params.get("target_utilization", 0.85)),
            variability=float(params.get("variability", 0.6)),
            warmup_weeks=params.get("warmup_weeks"),
            expedite_preemption=bool(params.get("expedite_preemption", True)),
        )

    # kind == "replay" — SimulationKind covers exactly five values, so this is
    # the last one; forecast.replay reads the board's own arrival history.
    return forecast.replay(
        db,
        board["id"],
        policies=params.get("policies"),
        seed=int(params.get("seed", 20260816)),
        weeks=params.get("weeks"),
        use_observed_service_times=bool(params.get("use_observed_service_times", True)),
        target_utilization=float(params.get("target_utilization", 0.85)),
        baseline_flow_wip_max=params.get("baseline_flow_wip_max"),
        class_mix=params.get("class_mix"),
        warmup_weeks=params.get("warmup_weeks"),
    )


@router.post(
    "/simulator/runs",
    response_model=SimRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run a simulation and persist its parameters and results",
)
def create_run(
    payload: SimRunCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Compute one scenario and store ``{params, results}`` verbatim (SPEC §13).

    The board addressed by ``board_id`` may be operational or simulator: every
    kind here is read-only analysis (it never writes a card), so replaying a
    real board's own history under new policies — the flagship use of
    ``replay`` — works directly against that board, with no clone required.
    Determinism follows from ``forecast`` alone: identical ``params`` (same
    ``seed``, same everything) always produce byte-identical ``results``.
    """
    board = get_board_or_404(db, payload.board_id)
    if payload.kind in _MONTE_CARLO_KINDS:
        require_flag("monte_carlo")(db)

    try:
        results = _run_kind(db, board, payload.kind, payload.params)
    except (KeyError, TypeError) as exc:
        raise BadRequest(
            f"Missing or invalid parameter for '{payload.kind}': {exc}",
            code="invalid_simulation_params",
            details={"kind": payload.kind, "params": payload.params},
        ) from exc
    except ValueError as exc:
        raise BadRequest(
            str(exc), code="simulation_failed", details={"kind": payload.kind}
        ) from exc

    run = SimulationRun(
        id=new_id(),
        board_id=board["id"],
        name=payload.name or f"{payload.kind.replace('_', ' ').title()} — {utcnow()}",
        kind=payload.kind,
        params=payload.params,
        results=results,
        created_at=utcnow(),
        created_by=user.get("id"),
    )
    insert(db, run)
    return _get_run_or_404(db, run.id)


@router.get(
    "/simulator/runs",
    response_model=ListResponse[SimRunOut],
    summary="List saved simulation runs",
)
def list_runs(
    board_id: str | None = Query(None),
    kind: str | None = Query(None),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []
    if board_id:
        where.append("r.board_id = ?")
        params.append(board_id)
    if kind:
        where.append("r.kind = ?")
        params.append(kind)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = db.q(
        "SELECT r.*, u.name AS created_by_name FROM simulation_runs r "
        f"LEFT JOIN users u ON u.id = r.created_by {clause} ORDER BY r.created_at DESC",
        params,
    )
    items = [_run_row_out(row) for row in rows]
    return {"items": items, "total": len(items)}


@router.get(
    "/simulator/runs/{id}",
    response_model=SimRunOut,
    summary="One saved simulation run",
)
def get_run(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    return _get_run_or_404(db, id)


@router.post(
    "/simulator/compare",
    response_model=SimCompareOut,
    summary="Compare saved runs side by side, with deltas and a verdict",
)
def compare_runs(
    payload: SimCompareRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Align 2–8 saved runs into one table (SPEC §8.6): metrics, deltas, verdict."""
    runs: list[dict[str, Any]] = []
    for run_id in payload.run_ids:
        row = db.q1("SELECT * FROM simulation_runs WHERE id = ?", run_id)
        if row is None:
            raise NotFound("Simulation run", run_id, code="run_not_found")
        runs.append(SimulationRun.from_row(row).to_dict())

    try:
        comparison = forecast.compare_runs(runs)
    except ValueError as exc:
        raise BadRequest(str(exc), code="compare_failed") from exc

    return {
        "runs": [_get_run_or_404(db, run["id"]) for run in runs],
        "metrics": comparison["metrics"],
        "rows": comparison["rows"],
        "deltas": comparison["deltas"],
        "verdict": comparison["verdict"],
    }
