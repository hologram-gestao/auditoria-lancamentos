"""Smoke test do endpoint /health — garante que a app sobe em testes."""

import pytest
from httpx import AsyncClient


@pytest.mark.unit
async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.unit
async def test_ready_returns_ready(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
