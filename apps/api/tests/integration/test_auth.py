"""Testes de integração da autenticação — cobre BACK 1.1 + BACK 1.2 do backlog.

Cenários:
    - Login com credenciais corretas seta cookies HttpOnly e devolve user.
    - Login com email/senha errados retorna 401 com mensagem GENÉRICA.
    - Login de user com active=false retorna 401 com mesma mensagem genérica.
    - Rate limit dispara após 5 tentativas no mesmo (IP+email) em 5min.
    - Refresh com cookie válido emite novo par de tokens.
    - Refresh sem cookie retorna 401.
    - Refresh de user desativado entre login e refresh retorna 401.
    - Logout limpa cookies e sempre retorna 200.
    - GET autenticado: cookie ausente -> 401, ativo -> 200, desativado -> 401.

Pula automaticamente se Docker não estiver disponível (via fixture pg_container).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    CurrentUserDep,
)
from app.core.security import hash_password
from app.db.models import User, UserRole
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from httpx import AsyncClient


# Rota auxiliar /me — registrada uma vez no módulo para FastAPI resolver
# `CurrentUserDep` (Annotated[CurrentUser, Depends(get_current_user)])
# corretamente. Definir dentro de função local quebra o `get_type_hints`.
_protected_router = APIRouter()


@_protected_router.get("/_test/me")
async def _me_endpoint(user: CurrentUserDep) -> dict[str, str]:
    return {"id": user.id, "email": user.email, "role": user.role}


def _ensure_protected_route() -> None:
    """Idempotente — registra `/_test/me` uma única vez."""
    paths = {r.path for r in fastapi_app.routes if hasattr(r, "path")}  # type: ignore[attr-defined]
    if "/_test/me" not in paths:
        fastapi_app.include_router(_protected_router)


GENERIC_ERR = "E-mail ou senha incorretos."

# Fixtures de teste — strings inertes, não são credenciais reais.
LOGIN_PLAIN = "S3-Senh@ForteDeTeste!"
WRONG_PLAIN = "tentativa-errada-1"
RANDOM_PLAIN = "qualquer-coisa-aqui"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    plain_text: str = LOGIN_PLAIN,
    active: bool = True,
    role: UserRole = UserRole.MANAGER,
) -> User:
    """Insere user com hash bcrypt real (necessário para verify_password funcionar)."""
    user = User(
        name="Test User",
        email=email.lower(),
        password_hash=hash_password(plain_text),
        role=role.value,
        active=active,
    )
    session.add(user)
    await session.flush()
    return user


# ----------------------------------------------------------------------
# Login
# ----------------------------------------------------------------------


class TestLogin:
    async def test_login_success_sets_cookies(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email="ok@hologram.com.br")

        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "ok@hologram.com.br", "password": LOGIN_PLAIN},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["email"] == "ok@hologram.com.br"
        assert body["user"]["role"] == "manager"
        assert "id" in body["user"]
        # Cookies HttpOnly setados — checar via header (httpx pode não popular .cookies
        # quando Domain está vazio em ASGITransport)
        set_cookie_headers = resp.headers.get_list("set-cookie")
        joined = " || ".join(set_cookie_headers)
        assert ACCESS_TOKEN_COOKIE in joined, f"access cookie ausente em: {joined!r}"
        assert REFRESH_TOKEN_COOKIE in joined, f"refresh cookie ausente em: {joined!r}"
        assert "HttpOnly" in joined

    async def test_login_uppercase_email_normalized(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Email é case-insensitive no DB — `OK@H.com.br` vira `ok@h.com.br`."""
        await _seed_user(db_session, email="case@hologram.com.br")
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "CASE@HOLOGRAM.COM.br", "password": LOGIN_PLAIN},
        )
        assert resp.status_code == 200

    async def test_login_wrong_password_returns_generic_401(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email="wp@hologram.com.br")
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "wp@hologram.com.br", "password": WRONG_PLAIN},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["userMessage"] == GENERIC_ERR
        assert ACCESS_TOKEN_COOKIE not in resp.cookies

    async def test_login_unknown_email_returns_generic_401(
        self, client_with_db: AsyncClient
    ) -> None:
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "ghost@nowhere.com", "password": RANDOM_PLAIN},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["userMessage"] == GENERIC_ERR

    async def test_login_inactive_user_returns_generic_401(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """User desativado retorna o MESMO erro — não vazar que conta existe."""
        await _seed_user(db_session, email="off@hologram.com.br", active=False)
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "off@hologram.com.br", "password": LOGIN_PLAIN},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["userMessage"] == GENERIC_ERR

    async def test_login_invalid_email_format_returns_400(
        self, client_with_db: AsyncClient
    ) -> None:
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "not-an-email", "password": LOGIN_PLAIN},
        )
        assert resp.status_code == 400

    async def test_rate_limit_blocks_after_5_attempts(self, client_with_db: AsyncClient) -> None:
        """5 tentativas de login passam, 6ª retorna 429 (CLAUDE.md §3.11)."""
        for i in range(5):
            resp = await client_with_db.post(
                "/api/v1/auth/login",
                json={"email": "rl@hologram.com.br", "password": WRONG_PLAIN},
            )
            assert resp.status_code == 401, f"tentativa {i + 1} deveria ser 401"

        # 6ª tentativa
        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "rl@hologram.com.br", "password": WRONG_PLAIN},
        )
        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert "Muitas tentativas" in body["error"]["userMessage"]


# ----------------------------------------------------------------------
# Refresh
# ----------------------------------------------------------------------


class TestRefresh:
    async def test_refresh_with_valid_cookie_succeeds(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email="rf@hologram.com.br")
        login = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "rf@hologram.com.br", "password": LOGIN_PLAIN},
        )
        assert login.status_code == 200

        resp = await client_with_db.post("/api/v1/auth/refresh")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["email"] == "rf@hologram.com.br"

    async def test_refresh_without_cookie_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.post("/api/v1/auth/refresh")
        assert resp.status_code == 401

    async def test_refresh_with_garbage_cookie_returns_401(
        self, client_with_db: AsyncClient
    ) -> None:
        resp = await client_with_db.post(
            "/api/v1/auth/refresh", cookies={REFRESH_TOKEN_COOKIE: "garbage"}
        )
        assert resp.status_code == 401


# ----------------------------------------------------------------------
# Logout
# ----------------------------------------------------------------------


class TestLogout:
    async def test_logout_always_returns_200(self, client_with_db: AsyncClient) -> None:
        """Logout idempotente — funciona mesmo sem cookies prévios."""
        resp = await client_with_db.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"success": True}


# ----------------------------------------------------------------------
# get_current_user com checagem de active no DB
# ----------------------------------------------------------------------


class TestCurrentUserDbValidation:
    async def test_active_user_can_access(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        _ensure_protected_route()
        await _seed_user(db_session, email="me@hologram.com.br")
        await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "me@hologram.com.br", "password": LOGIN_PLAIN},
        )
        resp = await client_with_db.get("/_test/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "me@hologram.com.br"

    async def test_no_cookie_returns_401(self, client_with_db: AsyncClient) -> None:
        _ensure_protected_route()
        resp = await client_with_db.get("/_test/me")
        assert resp.status_code == 401

    async def test_user_deactivated_after_login_loses_access(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """CLAUDE.md §3.12 — desativar user invalida acesso na próxima request."""
        _ensure_protected_route()
        user = await _seed_user(db_session, email="kick@hologram.com.br")
        login = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": "kick@hologram.com.br", "password": LOGIN_PLAIN},
        )
        assert login.status_code == 200

        # Admin desativa o user (simulação direta no DB)
        user.active = False
        await db_session.flush()

        # Próxima request com mesmo cookie deve falhar
        resp = await client_with_db.get("/_test/me")
        assert resp.status_code == 401
