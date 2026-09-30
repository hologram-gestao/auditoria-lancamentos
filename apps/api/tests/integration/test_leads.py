"""POST /api/v1/leads — captação pública da landing (86e3fr9ut), contra Postgres.

Cobre:
    - Sem login: o POST grava o lead com `consent_at`, versão do texto e origem, e
      responde `{"data": {"received": true}}` sem ecoar nada.
    - Slack aceitou → `notified_at` carimbado; Slack fora → lead gravado com
      `notified_at` nulo (fail-soft), e a mesma resposta.
    - Honeypot preenchido e 4º envio do mesmo e-mail (sem caixa) em 24 h → 200 igual,
      sem linha nova e sem aviso.
    - Forma inválida (consentimento falso) → 400 genérico, sem linha.
    - `lead_recebido` gravado em `usage_events`, só com booleanos.
    - O teto do slowapi responde 429 no envelope padrão.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select

from app.db.models import Lead, UsageEvent
from app.main import app as fastapi_app
from app.modules.leads.routes import get_lead_notifier
from app.modules.leads.schemas import CONSENT_TEXT_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.leads.notifier import LeadNotice

pytestmark = pytest.mark.integration

URL = "/api/v1/leads"


class _FakeNotifier:
    def __init__(self) -> None:
        self.accepts = True
        self.notices: list[LeadNotice] = []

    async def notify(self, notice: LeadNotice) -> bool:
        self.notices.append(notice)
        return self.accepts


@pytest.fixture
def notifier() -> Iterator[_FakeNotifier]:
    fake = _FakeNotifier()
    fastapi_app.dependency_overrides[get_lead_notifier] = lambda: fake
    try:
        yield fake
    finally:
        fastapi_app.dependency_overrides.pop(get_lead_notifier, None)


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Maria Contadora",
        "email": "maria@escritorio-exemplo.com.br",
        "company": "Escritório Exemplo",
        "whatsapp": "",
        "message": "Quero conhecer.",
        "consent": True,
        "website": "",
    }
    body.update(overrides)
    return body


async def _leads(db: AsyncSession) -> list[Lead]:
    return list((await db.execute(select(Lead).order_by(Lead.created_at))).scalars())


async def _count_events(db: AsyncSession) -> int:
    stmt = select(func.count(UsageEvent.id)).where(UsageEvent.event == "lead_recebido")
    return int((await db.execute(stmt)).scalar_one())


class TestCreateLead:
    async def test_records_the_lead_without_login(
        self, client_with_db: AsyncClient, db_session: AsyncSession, notifier: _FakeNotifier
    ) -> None:
        resp = await client_with_db.post(URL, json=_body())
        assert resp.status_code == 200
        assert resp.json() == {"data": {"received": True}}

        (lead,) = await _leads(db_session)
        assert lead.name == "Maria Contadora"
        assert lead.email == "maria@escritorio-exemplo.com.br"
        assert lead.company == "Escritório Exemplo"
        assert lead.whatsapp is None
        assert lead.consent_at is not None
        assert lead.consent_text_version == CONSENT_TEXT_VERSION
        assert lead.source == "landing"
        assert lead.notified_at is not None
        assert len(notifier.notices) == 1

        (event,) = (
            await db_session.execute(select(UsageEvent).where(UsageEvent.event == "lead_recebido"))
        ).scalars()
        assert event.session_id is None
        assert event.props == {
            "source": "landing",
            "has_company": True,
            "has_whatsapp": False,
            "has_message": True,
            "notified": True,
        }

    async def test_slack_down_still_records_the_lead(
        self, client_with_db: AsyncClient, db_session: AsyncSession, notifier: _FakeNotifier
    ) -> None:
        notifier.accepts = False
        resp = await client_with_db.post(URL, json=_body())
        assert resp.status_code == 200
        assert resp.json() == {"data": {"received": True}}
        (lead,) = await _leads(db_session)
        assert lead.notified_at is None

    async def test_honeypot_answers_the_same_and_records_nothing(
        self, client_with_db: AsyncClient, db_session: AsyncSession, notifier: _FakeNotifier
    ) -> None:
        resp = await client_with_db.post(URL, json=_body(website="https://spam.example"))
        assert resp.status_code == 200
        assert resp.json() == {"data": {"received": True}}
        assert await _leads(db_session) == []
        assert notifier.notices == []
        assert await _count_events(db_session) == 0

    async def test_fourth_submission_of_the_same_email_is_silently_dropped(
        self, client_with_db: AsyncClient, db_session: AsyncSession, notifier: _FakeNotifier
    ) -> None:
        emails = ["Ana@Exemplo.com", "ana@exemplo.com", "ANA@EXEMPLO.COM", "ana@Exemplo.com"]
        for email in emails:
            resp = await client_with_db.post(URL, json=_body(email=email))
            assert resp.status_code == 200
            assert resp.json() == {"data": {"received": True}}
        assert len(await _leads(db_session)) == 3
        assert len(notifier.notices) == 3
        assert await _count_events(db_session) == 3

        other = await client_with_db.post(URL, json=_body(email="outra@exemplo.com"))
        assert other.status_code == 200
        assert len(await _leads(db_session)) == 4

    async def test_without_consent_is_a_generic_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession, notifier: _FakeNotifier
    ) -> None:
        resp = await client_with_db.post(URL, json=_body(consent=False))
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "Maria" not in resp.text
        assert await _leads(db_session) == []
        assert notifier.notices == []

    async def test_rate_limit_answers_429_in_the_standard_envelope(
        self, client_with_db: AsyncClient, notifier: _FakeNotifier
    ) -> None:
        statuses = []
        for i in range(11):
            resp = await client_with_db.post(URL, json=_body(email=f"pessoa{i}@exemplo.com"))
            statuses.append(resp.status_code)
        assert statuses[:10] == [200] * 10
        assert statuses[10] == 429
        assert resp.json()["error"]["code"] == "RATE_LIMITED"
