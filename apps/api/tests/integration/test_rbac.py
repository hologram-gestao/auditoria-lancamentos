"""Testes de RBAC — `require_admin` e `require_manager_or_admin`.

Casos básicos de auth (cookie ausente, JWT inválido, refresh-em-vez-de-access)
estão em `test_auth.py`. Aqui foco em separar permissão por role.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AdminDep, CurrentUserDep, ManagerOrAdminDep
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from httpx import AsyncClient


LOGIN_PLAIN = "S3-Senh@RBAC!"


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name=f"User {role.value}",
        email=email.lower(),
        password_hash=hash_password(LOGIN_PLAIN),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


def _add_rbac_routes() -> None:
    """Adiciona rotas auxiliares com cada nível de RBAC, idempotente."""
    paths = {r.path for r in fastapi_app.routes if hasattr(r, "path")}  # type: ignore[attr-defined]
    if "/_test/rbac/me" in paths:
        return

    router = APIRouter()

    @router.get("/_test/rbac/me")
    async def me(user: CurrentUserDep) -> dict[str, str]:
        return {"id": user.id, "role": user.role}

    @router.get("/_test/rbac/admin")
    async def admin_only(user: AdminDep) -> dict[str, str]:
        return {"id": user.id}

    @router.get("/_test/rbac/staff")
    async def staff(user: ManagerOrAdminDep) -> dict[str, str]:
        return {"id": user.id}

    fastapi_app.include_router(router)


class TestRBACAdmin:
    async def test_admin_can_access_admin_route(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _add_rbac_routes()
        await _seed_user(db_session, email="adm@hologram.com.br", role=UserRole.ADMIN)
        await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "adm@hologram.com.br", "password": LOGIN_PLAIN},
        )
        resp = await client_with_db.get("/_test/rbac/admin")
        assert resp.status_code == 200

    async def test_manager_cannot_access_admin_route(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _add_rbac_routes()
        await _seed_user(db_session, email="mgr@hologram.com.br", role=UserRole.MANAGER)
        await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "mgr@hologram.com.br", "password": LOGIN_PLAIN},
        )
        resp = await client_with_db.get("/_test/rbac/admin")
        assert resp.status_code == 403


class TestRBACStaff:
    async def test_admin_can_access_staff_route(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _add_rbac_routes()
        await _seed_user(db_session, email="adm2@hologram.com.br", role=UserRole.ADMIN)
        await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "adm2@hologram.com.br", "password": LOGIN_PLAIN},
        )
        resp = await client_with_db.get("/_test/rbac/staff")
        assert resp.status_code == 200

    async def test_manager_can_access_staff_route(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _add_rbac_routes()
        await _seed_user(db_session, email="mgr2@hologram.com.br", role=UserRole.MANAGER)
        await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "mgr2@hologram.com.br", "password": LOGIN_PLAIN},
        )
        resp = await client_with_db.get("/_test/rbac/staff")
        assert resp.status_code == 200
