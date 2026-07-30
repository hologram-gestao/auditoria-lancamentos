"""Modelo de tenancy em `users` (Sprint 5 / R1 — BACK 05.1).

Cobre os critérios de aceite da task:
    - `scope` nasce 'system' e `client_id` nasce nulo (backfill/default) — nenhum
      usuário existente vira usuário de cliente.
    - A CHECK constraint é do BANCO: `scope='client'` sem `client_id` e
      `scope='system'` com `client_id` são rejeitados pelo Postgres (não por
      validação de aplicação) — por isso testes de integração, não mock.
    - `scope='client'` com `client_id` é aceito.
    - FK `ON DELETE RESTRICT`: apagar um cliente com usuários presos falha.
    - Regressão: usuário `system` (admin/manager) continua com login e carteira
      (`client_assignments`) intactos.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    SCOPE_CLIENT_ID_CHECK,
    SCOPE_CLIENT_ID_CONSTRAINT,
    Client,
    ClientAssignment,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@TenancyS5#1"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Tenancy User",
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


async def _seed_client(session: AsyncSession, *, creator: User, name: str) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("tenancy-app-key", hex_key)
    ct_s, iv_s = encrypt("tenancy-app-secret", hex_key)
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
    return cli


class TestDefaults:
    async def test_user_nasce_system_sem_tenant(self, db_session: AsyncSession) -> None:
        """Equivalente ao backfill: quem é criado sem escopo explícito é `system`."""
        user = User(
            name="Legado",
            email="legado-s5@hologram.com.br",
            password_hash=hash_password(PLAIN_PASSWORD),
            role=UserRole.MANAGER.value,
            active=True,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)

        assert user.scope == UserScope.SYSTEM.value
        assert user.client_id is None

    async def test_server_default_cobre_insert_fora_do_orm(self, db_session: AsyncSession) -> None:
        """INSERT cru sem a coluna `scope` cai no server_default 'system'.

        É exatamente o que o backfill da migration garante para linhas antigas.
        """
        user_id = uuid4()
        await db_session.execute(
            text(
                "INSERT INTO users (id, name, email, password_hash, role, active, "
                "created_at, updated_at) "
                "VALUES (:id, 'Cru', 'cru-s5@hologram.com.br', :ph, 'manager', true, "
                "now(), now())"
            ),
            {"id": user_id, "ph": hash_password(PLAIN_PASSWORD)},
        )
        row = (
            await db_session.execute(
                text("SELECT scope, client_id FROM users WHERE id = :id"), {"id": user_id}
            )
        ).one()
        assert row.scope == UserScope.SYSTEM.value
        assert row.client_id is None


class TestCheckConstraint:
    async def test_client_sem_client_id_e_rejeitado(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError) as exc:
            await _seed_user(
                db_session,
                email="client-sem-tenant@austral.com.br",
                role=UserRole.CLIENT_OPERATOR,
                scope=UserScope.CLIENT,
                client_id=None,
            )
        assert SCOPE_CLIENT_ID_CONSTRAINT in str(exc.value)
        await db_session.rollback()

    async def test_system_com_client_id_e_rejeitado(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, email="adm-ck@hologram.com.br", role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin, name="Austral CK")

        with pytest.raises(IntegrityError) as exc:
            await _seed_user(
                db_session,
                email="system-com-tenant@hologram.com.br",
                role=UserRole.MANAGER,
                scope=UserScope.SYSTEM,
                client_id=cli.id,
            )
        assert SCOPE_CLIENT_ID_CONSTRAINT in str(exc.value)
        await db_session.rollback()

    async def test_update_que_quebra_o_invariante_e_rejeitado(
        self, db_session: AsyncSession
    ) -> None:
        """Não basta barrar o INSERT — o UPDATE também tem de bater na constraint."""
        admin = await _seed_user(db_session, email="adm-upd@hologram.com.br", role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin, name="Austral UPD")
        op_user = await _seed_user(
            db_session,
            email="op-upd@austral.com.br",
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=cli.id,
        )

        op_user.client_id = None
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_client_com_client_id_e_aceito(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, email="adm-ok@hologram.com.br", role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin, name="Austral OK")

        user = await _seed_user(
            db_session,
            email="gerente@austral.com.br",
            role=UserRole.CLIENT_MANAGER,
            scope=UserScope.CLIENT,
            client_id=cli.id,
        )
        assert user.scope == UserScope.CLIENT.value
        assert user.client_id == cli.id

    async def test_predicado_da_constraint_bate_com_o_do_banco(
        self, db_session: AsyncSession
    ) -> None:
        """Impede drift entre o predicado do modelo e o que o Postgres aplica."""
        definition = (
            await db_session.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
                    "WHERE conname = :name"
                ),
                {"name": SCOPE_CLIENT_ID_CONSTRAINT},
            )
        ).scalar_one()
        # O Postgres reescreve o predicado com parênteses/casts próprios
        # (`((scope) = 'client'::text)`) — normalizamos antes de comparar.
        normalized = " ".join(
            str(definition).replace("::text", "").replace("(", " ").replace(")", " ").split()
        )
        for fragment in ("scope = 'client'", "client_id IS NOT NULL", "client_id IS NULL"):
            assert fragment in normalized, f"{fragment!r} ausente de {normalized!r}"
        # E o predicado declarado no modelo menciona os mesmos três fragmentos.
        assert "client_id IS NOT NULL" in SCOPE_CLIENT_ID_CHECK


class TestForeignKey:
    async def test_apagar_cliente_com_usuario_e_restrito(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, email="adm-fk@hologram.com.br", role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin, name="Austral FK")
        await _seed_user(
            db_session,
            email="op-fk@austral.com.br",
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=cli.id,
        )

        # O próprio DELETE bate na FK (não o flush posterior) — por isso ele é a
        # única sentença dentro do `raises`.
        stmt = delete(Client).where(Client.id == cli.id)
        with pytest.raises(IntegrityError):
            await db_session.execute(stmt)
        await db_session.rollback()


class TestRegressaoUsuarioSystem:
    async def test_manager_system_mantem_login_e_carteira(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email="adm-reg@hologram.com.br", role=UserRole.ADMIN)
        manager = await _seed_user(
            db_session, email="mgr-reg@hologram.com.br", role=UserRole.MANAGER
        )
        cli = await _seed_client(db_session, creator=admin, name="Austral REG")
        db_session.add(ClientAssignment(client_id=cli.id, user_id=manager.id, assigned_by=admin.id))
        await db_session.flush()

        login = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "mgr-reg@hologram.com.br", "password": PLAIN_PASSWORD},
        )
        assert login.status_code == 200

        # Listagem (não o detalhe, que bate no Omie real): a carteira do manager
        # de sistema continua sendo o filtro — o cliente atribuído aparece.
        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200
        assert [c["id"] for c in resp.json()["data"]] == [str(cli.id)]

        # A carteira continua sendo a fonte do escopo do manager de sistema.
        rows = (
            (
                await db_session.execute(
                    select(ClientAssignment).where(ClientAssignment.user_id == manager.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert manager.scope == UserScope.SYSTEM.value
        assert manager.client_id is None
