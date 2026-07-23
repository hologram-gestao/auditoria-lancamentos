"""Integração do gatilho sintético de alerta (Sprint 3, BACK 03.6).

`POST /api/v1/system/alert-test` — admin-only. Prova a entrega ponta a ponta:
o endpoint dispara um alerta ao webhook configurado (mockado via respx) e
reporta o resultado por canal. Consumido pela 03.7.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import User, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "alert-admin@hologram.com.br"
MANAGER_EMAIL = "alert-mgr@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
_WEBHOOK = "https://hooks.test/plantao-adl"


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Alert User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
class TestSyntheticAlertEndpoint:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.post("/api/v1/system/alert-test")
        assert resp.status_code == 401

    async def test_manager_forbidden(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        await _login(client_with_db, MANAGER_EMAIL)
        resp = await client_with_db.post("/api/v1/system/alert-test")
        assert resp.status_code == 403

    async def test_admin_triggers_synthetic_alert_delivered(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        # Configura o canal de webhook para esta request (o endpoint resolve
        # Settings via Depends → lê o singleton após o cache_clear).
        monkeypatch.setenv("ALERT_WEBHOOK_URL", _WEBHOOK)
        get_settings.cache_clear()

        try:
            with respx.mock:
                route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
                resp = await client_with_db.post("/api/v1/system/alert-test")
        finally:
            get_settings.cache_clear()

        assert resp.status_code == 200, resp.text
        assert route.called
        data = resp.json()["data"]
        assert data["delivered"] is True
        assert data["webhook"] is True
        assert data["email"] is None
