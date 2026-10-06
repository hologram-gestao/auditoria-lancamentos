"""Rotas de autenticação — POST /login, POST /refresh, POST /logout.

Princípios (Doc §7 + CLAUDE.md §3):
    - Tokens entregues APENAS em cookies HttpOnly + Secure (em prod) + SameSite=Lax.
    - Body do erro é genérico para login (não revela campo errado).
    - Rate limit em /login: 5 FALHAS / 5 min / e-mail (limitador por identidade)
      + teto de enxurrada por IP (slowapi). Ver `core/rate_limit.py`.
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
from app.core.exceptions import RateLimitedError, UnauthorizedError
from app.core.logging import get_logger
from app.core.rate_limit import LOGIN_FLOOD_LIMIT, limiter, login_identity_limiter
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

log = get_logger(__name__)

#: Mensagem do 429 por identidade: nomeia a janela real (a do handler do
#: slowapi fala em 1 minuto) e não diz se o e-mail existe (§3.9).
LOGIN_RATE_LIMITED_MESSAGE = (
    "Muitas tentativas de login com este e-mail. Aguarde 5 minutos e tente novamente."
)


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
    """Seta cookies HttpOnly + Secure (prod) + SameSite=lax para access e refresh.

    O `max_age` dos DOIS cookies = lifetime da SESSÃO (refresh). O `access_token`
    propositalmente sobrevive ao `exp` do JWT (que segue curto): quando o access
    expira, o cookie continua presente, o middleware do front (que só checa
    presença) deixa navegar, e a 1ª request pega TOKEN_EXPIRED → refresh em
    silêncio. Sem isso, o browser apaga o cookie ao expirar o JWT e a navegação
    seguinte cai no /login mesmo com refresh válido (bug de logout intermitente).
    O JWT expirado dentro do cookie é inofensivo — `decode_token` o rejeita.
    """
    domain = _cookie_domain(settings)
    # Cookie persiste pela SESSÃO; a validade REAL é o `exp` do JWT, não o max_age.
    session_max_age = settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=domain,
        max_age=session_max_age,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=domain,
        max_age=session_max_age,
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
@limiter.limit(LOGIN_FLOOD_LIMIT)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    auth: AuthServiceDep,
    settings: SettingsDep,
) -> LoginResponse:
    """Valida credenciais e emite par de tokens em cookies.

    Rate limit (86e3anx10), em duas camadas:
        - por IDENTIDADE: 5 falhas / 5 min por e-mail. A consulta vem ANTES da
          verificação, de propósito: quem já estourou recebe 429 mesmo com a
          senha certa e não gasta bcrypt. Só falha (qualquer 401 do
          `AuthService.login`) conta; sucesso não conta e não zera.
        - por IP: teto de enxurrada (`LOGIN_FLOOD_LIMIT`). Atrás do BFF o IP é
          o do proxy, então esse teto é global por instância e não distingue
          pessoas.
    Os dois respondem HTTP 429 RATE_LIMITED no envelope padrão.

    NOTA: `request: Request` PRECISA ser o primeiro parâmetro para o slowapi
    extrair o cliente — não mude essa ordem.
    """
    if login_identity_limiter.is_blocked(payload.email):
        # Só o prefixo do hash vai para log, nunca o e-mail (§3.3).
        log.warning(
            "login_identity_rate_limited",
            window=login_identity_limiter.window,
            identity_prefix=login_identity_limiter.identity_prefix(payload.email),
        )
        raise RateLimitedError(
            "Limite de falhas de login por identidade excedido.",
            user_message=LOGIN_RATE_LIMITED_MESSAGE,
        )
    try:
        ctx, access, refresh = await auth.login(email=payload.email, password=payload.password)
    except UnauthorizedError:
        login_identity_limiter.register_failure(payload.email)
        raise
    _set_auth_cookies(response, access_token=access, refresh_token=refresh, settings=settings)
    return LoginResponse(user=AuthService.to_authenticated_user(ctx))


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

    ctx, new_access, new_refresh = await auth.refresh(refresh_token=refresh_cookie)
    _set_auth_cookies(
        response, access_token=new_access, refresh_token=new_refresh, settings=settings
    )
    return RefreshResponse(user=AuthService.to_authenticated_user(ctx))


@router.post(
    "/logout",
    status_code=200,
    summary="Logout — limpa cookies HttpOnly. Sempre retorna 200.",
)
async def logout(response: Response, settings: SettingsDep) -> LogoutResponse:
    """Logout idempotente — não exige autenticação (limpa cookies mesmo se já estavam vazios)."""
    _clear_auth_cookies(response, settings)
    return LogoutResponse()
