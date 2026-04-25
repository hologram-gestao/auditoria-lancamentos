"""Rotas de autenticação — POST /login, POST /refresh, POST /logout.

Princípios (Doc §7 + CLAUDE.md §3):
    - Tokens entregues APENAS em cookies HttpOnly + Secure (em prod) + SameSite=Lax.
    - Body do erro é genérico para login (não revela campo errado).
    - Rate limit em /login: 5 tentativas / 5 min / IP+email (slowapi).
    - Logout limpa cookies — não há blacklist server-side de JWT (MVP).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.dependencies import (
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    DbSessionDep,
    SettingsDep,
)
from app.core.exceptions import UnauthorizedError
from app.core.rate_limit import limiter
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RefreshResponse,
)
from app.modules.auth.service import AuthService

if TYPE_CHECKING:
    from app.core.config import Settings


# Path do módulo — referenciado também em `path` de cookies abaixo,
# por isso constante (evita drift entre router prefix e cookie scope).
AUTH_PATH_PREFIX = "/api/v1/auth"

router = APIRouter(prefix=AUTH_PATH_PREFIX, tags=["auth"])


def _get_auth_service(db: DbSessionDep, settings: SettingsDep) -> AuthService:
    """Provider para injeção do service em endpoints."""
    return AuthService(AuthRepository(db), settings)


AuthServiceDep = Annotated[AuthService, Depends(_get_auth_service)]


def _cookie_domain(settings: Settings) -> str | None:
    """Normaliza COOKIE_DOMAIN: string vazia → None (cookie sem Domain attribute)."""
    return settings.COOKIE_DOMAIN or None


def _set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    settings: Settings,
) -> None:
    """Seta cookies HttpOnly + Secure (prod) + SameSite=lax para access e refresh."""
    domain = _cookie_domain(settings)
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=domain,
        max_age=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=domain,
        max_age=settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60,
        path=AUTH_PATH_PREFIX,  # refresh só é enviado nas rotas de auth (escopo mínimo)
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    """Remove ambos cookies — usado em logout e quando refresh expira."""
    domain = _cookie_domain(settings)
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/", domain=domain)
    response.delete_cookie(key=REFRESH_TOKEN_COOKIE, path=AUTH_PATH_PREFIX, domain=domain)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.post(
    "/login",
    status_code=200,
    summary="Login com email + senha. Seta cookies HttpOnly de access + refresh.",
)
@limiter.limit("5/5minutes")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> LoginResponse:
    """Valida credenciais e emite par de tokens em cookies.

    Rate limit: 5 tentativas / 5 min POR IP (TODO S16: combinar com email).
    Em violation, slowapi levanta `RateLimitExceeded` convertido pelo handler
    global em HTTP 429 RATE_LIMITED.

    NOTA: `request: Request` PRECISA ser o primeiro parâmetro para o slowapi
    extrair o cliente — não mude essa ordem.
    """
    user, access, refresh = await auth.login(email=payload.email, password=payload.password)
    _set_auth_cookies(response, access_token=access, refresh_token=refresh, settings=settings)
    return LoginResponse(user=AuthService.to_authenticated_user(user))


@router.post(
    "/refresh",
    status_code=200,
    summary="Renova tokens. Lê refresh do cookie HttpOnly e seta novos cookies.",
)
async def refresh(
    request: Request,
    response: Response,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> RefreshResponse:
    """Endpoint chamado pelo frontend quando access expira.

    Não recebe nada no body — refresh vem do cookie HttpOnly. Em sucesso,
    seta novo par de cookies (rotacionando refresh — boa prática).
    """
    refresh_cookie = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not refresh_cookie:
        raise UnauthorizedError("Cookie de refresh ausente.")

    user, new_access, new_refresh = await auth.refresh(refresh_token=refresh_cookie)
    _set_auth_cookies(
        response, access_token=new_access, refresh_token=new_refresh, settings=settings
    )
    return RefreshResponse(user=AuthService.to_authenticated_user(user))


@router.post(
    "/logout",
    status_code=200,
    summary="Logout — limpa cookies HttpOnly. Sempre retorna 200.",
)
async def logout(response: Response, settings: SettingsDep) -> LogoutResponse:
    """Logout idempotente — não exige autenticação (limpa cookies mesmo se já estavam vazios)."""
    _clear_auth_cookies(response, settings)
    return LogoutResponse()
