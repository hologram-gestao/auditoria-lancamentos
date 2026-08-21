"""Autor da conciliação exposto com máscara por escopo (86e2n39f1).

O `created_by` sempre foi gravado; estas são as primeiras responses que o
expõem. A regra de produto (decisão do Pedro, 22/08/2026): usuário DO CLIENTE
não vê nome nem e-mail de funcionário da Hologram — vê "Equipe Hologram". A
máscara é do SERVIDOR: payload com o nome real e UI escondendo não é barreira
(§4.9). Autor do próprio tenant e observador da Hologram veem tudo.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ReconciliationFile,
    ReconciliationSession,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "author-admin@hologram.com.br"
TENANT_MANAGER_EMAIL = "author-gerente@cliente.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_user(
    db: AsyncSession,
    *,
    name: str,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    user = User(
        name=name,
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_client(db: AsyncSession, *, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("author-app-key", hex_key)
    ct_secret, iv_secret = encrypt("author-app-secret", hex_key)
    client = Client(
        name="Cliente do Autor",
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    db.add(client)
    await db.flush()
    return client


async def _seed_session(
    db: AsyncSession, *, client: Client, creator: User, conta: int = 77
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=conta,
        reference_month=date(2026, 5, 1),
        date_tolerance_days=0,
        status="reviewing",
    )
    db.add(sess)
    await db.flush()
    db.add(
        ReconciliationFile(
            session_id=sess.id,
            file_hash=_hex64(str(sess.id)),
            status="parsed",
        )
    )
    await db.flush()
    return sess


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
class TestSessionAuthor:
    async def test_hologram_viewer_sees_real_author(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(
            db_session, name="Ana da Hologram", email=ADMIN_EMAIL, role=UserRole.ADMIN
        )
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detail.status_code == 200, detail.text
        author = detail.json()["data"]["created_by"]
        assert author == {"name": "Ana da Hologram", "email": ADMIN_EMAIL}

        lista = await client_with_db.get(f"/api/v1/clients/{cli.id}/reconciliations")
        item = lista.json()["data"][0]
        assert item["created_by"] == {"name": "Ana da Hologram", "email": ADMIN_EMAIL}

    async def test_tenant_viewer_sees_equipe_hologram_for_system_author(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Cliente NÃO vê pessoa da equipe: nem nome, nem e-mail — em nenhum
        dos dois endpoints. A máscara precisa vir do servidor."""
        admin = await _seed_user(
            db_session, name="Ana da Hologram", email=ADMIN_EMAIL, role=UserRole.ADMIN
        )
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_user(
            db_session,
            name="Gerente do Cliente",
            email=TENANT_MANAGER_EMAIL,
            role=UserRole.CLIENT_MANAGER,
            scope=UserScope.CLIENT,
            client_id=cli.id,
        )
        await _login(client_with_db, TENANT_MANAGER_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detail.status_code == 200, detail.text
        author = detail.json()["data"]["created_by"]
        assert author == {"name": "Equipe Hologram", "email": None}
        assert "Ana" not in detail.text
        assert ADMIN_EMAIL not in detail.text

        lista = await client_with_db.get(f"/api/v1/clients/{cli.id}/reconciliations")
        item = lista.json()["data"][0]
        assert item["created_by"] == {"name": "Equipe Hologram", "email": None}
        assert ADMIN_EMAIL not in lista.text

    async def test_tenant_viewer_sees_own_colleague(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Autor do PRÓPRIO tenant aparece com nome e e-mail reais."""
        admin = await _seed_user(
            db_session, name="Ana da Hologram", email=ADMIN_EMAIL, role=UserRole.ADMIN
        )
        cli = await _seed_client(db_session, creator=admin)
        gerente = await _seed_user(
            db_session,
            name="Gerente do Cliente",
            email=TENANT_MANAGER_EMAIL,
            role=UserRole.CLIENT_MANAGER,
            scope=UserScope.CLIENT,
            client_id=cli.id,
        )
        sess = await _seed_session(db_session, client=cli, creator=gerente, conta=78)
        await _login(client_with_db, TENANT_MANAGER_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        author = detail.json()["data"]["created_by"]
        assert author == {"name": "Gerente do Cliente", "email": TENANT_MANAGER_EMAIL}

    async def test_no_forbidden_user_fields_leak(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A response nunca traz campo de usuário além de name/email (§3.2)."""
        admin = await _seed_user(
            db_session, name="Ana da Hologram", email=ADMIN_EMAIL, role=UserRole.ADMIN
        )
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        author = detail.json()["data"]["created_by"]
        assert set(author.keys()) == {"name", "email"}
        assert "password" not in detail.text
