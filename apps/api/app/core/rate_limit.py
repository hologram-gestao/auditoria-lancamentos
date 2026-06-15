"""Rate limiting central via slowapi.

Padrão CLAUDE.md §3.11 + P0-004:
    - /auth/login: 5 tentativas / 5 min / IP.
    - /reconciliations/parse: 10/min / usuário — chama Anthropic ($$).
    - POST /reconciliations: 10/min / usuário — enfileira job ARQ.
    - /clients/test-connection: 30/min / usuário — chama Omie.
    - /clients/{id}/sync-accounts: 30/min / usuário — chama Omie.
    - Demais autenticados (CRUD em DB): sem limit explícito por enquanto
      (DB próprio é o cap natural).

Storage backend:
    - Hoje: in-memory (`memory://`) por processo. Com várias instâncias
      (Cloud Run maxScale>1) o limite NÃO é compartilhado — cada instância tem
      seu próprio contador. Aceitável como hardening de baixo risco por ora.
    - Limite consistente entre instâncias exigiria um storage compartilhado
      passado EXPLICITAMENTE ao `Limiter` (`storage_uri=...`) — o slowapi NÃO lê
      `RATELIMIT_STORAGE_URI` do ambiente sozinho. Decisão de storage adiada
      para o refactoring de infra (saída do Redis; possível edge/Cloud Armor).

Key functions:
    - `get_remote_address`: IP do cliente (default; usado em /login).
    - `user_id_key_func`: valida ASSINATURA do JWT no cookie (HS256 +
      JWT_SECRET) e usa `sub` como chave. Slowapi avalia a key ANTES do
      handler, então não dá pra reusar `get_current_user`. Pulamos `exp`
      de propósito — rate limit precisa funcionar para tokens recém-
      expirados também (o handler vai responder 401 e o atacante não
      ganha nada do "bucket" alocado). Assinatura inválida ou cookie
      ausente → fallback para IP, impedindo que atacante forge `sub`
      arbitrário e estoure o storage de rate limit com chaves fake.

TODO (futuro — S16 cont.):
    Combinar IP + email no rate limit do login (hoje só IP). Atacante com
    múltiplos IPs ainda consegue probar emails. Solução prevista: middleware
    HTTP que pré-lê o body do POST /auth/login e popula `request.state`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

if TYPE_CHECKING:
    from starlette.requests import Request

# Nome do cookie HTTP, não uma credencial — duplicado de `core.dependencies`
# para manter este módulo livre do import do FastAPI.
_ACCESS_TOKEN_COOKIE_NAME = "access_token"  # noqa: S105
_JWT_ALGORITHM = "HS256"


def user_id_key_func(request: Request) -> str:
    """Retorna `user:{sub}` se o JWT do cookie tem assinatura válida; senão IP.

    Valida a assinatura HS256 contra `JWT_SECRET` (mesma usada em
    `core.security.decode_token`). Pula `exp` — rate limit precisa contar
    requests de tokens recém-expirados também (o handler responde 401
    depois, sem dar bypass de bucket).

    Por que não reusar `decode_token`: queremos NÃO levantar em token
    expirado, e queremos cair em IP de forma silenciosa em qualquer outro
    erro (token malformado, cookie ausente). Aqui é melhor inline.
    """
    token = request.cookies.get(_ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        return get_remote_address(request)
    try:
        secret = get_settings().JWT_SECRET.get_secret_value()
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_JWT_ALGORITHM],
            options={"verify_exp": False, "verify_aud": False},
        )
    except JWTError:
        return get_remote_address(request)
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if isinstance(sub, str) and sub:
        return f"user:{sub}"
    return get_remote_address(request)


# Singleton — anexado a `app.state` em `main.create_app()`.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # nenhum limit global; aplicar por rota
    headers_enabled=True,  # X-RateLimit-* nos headers de resposta
)
