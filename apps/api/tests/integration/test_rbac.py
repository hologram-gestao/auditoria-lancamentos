"""Testes dos guards de papel — `StaffDep` e os guards por permissão da matriz.

Casos básicos de auth (cookie ausente, JWT inválido, refresh-em-vez-de-access)
estão em `test_auth.py`. Aqui foco em separar permissão por papel, com os três
escopos da camada de organizações: plataforma, staff de organização e usuário
de cliente. Os antigos `require_admin`/`require_manager_or_admin` (comparação
de string) não existem mais — a matriz é o único lugar que decide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from fastapi import APIRouter
from sqlalchemy import null
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.dependencies import (
    CurrentUserDep,
    ManageOrgUsersDep,
    ManagePlatformDep,
    StaffDep,
)
from app.core.security import hash_password
from app.db.models import Client, User, UserRole, UserScope
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from httpx import AsyncClient


LOGIN_PLAIN = "S3-Senh@RBAC!"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    extra: dict[str, object] = {}
    if scope is UserScope.PLATFORM:
        # `null()`, não `None`: com `server_default` o ORM omitiria o campo e o
        # banco preencheria a Hologram — e a plataforma não tem organização.
        extra["organization_id"] = null()
    user = User(
        name=f"User {role.value}",
        email=email.lower(),
        password_hash=hash_password(LOGIN_PLAIN),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        **extra,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("rbac-app-key", hex_key)
    ct_s, iv_s = encrypt("rbac-app-secret", hex_key)
    client = Client(
        name="Cliente RBAC",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


def _add_rbac_routes() -> None:
    """Adiciona rotas auxiliares com cada nível de guard, idempotente."""
    paths = {r.path for r in fastapi_app.routes if hasattr(r, "path")}  # type: ignore[attr-defined]
    if "/_test/rbac/me" in paths:
        return

    router = APIRouter()

    @router.get("/_test/rbac/me")
    async def me(user: CurrentUserDep) -> dict[str, str]:
        return {"id": user.id, "role": user.role}

    @router.get("/_test/rbac/staff")
    async def staff(user: StaffDep) -> dict[str, str]:
        return {"id": user.id}

    @router.get("/_test/rbac/org-users")
    async def org_users(user: ManageOrgUsersDep) -> dict[str, str]:
        return {"id": user.id}

    @router.get("/_test/rbac/platform")
    async def platform(user: ManagePlatformDep) -> dict[str, str]:
        return {"id": user.id}

    fastapi_app.include_router(router)


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": LOGIN_PLAIN})
    assert resp.status_code == 200, resp.text


# (papel, escopo, rota, status esperado)
CASES = [
    (UserRole.ADMIN, UserScope.SYSTEM, "staff", 200),
    (UserRole.ADMIN, UserScope.SYSTEM, "org-users", 200),
    (UserRole.ADMIN, UserScope.SYSTEM, "platform", 403),
    (UserRole.MANAGER, UserScope.SYSTEM, "staff", 200),
    (UserRole.MANAGER, UserScope.SYSTEM, "org-users", 403),
    (UserRole.MANAGER, UserScope.SYSTEM, "platform", 403),
    (UserRole.PLATFORM_ADMIN, UserScope.PLATFORM, "staff", 200),
    (UserRole.PLATFORM_ADMIN, UserScope.PLATFORM, "org-users", 200),
    (UserRole.PLATFORM_ADMIN, UserScope.PLATFORM, "platform", 200),
    (UserRole.CLIENT_MANAGER, UserScope.CLIENT, "staff", 403),
    (UserRole.CLIENT_MANAGER, UserScope.CLIENT, "org-users", 403),
    (UserRole.CLIENT_OPERATOR, UserScope.CLIENT, "platform", 403),
]


@pytest.mark.parametrize(
    ("role", "scope", "route", "expected"),
    CASES,
    ids=[f"{r.value}-{route}" for r, _, route, _ in CASES],
)
async def test_guard_por_papel(
    client_with_db: AsyncClient,
    db_session: AsyncSession,
    role: UserRole,
    scope: UserScope,
    route: str,
    expected: int,
) -> None:
    _add_rbac_routes()
    client_id: UUID | None = None
    if scope is UserScope.CLIENT:
        admin = await _seed_user(
            db_session, email="rbac-owner@hologram.com.br", role=UserRole.ADMIN
        )
        client_id = (await _seed_client(db_session, creator=admin)).id
    email = f"rbac-{role.value}@hologram.com.br"
    await _seed_user(db_session, email=email, role=role, scope=scope, client_id=client_id)
    await _login(client_with_db, email)

    resp = await client_with_db.get(f"/_test/rbac/{route}")

    assert resp.status_code == expected, resp.text
    if expected == 403:
        # Negação sem vazar nada além do que o requisitante já sabe.
        assert "Hologram" not in resp.text


async def test_plataforma_chega_com_escopo_e_sem_organizacao(
    client_with_db: AsyncClient, db_session: AsyncSession
) -> None:
    """A linha da plataforma é lida a cada request: escopo `platform`, org e tenant nulos."""
    _add_rbac_routes()
    await _seed_user(
        db_session,
        email="rbac-plataforma@hologram.com.br",
        role=UserRole.PLATFORM_ADMIN,
        scope=UserScope.PLATFORM,
    )
    await _login(client_with_db, "rbac-plataforma@hologram.com.br")

    resp = await client_with_db.get("/_test/rbac/me")

    assert resp.status_code == 200
    assert resp.json()["role"] == UserRole.PLATFORM_ADMIN.value
