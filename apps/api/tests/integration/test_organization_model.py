"""Comportamento das colunas de organização no schema do `create_all` (86e36ec7p).

O que o `server_default` promete — linha sem o campo é da Hologram — vale para
os testes do repositório inteiro, que constroem `User(...)`/`Client(...)` sem
org. Aqui isso é provado uma vez, junto com as garantias de integridade que
moram no banco: FK RESTRICT, nome de organização único e categoria única POR
organização (não mais globalmente).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    HOLOGRAM_ORGANIZATION_NAME,
    Client,
    ClientCategory,
    Organization,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_user(session: AsyncSession, *, email: str, **extra: object) -> User:
    user = User(
        name="Org User",
        email=email.lower(),
        password_hash=hash_password("Senh@OrgS8#1"),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
        **extra,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user, ["organization_id"])
    return user


async def _seed_client(session: AsyncSession, *, creator: User, **extra: object) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("app_key", hex_key)
    ct_secret, iv_secret = encrypt("app_secret", hex_key)
    client = Client(
        name=f"Cliente {uuid4().hex[:6]}",
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
        **extra,
    )
    session.add(client)
    await session.flush()
    await session.refresh(client, ["organization_id"])
    return client


async def _seed_org(session: AsyncSession, name: str) -> Organization:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    return org


class TestHologramEODefault:
    async def test_a_hologram_existe_no_schema_de_teste(self, db_session: AsyncSession) -> None:
        org = await db_session.get(Organization, HOLOGRAM_ORGANIZATION_ID)
        assert org is not None
        assert org.name == HOLOGRAM_ORGANIZATION_NAME
        assert org.active is True

    async def test_linha_sem_org_nasce_da_hologram(self, db_session: AsyncSession) -> None:
        """É o que faz os testes do repositório inteiro seguirem verdes sem tocar em fixture."""
        admin = await _seed_user(db_session, email="org-default@hologram.com.br")
        client = await _seed_client(db_session, creator=admin)
        category = ClientCategory(name=f"Cat {uuid4().hex[:6]}")
        db_session.add(category)
        await db_session.flush()
        await db_session.refresh(category, ["organization_id"])

        assert admin.organization_id == HOLOGRAM_ORGANIZATION_ID
        assert client.organization_id == HOLOGRAM_ORGANIZATION_ID
        assert category.organization_id == HOLOGRAM_ORGANIZATION_ID

    async def test_org_explicita_vence_o_default(self, db_session: AsyncSession) -> None:
        other = await _seed_org(db_session, f"Prospecta {uuid4().hex[:6]}")
        admin = await _seed_user(
            db_session, email=f"adm-{other.id.hex[:6]}@prospecta.com.br", organization_id=other.id
        )
        client = await _seed_client(db_session, creator=admin, organization_id=other.id)
        assert admin.organization_id == other.id
        assert client.organization_id == other.id


class TestIntegridadeNoBanco:
    async def test_nome_de_organizacao_e_unico(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError):
            await _seed_org(db_session, HOLOGRAM_ORGANIZATION_NAME)
        await db_session.rollback()

    async def test_apagar_organizacao_com_cliente_e_restrito(
        self, db_session: AsyncSession
    ) -> None:
        other = await _seed_org(db_session, f"Escritorio {uuid4().hex[:6]}")
        admin = await _seed_user(
            db_session, email=f"adm-{other.id.hex[:6]}@escritorio.com.br", organization_id=other.id
        )
        await _seed_client(db_session, creator=admin, organization_id=other.id)

        with pytest.raises(IntegrityError):
            await db_session.execute(delete(Organization).where(Organization.id == other.id))
        await db_session.rollback()

    async def test_categoria_com_o_mesmo_nome_cabe_em_organizacoes_diferentes(
        self, db_session: AsyncSession
    ) -> None:
        """A unicidade desceu de global para por organização (D3)."""
        other = await _seed_org(db_session, f"BPO {uuid4().hex[:6]}")
        name = f"Fintech {uuid4().hex[:6]}"
        db_session.add(ClientCategory(name=name))  # Hologram, pelo default
        db_session.add(ClientCategory(name=name, organization_id=other.id))
        await db_session.flush()

        rows = (
            await db_session.execute(select(ClientCategory).where(ClientCategory.name == name))
        ).scalars()
        assert {c.organization_id for c in rows} == {HOLOGRAM_ORGANIZATION_ID, other.id}

    async def test_categoria_repetida_na_mesma_organizacao_e_recusada(
        self, db_session: AsyncSession
    ) -> None:
        name = f"Comercio {uuid4().hex[:6]}"
        db_session.add(ClientCategory(name=name))
        await db_session.flush()
        db_session.add(ClientCategory(name=name))
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()
