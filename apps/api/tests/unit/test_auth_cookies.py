"""Unit — tempo de vida dos cookies de auth (fix do logout intermitente, defeito ③).

O cookie `access_token` deve viver a SESSÃO (= refresh), não o TTL curto do JWT.
Quando o cookie expirava junto com o JWT (60min), o browser o apagava e a
navegação seguinte caía no /login mesmo com refresh válido. Mantendo o cookie pela
sessão, o front renova em silêncio (o JWT expirado no cookie é rejeitado pelo
backend em `decode_token` de qualquer forma).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr
from starlette.responses import Response

from app.core.dependencies import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from app.modules.auth.routes import _set_auth_cookies

if TYPE_CHECKING:
    from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings stub com os defaults de cookie/JWT (access 60min, refresh 7 dias)."""
    from app.core.config import Settings

    return Settings(
        DATABASE_URL="postgresql+psycopg://t:t@localhost:5432/t",
        OMIE_ENCRYPTION_KEY=SecretStr("a" * 64),
        JWT_SECRET=SecretStr("b" * 64),
        SEARCH_BLIND_INDEX_KEY=SecretStr("c" * 64),
    )  # type: ignore[call-arg]


def _set_cookie_headers(response: Response) -> list[str]:
    """Coleta os headers Set-Cookie de uma Response do Starlette."""
    return [
        value.decode("latin-1")
        for key, value in response.raw_headers
        if key.decode("latin-1").lower() == "set-cookie"
    ]


def _max_age_for(set_cookie_headers: list[str], cookie_name: str) -> int:
    """Extrai o Max-Age (segundos) do Set-Cookie de `cookie_name`."""
    for header in set_cookie_headers:
        if header.startswith(f"{cookie_name}="):
            for part in header.split(";"):
                stripped = part.strip()
                if stripped.lower().startswith("max-age="):
                    return int(stripped.split("=", 1)[1])
    raise AssertionError(f"Max-Age não encontrado para {cookie_name} em {set_cookie_headers!r}")


def test_access_cookie_outlives_jwt_and_matches_session(settings: Settings) -> None:
    response = Response()
    _set_auth_cookies(
        response,
        access_token="access.jwt.token",
        refresh_token="refresh.jwt.token",
        settings=settings,
    )

    headers = _set_cookie_headers(response)
    access_max_age = _max_age_for(headers, ACCESS_TOKEN_COOKIE)
    refresh_max_age = _max_age_for(headers, REFRESH_TOKEN_COOKIE)

    access_jwt_ttl = settings.JWT_ACCESS_EXPIRE_MINUTES * 60
    session_ttl = settings.JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60

    # O cookie sobrevive ao JWT (não morre em 60min)...
    assert access_max_age > access_jwt_ttl
    # ...e dura a sessão inteira, igual ao refresh.
    assert access_max_age == session_ttl
    assert access_max_age == refresh_max_age
