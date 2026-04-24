"""Fixtures globais de pytest.

Em S2+, testcontainers (postgres, redis) serão inicializados aqui.
Por enquanto, apenas fixtures mínimas para o smoke test rodar.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client async conectado à app FastAPI em memória."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
