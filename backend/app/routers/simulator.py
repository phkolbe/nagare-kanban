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

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import iso, new_id, utcnow
from ..deps import CurrentUser, get_board_or_404, get_session, require_capability, require_flag
from ..errors import BadRequest, NotFound
from ..flow import forecast
from ..models import Board, Card, CardEvent, CardMovement, SimulationRun, User, as_dict, json_dumps
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
from ..tenancy import in_product
from ..texts import N_, _
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

#: ``kind.replace("_", " ").title()`` for every run kind, as English catalog keys,
#: so a run's default name reads in the caller's language.
_KIND_TITLE: dict[str, str] = {
    "monte_carlo_when": N_("Monte Carlo When"),
    "monte_carlo_how_many": N_("Monte Carlo How Many"),
    "wip_experiment": N_("Wip Experiment"),
    "class_of_service_mix": N_("Class Of Service Mix"),
    "replay": N_("Replay"),
}

#: Service classes as written inside a sentence (the English text is the key itself).
_SERVICE_CLASS_WORD: dict[str, str] = {
    "expedite": N_("expedite"),
    "standard": N_("standard"),
    "fixed_date": N_("fixed_date"),
    "intangible": N_("intangible"),
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


async def _get_simulator_board(s: AsyncSession, board_id: str) -> dict[str, Any]:
    """A board row, guaranteed ``kind == 'simulator'`` (every write path, SPEC §8)."""
    board = await get_board_or_404(s, board_id)
    if board["kind"] != "simulator":
        raise BadRequest(
            "That board is not a simulator board.",
            code="not_a_simulator_board",
            details={"board_id": board["id"], "kind": board["kind"]},
        )
    return board


async def _clear_board_cards(s: AsyncSession, board_id: str) -> int:
    """Delete every card on a board. FK ``ON DELETE CASCADE`` takes the rest.

    ``card_movements``, ``card_events``, ``card_tasks``, ``blockers`` and
    ``comments`` all reference ``cards.id`` with ``ON DELETE CASCADE``, so one
    statement is enough — and, critically, it can never reach past the one
    board it was asked to clear. Runs in the caller's transaction.
    """
    count = int(await s.scalar(select(func.count()).where(Card.board_id == board_id)) or 0)
    await s.execute(delete(Card).where(Card.board_id == board_id))
    return count


def _board_key_prefix(board: dict[str, Any]) -> str:
    """A short, human card-key prefix derived from the board's own name."""
    letters = "".join(ch for ch in str(board.get("name") or "") if ch.isalpha())
    return letters.upper()[:4] or "SIM"


def _as_json(value: Any) -> Any:
    """Exactly what a JSON column will hand back (dates and other extras as text)."""
    return json.loads(json_dumps(value))


def _run_select() -> Any:
    """Runs of the current product's boards, with the author's name."""
    return (
        select(SimulationRun, User.name.label("created_by_name"))
        .join(Board, Board.id == SimulationRun.board_id)
        .outerjoin(User, User.id == SimulationRun.created_by)
        .where(*in_product(Board))
    )


def _run_row_out(row: Any) -> dict[str, Any]:
    """One ``simulation_runs`` row with the author's name."""
    out = as_dict(row[0]) or {}
    out["params"] = dict(out.get("params") or {})
    out["results"] = dict(out.get("results") or {})
    out["created_by_name"] = row._mapping["created_by_name"]
    return out


def _run_kind_title(kind: str) -> str:
    """The run kind in words, for a run saved without a name."""
    title = _KIND_TITLE.get(kind)
    return _(title) if title else kind.replace("_", " ").title()


async def _get_run_or_404(s: AsyncSession, run_id: str) -> dict[str, Any]:
    row = (await s.execute(_run_select().where(SimulationRun.id == run_id))).first()
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
async def list_simulator_boards(
    q: str | None = Query(None, description="Match the board name or slug"),
    archived: bool = Query(False),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Every ``kind='simulator'`` board, newest first, with a live card count."""
    conditions: list[Any] = [
        Board.kind == "simulator",
        Board.archived.is_(bool(archived)),
        *in_product(Board),
    ]
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(or_(Board.name.ilike(needle), Board.slug.ilike(needle)))
    card_count = (
        select(func.count())
        .where(Card.board_id == Board.id, Card.archived.is_(False))
        .correlate(Board)
        .scalar_subquery()
    )
    rows = await s.execute(
        select(Board, card_count.label("card_count"))
        .where(*conditions)
        .order_by(Board.created_at.desc())
    )
    items = [{**(as_dict(row[0]) or {}), "card_count": int(row[1] or 0)} for row in rows]
    return {"items": items, "total": len(items)}


@router.post(
    "/simulator/boards",
    response_model=BoardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a simulator board — blank/template, or a deep clone",
)
async def create_simulator_board(
    payload: SimBoardCreate,
    s: AsyncSession = Depends(get_session),
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
        return await clone_board(payload.source_board_id, clone_payload, s=s, user=user)

    create_payload = BoardCreateRequest(
        name=payload.name, kind="simulator", template=payload.template or "default"
    )
    return await create_board(create_payload, s=s, user=user)


@router.post(
    "/simulator/boards/{id}/reset",
    response_model=OkResponse,
    summary="Wipe a simulator board's cards and movement history",
)
async def reset_simulator_board(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Empty a simulator board so a fresh scenario can be generated onto it.

    The board's columns, groups and policies are untouched — only the content
    that ``generate`` or a clone-with-cards would have written is removed.
    """
    board = await _get_simulator_board(s, id)
    removed = await _clear_board_cards(s, board["id"])
    await s.commit()
    return {
        "ok": True,
        "id": board["id"],
        "message": _("Removed {count} card(s) and their history.", count=removed),
    }


@router.post(
    "/simulator/boards/{id}/generate",
    summary="Materialise a synthetic demand stream onto a simulator board",
)
async def generate_demand(
    id: str,
    payload: SimGenerateRequest,
    s: AsyncSession = Depends(get_session),
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
    board = await _get_simulator_board(s, id)
    columns = await canonical_columns(s, board["id"])
    if len(columns) < 2:
        raise BadRequest(
            "This board needs at least two columns before demand can flow through it.",
            code="board_has_no_columns",
            details={"board_id": board["id"]},
        )

    removed = await _clear_board_cards(s, board["id"]) if payload.reset else 0

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

    start_dt = datetime.now(UTC) - timedelta(days=result.horizon_days)
    specs = result.specs
    allocator = KeyAllocator(s, board["product_id"])
    prefix = _board_key_prefix(board)

    def _at(day: float) -> str:
        return iso(start_dt + timedelta(days=day))

    cards: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    id_by_index: dict[int, str] = {}
    for record in result.cards:
        card_id = new_id()
        id_by_index[record["index"]] = card_id
        created_at = _at(record["arrival_day"])
        cards.append(
            {
                "id": card_id,
                "product_id": board["product_id"],
                "board_id": board["id"],
                "column_id": specs[record["final_index"]].key,
                "project_id": None,
                "key": await allocator.take(prefix),
                "title": _(
                    "Simulated {service_class} demand #{number}",
                    service_class=_(
                        _SERVICE_CLASS_WORD.get(record["service_class"], record["service_class"])
                    ),
                    number=record["index"] + 1,
                ),
                "description": None,
                "service_class": record["service_class"],
                "assignee_id": None,
                "requester": _("Simulator"),
                "size": record["size"],
                "value_area": record["value_area"],
                "demand_type": record["demand_type"],
                "due_date": _at(record["due_day"]) if record["due_day"] is not None else None,
                "position": 0,
                "envelope_state": "opened",
                "is_blocked": False,
                "tags": ["simulated"],
                "custom_fields": {},
                "created_at": created_at,
                "opened_at": created_at,
                "committed_at": (
                    _at(record["committed_day"]) if record["committed_day"] is not None else None
                ),
                "delivered_at": (
                    _at(record["delivered_day"]) if record["delivered_day"] is not None else None
                ),
                "archived": False,
                "created_by": user.get("id"),
            }
        )
        events.append(
            {
                "id": new_id(),
                "card_id": card_id,
                "board_id": board["id"],
                "actor_id": user.get("id"),
                "event_type": "created",
                "field": None,
                "old_value": None,
                "new_value": None,
                "created_at": created_at,
            }
        )

    # Dense, deterministic per-column ordering — cosmetic only, but it keeps a
    # freshly generated board's card list as legible as a real one's.
    counters: dict[str, int] = {}
    for card in sorted(
        cards,
        key=lambda c: (c["column_id"], c["delivered_at"] or c["committed_at"] or c["created_at"]),
    ):
        card["position"] = counters.get(card["column_id"], 0)
        counters[card["column_id"]] = card["position"] + 1

    spec_by_key = {spec.key: spec for spec in specs}
    movements: list[dict[str, Any]] = []
    for m in result.movements:
        to_spec = spec_by_key[m["to_key"]]
        from_spec = spec_by_key.get(m["from_key"]) if m["from_key"] else None
        seconds = m["seconds_in_from_column"]
        movements.append(
            {
                "id": new_id(),
                "card_id": id_by_index[m["card_index"]],
                "board_id": board["id"],
                "from_column_id": m["from_key"],
                "to_column_id": m["to_key"],
                "from_segment": from_spec.segment if from_spec else None,
                "to_segment": to_spec.segment,
                "moved_at": _at(m["at_day"]),
                "moved_by": user.get("id"),
                "seconds_in_from_column": round(seconds) if seconds is not None else None,
                "note": None,
                "is_backward": False,
                "source": "simulation",
            }
        )

    for model, rows in ((Card, cards), (CardMovement, movements), (CardEvent, events)):
        for start in range(0, len(rows), 1000):
            await s.execute(insert(model), rows[start : start + 1000])
    await s.commit()

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


async def _observed_weekly_throughput(
    s: AsyncSession, board_id: str, weeks: int | None
) -> list[int]:
    """A board's own delivered-per-week history, for a Monte Carlo sample pool."""
    delivered: Sequence[str] = (
        await s.scalars(
            select(Card.delivered_at).where(
                Card.board_id == board_id,
                Card.delivered_at.is_not(None),
                Card.archived.is_(False),
            )
        )
    ).all()
    return forecast.weekly_throughput_from_deliveries(iter(delivered), weeks=weeks)


async def _run_kind(
    s: AsyncSession, board: dict[str, Any], kind: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch one ``simulation_runs.kind`` onto its ``forecast`` function.

    ``params`` is exactly the caller's request body — untouched — so the same
    dict is what gets persisted back onto the stored run.
    """
    if kind in _MONTE_CARLO_KINDS:
        samples = params.get("throughput_samples") or await _observed_weekly_throughput(
            s, board["id"], params.get("sample_weeks")
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
            await canonical_columns(s, board["id"]),
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
            await canonical_columns(s, board["id"]),
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
    # the last one; forecast.replay reads the board's own arrival history
    # through the session's synchronous face.
    return await s.run_sync(
        lambda session: forecast.replay(
            session,
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
    )


@router.post(
    "/simulator/runs",
    response_model=SimRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run a simulation and persist its parameters and results",
)
async def create_run(
    payload: SimRunCreate,
    s: AsyncSession = Depends(get_session),
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
    board = await get_board_or_404(s, payload.board_id)
    if payload.kind in _MONTE_CARLO_KINDS:
        await require_flag("monte_carlo")(s)

    try:
        results = await _run_kind(s, board, payload.kind, payload.params)
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

    run_id = new_id()
    s.add(
        SimulationRun(
            id=run_id,
            board_id=board["id"],
            name=payload.name or f"{_run_kind_title(payload.kind)} — {utcnow()}",
            kind=payload.kind,
            params=_as_json(payload.params),
            results=_as_json(results),
            created_at=utcnow(),
            created_by=user.get("id"),
        )
    )
    await s.commit()
    return await _get_run_or_404(s, run_id)


@router.get(
    "/simulator/runs",
    response_model=ListResponse[SimRunOut],
    summary="List saved simulation runs",
)
async def list_runs(
    board_id: str | None = Query(None),
    kind: str | None = Query(None),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    conditions: list[Any] = []
    if board_id:
        conditions.append(SimulationRun.board_id == board_id)
    if kind:
        conditions.append(SimulationRun.kind == kind)
    rows = await s.execute(
        _run_select().where(*conditions).order_by(SimulationRun.created_at.desc())
    )
    items = [_run_row_out(row) for row in rows]
    return {"items": items, "total": len(items)}


@router.get(
    "/simulator/runs/{id}",
    response_model=SimRunOut,
    summary="One saved simulation run",
)
async def get_run(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    return await _get_run_or_404(s, id)


@router.post(
    "/simulator/compare",
    response_model=SimCompareOut,
    summary="Compare saved runs side by side, with deltas and a verdict",
)
async def compare_runs(
    payload: SimCompareRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("simulator_boards")),
) -> dict[str, Any]:
    """Align 2–8 saved runs into one table (SPEC §8.6): metrics, deltas, verdict.

    The baseline is the run the person picked (``baseline_run_id``), moved to the
    front: the comparison measures every delta against its first run.
    """
    ordered = list(dict.fromkeys(payload.run_ids))
    if payload.baseline_run_id is not None:
        if payload.baseline_run_id not in ordered:
            raise BadRequest(
                "The baseline must be one of the compared runs.",
                code="compare_failed",
                details={"baseline_run_id": payload.baseline_run_id},
            )
        ordered.remove(payload.baseline_run_id)
        ordered.insert(0, payload.baseline_run_id)
    runs: list[dict[str, Any]] = []
    for run_id in ordered:
        run = await _get_run_or_404(s, run_id)
        run.pop("created_by_name", None)
        runs.append(run)

    try:
        comparison = forecast.compare_runs(runs)
    except ValueError as exc:
        raise BadRequest(str(exc), code="compare_failed") from exc

    return {
        "runs": [await _get_run_or_404(s, run["id"]) for run in runs],
        "metrics": comparison["metrics"],
        "rows": comparison["rows"],
        "deltas": comparison["deltas"],
        "verdict": comparison["verdict"],
        "baseline_run_id": runs[0]["id"],
    }
