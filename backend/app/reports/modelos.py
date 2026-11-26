"""The report templates of wave 1 (Reports plan, 24/11/2026).

Each template is an async function ``build(s, request) -> Document`` (see :mod:`.documento`)
and an entry in :data:`TEMPLATES`. They compute nothing new where the metrics engine already
does: the board reports read :mod:`backend.app.flow.metrics` through two
:class:`~backend.app.flow.metrics.MetricsContext` (the period and the one before), so the
numbers match the Metrics screen for the same period.

=========  ======================  =========================================================
Code       Template                Reads
=========  ======================  =========================================================
REP-01     ``delivery_review``     summary, throughput, scatter, aging, blockers, demand, Little
REP-02     ``project_status``      the project's cards, aging, blockers, hours
REP-03     ``hours``               time entries by client and by project
REP-04     ``portfolio``           every product the person opens (``products_for``)
REP-05     ``risks``               blockers
REP-11     ``waiting``             flow efficiency and time per column (movements)
REP-13     ``policy_exceptions``   WIP overrides, WIP limit changes, class changes
=========  ======================  =========================================================
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..deps import get_board_or_404
from ..errors import BadRequest, NotFound
from ..flow.intervals import DAY
from ..flow.metrics import (
    MetricsContext,
    aging,
    blockers,
    demand,
    flow_efficiency,
    littles_law,
    percentile,
    scatter,
    summary,
    throughput,
)
from ..integration import products_for
from ..models import (
    Board,
    BoardColumn,
    Card,
    CardEvent,
    CardMovement,
    Project,
    TimeEntry,
    User,
    WipLimitChange,
    as_dict,
)
from ..tenancy import current_product, in_product, set_current_product
from ..texts import N_, _, date_label
from .documento import (
    FORMAT_VERSION,
    Period,
    bars,
    kpi,
    kpi_from_summary,
    kpis,
    note,
    section,
    table,
)

__all__ = ["TEMPLATES", "ReportRequest", "Template", "template_or_400"]


@dataclass
class ReportRequest:
    """What a report was asked for: the period and the scope (board, project or projects)."""

    period: Period
    user: dict[str, Any]
    board_id: str | None = None
    project_id: str | None = None
    project_ids: list[str] = field(default_factory=list)

    def params(self) -> dict[str, Any]:
        return {
            "period": self.period.kind,
            "from": self.period.start.isoformat(),
            "to": self.period.end.isoformat(),
            "board_id": self.board_id,
            "project_id": self.project_id,
            "project_ids": self.project_ids or None,
        }


Builder = Callable[[AsyncSession, ReportRequest], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Template:
    id: str
    code: str
    title: str
    description: str
    #: what the report needs: a ``board``, a ``project``, optional ``projects`` or nothing
    scope: str
    flag: str
    build: Builder

    def card(self) -> dict[str, Any]:
        """The catalogue entry, in the caller's language."""
        return {
            "id": self.id,
            "code": self.code,
            "title": _(self.title),
            "description": _(self.description),
            "scope": self.scope,
            "flag": self.flag,
        }


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


async def _contexts(
    s: AsyncSession, board: dict[str, Any], period: Period, project_id: str | None = None
) -> tuple[MetricsContext, MetricsContext]:
    """The period and the previous period of one board, read once each."""
    start, end = period.window
    previous_start, previous_end = period.previous_window

    def build(session: Any) -> tuple[MetricsContext, MetricsContext]:
        return (
            MetricsContext(session, board, frm=start, to=end, project_id=project_id),
            MetricsContext(
                session, board, frm=previous_start, to=previous_end, project_id=project_id
            ),
        )

    return await s.run_sync(build)


def _document(
    template: str,
    code: str,
    title: str,
    subtitle: str | None,
    request: ReportRequest,
    scope: dict[str, Any],
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "template": template,
        "code": code,
        "title": title,
        "subtitle": subtitle,
        "format_version": FORMAT_VERSION,
        "product_id": current_product(),
        "params": request.params(),
        "period": request.period.describe(),
        "previous": request.period.describe_previous(),
        "generated_at": utcnow(),
        "scope": scope,
        "sections": sections,
    }


def _card(key: Any, title: Any) -> dict[str, Any]:
    return {"key": key, "title": title}


async def _names(s: AsyncSession) -> dict[str, str]:
    return {u.id: u.name for u in await s.scalars(select(User))}


def _board_scope(board: dict[str, Any]) -> dict[str, Any]:
    return {
        "board": {
            "id": board["id"],
            "name": board.get("name"),
            "sle_days": board.get("sle_days"),
            "sle_percentile": board.get("sle_percentile"),
        }
    }


async def _board(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    if not request.board_id:
        raise BadRequest("Choose a board.", code="report_board_required")
    return await get_board_or_404(s, request.board_id)


def _class_label(key: Any) -> str:
    return _(str(key)) if key else "—"


# ---------------------------------------------------------------------------
# REP-01 delivery review
# ---------------------------------------------------------------------------


async def delivery_review(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    board = await _board(s, request)
    now, before = await _contexts(s, board, request.period)
    current, previous = summary(now), summary(before)
    sle_days = board.get("sle_days")

    # 1. deliveries and pace
    p50_now = (current.get("percentiles") or {}).get("p50")
    p50_before = (previous.get("percentiles") or {}).get("p50")
    pace = [
        kpi_from_summary(current, previous, "delivered"),
        kpi_from_summary(current, previous, "throughput"),
        kpi(
            "lead_time_p50",
            _("Lead time (50th pct)"),
            p50_now,
            "days",
            previous=p50_before,
            higher_is_better=False,
            hint=_("Half of the delivered work finished within this many days."),
        ),
        kpi_from_summary(current, previous, "customer_lead_time_p85"),
        kpi_from_summary(current, previous, "system_lead_time"),
    ]
    weekly = throughput(now, period="week", rolling=4)
    bars_per_week = [
        {
            "label": _(
                "Week of {day}",
                day=date_label(date.fromisoformat(str(week)[:10]), request.period.end),
            ),
            "value": point.get("y") or 0,
        }
        for week, point in zip(
            weekly.get("categories") or [],
            (weekly["series"][0]["points"] if weekly["series"] else []),
            strict=False,
        )
    ]
    points = scatter(now, metric="customer")["series"][0]["points"]
    delivered_rows = [
        {
            "date": p.get("date"),
            "card": _card(p.get("key"), p.get("title")),
            "project": p.get("project") or "—",
            "service_class": _class_label(p.get("service_class")),
            "lead_time": p.get("customer_days"),
            "within_sle": (
                (_("yes") if float(p.get("customer_days") or 0) <= float(sle_days) else _("no"))
                if sle_days
                else "—"
            ),
        }
        for p in sorted(points, key=lambda p: p.get("x") or "")
    ]
    section_pace = section(
        "pace",
        _("Deliveries and pace"),
        [
            kpis(pace),
            bars(bars_per_week, title=_("Delivered per week"), unit="count"),
            table(
                [
                    ("date", _("Delivered on"), "date"),
                    ("card", _("Card"), "card"),
                    ("project", _("Project"), "text"),
                    ("service_class", _("Class of service"), "text"),
                    ("lead_time", _("Lead time (days)"), "days"),
                    ("within_sle", _("Within the SLE"), "text"),
                ],
                delivered_rows,
                title=_("What was delivered"),
                empty=_("Nothing was delivered in this period."),
            ),
        ],
    )

    # 2. due dates and aging
    ages = aging(now)
    late = [
        p
        for p in ages["series"][0]["points"]
        if p.get("committed") and (p.get("over_sle") or p.get("band") in ("p85", "p95"))
    ]
    late.sort(key=lambda p: -(p.get("days_in_flight") or 0))
    section_aging = section(
        "aging",
        _("Due dates and aging"),
        [
            kpis(
                [
                    kpi_from_summary(current, previous, "sle_attainment"),
                    kpi_from_summary(current, previous, "wip"),
                    kpi_from_summary(current, previous, "average_age"),
                    kpi_from_summary(current, previous, "oldest_age"),
                ]
            ),
            note(
                _(
                    "Service level expectation: {percentile}% of the work delivered "
                    "within {days} days.",
                    percentile=board.get("sle_percentile") or 85,
                    days=sle_days,
                )
                if sle_days
                else _("This board has no service level expectation (SLE) set.")
            ),
            table(
                [
                    ("card", _("Card"), "card"),
                    ("column", _("Column"), "text"),
                    ("age", _("Age"), "days"),
                    ("band", _("Past the"), "text"),
                    ("blocked", _("Blocked"), "text"),
                ],
                [
                    {
                        "card": _card(p.get("key"), p.get("title")),
                        "column": p.get("column_name"),
                        "age": p.get("days_in_flight"),
                        "band": _("SLE") if p.get("over_sle") else str(p.get("band") or "").upper(),
                        "blocked": _("yes") if p.get("is_blocked") else "",
                    }
                    for p in late
                ],
                title=_(
                    "Cards in progress past the 85th percentile or the SLE at the end of the period"
                ),
                empty=_("No card in progress past the 85th percentile or the SLE."),
            ),
        ],
    )

    # 3. blockers and classes of service
    blocked = blockers(now)
    mix = demand(now)["meta"]
    section_blockers = section(
        "blockers",
        _("Blockers and classes of service"),
        [
            kpis(
                [
                    kpi_from_summary(current, previous, "blocked_time"),
                    kpi_from_summary(current, previous, "blocked_cards"),
                ]
            ),
            bars(
                [
                    {"label": row["label"], "value": row["days"], "note": row["count"]}
                    for row in blocked["meta"]["pareto"]
                ],
                title=_("Blocked days by cause"),
                unit="days",
                empty=_("No blocker in this period."),
            ),
            table(
                [
                    ("label", _("Class of service"), "text"),
                    ("count", _("Delivered"), "count"),
                    ("pct", _("Share"), "percent"),
                    ("p50_days", _("Lead time (50th pct)"), "days"),
                    ("p85_days", _("Lead time (85th pct)"), "days"),
                ],
                [
                    {
                        "label": row.get("label"),
                        "count": row.get("count"),
                        "pct": next(
                            (
                                m.get("pct")
                                for m in mix.get("service_class_mix", [])
                                if m.get("key") == row.get("service_class")
                            ),
                            None,
                        ),
                        "p50_days": row.get("p50_days"),
                        "p85_days": row.get("p85_days"),
                    }
                    for row in mix.get("lead_time_by_class", [])
                ],
                title=_("Classes of service delivered"),
                empty=_("Nothing was delivered in this period."),
            ),
        ],
    )

    # 4. Little's Law
    little = littles_law(now)["meta"]
    little_before = littles_law(before)["meta"]
    section_little = section(
        "littles_law",
        _("Little's Law"),
        [
            kpis(
                [
                    kpi(
                        "avg_wip",
                        _("Average WIP"),
                        little.get("avg_wip"),
                        "count",
                        previous=little_before.get("avg_wip"),
                        higher_is_better=False,
                    ),
                    kpi(
                        "throughput_per_week",
                        _("Throughput"),
                        little.get("throughput_per_week"),
                        "cards/week",
                        previous=little_before.get("throughput_per_week"),
                        higher_is_better=True,
                    ),
                    kpi(
                        "avg_system_lead_time_days",
                        _("System lead time"),
                        little.get("avg_system_lead_time_days"),
                        "days",
                        previous=little_before.get("avg_system_lead_time_days"),
                        higher_is_better=False,
                    ),
                    kpi(
                        "coherence_ratio",
                        _("Little's Law coherence"),
                        little.get("coherence_ratio"),
                        "ratio",
                        previous=little_before.get("coherence_ratio"),
                        hint=_(
                            "Observed average WIP divided by throughput × lead time. "
                            "Near 1.0 the flow is stable and the other numbers can be trusted."
                        ),
                    ),
                ]
            ),
            note(str(little.get("verdict") or ""), "info" if little.get("stable") else "warning"),
        ],
    )
    return _document(
        "delivery_review",
        "REP-01",
        _("Delivery review"),
        board.get("name"),
        request,
        _board_scope(board),
        [section_pace, section_aging, section_blockers, section_little],
    )


# ---------------------------------------------------------------------------
# REP-02 project status
# ---------------------------------------------------------------------------


async def project_status(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    if not request.project_id:
        raise BadRequest("Choose a project.", code="report_project_required")
    project = as_dict(
        await s.scalar(
            select(Project).where(Project.id == request.project_id, *in_product(Project))
        )
    )
    if not project:
        # 404 like the rest of the API (RM-13 / NGK-117, 25/11/2026); it was a 400.
        raise NotFound("Project", request.project_id, code="project_not_found")
    cards = [
        as_dict(c) or {}
        for c in await s.scalars(
            select(Card).where(
                Card.project_id == project["id"], Card.archived.is_(False), *in_product(Card)
            )
        )
    ]
    # The project's board: where most of its cards are (or the one asked for).
    per_board: dict[str, int] = defaultdict(int)
    for card in cards:
        per_board[str(card["board_id"])] += 1
    board_id = request.board_id or (
        max(per_board, key=per_board.__getitem__) if per_board else None
    )
    board = await get_board_or_404(s, board_id) if board_id else None

    delivered = [c for c in cards if c.get("delivered_at")]
    doing = [c for c in cards if not c.get("delivered_at") and c.get("committed_at")]
    todo = [c for c in cards if not c.get("delivered_at") and not c.get("committed_at")]

    blocks: list[dict[str, Any]] = []
    risk_blocks: list[dict[str, Any]] = []
    if board is not None:
        now, before = await _contexts(s, board, request.period, project_id=project["id"])
        current, previous = summary(now), summary(before)
        blocks.append(
            kpis(
                [
                    kpi(
                        "delivered_total",
                        _("Delivered so far"),
                        len(delivered),
                        "count",
                        compare=False,
                    ),
                    kpi("in_progress", _("In progress"), len(doing), "count", compare=False),
                    kpi("to_do", _("Not started"), len(todo), "count", compare=False),
                    kpi_from_summary(current, previous, "delivered"),
                    kpi_from_summary(current, previous, "throughput"),
                ]
            )
        )
        columns = {
            c.id: c.name
            for c in await s.scalars(select(BoardColumn).where(BoardColumn.board_id == board["id"]))
        }
        open_by_column: dict[str, int] = defaultdict(int)
        for card in doing + todo:
            open_by_column[columns.get(str(card.get("column_id")), "—")] += 1
        blocks.append(
            bars(
                [{"label": k, "value": v} for k, v in open_by_column.items()],
                title=_("Open cards by column"),
                unit="count",
                empty=_("The project has no open card."),
            )
        )
        points = scatter(now, metric="customer")["series"][0]["points"]
        blocks.append(
            table(
                [
                    ("date", _("Delivered on"), "date"),
                    ("card", _("Card"), "card"),
                    ("lead_time", _("Lead time (days)"), "days"),
                ],
                [
                    {
                        "date": p.get("date"),
                        "card": _card(p.get("key"), p.get("title")),
                        "lead_time": p.get("customer_days"),
                    }
                    for p in sorted(points, key=lambda p: p.get("x") or "")
                ],
                title=_("Delivered in the period"),
                empty=_("Nothing was delivered in this period."),
            )
        )
        ages = aging(now)["series"][0]["points"]
        risky = [
            p
            for p in ages
            if p.get("is_blocked")
            or (p.get("committed") and (p.get("over_sle") or p.get("band") in ("p85", "p95")))
        ]
        risky.sort(key=lambda p: -(p.get("days_in_flight") or 0))
        risk_blocks.append(
            table(
                [
                    ("card", _("Card"), "card"),
                    ("column", _("Column"), "text"),
                    ("age", _("Age"), "days"),
                    ("why", _("Why"), "text"),
                ],
                [
                    {
                        "card": _card(p.get("key"), p.get("title")),
                        "column": p.get("column_name"),
                        "age": p.get("days_in_flight"),
                        "why": ", ".join(
                            reason
                            for reason, on in (
                                (_("blocked"), p.get("is_blocked")),
                                (_("past the SLE"), p.get("over_sle")),
                                (
                                    _("past the 85th percentile"),
                                    not p.get("over_sle") and p.get("band") in ("p85", "p95"),
                                ),
                            )
                            if on
                        ),
                    }
                    for p in risky
                ],
                title=_("Blocked or aging cards"),
                empty=_("No card of the project is blocked or aging."),
            )
        )
    else:
        blocks.append(note(_("The project has no card yet.")))

    # hours of the project in the period, by card
    start, end = request.period.start.isoformat(), request.period.end.isoformat()
    rows = (
        await s.execute(
            select(Card.key, Card.title, func.sum(TimeEntry.minutes))
            .join(Card, Card.id == TimeEntry.card_id)
            .where(
                TimeEntry.project_id == project["id"],
                TimeEntry.worked_on >= start,
                TimeEntry.worked_on <= end,
                *in_product(TimeEntry),
            )
            .group_by(Card.key, Card.title)
            .order_by(func.sum(TimeEntry.minutes).desc())
        )
    ).all()
    total_minutes = sum(int(r[2] or 0) for r in rows)
    hours_blocks = [
        kpis([kpi("hours", _("Hours in the period"), total_minutes / 60, "hours", compare=False)]),
        table(
            [("card", _("Card"), "card"), ("hours", _("Hours"), "hours")],
            [{"card": _card(k, t), "hours": round(int(m or 0) / 60, 2)} for k, t, m in rows],
            title=_("Hours by card"),
            empty=_("No hours logged on the project in this period."),
        ),
    ]
    scope = {
        "project": {
            "id": project["id"],
            "key": project.get("key"),
            "name": project.get("name"),
            "client_name": project.get("client_name"),
        },
        **(_board_scope(board) if board else {}),
    }
    subtitle = project.get("name")
    if project.get("client_name"):
        subtitle = f"{subtitle} · {project['client_name']}"
    return _document(
        "project_status",
        "REP-02",
        _("Project status"),
        subtitle,
        request,
        scope,
        [
            section("delivered_vs_remaining", _("Delivered and remaining"), blocks),
            section("risks", _("Risks"), risk_blocks or [note(_("The project has no card yet."))]),
            section("hours", _("Project hours"), hours_blocks),
        ],
    )


# ---------------------------------------------------------------------------
# REP-03 hours
# ---------------------------------------------------------------------------


async def hours(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    projects = {p.id: p for p in await s.scalars(select(Project).where(*in_product(Project)))}
    wanted = [pid for pid in request.project_ids if pid in projects]
    if request.project_ids and not wanted:
        raise NotFound("Project", ",".join(request.project_ids), code="project_not_found")

    async def minutes_by_project(first: date, last: date) -> dict[str | None, int]:
        query = (
            select(TimeEntry.project_id, func.sum(TimeEntry.minutes))
            .where(
                TimeEntry.worked_on >= first.isoformat(),
                TimeEntry.worked_on <= last.isoformat(),
                *in_product(TimeEntry),
            )
            .group_by(TimeEntry.project_id)
        )
        if wanted:
            query = query.where(TimeEntry.project_id.in_(wanted))
        return {pid: int(m or 0) for pid, m in (await s.execute(query)).all()}

    period = request.period
    now = await minutes_by_project(period.start, period.end)
    before = await minutes_by_project(period.previous_start, period.previous_end)
    no_client = _("No client")
    no_project = _("No project")

    def client_of(pid: str | None) -> str:
        project = projects.get(pid) if pid else None
        return (project.client_name if project else None) or no_client

    total_now, total_before = sum(now.values()), sum(before.values())
    by_client: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for pid, minutes in now.items():
        by_client[client_of(pid)][0] += minutes
        by_client[client_of(pid)][2] += 1
    for pid, minutes in before.items():
        by_client[client_of(pid)][1] += minutes

    def share(minutes: int) -> float | None:
        return round(minutes / total_now * 100, 1) if total_now else None

    client_rows = [
        {
            "client": name,
            "hours": round(m / 60, 2),
            "previous": round(p / 60, 2),
            "share": share(m),
            "projects": n,
        }
        for name, (m, p, n) in sorted(by_client.items(), key=lambda kv: -kv[1][0])
        if m or p
    ]
    project_rows = [
        {
            "project": (projects[pid].name if pid in projects else no_project),
            "key": projects[pid].key if pid in projects else "",
            "client": client_of(pid),
            "hours": round(now.get(pid, 0) / 60, 2),
            "previous": round(before.get(pid, 0) / 60, 2),
            "share": share(now.get(pid, 0)),
        }
        for pid in sorted(set(now) | set(before), key=lambda pid: -now.get(pid, 0))
    ]
    sections = [
        section(
            "total",
            _("Total"),
            [
                kpis(
                    [
                        kpi(
                            "hours",
                            _("Hours in the period"),
                            total_now / 60,
                            "hours",
                            previous=total_before / 60,
                        ),
                        kpi(
                            "clients",
                            _("Clients"),
                            sum(1 for r in client_rows if r["hours"]),
                            "count",
                            compare=False,
                        ),
                        kpi(
                            "projects",
                            _("Projects"),
                            sum(1 for r in project_rows if r["hours"]),
                            "count",
                            compare=False,
                        ),
                    ]
                )
            ],
        ),
        section(
            "by_client",
            _("By client"),
            [
                table(
                    [
                        ("client", _("Client"), "text"),
                        ("hours", _("Hours"), "hours"),
                        ("previous", _("Previous period"), "hours"),
                        ("share", _("Share"), "percent"),
                        ("projects", _("Projects"), "count"),
                    ],
                    client_rows,
                    empty=_("No hours logged in this period."),
                )
            ],
            description=_("The client is the one of each card's project."),
        ),
        section(
            "by_project",
            _("By project"),
            [
                table(
                    [
                        ("project", _("Project"), "text"),
                        ("key", _("Key"), "text"),
                        ("client", _("Client"), "text"),
                        ("hours", _("Hours"), "hours"),
                        ("previous", _("Previous period"), "hours"),
                        ("share", _("Share"), "percent"),
                    ],
                    project_rows,
                    empty=_("No hours logged in this period."),
                )
            ],
        ),
    ]
    subtitle = ", ".join(projects[pid].name for pid in wanted) if wanted else _("Every project")
    return _document(
        "hours",
        "REP-03",
        _("Hours"),
        subtitle,
        request,
        {"projects": [{"id": pid, "name": projects[pid].name} for pid in wanted]},
        sections,
    )


# ---------------------------------------------------------------------------
# REP-04 portfolio
# ---------------------------------------------------------------------------


async def portfolio(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    home = current_product()
    rows: list[dict[str, Any]] = []
    try:
        for product in await products_for(s, request.user):
            set_current_product(product["id"])
            if product.get("board"):
                boards = [await get_board_or_404(s, product["board"]["id"])]
                names = {
                    boards[0]["id"]: (product.get("project") or {}).get("name") or product["id"]
                }
            else:
                found = (
                    await s.scalars(
                        select(Board)
                        .where(
                            Board.kind == "operational",
                            Board.archived.is_(False),
                            *in_product(Board),
                        )
                        .order_by(Board.created_at)
                    )
                ).all()
                boards = [as_dict(b) or {} for b in found]
                names = {b["id"]: f"Nagare · {b.get('name')}" for b in boards}
            for board in boards:
                now, before = await _contexts(s, board, request.period)
                current, previous = summary(now), summary(before)
                metric = current["metrics"]
                before_metric = previous["metrics"]
                ages = aging(now)["meta"]
                blocked_now = blockers(now)["meta"]
                rows.append(
                    {
                        "product": names[board["id"]],
                        "delivered": metric["delivered"]["value"],
                        "delivered_previous": before_metric["delivered"]["value"],
                        "trend": kpi(
                            "t",
                            "",
                            metric["delivered"]["value"],
                            "count",
                            previous=before_metric["delivered"]["value"],
                        )["trend_pct"],
                        "throughput": metric["throughput"]["value"],
                        "lead_time_p85": metric["customer_lead_time_p85"]["value"],
                        "wip": metric["wip"]["value"],
                        "aging": ages.get("over_p85"),
                        "blocked": blocked_now.get("currently_blocked"),
                        "link": {"product": product["id"], "board_id": board["id"]},
                    }
                )
    finally:
        set_current_product(home)
    columns = [
        ("product", _("Product"), "text"),
        ("delivered", _("Delivered"), "count"),
        ("delivered_previous", _("Previous period"), "count"),
        ("trend", _("Change"), "percent"),
        ("throughput", _("Throughput"), "cards/week"),
        ("lead_time_p85", _("Lead time (85th pct)"), "days"),
        ("wip", _("Work in progress"), "count"),
        ("aging", _("Past the 85th percentile"), "count"),
        ("blocked", _("Blocked now"), "count"),
    ]
    return _document(
        "portfolio",
        "REP-04",
        _("Product portfolio"),
        None,
        request,
        {},
        [
            section(
                "products",
                _("Products side by side"),
                [table(columns, rows, empty=_("No product to show."))],
                description=_(
                    "Every product your profile opens. Open a product to read its delivery review."
                ),
            )
        ],
    )


# ---------------------------------------------------------------------------
# REP-05 risks and blockers
# ---------------------------------------------------------------------------


async def risks(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    board = await _board(s, request)
    now, before = await _contexts(s, board, request.period)
    meta, meta_before = blockers(now)["meta"], blockers(before)["meta"]

    def figure(key: str, label: str, unit: str, better: bool | None = False) -> dict[str, Any]:
        return kpi(
            key, label, meta.get(key), unit, previous=meta_before.get(key), higher_is_better=better
        )

    recurring = [row for row in meta["pareto"] if (row.get("count") or 0) > 1]
    sections = [
        section(
            "overview",
            _("Overview"),
            [
                kpis(
                    [
                        figure("total_blocked_days", _("Blocked days"), "days"),
                        figure("blocker_count", _("Blockers"), "count"),
                        figure("cards_affected", _("Cards affected"), "count"),
                        figure("p85_duration_days", _("Blocker duration (85th pct)"), "days"),
                        figure("pct_of_lead_time_blocked", _("Lead time blocked"), "percent"),
                        kpi(
                            "currently_blocked",
                            _("Blocked now"),
                            meta.get("currently_blocked"),
                            "count",
                            compare=False,
                        ),
                    ]
                )
            ],
        ),
        section(
            "causes",
            _("Causes, heaviest first"),
            [
                bars(
                    [
                        {"label": r["label"], "value": r["days"], "note": r["count"]}
                        for r in meta["pareto"]
                    ],
                    title=_("Blocked days by cause"),
                    unit="days",
                    empty=_("No blocker in this period."),
                ),
                table(
                    [
                        ("label", _("Cause"), "text"),
                        ("count", _("Blockers"), "count"),
                        ("days", _("Blocked days"), "days"),
                        ("mean_days", _("Mean duration"), "days"),
                        ("share_pct", _("Share"), "percent"),
                        ("cumulative_pct", _("Cumulative"), "percent"),
                    ],
                    meta["pareto"],
                    empty=_("No blocker in this period."),
                ),
                note(
                    _(
                        "Causes that came back in the period: {causes}.",
                        causes=", ".join(r["label"] for r in recurring),
                    )
                    if recurring
                    else _("No cause came back more than once in the period."),
                    "warning" if recurring else "info",
                ),
            ],
        ),
        section(
            "blocked_now",
            _("Blocked now"),
            [
                table(
                    [
                        ("card", _("Card"), "card"),
                        ("cause", _("Cause"), "text"),
                        ("reason", _("Reason"), "text"),
                        ("since", _("Since"), "datetime"),
                        ("days", _("Blocked days"), "days"),
                    ],
                    [
                        {
                            "card": _card(b.get("card_key"), b.get("card_title")),
                            "cause": str(b.get("category") or "other"),
                            "reason": b.get("reason") or "",
                            "since": b.get("started_at"),
                            "days": b.get("total_days"),
                        }
                        for b in meta.get("open_blockers", [])
                    ],
                    empty=_("No card is blocked at the end of the period."),
                )
            ],
        ),
        section(
            "most_blocked",
            _("Most blocked cards"),
            [
                table(
                    [
                        ("card", _("Card"), "card"),
                        ("count", _("Blockers"), "count"),
                        ("days", _("Blocked days"), "days"),
                        ("open", _("Still blocked"), "text"),
                    ],
                    [
                        {
                            "card": _card(c.get("key"), c.get("title")),
                            "count": c.get("count"),
                            "days": c.get("days"),
                            "open": _("yes") if c.get("open") else "",
                        }
                        for c in meta.get("top_blocked_cards", [])
                    ],
                    empty=_("No blocker in this period."),
                )
            ],
        ),
    ]
    # The cause's label, as the pareto already translated it.
    labels = {row["category"]: row["label"] for row in meta["pareto"]}
    for row in sections[2]["blocks"][0]["rows"]:
        row["cause"] = labels.get(row["cause"], row["cause"])
    return _document(
        "risks",
        "REP-05",
        _("Risks and blockers"),
        board.get("name"),
        request,
        _board_scope(board),
        sections,
    )


# ---------------------------------------------------------------------------
# REP-11 where the work waits
# ---------------------------------------------------------------------------


async def waiting(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    board = await _board(s, request)
    now, before = await _contexts(s, board, request.period)
    meta, meta_before = flow_efficiency(now)["meta"], flow_efficiency(before)["meta"]

    # time per column: how long each card stayed in a column it left during the period
    start, end = request.period.window
    rows = (
        await s.execute(
            select(CardMovement.from_column_id, CardMovement.seconds_in_from_column).where(
                CardMovement.board_id == board["id"],
                CardMovement.moved_at >= start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                CardMovement.moved_at <= end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                CardMovement.from_column_id.is_not(None),
                CardMovement.seconds_in_from_column.is_not(None),
            )
        )
    ).all()
    stays: dict[str, list[float]] = defaultdict(list)
    for column_id, seconds in rows:
        stays[str(column_id)].append(float(seconds) / DAY)
    columns = [
        as_dict(c) or {}
        for c in await s.scalars(
            select(BoardColumn)
            .where(BoardColumn.board_id == board["id"])
            .order_by(BoardColumn.position)
        )
    ]
    by_column = {row["column_id"]: row for row in meta.get("by_column", [])}
    kinds = {
        "queue": _("queue"),
        "activity": _("activity"),
        "envelope": _("envelope"),
        "done": _("done"),
    }
    column_rows = []
    for column in columns:
        values = stays.get(column["id"], [])
        if column.get("kind") == "done" and not values:
            continue
        column_rows.append(
            {
                "column": column.get("name"),
                "kind": kinds.get(str(column.get("kind")), column.get("kind")),
                "cards": len(values),
                "p50": percentile(values, 50) if values else None,
                "p85": percentile(values, 85) if values else None,
                "share": (by_column.get(column["id"]) or {}).get("share_of_lead_time_pct"),
            }
        )
    worst = meta.get("worst_queue") or {}
    sections = [
        section(
            "efficiency",
            _("Flow efficiency"),
            [
                kpis(
                    [
                        kpi(
                            "flow_efficiency",
                            _("Flow efficiency"),
                            meta.get("flow_efficiency"),
                            "percent",
                            previous=meta_before.get("flow_efficiency"),
                            higher_is_better=True,
                        ),
                        kpi(
                            "touch_days",
                            _("Days worked"),
                            meta.get("touch_days"),
                            "days",
                            previous=meta_before.get("touch_days"),
                        ),
                        kpi(
                            "wait_days",
                            _("Days waiting"),
                            meta.get("wait_days"),
                            "days",
                            previous=meta_before.get("wait_days"),
                            higher_is_better=False,
                        ),
                        kpi(
                            "median_card_efficiency",
                            _("Median card efficiency"),
                            meta.get("median_card_efficiency"),
                            "percent",
                            previous=meta_before.get("median_card_efficiency"),
                            higher_is_better=True,
                        ),
                    ]
                ),
                note(str(meta.get("interpretation") or "")),
            ],
            description=_(
                "Time worked (activity columns) against time waiting (queue columns), "
                "for the cards delivered in the period."
            ),
        ),
        section(
            "per_column",
            _("Time in each column"),
            [
                table(
                    [
                        ("column", _("Column"), "text"),
                        ("kind", _("Kind"), "text"),
                        ("cards", _("Cards that left it"), "count"),
                        ("p50", _("Time in the column (50th pct)"), "days"),
                        ("p85", _("Time in the column (85th pct)"), "days"),
                        ("share", _("Share of the lead time"), "percent"),
                    ],
                    column_rows,
                    empty=_("No card left a column in this period."),
                )
            ],
            description=_("How long the cards that left each column in the period stayed in it."),
        ),
        section(
            "bottleneck",
            _("Where the work waits most"),
            [
                bars(
                    [
                        {"label": r.get("column_name"), "value": r.get("days")}
                        for r in meta.get("wait_pareto", [])
                    ],
                    title=_("Waiting days by queue column"),
                    unit="days",
                    empty=_("No waiting in this period."),
                ),
                note(
                    _(
                        "The column that holds the flow most is {column}: {share}% of the waiting.",
                        column=worst.get("column_name"),
                        share=worst.get("share_pct"),
                    ),
                    "warning",
                )
                if worst.get("column_name")
                else note(_("No waiting in this period.")),
            ],
        ),
    ]
    return _document(
        "waiting",
        "REP-11",
        _("Where the work waits"),
        board.get("name"),
        request,
        _board_scope(board),
        sections,
    )


# ---------------------------------------------------------------------------
# REP-13 policy exceptions
# ---------------------------------------------------------------------------


async def policy_exceptions(s: AsyncSession, request: ReportRequest) -> dict[str, Any]:
    board = await _board(s, request)
    names = await _names(s)
    start, end = request.period.window
    first, last = start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
    columns = {
        c.id: c.name
        for c in await s.scalars(select(BoardColumn).where(BoardColumn.board_id == board["id"]))
    }

    overrides = (
        await s.execute(
            select(CardMovement, Card.key, Card.title)
            .join(Card, Card.id == CardMovement.card_id)
            .where(
                CardMovement.board_id == board["id"],
                CardMovement.wip_override.is_(True),
                CardMovement.moved_at >= first,
                CardMovement.moved_at <= last,
            )
            .order_by(CardMovement.moved_at)
        )
    ).all()
    override_rows = [
        {
            "when": m.moved_at,
            "card": _card(key, title),
            "column": columns.get(str(m.to_column_id), "—"),
            "by": names.get(str(m.moved_by), "—"),
        }
        for m, key, title in overrides
    ]

    changes = (
        await s.scalars(
            select(WipLimitChange)
            .where(
                WipLimitChange.board_id == board["id"],
                WipLimitChange.changed_at >= first,
                WipLimitChange.changed_at <= last,
            )
            .order_by(WipLimitChange.changed_at)
        )
    ).all()
    scopes = {"board": _("whole flow"), "group": _("group"), "column": _("column")}
    fields = {"wip_min": _("minimum"), "wip_max": _("maximum")}

    def limit(value: int | None) -> str:
        return "—" if value is None else str(value)

    change_rows = [
        {
            "when": c.changed_at,
            "where": f"{scopes.get(c.scope, c.scope)}: {c.target_name}",
            "limit": fields.get(c.field, c.field),
            "change": f"{limit(c.old_value)} → {limit(c.new_value)}",
            "by": names.get(str(c.changed_by), "—"),
        }
        for c in changes
    ]

    class_changes = (
        await s.execute(
            select(CardEvent, Card.key, Card.title)
            .join(Card, Card.id == CardEvent.card_id)
            .where(
                CardEvent.board_id == board["id"],
                CardEvent.event_type == "updated",
                CardEvent.field == "service_class",
                CardEvent.created_at >= first,
                CardEvent.created_at <= last,
            )
            .order_by(CardEvent.created_at)
        )
    ).all()
    class_rows = [
        {
            "when": e.created_at,
            "card": _card(key, title),
            "change": f"{_class_label(e.old_value)} → {_class_label(e.new_value)}",
            "by": names.get(str(e.actor_id), "—"),
        }
        for e, key, title in class_changes
    ]

    sections = [
        section(
            "overview",
            _("Overview"),
            [
                kpis(
                    [
                        kpi(
                            "wip_overrides",
                            _("Passed over a WIP limit"),
                            len(override_rows),
                            "count",
                            compare=False,
                        ),
                        kpi(
                            "wip_changes",
                            _("WIP limit changes"),
                            len(change_rows),
                            "count",
                            compare=False,
                        ),
                        kpi(
                            "class_changes",
                            _("Class of service changes"),
                            len(class_rows),
                            "count",
                            compare=False,
                        ),
                    ]
                ),
                note(
                    _(
                        "Passes over a WIP limit and WIP limit changes are kept only from the "
                        "update that started recording them; earlier periods show none."
                    )
                ),
            ],
        ),
        section(
            "wip_overrides",
            _("Passed over a WIP limit"),
            [
                table(
                    [
                        ("when", _("When"), "datetime"),
                        ("card", _("Card"), "card"),
                        ("column", _("Into the column"), "text"),
                        ("by", _("By"), "text"),
                    ],
                    override_rows,
                    empty=_("No card passed over a WIP limit in this period."),
                )
            ],
            description=_("Expedite cards that went into a column already at its WIP limit."),
        ),
        section(
            "wip_changes",
            _("WIP limit changes"),
            [
                table(
                    [
                        ("when", _("When"), "datetime"),
                        ("where", _("Where"), "text"),
                        ("limit", _("Limit"), "text"),
                        ("change", _("Before → after"), "text"),
                        ("by", _("By"), "text"),
                    ],
                    change_rows,
                    empty=_("No WIP limit changed in this period."),
                )
            ],
        ),
        section(
            "class_changes",
            _("Class of service changes"),
            [
                table(
                    [
                        ("when", _("When"), "datetime"),
                        ("card", _("Card"), "card"),
                        ("change", _("Before → after"), "text"),
                        ("by", _("By"), "text"),
                    ],
                    class_rows,
                    empty=_("No card changed its class of service in this period."),
                )
            ],
        ),
    ]
    return _document(
        "policy_exceptions",
        "REP-13",
        _("Policy exceptions"),
        board.get("name"),
        request,
        _board_scope(board),
        sections,
    )


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, Template] = {
    t.id: t
    for t in (
        Template(
            "delivery_review",
            "REP-01",
            N_("Delivery review"),
            N_(
                "A board's week or month against the previous one: deliveries and pace, due dates "
                "and aging, blockers and classes of service, Little's Law."
            ),
            "board",
            "reports",
            delivery_review,
        ),
        Template(
            "project_status",
            "REP-02",
            N_("Project status"),
            N_(
                "A project: delivered and remaining, the risks (blocked and aging cards) and the "
                "hours logged in the period."
            ),
            "project",
            "reports",
            project_status,
        ),
        Template(
            "hours",
            "REP-03",
            N_("Hours"),
            N_(
                "Hours logged in the period by client and by project, against the previous "
                "period. The basis for billing."
            ),
            "projects",
            "reports",
            hours,
        ),
        Template(
            "portfolio",
            "REP-04",
            N_("Product portfolio"),
            N_(
                "Every product your profile opens, side by side: throughput and its trend, lead "
                "time, work in progress, aging and blocked cards."
            ),
            "none",
            "reports_portfolio",
            portfolio,
        ),
        Template(
            "risks",
            "REP-05",
            N_("Risks and blockers"),
            N_(
                "The causes of blockers by weight, blocked time, the cards blocked now and the "
                "causes that come back."
            ),
            "board",
            "reports",
            risks,
        ),
        Template(
            "waiting",
            "REP-11",
            N_("Where the work waits"),
            N_(
                "Time in each column, flow efficiency (worked against waiting) and the column that "
                "holds the flow most."
            ),
            "board",
            "reports",
            waiting,
        ),
        Template(
            "policy_exceptions",
            "REP-13",
            N_("Policy exceptions"),
            N_(
                "Expedite cards that passed over a WIP limit, WIP limit changes and class of "
                "service changes."
            ),
            "board",
            "reports",
            policy_exceptions,
        ),
    )
}


def template_or_400(template_id: str) -> Template:
    template = TEMPLATES.get(template_id)
    if template is None:
        raise BadRequest(
            "Unknown report.", code="report_unknown", details={"template": template_id}
        )
    return template
