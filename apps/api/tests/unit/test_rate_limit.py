"""Unit — storage do rate-limit (Fase 3 / defeito ④).

Sem `RATELIMIT_STORAGE_URI` → in-memory (dev/test/single-instance, sem depender de
Redis). Com a env setada → usa o storage informado (Redis compartilhado entre
instâncias). O slowapi recebe o `storage_uri` EXPLICITAMENTE — não lê a env sozinho.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.core.rate_limit import _resolve_storage_uri

if TYPE_CHECKING:
    from app.core.config import Settings


def _settings(*, ratelimit_storage_uri: str | None) -> Settings:
    """Settings stub variando apenas o RATELIMIT_STORAGE_URI."""
    from app.core.config import Settings

    return Settings(
        DATABASE_URL="postgresql+psycopg://t:t@localhost:5432/t",
        OMIE_ENCRYPTION_KEY=SecretStr("a" * 64),
        JWT_SECRET=SecretStr("b" * 64),
        SEARCH_BLIND_INDEX_KEY=SecretStr("c" * 64),
        RATELIMIT_STORAGE_URI=ratelimit_storage_uri,
    )  # type: ignore[call-arg]


def test_storage_defaults_to_memory_when_unset() -> None:
    # Sem env: dev/test/single-instance não dependem de Redis.
    assert _resolve_storage_uri(_settings(ratelimit_storage_uri=None)) == "memory://"


def test_storage_uses_configured_uri() -> None:
    uri = "rediss://default:tok@example.upstash.io:6379"
    assert _resolve_storage_uri(_settings(ratelimit_storage_uri=uri)) == uri
