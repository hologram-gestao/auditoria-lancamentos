"""Integração da auditoria de acesso (Sprint 3, BACK 03.5).

Cobre os critérios de aceite da `access_audit`:
    - denied: acesso fora da carteira → 404 ao cliente E 1 linha action='denied'
      com o client_id alvo, gravada ANTES da conversão 403→404 (persiste mesmo
      com a request terminando em erro).
    - view: abrir a tela de conciliação (GET /reconciliations/{id}) → 1 linha 'view'.
    - guardrail de volume: listar clientes na home (GET /clients) NÃO gera registro.
    - 404 não vaza nome/razão social; nenhuma PII na linha (só IDs).

(O evento `export` é coberto em test_export_endpoint.py, reusando o setup de export.)
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "audit-admin@hologram.com.br"
MANAGER_A_EMAIL = "audit-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "audit-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "audit-app-key"
FAKE_APP_SECRET = "audit-app-secret"
SECRET_NAME = "Cliente Secretíssimo LTDA"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Audit User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User, manager: User, name: str) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(FAKE_APP_KEY, hex_key)
    ct_s, iv_s = encrypt(FAKE_APP_SECRET, hex_key)
    cli = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(cli)
    await session.flush()
    session.add(ClientAssignment(client_id=cli.id, user_id=manager.id, assigned_by=creator.id))
    await session.flush()
    return cli


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=_hex64(f"audit-{uuid4().hex}"),
        status="reviewing",
        balance_start=Decimal("0.00"),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _audit_rows(db: AsyncSession, action: str) -> list[AccessAudit]:
    return list(
        (await db.execute(select(AccessAudit).where(AccessAudit.action == action))).scalars().all()
    )


@pytest.mark.integration
class TestAccessAudit:
    async def test_denied_outside_portfolio_writes_denied_row_and_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli_b = await _seed_client(db_session, creator=admin, manager=mgr_b, name=SECRET_NAME)
        sess_b = await _seed_session(db_session, client=cli_b, creator=admin)
        await _login(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess_b.id}")

        # 404 anti-enumeração ao cliente, sem vazar nada do cliente-alvo.
        assert resp.status_code == 404
        assert SECRET_NAME not in resp.text

        denied = await _audit_rows(db_session, "denied")
        assert len(denied) == 1
        assert denied[0].client_id == cli_b.id
        assert denied[0].user_id == mgr_a.id
        assert denied[0].session_id is None
        # Só IDs — nenhuma PII do cliente-alvo.
        assert SECRET_NAME not in str(denied[0].rota)
        # E NÃO gerou 'view' (denied acontece antes).
        assert await _audit_rows(db_session, "view") == []

    async def test_view_writes_view_row(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli = await _seed_client(db_session, creator=admin, manager=mgr_b, name="Cliente Visível")
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text

        view = await _audit_rows(db_session, "view")
        assert len(view) == 1
        assert view[0].client_id == cli.id
        assert view[0].session_id == sess.id
        assert view[0].user_id == admin.id

    async def test_status_polling_does_not_write_view(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Guardrail: o /status (polling) NÃO é 'view' — só o header da tela."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli = await _seed_client(db_session, creator=admin, manager=mgr_b, name="Cliente Poll")
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/status")
        assert resp.status_code == 200, resp.text
        assert await _audit_rows(db_session, "view") == []

    async def test_listing_clients_home_writes_no_audit(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Guardrail de volume: listar clientes na home NÃO gera registro."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        await _seed_client(db_session, creator=admin, manager=mgr_b, name="Cliente Home")
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200, resp.text

        all_rows = list((await db_session.execute(select(AccessAudit))).scalars().all())
        assert all_rows == []
