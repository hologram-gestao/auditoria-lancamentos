"""Smoke test do endpoint /health — garante que a app sobe em testes.

`/health` é liveness probe (sem DB) — usa fixture `client` simples.
`/health/ready` migrou para integração (precisa de DB) — ver `tests/integration/`.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.unit
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
