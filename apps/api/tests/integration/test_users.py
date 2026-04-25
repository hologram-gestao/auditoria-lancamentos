"""Testes de integração do CRUD de usuários — cobre BACK 2.1 do backlog.

Cenários:
    - List paginado feliz, busca por nome ou email (case-insensitive).
    - Criação feliz.
    - Email duplicado retorna 409 com mensagem amigável.
    - Senha curta retorna 400 (validação Pydantic).
    - Update parcial (PATCH) — só campos enviados são alterados.
    - Update com email já em uso por outro user retorna 409.
    - Admin não pode rebaixar a si mesmo a manager.
    - Deactivate / activate trocam o flag `active` do DB.
    - Admin não pode desativar a si mesmo (Doc §8.2/§8.5).
    - Manager autenticado em qualquer rota retorna 403 (RBAC).
    - Não autenticado retorna 401.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models import User, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient


# Constantes de teste — strings inertes, não credenciais reais.
ADMIN_EMAIL = "users-admin@hologram.com.br"
ADMIN_PLAIN = "Adm1n!Senh@Forte#"
NEW_USER_PLAIN = "Nov0!Senh@Forte#"
SHORT_PLAIN = "abc"


async def _seed(
    session: AsyncSession,
    *,
    email: str,
    plain_text: str = ADMIN_PLAIN,
    role: UserRole = UserRole.ADMIN,
    active: bool = True,
    name: str = "Test",
) -> User:
    user = User(
        name=name,
        email=email.lower(),
        password_hash=hash_password(plain_text),
        role=role.value,
        active=active,
    )
    session.add(user)
    await session.flush()
    return user


async def _login_as(client: AsyncClient, email: str, plain_text: str = ADMIN_PLAIN) -> None:
    """Faz login no client (cookies persistem no jar para próximas requests)."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": plain_text},
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# RBAC — quem pode acessar
# ----------------------------------------------------------------------


class TestUsersRBAC:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get("/api/v1/users")
        assert resp.status_code == 401

    async def test_manager_returns_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email="mgr-403@hologram.com.br", role=UserRole.MANAGER)
        await _login_as(client_with_db, "mgr-403@hologram.com.br")
        resp = await client_with_db.get("/api/v1/users")
        assert resp.status_code == 403


# ----------------------------------------------------------------------
# GET /users (list)
# ----------------------------------------------------------------------


class TestListUsers:
    async def test_list_returns_paginated(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        for i in range(3):
            await _seed(
                db_session,
                email=f"u{i}@hologram.com.br",
                role=UserRole.MANAGER,
                name=f"User {i}",
            )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/users?page=1&pageSize=10")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert "pagination" in body
        assert body["pagination"]["total"] >= 4
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["pageSize"] == 10
        # Não expõe password_hash
        for u in body["data"]:
            assert "password_hash" not in u
            assert "id" in u
            assert "email" in u
            assert "role" in u

    async def test_search_by_name(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _seed(
            db_session,
            email="alice@hologram.com.br",
            role=UserRole.MANAGER,
            name="Alice Souza",
        )
        await _seed(
            db_session,
            email="bob@hologram.com.br",
            role=UserRole.MANAGER,
            name="Bob Lima",
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/users?search=alice")
        assert resp.status_code == 200
        emails = {u["email"] for u in resp.json()["data"]}
        assert "alice@hologram.com.br" in emails
        assert "bob@hologram.com.br" not in emails

    async def test_search_by_email_case_insensitive(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _seed(
            db_session,
            email="Carol@hologram.com.br",
            role=UserRole.MANAGER,
            name="Carol",
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/users?search=CAROL")
        assert resp.status_code == 200
        emails = {u["email"] for u in resp.json()["data"]}
        assert "carol@hologram.com.br" in emails


# ----------------------------------------------------------------------
# POST /users (create)
# ----------------------------------------------------------------------


class TestCreateUser:
    async def test_admin_creates_user(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/users",
            json={
                "name": "Novo Manager",
                "email": "novo@hologram.com.br",
                "password": NEW_USER_PLAIN,
                "role": "manager",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["email"] == "novo@hologram.com.br"
        assert body["role"] == "manager"
        assert body["active"] is True
        assert "password_hash" not in body

    async def test_email_uppercase_normalized(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/users",
            json={
                "name": "Up",
                "email": "UPPER@HOLOGRAM.COM.BR",
                "password": NEW_USER_PLAIN,
                "role": "manager",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "upper@hologram.com.br"

    async def test_duplicate_email_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _seed(db_session, email="dup@hologram.com.br", role=UserRole.MANAGER)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/users",
            json={
                "name": "Dup",
                "email": "dup@hologram.com.br",
                "password": NEW_USER_PLAIN,
                "role": "manager",
            },
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "CONFLICT"
        assert "já está em uso" in body["error"]["userMessage"]

    async def test_short_password_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(
            "/api/v1/users",
            json={
                "name": "Short",
                "email": "short@hologram.com.br",
                "password": SHORT_PLAIN,
                "role": "manager",
            },
        )
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# PATCH /users/{id} (update parcial)
# ----------------------------------------------------------------------


class TestUpdateUser:
    async def test_admin_updates_name_only(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        target = await _seed(
            db_session,
            email="upd@hologram.com.br",
            role=UserRole.MANAGER,
            name="Old Name",
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/users/{target.id}",
            json={"name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"
        assert resp.json()["email"] == "upd@hologram.com.br"  # email não mexido

    async def test_email_collision_returns_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        await _seed(db_session, email="taken@hologram.com.br", role=UserRole.MANAGER)
        target = await _seed(
            db_session,
            email="updtarget@hologram.com.br",
            role=UserRole.MANAGER,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/users/{target.id}",
            json={"email": "taken@hologram.com.br"},
        )
        assert resp.status_code == 409

    async def test_admin_cannot_demote_self(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/users/{admin.id}",
            json={"role": "manager"},
        )
        assert resp.status_code == 403


# ----------------------------------------------------------------------
# Activate / Deactivate
# ----------------------------------------------------------------------


class TestActivateDeactivate:
    async def test_admin_deactivates_other_user(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        target = await _seed(db_session, email="deact@hologram.com.br", role=UserRole.MANAGER)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(f"/api/v1/users/{target.id}/deactivate")
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    async def test_admin_reactivates(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed(db_session, email=ADMIN_EMAIL)
        target = await _seed(
            db_session,
            email="react@hologram.com.br",
            role=UserRole.MANAGER,
            active=False,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(f"/api/v1/users/{target.id}/activate")
        assert resp.status_code == 200
        assert resp.json()["active"] is True

    async def test_admin_cannot_deactivate_self(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed(db_session, email=ADMIN_EMAIL)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(f"/api/v1/users/{admin.id}/deactivate")
        assert resp.status_code == 403
        assert resp.json()["error"]["userMessage"] == "Você não pode desativar a si mesmo."
