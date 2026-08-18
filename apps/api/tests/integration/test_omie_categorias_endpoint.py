"""`GET /api/v1/omie/categorias` (Sprint 7 / BACK 07.3).

O endpoint entrega a fonte de categorias que o passo de classificação precisa
(`cCodCateg` é obrigatório no `IncluirLancCC` e **não** vem da fatura). O que
estes testes travam:

  - o tenant vem da **sessão**, nunca da query — sessão de outro tenant é 404
    e o combobox nunca é populado com o vocabulário contábil alheio;
  - **cache por cliente**: a 2ª chamada dentro do TTL não vai à Omie;
  - o envelope é `{data, total}` com a lista COMPLETA (sem paginação —
    decisão registrada em ADR-024-BE).

A chamada à Omie é evitada pelo caminho que o repo já usa em dev: credencial
com o prefixo `FAKE_DEMO_OMIE_` faz o `omie_factory` devolver o
`MockOmieClient`. Nada de rede, e o percurso rota → service → cache → cliente
é o mesmo de produção.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Categorias#1"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Categorias",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User, name: str) -> Client:
    """Cliente com credencial FAKE_DEMO — o factory devolve o MockOmieClient."""
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(f"{FAKE_DEMO_KEY_PREFIX}{uuid4().hex[:8]}", hex_key)
    ct_s, iv_s = encrypt("demo-secret", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=900_000_001,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=None,
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(sess)
    await session.flush()
    return sess


class Tenants(NamedTuple):
    admin: User
    client_a: Client
    client_b: Client
    session_a: ReconciliationSession
    session_b: ReconciliationSession
    operator_a: User


@pytest.fixture
async def tenants(db_session: AsyncSession) -> Tenants:
    admin = await _seed_user(
        db_session, email=f"cat-admin-{uuid4().hex[:8]}@hologram.com.br", role=UserRole.ADMIN
    )
    client_a = await _seed_client(db_session, creator=admin, name="Tenant A")
    client_b = await _seed_client(db_session, creator=admin, name="Tenant B")
    operator_a = await _seed_user(
        db_session,
        email=f"cat-op-{uuid4().hex[:8]}@tenant-a.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=client_a.id,
    )
    db_session.add(ClientAssignment(client_id=client_a.id, user_id=admin.id, assigned_by=admin.id))
    session_a = await _seed_session(db_session, client=client_a, creator=admin)
    session_b = await _seed_session(db_session, client=client_b, creator=admin)
    return Tenants(admin, client_a, client_b, session_a, session_b, operator_a)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Cache limpo por teste — senão a contagem de chamadas vaza entre eles."""
    fastapi_app.state.omie_categorias_cache = OmieCategoriasCache()


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
class TestCategoriasEndpoint:
    async def test_returns_the_full_active_list(
        self, client_with_db: AsyncClient, tenants: Tenants
    ) -> None:
        await _login(client_with_db, tenants.admin.email)
        resp = await client_with_db.get(
            "/api/v1/omie/categorias", params={"session_id": str(tenants.session_a.id)}
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"data", "total"}
        assert body["total"] == len(body["data"])
        codigos = [item["codigo"] for item in body["data"]]
        assert "1.01.01" in codigos
        assert "9.99.99" not in codigos, "categoria inativa foi oferecida para classificação"
        for item in body["data"]:
            assert set(item) == {"codigo", "descricao"}

    async def test_second_call_is_served_from_cache(
        self, client_with_db: AsyncClient, tenants: Tenants, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A prova é a CONTAGEM de chamadas ao cliente Omie, não a resposta."""
        calls: list[str] = []
        original = MockOmieClient.listar_categorias

        async def counting(self: MockOmieClient) -> list:  # type: ignore[type-arg]
            calls.append("listar_categorias")
            return await original(self)

        monkeypatch.setattr(MockOmieClient, "listar_categorias", counting)

        await _login(client_with_db, tenants.admin.email)
        params = {"session_id": str(tenants.session_a.id)}
        first = await client_with_db.get("/api/v1/omie/categorias", params=params)
        second = await client_with_db.get("/api/v1/omie/categorias", params=params)

        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert len(calls) == 1, f"a 2ª chamada foi à Omie ({len(calls)} chamadas)"

    async def test_refresh_bypasses_the_cache(
        self, client_with_db: AsyncClient, tenants: Tenants, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        original = MockOmieClient.listar_categorias

        async def counting(self: MockOmieClient) -> list:  # type: ignore[type-arg]
            calls.append("listar_categorias")
            return await original(self)

        monkeypatch.setattr(MockOmieClient, "listar_categorias", counting)

        await _login(client_with_db, tenants.admin.email)
        await client_with_db.get(
            "/api/v1/omie/categorias", params={"session_id": str(tenants.session_a.id)}
        )
        await client_with_db.get(
            "/api/v1/omie/categorias",
            params={"session_id": str(tenants.session_a.id), "refresh": "true"},
        )
        assert len(calls) == 2

    async def test_unknown_session_is_404(
        self, client_with_db: AsyncClient, tenants: Tenants
    ) -> None:
        await _login(client_with_db, tenants.admin.email)
        resp = await client_with_db.get(
            "/api/v1/omie/categorias", params={"session_id": str(uuid4())}
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestCategoriasTenantIsolation:
    async def test_client_user_cannot_read_another_tenants_categories(
        self, client_with_db: AsyncClient, tenants: Tenants
    ) -> None:
        """Operador do tenant A apontando para a sessão do tenant B: 404.

        O vocabulário contábil de um cliente é dado dele — servir a lista de B
        para um usuário de A seria vazamento, mesmo sem valores financeiros.
        """
        await _login(client_with_db, tenants.operator_a.email)
        resp = await client_with_db.get(
            "/api/v1/omie/categorias", params={"session_id": str(tenants.session_b.id)}
        )
        assert resp.status_code == 404, resp.text
        assert "Tenant B" not in resp.text

    async def test_own_tenant_still_works_for_the_client_user(
        self, client_with_db: AsyncClient, tenants: Tenants
    ) -> None:
        """O contraponto: o mesmo usuário lê o PRÓPRIO tenant sem atrito."""
        await _login(client_with_db, tenants.operator_a.email)
        resp = await client_with_db.get(
            "/api/v1/omie/categorias", params={"session_id": str(tenants.session_a.id)}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] > 0
