"""`scripts/promote_platform_admin.py` — o único caminho para nascer `platform_admin`.

Critérios de aceite (86e36ecqz): idempotente (a 2ª execução é no-op), recusa
usuário de tenant, recusa e-mail inexistente, e a linha promovida passa no
CHECK `ck_users_scope_consistency` (scope/role/org/tenant mudam JUNTOS).

Isolamento: UMA conexão com transação externa + `async_sessionmaker` em
`create_savepoint`, então o `commit()` do script vira savepoint e o
`rollback()` final desfaz tudo (mesma estratégia de `test_rotate_encryption_key`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from scripts.promote_platform_admin import (
    PromotionOutcome,
    _parse_args,
    promote_platform_admin,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Client, ClientAssignment, User, UserRole, UserScope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
        finally:
            if outer.is_active:
                await outer.rollback()


async def _seed_user(
    s: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
    active: bool = True,
) -> User:
    user = User(
        name=email.split("@")[0],
        email=email,
        password_hash=hash_password("Senh@Promocao#1"),
        role=role.value,
        active=active,
        scope=scope.value,
        client_id=client_id,
    )
    s.add(user)
    await s.flush()
    return user


async def _reload(factory: async_sessionmaker[AsyncSession], email: str) -> User:
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email))).scalar_one()


class TestPromoteScript:
    async def test_promove_admin_e_a_segunda_execucao_e_no_op(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        email = f"promo-admin-{uuid4().hex[:6]}@hologramgestao.com"
        async with factory() as s:
            await _seed_user(s, email=email, role=UserRole.ADMIN)
            await s.commit()

        first = await promote_platform_admin(session_factory=factory, email=email.upper())
        assert first.outcome is PromotionOutcome.PROMOTED
        assert first.ok
        assert first.inactive is False
        assert first.assignments_left == 0

        row = await _reload(factory, email)
        assert row.scope == UserScope.PLATFORM.value
        assert row.role == UserRole.PLATFORM_ADMIN.value
        assert row.organization_id is None
        assert row.client_id is None
        assert row.active is True

        second = await promote_platform_admin(session_factory=factory, email=email)
        assert second.outcome is PromotionOutcome.ALREADY_PLATFORM
        assert second.ok
        again = await _reload(factory, email)
        assert again.updated_at == row.updated_at

    async def test_recusa_usuario_de_cliente_e_nao_muda_a_linha(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        email = f"promo-op-{uuid4().hex[:6]}@cliente.com.br"
        async with factory() as s:
            admin = await _seed_user(
                s, email=f"promo-dono-{uuid4().hex[:6]}@h.com", role=UserRole.ADMIN
            )
            hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
            ct_k, iv_k = encrypt("k", hex_key)
            ct_s, iv_s = encrypt("s", hex_key)
            cli = Client(
                name="Cliente da promoção",
                omie_app_key_encrypted=ct_k,
                omie_app_key_iv=iv_k,
                omie_app_secret_encrypted=ct_s,
                omie_app_secret_iv=iv_s,
                active=True,
                created_by=admin.id,
            )
            s.add(cli)
            await s.flush()
            await _seed_user(
                s,
                email=email,
                role=UserRole.CLIENT_OPERATOR,
                scope=UserScope.CLIENT,
                client_id=cli.id,
            )
            await s.commit()

        result = await promote_platform_admin(session_factory=factory, email=email)
        assert result.outcome is PromotionOutcome.REFUSED_CLIENT_SCOPE
        assert not result.ok
        row = await _reload(factory, email)
        assert row.scope == UserScope.CLIENT.value
        assert row.role == UserRole.CLIENT_OPERATOR.value
        assert row.client_id is not None

    async def test_recusa_email_inexistente(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        result = await promote_platform_admin(
            session_factory=factory, email=f"ninguem-{uuid4().hex[:6]}@nada.com"
        )
        assert result.outcome is PromotionOutcome.REFUSED_NOT_FOUND
        assert not result.ok

    async def test_dry_run_nao_grava(self, factory: async_sessionmaker[AsyncSession]) -> None:
        email = f"promo-dry-{uuid4().hex[:6]}@h.com"
        async with factory() as s:
            await _seed_user(s, email=email, role=UserRole.MANAGER)
            await s.commit()

        result = await promote_platform_admin(session_factory=factory, email=email, dry_run=True)
        assert result.outcome is PromotionOutcome.PROMOTED
        row = await _reload(factory, email)
        assert row.scope == UserScope.SYSTEM.value
        assert row.role == UserRole.MANAGER.value
        assert row.organization_id is not None

    async def test_gerente_com_carteira_e_promovido_e_a_carteira_e_so_aviso(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        email = f"promo-ger-{uuid4().hex[:6]}@h.com"
        async with factory() as s:
            manager = await _seed_user(s, email=email, role=UserRole.MANAGER, active=False)
            hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
            ct_k, iv_k = encrypt("k", hex_key)
            ct_s, iv_s = encrypt("s", hex_key)
            cli = Client(
                name="Cliente do gerente",
                omie_app_key_encrypted=ct_k,
                omie_app_key_iv=iv_k,
                omie_app_secret_encrypted=ct_s,
                omie_app_secret_iv=iv_s,
                active=True,
                created_by=manager.id,
            )
            s.add(cli)
            await s.flush()
            s.add(
                ClientAssignment(
                    client_id=cli.id, user_id=manager.id, assigned_by=manager.id, is_primary=True
                )
            )
            await s.commit()

        result = await promote_platform_admin(session_factory=factory, email=email)
        assert result.outcome is PromotionOutcome.PROMOTED
        assert result.assignments_left == 1
        assert result.inactive is True
        row = await _reload(factory, email)
        assert row.scope == UserScope.PLATFORM.value
        assert row.active is False  # o script não reativa ninguém
        async with factory() as s:
            left = (
                (
                    await s.execute(
                        select(ClientAssignment).where(ClientAssignment.user_id == row.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(left) == 1

    async def test_carteira_em_cliente_encerrado_nao_conta_como_aviso(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """A linha de carteira de cliente ENCERRADO é retida de propósito (§4.12)
        e não aceita escrita (409) — avisá-la seria pendência sem ação. Caso real
        de 21/09: promoção com `assignments_left=1` num cliente já encerrado."""
        email = f"promo-enc-{uuid4().hex[:6]}@h.com"
        async with factory() as s:
            manager = await _seed_user(s, email=email, role=UserRole.MANAGER)
            hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
            clients: list[Client] = []
            for name, closed in (("Encerrado", True), ("Aberto", False)):
                ct_k, iv_k = encrypt("k", hex_key)
                ct_s, iv_s = encrypt("s", hex_key)
                cli = Client(
                    name=name,
                    omie_app_key_encrypted=ct_k,
                    omie_app_key_iv=iv_k,
                    omie_app_secret_encrypted=ct_s,
                    omie_app_secret_iv=iv_s,
                    active=not closed,
                    closed_at=datetime.now(UTC) if closed else None,
                    created_by=manager.id,
                )
                s.add(cli)
                clients.append(cli)
            await s.flush()
            for cli in clients:
                s.add(
                    ClientAssignment(
                        client_id=cli.id,
                        user_id=manager.id,
                        assigned_by=manager.id,
                        is_primary=True,
                    )
                )
            await s.commit()

        result = await promote_platform_admin(session_factory=factory, email=email)
        assert result.outcome is PromotionOutcome.PROMOTED
        # Duas linhas no banco, UMA em cliente aberto: só essa é aviso.
        assert result.assignments_left == 1
        row = await _reload(factory, email)
        async with factory() as s:
            left = (
                (
                    await s.execute(
                        select(ClientAssignment).where(ClientAssignment.user_id == row.id)
                    )
                )
                .scalars()
                .all()
            )
        assert len(left) == 2  # nada é apagado: o aviso é só sobre a linha acionável


def test_cli_exige_email_e_aceita_dry_run() -> None:
    args = _parse_args(["--email", "Pessoa@Hologramgestao.com", "--dry-run"])
    assert args.email == "Pessoa@Hologramgestao.com"
    assert args.dry_run is True
    with pytest.raises(SystemExit):
        _parse_args([])
