"""Rate limiting central via slowapi.

Padrão CLAUDE.md §3.11:
    - /auth/login: 5 tentativas / 5 min / IP (ver TODO abaixo).
    - Endpoints autenticados gerais: aplicar em S16 (hardening).
    - Endpoints pesados (parsing, export): aplicar em S9, S14.

Storage backend:
    - Default: in-memory (`memory://`) — suficiente para dev e single-instance.
    - Em prod multi-instância: setar env var `RATELIMIT_STORAGE_URI=redis://...`
      (slowapi consome essa env automaticamente via `limits` library).

TODO (S16 — hardening):
    Combinar IP + email no rate limit do login. Slowapi avalia a key ANTES
    do handler executar, então `request.state.login_email` populado no handler
    não funciona como key_func. Solução prevista: middleware HTTP que pré-lê
    o body do POST /auth/login (replay-safe) e popula `request.state` antes
    do roteamento, OU rate limit manual via Redis no service layer.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# Singleton — anexado a `app.state` em `main.create_app()`.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # nenhum limit global; aplicar por rota
    headers_enabled=True,  # X-RateLimit-* nos headers de resposta
)
