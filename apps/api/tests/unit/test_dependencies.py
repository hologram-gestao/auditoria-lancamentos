"""Testes das dependencies de autenticação e RBAC.

Testes via cliente HTTP da app (não diretamente as funções) para garantir
que a integração FastAPI + Depends funciona ponta a ponta.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import APIRouter, FastAPI

from app.core.config import get_settings
from app.core.dependencies import (
    ACCESS_TOKEN_COOKIE,
    AdminDep,
    CurrentUserDep,
    ManagerOrAdminDep,
)
from app.core.security import create_access_token, create_refresh_token
from app.main import _register_exception_handlers

if TYPE_CHECKING:
    from httpx import AsyncClient


def _build_app() -> FastAPI:
    """App mínima com endpoints que exigem cada nível de RBAC.

    Inclui os mesmos exception handlers do main para que `AppError` lançado
    pelas dependencies vire HTTP 401/403 (e não 500 default do FastAPI).
    """
    app = FastAPI()
    _register_exception_handlers(app)
    router = APIRouter()

    @router.get("/me")
    async def me(user: CurrentUserDep) -> dict[str, str]:
        return {"id": user.id, "role": user.role}

    @router.get("/admin")
    async def admin_only(user: AdminDep) -> dict[str, str]:
        return {"id": user.id}

    @router.get("/staff")
    async def staff(user: ManagerOrAdminDep) -> dict[str, str]:
        return {"id": user.id}

    app.include_router(router)
    return app


@pytest.fixture
def app() -> FastAPI:
    return _build_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


class TestGetCurrentUser:
    async def test_no_cookie_returns_401(self, client: AsyncClient) -> None:
        response = await client.get("/me")
        assert response.status_code == 401

    async def test_valid_cookie_returns_user(self, client: AsyncClient) -> None:
        token = create_access_token(subject="user-1", role="admin", settings=get_settings())
        response = await client.get("/me", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 200
        assert response.json() == {"id": "user-1", "role": "admin"}

    async def test_invalid_cookie_returns_401(self, client: AsyncClient) -> None:
        response = await client.get("/me", cookies={ACCESS_TOKEN_COOKIE: "garbage"})
        assert response.status_code == 401

    async def test_refresh_token_in_access_cookie_returns_401(self, client: AsyncClient) -> None:
        """Refresh token enviado no cookie de access deve falhar (tipo errado)."""
        token = create_refresh_token(subject="user-1", role="admin", settings=get_settings())
        response = await client.get("/me", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 401


class TestRBAC:
    async def test_admin_can_access_admin_route(self, client: AsyncClient) -> None:
        token = create_access_token(subject="u1", role="admin", settings=get_settings())
        response = await client.get("/admin", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 200

    async def test_manager_cannot_access_admin_route(self, client: AsyncClient) -> None:
        token = create_access_token(subject="u2", role="manager", settings=get_settings())
        response = await client.get("/admin", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 403

    async def test_admin_can_access_staff_route(self, client: AsyncClient) -> None:
        token = create_access_token(subject="u1", role="admin", settings=get_settings())
        response = await client.get("/staff", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 200

    async def test_manager_can_access_staff_route(self, client: AsyncClient) -> None:
        token = create_access_token(subject="u2", role="manager", settings=get_settings())
        response = await client.get("/staff", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 200

    async def test_unknown_role_cannot_access_staff_route(self, client: AsyncClient) -> None:
        token = create_access_token(subject="u3", role="guest", settings=get_settings())
        response = await client.get("/staff", cookies={ACCESS_TOKEN_COOKIE: token})
        assert response.status_code == 403
