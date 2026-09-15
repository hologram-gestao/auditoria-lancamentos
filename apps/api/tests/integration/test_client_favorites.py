"""Testes de integração dos favoritos de cliente por usuário — 86e34jd5a.

Cobre:
    - PUT marca, DELETE desmarca, os dois idempotentes (UNIQUE no banco +
      `ON CONFLICT DO NOTHING`; desmarcar o que não existe é o estado final).
    - Favorito de quem pede vai para o TOPO da lista — inclusive vindo de
      outra página, porque a ordenação é do SELECT, não da página já cortada.
    - Favorito é POR USUÁRIO: o do manager não muda a lista do admin.
    - Manager fora da carteira → 403 (`AccessibleClientDep`); sem login → 401;
      cliente inexistente → 404.

O caso negativo cross-tenant (operador do tenant A contra cliente do tenant B)
mora no parametrizado de `test_sensitive_endpoints.py`, que lê as duas rotas
da lista canônica — não é repetido aqui.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Client, ClientAssignment, User, UserClientFavorite, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"

ADMIN_EMAIL = "admin-fav@hologram.com.br"
MANAGER_EMAIL = "manager-fav@hologram.com.br"
OTHER_MANAGER_EMAIL = "manager-fora@hologram.com.br"


# ----------------------------------------------------------------------
# Seeds (mesmo padrão dos vizinhos — cada arquivo é autossuficiente)
# ----------------------------------------------------------------------


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Test User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    manager: User | None,
    created_at: datetime,
) -> Client:
    """Cliente com credenciais cifradas e `created_at` EXPLÍCITO.

    A ordem padrão da lista é `created_at desc`; sem fixar o carimbo, três
    clientes criados no mesmo flush poderiam empatar e o teste ficaria frágil.
    """
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
        created_at=created_at,
    )
    session.add(client)
    await session.flush()
    if manager is not None:
        session.add(
            ClientAssignment(
                client_id=client.id, user_id=manager.id, assigned_by=creator.id, is_primary=True
            )
        )
        await session.flush()
    return client


async def _seed_three(
    session: AsyncSession, *, creator: User, manager: User | None
) -> tuple[Client, Client, Client]:
    """C1 é o mais ANTIGO (último na ordem padrão), C3 o mais novo (primeiro)."""
    now = datetime.now(UTC)
    c1 = await _seed_client(
        session, name="C1", creator=creator, manager=manager, created_at=now - timedelta(days=3)
    )
    c2 = await _seed_client(
        session, name="C2", creator=creator, manager=manager, created_at=now - timedelta(days=2)
    )
    c3 = await _seed_client(
        session, name="C3", creator=creator, manager=manager, created_at=now - timedelta(days=1)
    )
    return c1, c2, c3


async def _login_as(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _names(resp: Response) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [row["name"] for row in resp.json()["data"]]


def _favorites(resp: Response) -> dict[str, bool]:
    assert resp.status_code == 200, resp.text
    return {row["name"]: row["is_favorite"] for row in resp.json()["data"]}


async def _count_rows(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count(UserClientFavorite.id)))).scalar_one())


# ----------------------------------------------------------------------
# Ordenação: favorito de quem pede vai para o topo
# ----------------------------------------------------------------------


class TestFavoriteOrdering:
    async def test_put_sobe_para_o_topo_e_delete_desfaz(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        c1, _c2, _c3 = await _seed_three(db_session, creator=admin, manager=None)
        await _login_as(client_with_db, ADMIN_EMAIL)

        antes = await client_with_db.get("/api/v1/clients")
        assert _names(antes) == ["C3", "C2", "C1"]
        assert _favorites(antes) == {"C1": False, "C2": False, "C3": False}

        put = await client_with_db.put(f"/api/v1/clients/{c1.id}/favorite")
        assert put.status_code == 200, put.text
        assert put.json()["id"] == str(c1.id)
        assert put.json()["is_favorite"] is True
        # Response de cliente NUNCA carrega credencial (§3.2) — nem aqui.
        assert "omie_app_key" not in put.text

        depois = await client_with_db.get("/api/v1/clients")
        assert _names(depois) == ["C1", "C3", "C2"]
        assert _favorites(depois) == {"C1": True, "C2": False, "C3": False}
        assert await _count_rows(db_session) == 1

        dele = await client_with_db.delete(f"/api/v1/clients/{c1.id}/favorite")
        assert dele.status_code == 200, dele.text
        assert dele.json()["is_favorite"] is False

        volta = await client_with_db.get("/api/v1/clients")
        assert _names(volta) == ["C3", "C2", "C1"]
        assert await _count_rows(db_session) == 0

    async def test_put_e_delete_sao_idempotentes(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        c1, _c2, _c3 = await _seed_three(db_session, creator=admin, manager=None)
        await _login_as(client_with_db, ADMIN_EMAIL)

        for _ in range(2):
            resp = await client_with_db.put(f"/api/v1/clients/{c1.id}/favorite")
            assert resp.status_code == 200, resp.text
            assert resp.json()["is_favorite"] is True
        # Dois cliques, UMA linha — a garantia é a UNIQUE + ON CONFLICT, no banco.
        assert await _count_rows(db_session) == 1

        for _ in range(2):
            resp = await client_with_db.delete(f"/api/v1/clients/{c1.id}/favorite")
            assert resp.status_code == 200, resp.text
            assert resp.json()["is_favorite"] is False
        assert await _count_rows(db_session) == 0

    async def test_favorito_de_outra_pagina_sobe_para_a_primeira(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O critério de aceite da task: o favorito da página 2 aparece na 1."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        c1, _c2, _c3 = await _seed_three(db_session, creator=admin, manager=None)
        await _login_as(client_with_db, ADMIN_EMAIL)

        pagina1 = await client_with_db.get("/api/v1/clients", params={"pageSize": 2, "page": 1})
        assert _names(pagina1) == ["C3", "C2"]
        pagina2 = await client_with_db.get("/api/v1/clients", params={"pageSize": 2, "page": 2})
        assert _names(pagina2) == ["C1"]

        put = await client_with_db.put(f"/api/v1/clients/{c1.id}/favorite")
        assert put.status_code == 200, put.text

        pagina1 = await client_with_db.get("/api/v1/clients", params={"pageSize": 2, "page": 1})
        assert _names(pagina1) == ["C1", "C3"]
        assert pagina1.json()["pagination"]["total"] == 3
        pagina2 = await client_with_db.get("/api/v1/clients", params={"pageSize": 2, "page": 2})
        assert _names(pagina2) == ["C2"]


# ----------------------------------------------------------------------
# Por usuário: o favorito de um não muda a lista do outro
# ----------------------------------------------------------------------


class TestFavoritePerUser:
    async def test_favorito_do_manager_nao_muda_a_lista_do_admin(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        manager = await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        c1, _c2, _c3 = await _seed_three(db_session, creator=admin, manager=manager)

        await _login_as(client_with_db, MANAGER_EMAIL)
        put = await client_with_db.put(f"/api/v1/clients/{c1.id}/favorite")
        assert put.status_code == 200, put.text
        do_manager = await client_with_db.get("/api/v1/clients")
        assert _names(do_manager) == ["C1", "C3", "C2"]
        assert _favorites(do_manager)["C1"] is True

        await _login_as(client_with_db, ADMIN_EMAIL)
        do_admin = await client_with_db.get("/api/v1/clients")
        assert _names(do_admin) == ["C3", "C2", "C1"]
        assert _favorites(do_admin) == {"C1": False, "C2": False, "C3": False}
        assert await _count_rows(db_session) == 1


# ----------------------------------------------------------------------
# Acesso: quem não enxerga o cliente não favorita
# ----------------------------------------------------------------------


class TestFavoriteAccess:
    async def test_manager_fora_da_carteira_recebe_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        dono = await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        fora = await _seed_user(db_session, email=OTHER_MANAGER_EMAIL, role=UserRole.MANAGER)
        del fora
        c1, _c2, _c3 = await _seed_three(db_session, creator=admin, manager=dono)

        await _login_as(client_with_db, OTHER_MANAGER_EMAIL)
        put = await client_with_db.put(f"/api/v1/clients/{c1.id}/favorite")
        assert put.status_code == 403, put.text
        # Negação não vaza o alvo (§3.15).
        assert "C1" not in put.text
        assert await _count_rows(db_session) == 0

    async def test_sem_login_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.put(f"/api/v1/clients/{uuid4()}/favorite")
        assert resp.status_code == 401

    async def test_cliente_inexistente_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.put(f"/api/v1/clients/{uuid4()}/favorite")
        assert resp.status_code == 404, resp.text
