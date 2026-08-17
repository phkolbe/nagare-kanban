"""Flow metrics: exact-value tests against a hand-built, fully deterministic board.

Every other test module in this suite reads the seeded "Nagare Flow" demo data,
which is realistic but not exact — nobody can hand-verify a mean lead time over
220 cards. This module instead builds a tiny synthetic board with known columns,
known cards and known movement timestamps, so every number asserted below is
computed twice: once by :mod:`backend.app.flow.metrics` / ``kmm``, and once by a
small reference implementation written directly in this file from the same raw
timestamps. When the two disagree, the metric — not the fixture — is wrong.

Board shape (upstream Backlog, then downstream Doing -> Waiting -> Done, with
Doing as the commitment point and Done as the delivery point) gives every card a
clean touch/wait split: ``Doing`` is the only ``activity`` column, ``Backlog``
and ``Waiting`` are ``queue`` columns, and ``Done`` is terminal. No envelope
column is used, so ``count_envelope_in_metrics`` stays off and the customer
clock starts at ``created_at`` for every card.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest

from backend.app.db import Database, connect, new_id
from backend.app.flow.kmm import compute_kmm
from backend.app.flow.metrics import (
    LOW_CONFIDENCE_SAMPLE,
    MetricsContext,
    aging,
    blockers,
    cfd,
    demand,
    distribution,
    flow_efficiency,
    littles_law,
    scatter,
    summary,
    throughput,
)

# ---------------------------------------------------------------------------
# the synthetic board
# ---------------------------------------------------------------------------

#: (key, name, segment, kind, position)
COLUMN_DEFS: tuple[tuple[str, str, str, str, int], ...] = (
    ("backlog", "Backlog", "upstream", "queue", 0),
    ("doing", "Doing", "downstream", "activity", 1),
    ("waiting", "Waiting", "downstream", "queue", 2),
    ("done", "Done", "downstream", "done", 3),
)
KIND_BY_COLUMN: dict[str, str] = {key: kind for key, _, _, kind, _ in COLUMN_DEFS}

#: Eight cards, each with a fully explicit movement history. "D" is the only
#: card left in flight (committed, never delivered) — everything else is
#: delivered so touch/wait, lead time and demand metrics have a real sample.
CARD_DEFS: tuple[dict[str, Any], ...] = (
    {
        "suffix": "A", "service_class": "standard", "demand_type": "value", "due_date": None,
        "moves": (
            ("backlog", "2026-01-01T00:00:00Z"),
            ("doing", "2026-01-02T00:00:00Z"),
            ("waiting", "2026-01-03T12:00:00Z"),
            ("done", "2026-01-04T00:00:00Z"),
        ),
    },
    {
        "suffix": "B", "service_class": "standard", "demand_type": "value", "due_date": None,
        "moves": (
            ("backlog", "2026-01-01T00:00:00Z"),
            ("doing", "2026-01-01T12:00:00Z"),
            ("waiting", "2026-01-03T12:00:00Z"),
            ("done", "2026-01-04T12:00:00Z"),
        ),
    },
    {
        "suffix": "C", "service_class": "expedite", "demand_type": "value", "due_date": None,
        "moves": (
            ("backlog", "2026-01-05T00:00:00Z"),
            ("doing", "2026-01-05T01:00:00Z"),
            ("done", "2026-01-05T04:00:00Z"),
        ),
    },
    {
        # In flight: committed, never delivered.
        "suffix": "D", "service_class": "standard", "demand_type": "value", "due_date": None,
        "moves": (
            ("backlog", "2026-01-10T00:00:00Z"),
            ("doing", "2026-01-10T06:00:00Z"),
        ),
    },
    {
        "suffix": "E", "service_class": "fixed_date", "demand_type": "value",
        "due_date": "2026-01-03T00:00:00Z",  # delivered late
        "moves": (
            ("backlog", "2026-01-01T00:00:00Z"),
            ("doing", "2026-01-01T06:00:00Z"),
            ("done", "2026-01-05T00:00:00Z"),
        ),
    },
    {
        "suffix": "F", "service_class": "fixed_date", "demand_type": "value",
        "due_date": "2026-01-10T00:00:00Z",  # delivered on time
        "moves": (
            ("backlog", "2026-01-01T00:00:00Z"),
            ("doing", "2026-01-01T12:00:00Z"),
            ("done", "2026-01-03T00:00:00Z"),
        ),
    },
    {
        "suffix": "G", "service_class": "standard", "demand_type": "failure", "due_date": None,
        "moves": (
            ("backlog", "2026-01-02T00:00:00Z"),
            ("doing", "2026-01-02T06:00:00Z"),
            ("done", "2026-01-03T06:00:00Z"),
        ),
    },
    {
        "suffix": "H", "service_class": "standard", "demand_type": "value", "due_date": None,
        "moves": (
            ("backlog", "2026-01-01T00:00:00Z"),
            ("doing", "2026-01-01T06:00:00Z"),
            ("done", "2026-01-03T06:00:00Z"),
        ),
        "blocker": {
            "category": "dependency",
            "reason": "Waiting on a vendor API key.",
            "started_at": "2026-01-01T12:00:00Z",
            "ended_at": "2026-01-01T18:00:00Z",  # resolved: 6h = 0.25 day
        },
    },
)

DELIVERED_SUFFIXES: tuple[str, ...] = tuple(
    c["suffix"] for c in CARD_DEFS if c["moves"][-1][0] == "done"
)
WINDOW_FROM = "2026-01-01T00:00:00Z"
WINDOW_TO = "2026-01-15T00:00:00Z"
NOW = "2026-01-15T00:00:00Z"


# ---------------------------------------------------------------------------
# reference math — independent of backend.app.flow, derived straight from the
# CARD_DEFS timestamps above
# ---------------------------------------------------------------------------


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _card_lead_times(card_def: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """(customer, discovery, system) lead times in days, straight from the moves."""
    moves = card_def["moves"]
    created = _dt(moves[0][1])
    committed = next((_dt(ts) for col, ts in moves if col == "doing"), None)
    last_col, last_ts = moves[-1]
    delivered = _dt(last_ts) if last_col == "done" else None
    customer = (delivered - created).total_seconds() / 86400.0 if delivered else None
    system = (
        (delivered - committed).total_seconds() / 86400.0
        if delivered and committed
        else None
    )
    discovery = (committed - created).total_seconds() / 86400.0 if committed else None
    return customer, discovery, system


def _card_touch_wait(card_def: dict[str, Any]) -> tuple[float, float]:
    """(touch_days, wait_days) — one interval per consecutive pair of moves."""
    touch = wait = 0.0
    moves = card_def["moves"]
    for index in range(len(moves) - 1):
        col_key, ts = moves[index]
        _, next_ts = moves[index + 1]
        seconds = (_dt(next_ts) - _dt(ts)).total_seconds()
        kind = KIND_BY_COLUMN[col_key]
        if kind == "activity":
            touch += seconds
        elif kind == "queue":
            wait += seconds
    return touch / 86400.0, wait / 86400.0


def _percentile_ref(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (R type 7), independent of metrics.percentile."""
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return ordered[0]
    rank = (p / 100.0) * (n - 1)
    low, high = int(rank // 1), int(-(-rank // 1))
    if low == high:
        return ordered[int(rank)]
    weight = rank - low
    return ordered[low] + weight * (ordered[high] - ordered[low])


def _week_start(moment: datetime) -> datetime:
    anchored = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return anchored - timedelta(days=anchored.weekday())


def _expected_weekly_counts(
    delivered_at: list[datetime], start: datetime, end: datetime
) -> list[int]:
    counts: list[int] = []
    cursor = _week_start(start)
    while cursor <= end:
        following = cursor + timedelta(days=7)
        counts.append(sum(1 for moment in delivered_at if cursor <= moment < following))
        cursor = following
    return counts


def _expected_daily_wip_avg(card_defs: tuple[dict[str, Any], ...], start: datetime, end: datetime) -> float:
    """Mirrors ``MetricsContext.daily_wip``'s one-sample-per-day loop exactly."""
    spans: list[tuple[datetime, datetime | None]] = []
    for card_def in card_defs:
        committed = next((_dt(ts) for col, ts in card_def["moves"] if col == "doing"), None)
        if committed is None:
            continue
        last_col, last_ts = card_def["moves"][-1]
        delivered = _dt(last_ts) if last_col == "done" else None
        spans.append((committed, delivered))

    counts: list[int] = []
    cursor = start
    while cursor <= end:
        counts.append(
            sum(1 for committed, delivered in spans if committed <= cursor and (delivered is None or delivered > cursor))
        )
        cursor += timedelta(days=1)
    return sum(counts) / len(counts)


# ---------------------------------------------------------------------------
# database construction — raw SQL, matching schema.sql exactly
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "board"


def _insert_board(db: Database, name: str, *, sle_days: int | None = 10) -> tuple[str, dict[str, dict[str, Any]]]:
    board_id = new_id()
    slug = f"{_slugify(name)}-{board_id[:8]}"
    db.ex(
        "INSERT INTO boards (id, name, slug, description, kind, source_board_id, "
        "count_envelope_in_metrics, confirm_moves, wip_min, wip_max, sle_days, "
        "sle_percentile, created_at, archived) VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?,?,0)",
        board_id, name, slug, "Synthetic test fixture", "operational",
        0, 0, None, None, sle_days, 85, "2020-01-01T00:00:00Z",
    )
    columns: dict[str, dict[str, Any]] = {}
    for key, col_name, segment, kind, position in COLUMN_DEFS:
        column_id = new_id()
        db.ex(
            "INSERT INTO columns (id, board_id, group_id, name, position, segment, kind, "
            "wip_min, wip_max, is_commitment_point, is_delivery_point, color, description) "
            "VALUES (?,?,NULL,?,?,?,?,NULL,NULL,?,?,NULL,NULL)",
            column_id, board_id, col_name, position, segment, kind,
            1 if key == "doing" else 0,
            1 if kind == "done" else 0,
        )
        columns[key] = {"id": column_id, "name": col_name, "segment": segment, "kind": kind}
    return board_id, columns


def _insert_card(
    db: Database, board_id: str, columns: dict[str, dict[str, Any]], card_def: dict[str, Any], key: str
) -> str:
    card_id = new_id()
    moves = card_def["moves"]
    created_at = moves[0][1]
    committed_at = next((ts for col, ts in moves if col == "doing"), None)
    last_col, last_ts = moves[-1]
    delivered_at = last_ts if last_col == "done" else None
    current_column = columns[last_col]
    blocker = card_def.get("blocker")
    is_blocked = 1 if (blocker and not blocker.get("ended_at")) else 0

    db.ex(
        "INSERT INTO cards (id, board_id, column_id, project_id, key, title, description, "
        "service_class, assignee_id, requester, size, value_area, demand_type, due_date, "
        "position, envelope_state, is_blocked, tags, custom_fields, created_at, opened_at, "
        "committed_at, delivered_at, archived, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        card_id, board_id, current_column["id"], None, key, f"Synthetic card {key}", None,
        card_def["service_class"], None, None, None, "feature", card_def["demand_type"],
        card_def.get("due_date"), 0, "opened", is_blocked, "[]", "{}",
        created_at, None, committed_at, delivered_at, 0, None,
    )

    previous_key: str | None = None
    previous_ts: str | None = None
    for col_key, ts in moves:
        column = columns[col_key]
        seconds_in_from = (
            int((_dt(ts) - _dt(previous_ts)).total_seconds()) if previous_ts else None
        )
        db.ex(
            "INSERT INTO card_movements (id, card_id, board_id, from_column_id, to_column_id, "
            "from_segment, to_segment, moved_at, moved_by, seconds_in_from_column, note, "
            "is_backward, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'seed')",
            new_id(), card_id, board_id,
            columns[previous_key]["id"] if previous_key else None,
            column["id"],
            columns[previous_key]["segment"] if previous_key else None,
            column["segment"],
            ts, None, seconds_in_from, None,
        )
        previous_key, previous_ts = col_key, ts

    if blocker:
        db.ex(
            "INSERT INTO blockers (id, card_id, reason, category, started_at, ended_at, "
            "created_by) VALUES (?,?,?,?,?,?,NULL)",
            new_id(), card_id, blocker["reason"], blocker["category"],
            blocker["started_at"], blocker.get("ended_at"),
        )
    return card_id


def _build_board(
    db: Database, name: str, card_defs: tuple[dict[str, Any], ...], *, sle_days: int | None = 10
) -> dict[str, Any]:
    board_id, columns = _insert_board(db, name, sle_days=sle_days)
    prefix = (re.sub(r"[^A-Z]", "", name.upper()) or "TST")[:4]
    card_ids = {
        card_def["suffix"]: _insert_card(db, board_id, columns, card_def, f"{prefix}-{card_def['suffix']}")
        for card_def in card_defs
    }
    board = db.q1("SELECT * FROM boards WHERE id = ?", board_id)
    assert board is not None
    board["count_envelope_in_metrics"] = bool(board["count_envelope_in_metrics"])
    board["confirm_moves"] = bool(board["confirm_moves"])
    board["archived"] = bool(board["archived"])
    return {"board": board, "columns": columns, "card_ids": card_ids}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mdb(db_path: Path) -> Iterator[Database]:
    """A dedicated connection to the shared session database, at module scope.

    The synthetic boards below are additive (new rows, new board ids) and never
    touch the seeded "Nagare Flow" data, so building them once per module and
    sharing them across every test in this file is both safe and fast.
    """
    database = Database(connect(db_path))
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(scope="module")
def synthetic(mdb: Database) -> Iterator[dict[str, Any]]:
    """The eight-card board every exact-value test in this module reads.

    Torn down at the end of the module: ``boards`` cascades to its columns,
    cards, movements and blockers, so this board never leaks into another test
    module's whole-database invariants (e.g. test_smoke_api's "every delivered
    card sits in *the* seeded delivery column" check).
    """
    built = _build_board(mdb, "Synthetic Metrics Board", CARD_DEFS)
    yield built
    mdb.ex("DELETE FROM boards WHERE id = ?", built["board"]["id"])


@pytest.fixture()
def ctx(mdb: Database, synthetic: dict[str, Any]) -> MetricsContext:
    """A fresh, fully pinned :class:`MetricsContext` over the synthetic board."""
    return MetricsContext(mdb, synthetic["board"], frm=WINDOW_FROM, to=WINDOW_TO, now=NOW)


@pytest.fixture(scope="module")
def empty_board(mdb: Database) -> Iterator[dict[str, Any]]:
    """Same column shape, zero cards — the "must not divide by zero" edge case."""
    built = _build_board(mdb, "Empty Metrics Board", ())
    yield built
    mdb.ex("DELETE FROM boards WHERE id = ?", built["board"]["id"])


@pytest.fixture(scope="module")
def single_card_board(mdb: Database) -> Iterator[dict[str, Any]]:
    """One delivered card and nothing else."""
    built = _build_board(mdb, "Single Card Board", (CARD_DEFS[0],))
    yield built
    mdb.ex("DELETE FROM boards WHERE id = ?", built["board"]["id"])


@pytest.fixture()
def disable_flag(db: Database):
    """``disable_flag("kmm_dashboard")`` — restored automatically after the test."""
    changed: list[str] = []

    def _disable(key: str) -> None:
        changed.append(key)
        db.ex("UPDATE feature_flags SET is_enabled = 0 WHERE key = ?", key)

    yield _disable
    for key in changed:
        db.ex("UPDATE feature_flags SET is_enabled = 1 WHERE key = ?", key)


# ---------------------------------------------------------------------------
# summary()
# ---------------------------------------------------------------------------


def test_summary_customer_lead_time_matches_reference(ctx: MetricsContext) -> None:
    data = summary(ctx)
    values = [
        _card_lead_times(c)[0] for c in CARD_DEFS if c["suffix"] in DELIVERED_SUFFIXES
    ]
    metric = data["metrics"]["customer_lead_time"]
    assert metric["sample_size"] == len(values) == 7
    assert metric["value"] == pytest.approx(round(sum(values) / len(values), 2), abs=0.01)
    # 7 delivered cards sits just below the SPEC §7.1 confidence threshold.
    assert metric["low_confidence"] is (len(values) < LOW_CONFIDENCE_SAMPLE)


def test_summary_wip_and_aging_reflect_the_one_in_flight_card(ctx: MetricsContext) -> None:
    data = summary(ctx)
    assert data["metrics"]["wip"]["value"] == 1
    assert data["metrics"]["average_age"]["value"] == pytest.approx(4.75, abs=0.01)
    assert data["metrics"]["oldest_age"]["value"] == pytest.approx(4.75, abs=0.01)

    expected_avg_wip = _expected_daily_wip_avg(CARD_DEFS, _dt(WINDOW_FROM), _dt(WINDOW_TO))
    assert data["metrics"]["avg_wip"]["value"] == pytest.approx(round(expected_avg_wip, 2), abs=0.01)


def test_summary_due_date_and_failure_demand(ctx: MetricsContext) -> None:
    data = summary(ctx)
    # Two fixed_date cards: E is late, F is on time.
    assert data["metrics"]["due_date_performance"]["value"] == pytest.approx(50.0, abs=0.01)
    assert data["metrics"]["due_date_performance"]["sample_size"] == 2
    # One failure-demand card (G) out of seven delivered.
    assert data["metrics"]["failure_demand"]["value"] == pytest.approx(100.0 / 7.0, abs=0.05)
    # sle_days=10 and every lead time is well under it.
    assert data["metrics"]["sle_attainment"]["value"] == pytest.approx(100.0, abs=0.01)


def test_summary_delivered_and_arrival_counts(ctx: MetricsContext) -> None:
    data = summary(ctx)
    assert data["metrics"]["delivered"]["value"] == 7
    assert data["metrics"]["arrival_rate"]["sample_size"] == 8  # every card "arrives"


# ---------------------------------------------------------------------------
# flow_efficiency() — touch/wait split
# ---------------------------------------------------------------------------


def test_flow_efficiency_matches_reference(ctx: MetricsContext) -> None:
    data = flow_efficiency(ctx)
    delivered_defs = [c for c in CARD_DEFS if c["suffix"] in DELIVERED_SUFFIXES]
    touch_total = wait_total = 0.0
    for card_def in delivered_defs:
        touch, wait = _card_touch_wait(card_def)
        touch_total += touch
        wait_total += wait
    expected_pct = touch_total / (touch_total + wait_total) * 100.0

    assert data["meta"]["touch_days"] == pytest.approx(round(touch_total, 2), abs=0.01)
    assert data["meta"]["wait_days"] == pytest.approx(round(wait_total, 2), abs=0.01)
    assert data["meta"]["flow_efficiency"] == pytest.approx(round(expected_pct, 2), abs=0.01)
    assert data["sample_size"] == 7


# ---------------------------------------------------------------------------
# distribution() and scatter() — percentiles
# ---------------------------------------------------------------------------


def test_distribution_percentiles_match_reference(ctx: MetricsContext) -> None:
    data = distribution(ctx, metric="customer")
    values = [_card_lead_times(c)[0] for c in CARD_DEFS if c["suffix"] in DELIVERED_SUFFIXES]
    for p in (50, 70, 85, 95):
        assert data["percentiles"][f"p{p}"] == pytest.approx(_percentile_ref(values, p), abs=1e-3)
    assert data["sample_size"] == 7
    assert data["low_confidence"] is True  # 7 < LOW_CONFIDENCE_SAMPLE


def test_scatter_matches_delivered_count_and_percentiles(ctx: MetricsContext) -> None:
    data = scatter(ctx, metric="customer")
    values = [_card_lead_times(c)[0] for c in CARD_DEFS if c["suffix"] in DELIVERED_SUFFIXES]
    dot_series = next(s for s in data["series"] if s["key"] == "delivered")
    assert len(dot_series["points"]) == 7
    for p in (50, 70, 85, 95):
        assert data["percentiles"][f"p{p}"] == pytest.approx(_percentile_ref(values, p), abs=1e-3)


# ---------------------------------------------------------------------------
# throughput() — weekly bucketing
# ---------------------------------------------------------------------------


def test_throughput_weekly_bucketing_matches_reference(ctx: MetricsContext) -> None:
    data = throughput(ctx, period="week")
    delivered_at = [
        _dt(c["moves"][-1][1]) for c in CARD_DEFS if c["suffix"] in DELIVERED_SUFFIXES
    ]
    expected_counts = _expected_weekly_counts(delivered_at, _dt(WINDOW_FROM), _dt(WINDOW_TO))
    actual_counts = [point["y"] for point in data["series"][0]["points"]]
    assert actual_counts == expected_counts
    assert sum(actual_counts) == 7
    assert data["meta"]["total"] == 7


# ---------------------------------------------------------------------------
# cfd() — monotonicity
# ---------------------------------------------------------------------------


def test_cfd_is_monotonic_and_reaches_every_card(ctx: MetricsContext) -> None:
    data = cfd(ctx)
    assert data["sample_size"] == 8  # all eight cards, including the one in flight
    assert len(data["series"]) == 4

    for series in data["series"]:
        cumulative = [point["cumulative"] for point in series["points"]]
        assert cumulative == sorted(cumulative), f"{series['label']} CFD band is not monotonic"
        assert all(point["y"] >= 0 for point in series["points"])

    backlog = next(s for s in data["series"] if s["label"] == "Backlog")
    done = next(s for s in data["series"] if s["label"] == "Done")
    assert backlog["points"][-1]["cumulative"] == 8
    assert done["points"][-1]["cumulative"] == 7


# ---------------------------------------------------------------------------
# aging() — the one in-flight card
# ---------------------------------------------------------------------------


def test_aging_tracks_the_only_in_flight_card(ctx: MetricsContext) -> None:
    data = aging(ctx)
    assert data["sample_size"] == 1
    point = data["series"][0]["points"][0]
    assert point["column_name"] == "Doing"
    assert point["days_in_flight"] == pytest.approx(4.75, abs=0.01)
    assert point["committed"] is True


# ---------------------------------------------------------------------------
# blockers()
# ---------------------------------------------------------------------------


def test_blockers_matches_the_single_recorded_blocker(ctx: MetricsContext) -> None:
    data = blockers(ctx)
    assert data["meta"]["blocker_count"] == 1
    assert data["meta"]["cards_affected"] == 1
    assert data["meta"]["total_blocked_days"] == pytest.approx(0.25, abs=0.001)
    assert data["meta"]["currently_blocked"] == 0  # it was resolved same day
    assert data["meta"]["pareto"][0]["category"] == "dependency"


# ---------------------------------------------------------------------------
# demand()
# ---------------------------------------------------------------------------


def test_demand_breakdown_matches_reference(ctx: MetricsContext) -> None:
    data = demand(ctx)
    meta = data["meta"]

    assert meta["due_date_performance"] == {
        "total": 2,
        "on_time": 1,
        "late": 1,
        "pct": pytest.approx(50.0, abs=0.01),
        "mean_lateness_days": pytest.approx(2.0, abs=0.01),
        "worst_lateness_days": pytest.approx(2.0, abs=0.01),
    }

    mix = {row["key"]: row["count"] for row in meta["service_class_mix"]}
    assert mix == {"standard": 4, "expedite": 1, "fixed_date": 2}

    demand_type_mix = {row["key"]: row["count"] for row in meta["demand_type_mix"]}
    assert demand_type_mix == {"value": 6, "failure": 1}
    assert meta["failure_demand_pct"] == pytest.approx(100.0 / 7.0, abs=0.05)

    assert meta["flow_debt"]["available"] is True


# ---------------------------------------------------------------------------
# littles_law()
# ---------------------------------------------------------------------------


def test_littles_law_avg_wip_matches_reference(ctx: MetricsContext) -> None:
    data = littles_law(ctx)
    expected_avg_wip = _expected_daily_wip_avg(CARD_DEFS, _dt(WINDOW_FROM), _dt(WINDOW_TO))
    assert data["meta"]["avg_wip"] == pytest.approx(round(expected_avg_wip, 2), abs=0.01)
    assert data["meta"]["wip_now"] == 1
    assert data["meta"]["departures"] == 7
    assert data["meta"]["arrivals"] == 8


# ---------------------------------------------------------------------------
# compute_kmm() — the maturity ladder
# ---------------------------------------------------------------------------


def test_kmm_ladder_on_the_synthetic_board(ctx: MetricsContext) -> None:
    result = compute_kmm(ctx)
    assert [level["level"] for level in result["levels"]] == [
        "ML0", "ML1", "ML2", "ML3", "ML4", "ML5", "ML6",
    ]
    by_level = {level["level"]: level for level in result["levels"]}

    assert by_level["ML0"]["readiness_pct"] == 100.0
    assert by_level["ML0"]["metrics"] == []

    assert by_level["ML1"]["readiness_pct"] == 100.0
    ml1 = {m["key"]: m["available"] for m in by_level["ML1"]["metrics"]}
    assert ml1 == {"board_in_use": True, "wip_count": True, "cards_visualised": True}

    # ML2 is fully satisfied: an average lead time, throughput, aging WIP, a
    # recorded blocker and more than one demand type all exist on this board.
    ml2 = {m["key"]: m["available"] for m in by_level["ML2"]["metrics"]}
    assert ml2 == {
        "lead_time_avg": True,
        "throughput": True,
        "wip_aging": True,
        "blocker_count": True,
        "work_item_types": True,
    }
    assert by_level["ML2"]["readiness_pct"] == 100.0

    # ML3 needs 8+ delivered cards for a trustworthy distribution; this board
    # only has 7, so the distribution- and efficiency-shaped metrics fall short.
    ml3 = {m["key"]: m["available"] for m in by_level["ML3"]["metrics"]}
    assert ml3["lead_time_distribution"] is False
    assert ml3["flow_efficiency"] is False
    assert ml3["due_date_performance"] is True
    assert ml3["class_of_service_mix"] is True
    assert by_level["ML3"]["readiness_pct"] < 80.0

    # The ladder tops out at ML2, and every metric carries a grounded "why".
    assert result["current_level"] == "ML2"
    assert all(m["why"] for level in result["levels"] for m in level["metrics"])
    assert result["next_actions"]
    assert any("Lead-time distribution" in action for action in result["next_actions"])


# ---------------------------------------------------------------------------
# edge cases — zero data and a single card must never divide by zero
# ---------------------------------------------------------------------------


def test_zero_data_board_does_not_crash_any_metric(mdb: Database, empty_board: dict[str, Any]) -> None:
    context = MetricsContext(mdb, empty_board["board"], frm=WINDOW_FROM, to=WINDOW_TO, now=NOW)

    summary_data = summary(context)
    assert summary_data["sample_size"] == 0
    assert summary_data["metrics"]["customer_lead_time"]["value"] is None
    assert summary_data["metrics"]["customer_lead_time"]["low_confidence"] is True
    assert summary_data["metrics"]["wip"]["value"] == 0

    dist = distribution(context)
    assert dist["sample_size"] == 0
    assert dist["series"][0]["points"] == []
    assert dist["percentiles"] == {}

    assert cfd(context)["sample_size"] == 0
    assert throughput(context)["meta"]["total"] == 0
    assert aging(context)["sample_size"] == 0

    blocker_data = blockers(context)
    assert blocker_data["meta"]["total_blocked_days"] == 0.0
    assert blocker_data["meta"]["blocker_count"] == 0

    demand_data = demand(context)
    assert demand_data["meta"]["delivered_total"] == 0
    assert demand_data["meta"]["due_date_performance"]["pct"] is None

    ll = littles_law(context)
    assert ll["meta"]["coherence_ratio"] is None
    assert ll["meta"]["avg_wip"] == 0.0
    assert "Nothing was delivered" in ll["meta"]["verdict"]

    kmm_result = compute_kmm(context)
    assert kmm_result["current_level"] == "ML0"
    ml1 = {m["key"]: m["available"] for m in kmm_result["levels"][1]["metrics"]}
    assert ml1["board_in_use"] is False
    assert ml1["wip_count"] is True  # the commitment/delivery columns still exist


def test_single_card_board_edge_cases(mdb: Database, single_card_board: dict[str, Any]) -> None:
    board = single_card_board["board"]
    context = MetricsContext(mdb, board, frm=WINDOW_FROM, to=WINDOW_TO, now=NOW)
    expected_customer, _, _ = _card_lead_times(CARD_DEFS[0])

    summary_data = summary(context)
    metric = summary_data["metrics"]["customer_lead_time"]
    assert metric["sample_size"] == 1
    assert metric["low_confidence"] is True
    assert metric["value"] == pytest.approx(round(expected_customer, 2), abs=0.01)

    dist = distribution(context)
    assert dist["sample_size"] == 1
    # With one observation every percentile collapses to that single value.
    assert dist["percentiles"]["p50"] == pytest.approx(expected_customer, abs=1e-3)
    assert dist["percentiles"]["p95"] == pytest.approx(expected_customer, abs=1e-3)

    assert aging(context)["sample_size"] == 0  # the only card was delivered, none in flight
    assert littles_law(context)["meta"]["wip_now"] == 0

    kmm_result = compute_kmm(context)
    # One delivered card is enough for ML1, not for ML2 (no in-flight aging, no
    # blockers, and only one demand type/service class).
    assert kmm_result["current_level"] == "ML1"


# ---------------------------------------------------------------------------
# HTTP surface — routing, RBAC and flag gating
# ---------------------------------------------------------------------------


def test_metrics_endpoint_requires_authentication(client, synthetic: dict[str, Any]) -> None:
    board_id = synthetic["board"]["id"]
    response = client.get(f"/api/v1/boards/{board_id}/metrics/summary")
    assert response.status_code == 401
    assert response.json()["error"]["code"]


@pytest.mark.parametrize(
    "role", ["viewer", "editor", "simulator", "administrator", "master"]
)
def test_summary_endpoint_is_readable_by_every_role(client, auth, synthetic: dict[str, Any], role: str) -> None:
    board_id = synthetic["board"]["id"]
    response = client.get(
        f"/api/v1/boards/{board_id}/metrics/summary?from={WINDOW_FROM}&to={WINDOW_TO}",
        headers=auth(role),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["board_id"] == board_id
    assert "customer_lead_time" in body["metrics"]


def test_cfd_endpoint_returns_a_chartable_envelope(client, auth, synthetic: dict[str, Any]) -> None:
    board_id = synthetic["board"]["id"]
    response = client.get(
        f"/api/v1/boards/{board_id}/metrics/cfd?from={WINDOW_FROM}&to={WINDOW_TO}",
        headers=auth("viewer"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "cfd"
    assert len(body["series"]) == 4
    for series in body["series"]:
        assert "points" in series
        assert "color_role" in series


def test_kmm_endpoint_matches_direct_computation(client, auth, synthetic: dict[str, Any]) -> None:
    board_id = synthetic["board"]["id"]
    response = client.get(
        f"/api/v1/boards/{board_id}/metrics/kmm?from={WINDOW_FROM}&to={WINDOW_TO}",
        headers=auth("viewer"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_level"] == "ML2"
    assert len(body["levels"]) == 7


def test_kmm_endpoint_is_guarded_by_its_feature_flag(
    client, auth, synthetic: dict[str, Any], disable_flag
) -> None:
    board_id = synthetic["board"]["id"]
    disable_flag("kmm_dashboard")
    response = client.get(f"/api/v1/boards/{board_id}/metrics/kmm", headers=auth("viewer"))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "feature_disabled"


def test_unknown_board_returns_404(client, auth) -> None:
    response = client.get(
        "/api/v1/boards/does-not-exist/metrics/summary", headers=auth("viewer")
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "board_not_found"
