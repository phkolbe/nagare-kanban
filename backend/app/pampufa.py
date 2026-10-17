"""The Nagare's side of the Pampufa integration: identities in, notifications out.

* **Identities in (NG5, NG6).** A Pampufa access token (EdDSA, ``aud`` containing
  ``nagare``) is validated offline against the platform's JWKS with the
  ``pampufa-sdk`` validator. The token carries roles but no name or e-mail, so the
  first time a person shows up the Nagare reads ``GET /api/v1/auth/eu`` with that
  same token and creates — or links, by e-mail — the local user.
* **Notifications out (NG10).** A card that stands for a providência tells the
  Pampufa when it is delivered or archived. The message is written to
  ``webhook_outbox`` in the same transaction as the change and posted with the
  Nagare's service token until the platform confirms it.

Everything that talks to the network sits behind :class:`PampufaGateway`, which
the tests replace with an in-memory double.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from pampufa_sdk.auth.token import Identidade, TokenInvalido, ValidadorDeToken
from pampufa_sdk.servicos import ClienteDeServico, ErroDeIntegracao
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings
from .db import add_days, iso, new_id, parse_iso, utcnow
from .errors import Unauthorized
from .models import User, WebhookOutbox

log = logging.getLogger("nagare.pampufa")

__all__ = [
    "PampufaGateway",
    "PampufaProfile",
    "deliver_pending",
    "enqueue_webhook",
    "is_pampufa_token",
    "user_for_identity",
    "webhook_loop",
]

#: Events the Nagare sends to the Pampufa.
EVENT_DELIVERED = "cartao.entregue"
EVENT_ARCHIVED = "cartao.arquivado"
#: A swap of flow (NG14) recreated the card on the new board: new key and link.
EVENT_MOVED = "cartao.movido"

_MAX_BACKOFF = timedelta(minutes=10)


@dataclass(frozen=True)
class PampufaProfile:
    """Who a Pampufa identity is, as ``/auth/eu`` tells it."""

    sub: str
    email: str
    name: str


class PampufaGateway:
    """Network access to the Pampufa: JWKS validation, profiles and the webhook."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.validator = ValidadorDeToken(
            servico=settings.pampufa_audience,
            emissor=settings.pampufa_issuer,
            jwks_url=settings.pampufa_jwks_url,
        )
        self._service = ClienteDeServico(
            pampufa_url=settings.pampufa_url,
            cliente_id=settings.pampufa_client_id,
            segredo=settings.pampufa_client_secret,
        )

    async def validate(self, token: str) -> Identidade:
        return await self.validator.validar(token)

    async def profile(self, token: str) -> PampufaProfile:
        """``GET /api/v1/auth/eu`` with the person's own token."""
        url = f"{self.settings.pampufa_url}/api/v1/auth/eu"
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(url, headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise Unauthorized(
                "The Pampufa did not confirm who this token belongs to.",
                code="pampufa_profile_unavailable",
                details={"status": response.status_code},
            )
        user = response.json().get("usuario") or {}
        return PampufaProfile(
            sub=str(user.get("id")), email=str(user.get("email")), name=str(user.get("nome"))
        )

    async def people(self, product_id: str) -> list[dict[str, Any]]:
        """Who has a ``nagare.*`` role in the product (decision of 17/10/2026).

        ``[{sub, email, nome, papeis}]`` from the Pampufa, with the Nagare's service
        token: the Pampufa is the directory, the Nagare only mirrors it.
        """
        url = f"{self.settings.pampufa_url}/api/v1/integracao/nagare/pessoas"
        response = await self._service.requisitar("GET", url, params={"produto": product_id})
        return list(response.json())

    async def post_event(self, event: str, payload: dict[str, Any]) -> None:
        """Deliver one notification; raises when the Pampufa does not take it."""
        url = f"{self.settings.pampufa_url}/api/v1/integracao/nagare/eventos"
        await self._service.requisitar("POST", url, json={"evento": event, **payload})


def is_pampufa_token(token: str) -> bool:
    """Pampufa tokens are EdDSA with a ``kid``; the local login signs HS256."""
    import jwt

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return False
    return header.get("alg") == "EdDSA"


async def user_for_identity(
    s: AsyncSession, gateway: PampufaGateway, identity: Identidade, token: str
) -> User:
    """The Nagare user behind a Pampufa identity, created or linked on first sight.

    Lookup order: the Pampufa ``sub`` already linked; else a local user with the
    same e-mail, which gets linked (same person, same history); else a new user
    without a password (it only signs in through the Pampufa). Service identities
    (``servico:<name>``) map to one ``system`` user each.
    """
    user = await s.scalar(select(User).where(User.external_subject == identity.sub))
    if user is not None:
        return user

    if identity.eh_servico:
        name = identity.sub.split(":", 1)[-1]
        user = User(
            id=new_id(),
            email=f"{name}@servico.pampufa",
            name=f"Pampufa ({name})",
            password_hash=None,
            role="system",
            is_active=True,
            created_at=utcnow(),
            external_subject=identity.sub,
        )
        s.add(user)
        await s.commit()
        return user

    profile = await gateway.profile(token)
    email = profile.email.strip().lower()
    user = await s.scalar(select(User).where(User.email == email))
    if user is not None:
        if user.external_subject and user.external_subject != identity.sub:
            raise Unauthorized(
                "This e-mail is already linked to another Pampufa account.",
                code="pampufa_link_conflict",
            )
        user.external_subject = identity.sub
        log.info("pampufa.user_linked", extra={"user_id": user.id})
    else:
        # The local role only matters for the local login, which this user does not
        # have; the role inside each product comes from the Pampufa token.
        user = User(
            id=new_id(),
            email=email,
            name=profile.name,
            password_hash=None,
            role="viewer",
            is_active=True,
            created_at=utcnow(),
            external_subject=identity.sub,
        )
        s.add(user)
    await s.commit()
    return user


# ---------------------------------------------------------------------------
# webhook outbox
# ---------------------------------------------------------------------------


def enqueue_webhook(s: AsyncSession, event: str, product_id: str, payload: dict[str, Any]) -> None:
    """Write one notification in the caller's transaction (sent by :func:`webhook_loop`)."""
    now = utcnow()
    s.add(
        WebhookOutbox(
            id=new_id(),
            event=event,
            product_id=product_id,
            payload=payload,
            created_at=now,
            attempts=0,
            next_attempt_at=now,
        )
    )


def _backoff(attempts: int) -> timedelta:
    return min(timedelta(seconds=5 * 2 ** max(0, attempts - 1)), _MAX_BACKOFF)


async def deliver_pending(
    sessions: async_sessionmaker[AsyncSession], gateway: PampufaGateway, limit: int = 50
) -> int:
    """Post every due notification once; returns how many the Pampufa accepted."""
    now = utcnow()
    delivered = 0
    async with sessions() as s:
        rows = (
            await s.scalars(
                select(WebhookOutbox)
                .where(WebhookOutbox.delivered_at.is_(None), WebhookOutbox.next_attempt_at <= now)
                .order_by(WebhookOutbox.created_at, WebhookOutbox.id)
                .limit(limit)
            )
        ).all()
        for row in rows:
            try:
                await gateway.post_event(row.event, {"product_id": row.product_id, **row.payload})
            except (ErroDeIntegracao, httpx.HTTPError, TokenInvalido) as error:
                row.attempts += 1
                row.last_error = str(error)[:500]
                moment = parse_iso(now)
                row.next_attempt_at = (
                    iso(moment + _backoff(row.attempts)) if moment else add_days(now, 0)
                )
                log.warning(
                    "pampufa.webhook_failed",
                    extra={"event": row.event, "attempts": row.attempts, "error": row.last_error},
                )
            else:
                row.attempts += 1
                row.delivered_at = utcnow()
                row.last_error = None
                delivered += 1
        await s.commit()
    return delivered


async def webhook_loop(
    sessions: async_sessionmaker[AsyncSession], gateway: PampufaGateway, interval_s: float
) -> None:
    """Background task: keep delivering until the app shuts down."""
    while True:
        try:
            await deliver_pending(sessions, gateway)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - a bad round must not kill the loop
            log.exception("pampufa.webhook_round_failed")
        await asyncio.sleep(interval_s)
