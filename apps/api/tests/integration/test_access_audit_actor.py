"""Escopo e tenant do ATOR na `access_audit` (Sprint 5 / R6 — BACK 05.2).

A S3 registrava só o tenant ALVO. Aqui verificamos que a linha passa a dizer
**de onde** partiu o acesso (`user_scope` + `actor_client_id`), que a negação
cross-tenant produz EXATAMENTE 1 linha `denied` + o evento
`acesso_cross_tenant_negado` com as 4 propriedades do PRD, que navegação normal
dentro do próprio tenant NÃO infla a auditoria, e que nenhuma coluna carrega PII.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import structlog
from sqlalchemy import select

from app.core.audit import record_cross_tenant_denied
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.core.telemetry import EVENT_ACESSO_CROSS_TENANT_NEGADO, EVENT_ACESSO_NEGADO
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    ReconciliationSession,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@AtorS5#1"
SECRET_NAME = "Fulana Comércio de Peças LTDA"
ADMIN_EMAIL = "ator-admin@hologram.com.br"
MANAGER_A_EMAIL = "ator-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "ator-mgr-b@hologram.com.br"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Ator",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession, *, creator: User, manager: User | None, name: str
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("ator-app-key", hex_key)
    ct_s, iv_s = encrypt("ator-app-secret", hex_key)
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
    if manager is not None:
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
        file_hash=_hex64(f"ator-{uuid4().hex}"),
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


async def _all_rows(db: AsyncSession) -> list[AccessAudit]:
    return list((await db.execute(select(AccessAudit))).scalars().all())


class TestRecordCrossTenantDenied:
    async def test_grava_uma_linha_com_ator_e_alvo(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli_a = await _seed_client(db_session, creator=admin, manager=None, name="Austral")
        cli_b = await _seed_client(db_session, creator=admin, manager=None, name=SECRET_NAME)
        operador = await _seed_user(
            db_session,
            email="op@austral.com.br",
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=cli_a.id,
        )

        await record_cross_tenant_denied(
            db_session,
            user_id=operador.id,
            user_scope=operador.scope,
            actor_client_id=operador.client_id,
            target_client_id=cli_b.id,
            rota="/api/v1/reconciliations/{id}",
        )

        rows = await _all_rows(db_session)
        assert len(rows) == 1
        row = rows[0]
        assert row.action == "denied"
        assert row.user_id == operador.id
        assert row.user_scope == UserScope.CLIENT.value
        assert row.actor_client_id == cli_a.id  # tenant do ator
        assert row.client_id == cli_b.id  # tenant alvo
        assert row.rota == "/api/v1/reconciliations/{id}"

    async def test_emite_os_dois_eventos_com_as_propriedades_do_prd(
        self, db_session: AsyncSession
    ) -> None:
        """O evento da S5 (4 props) + o da S3 (mantido, para não zerar a métrica)."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli_a = await _seed_client(db_session, creator=admin, manager=None, name="Austral")
        cli_b = await _seed_client(db_session, creator=admin, manager=None, name=SECRET_NAME)
        operador = await _seed_user(
            db_session,
            email="op2@austral.com.br",
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=cli_a.id,
        )

        with structlog.testing.capture_logs() as logs:
            await record_cross_tenant_denied(
                db_session,
                user_id=operador.id,
                user_scope=operador.scope,
                actor_client_id=operador.client_id,
                target_client_id=cli_b.id,
                rota="/api/v1/anomalies/{id}",
            )

        by_event = {entry["event"]: entry for entry in logs}
        assert EVENT_ACESSO_CROSS_TENANT_NEGADO in by_event
        assert EVENT_ACESSO_NEGADO in by_event

        cross = by_event[EVENT_ACESSO_CROSS_TENANT_NEGADO]
        assert cross["user_scope"] == UserScope.CLIENT.value
        assert cross["tenant_ator"] == str(cli_a.id)
        assert cross["tenant_alvo"] == str(cli_b.id)
        assert cross["rota"] == "/api/v1/anomalies/{id}"
        # SEM PII: o nome do tenant alvo não aparece em lugar nenhum do evento.
        assert SECRET_NAME not in str(logs)

    async def test_ator_system_grava_tenant_nulo(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli_b = await _seed_client(db_session, creator=admin, manager=None, name=SECRET_NAME)

        await record_cross_tenant_denied(
            db_session,
            user_id=admin.id,
            user_scope=admin.scope,
            actor_client_id=admin.client_id,
            target_client_id=cli_b.id,
            rota="/r",
        )

        rows = await _all_rows(db_session)
        assert len(rows) == 1
        assert rows[0].user_scope == UserScope.SYSTEM.value
        assert rows[0].actor_client_id is None


class TestAtorNasRotas:
    async def test_denied_de_manager_fora_da_carteira_registra_ator_system(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Regressão da S3 + dimensão nova: 1 linha, ator `system` sem tenant."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli_b = await _seed_client(db_session, creator=admin, manager=mgr_b, name=SECRET_NAME)
        sess_b = await _seed_session(db_session, client=cli_b, creator=admin)
        await _login(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess_b.id}")

        assert resp.status_code == 404  # anti-enumeração preservada
        assert SECRET_NAME not in resp.text

        rows = await _all_rows(db_session)
        assert len(rows) == 1
        assert rows[0].action == "denied"
        assert rows[0].user_id == mgr_a.id
        assert rows[0].user_scope == UserScope.SYSTEM.value
        assert rows[0].actor_client_id is None
        assert rows[0].client_id == cli_b.id

    async def test_view_registra_escopo_do_ator(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli = await _seed_client(db_session, creator=admin, manager=mgr_b, name="Cliente Visível")
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text

        rows = await _all_rows(db_session)
        assert len(rows) == 1
        assert rows[0].action == "view"
        assert rows[0].user_scope == UserScope.SYSTEM.value
        assert rows[0].actor_client_id is None
        assert rows[0].client_id == cli.id


class TestGuardrails:
    async def test_navegacao_no_proprio_tenant_nao_infla_auditoria(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Continua NÃO sendo 'todo GET': a lista fechada da S3 permanece."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli = await _seed_client(db_session, creator=admin, manager=mgr_b, name="Cliente Home")
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        assert (await client_with_db.get("/api/v1/clients")).status_code == 200
        assert (await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/status")).status_code

        assert await _all_rows(db_session) == []

    async def test_nenhuma_coluna_carrega_pii(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Toda coluna gravada é ID, enum ou path — nunca nome/e-mail/razão social."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli_b = await _seed_client(db_session, creator=admin, manager=mgr_b, name=SECRET_NAME)
        sess_b = await _seed_session(db_session, client=cli_b, creator=admin)
        await _login(client_with_db, MANAGER_A_EMAIL)
        await client_with_db.get(f"/api/v1/reconciliations/{sess_b.id}")

        rows = await _all_rows(db_session)
        assert len(rows) == 1
        serialized = " ".join(
            str(getattr(rows[0], column.name)) for column in AccessAudit.__table__.columns
        )
        for forbidden in (SECRET_NAME, MANAGER_A_EMAIL, ADMIN_EMAIL, "Ator", "@"):
            assert forbidden not in serialized, f"PII {forbidden!r} vazou em {serialized!r}"
