"""What the Nagare answers without signing in (wave 2 of the Reports, 25/11/2026).

* ``GET /public/reports/{token}`` — the issued report behind a client link (decisions R5,
  R18, R19): only that frozen copy, only while the link is valid. The token is the
  credential; the product comes from the link itself (there is no ``X-Produto`` and no
  user), and the product's plan must still include the Reports. Each call counts an access.

Unknown token → ``404 report_link_not_found``; revoked → ``410 report_link_revoked``;
expired → ``410 report_link_expired``; over the per-IP limit → ``429 too_many_requests``.
Answers carry ``X-Robots-Tag: noindex`` and are never cached.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..deps import check_flag, get_session
from ..errors import ApiError, NotFound
from ..models import ReportIssue, ReportLink, User, as_dict
from ..reports.emitidos import hash_token, link_projects, link_state, public_limit
from ..tenancy import set_current_product
from ..texts import set_locale, set_via

router = APIRouter(prefix="/public", tags=["public"])

__all__ = ["router"]

#: Headers of every answer of the public page: not indexed, not cached, no referrer.
PUBLIC_HEADERS = {
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}
_TOKEN_MAX = 200


@router.get("/reports/{token}", summary="The issued report behind a client link (no sign-in)")
async def public_report(
    token: str,
    request: Request,
    response: Response,
    s: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    public_limit.check(request.client.host if request.client else None)
    link = (
        await s.scalar(select(ReportLink).where(ReportLink.token_hash == hash_token(token)))
        if 0 < len(token) <= _TOKEN_MAX
        else None
    )
    if link is None:
        raise NotFound("Report link", code="report_link_not_found")
    data = as_dict(link) or {}
    state = link_state(data, datetime.now(UTC))
    if state == "revoked":
        raise ApiError("report_link_revoked", "This link was revoked.", status=410)
    if state == "expired":
        raise ApiError(
            "report_link_expired",
            "This link has expired.",
            status=410,
            details={"expired_at": data["expires_at"]},
        )
    # The product of the link, for every query and for the plan's flags.
    set_current_product(link.product_id)
    request.state.product_id = link.product_id
    set_via("local")
    issue = await s.scalar(
        select(ReportIssue).where(
            ReportIssue.id == link.issue_id, ReportIssue.product_id == link.product_id
        )
    )
    if issue is None:  # pragma: no cover - the link is deleted with its report
        raise NotFound("Report link", code="report_link_not_found")
    await check_flag(s, "reports")
    # Defence in depth: the frozen copy must still be one of the link's projects (R18).
    if not set(link_projects(issue.template, issue.document)) <= set(link.project_ids or []):
        raise NotFound("Report link", code="report_link_not_found")
    set_locale(issue.locale)
    now = utcnow()
    await s.execute(
        update(ReportLink)
        .where(ReportLink.id == link.id)
        .values(access_count=ReportLink.access_count + 1, last_access_at=now)
    )
    await s.commit()
    issuer = await s.get(User, issue.issued_by) if issue.issued_by else None
    response.headers.update(PUBLIC_HEADERS)
    return {
        "document": issue.document,
        "issued_at": issue.issued_at,
        "issued_by_name": issuer.name if issuer else None,
        "expires_at": link.expires_at,
        "locale": issue.locale,
    }
