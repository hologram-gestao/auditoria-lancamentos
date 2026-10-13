"""`GET /clients/{id}/omie/departamentos` e `/omie/fornecedores` (86e3n70q0, bloco B).

A Omie é o `MockOmieClient` (credencial `FAKE_DEMO_OMIE_`): o percurso rota →
serviço → cache → client é o de produção. O que estes testes travam:

- departamentos: só ativos, cache por cliente, envelope `{data, total}`;
- fornecedor: achado devolve código e nomes; inexistente é 404 tipado; CNPJ
  inválido é 400 sem ecoar o valor; a busca NUNCA cadastra;
- cliente só-arquivo: 409 `CAPACIDADE_AUSENTE` nas duas;
- outro tenant: negado antes de qualquer chamada à Omie (a bateria de endpoints
  sensíveis cobre os três atacantes; aqui, o operador do próprio tenant LÊ).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientConnection,
    ConnectionStatus,
    ProviderType,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie import mock_client
from app.integrations.omie.mock_client import (
    FAKE_DEMO_KEY_PREFIX,
    MOCK_FORNECEDOR_EXCLUIDO_CODIGO,
    reset_mock_payables_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Fornecedor#1"


@pytest.fixture(autouse=True)
def _fast_mock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(mock_client, "_DELAY_LISTAR_CONTAS_SECONDS", 0.0)
    reset_mock_payables_state()
    yield
    reset_mock_payables_state()


async def _seed_user(
    db: AsyncSession,
    *,
    role: UserRole = UserRole.ADMIN,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Leitor",
        email=f"leitor-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_omie_client(db: AsyncSession, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(f"{FAKE_DEMO_KEY_PREFIX}{uuid4().hex[:8]}", hex_key)
    ct_s, iv_s = encrypt("demo-secret", hex_key)
    client = Client(
        name=f"Cli {uuid4().hex[:6]}",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    db.add(client)
    await db.flush()
    return client


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


class TestDepartamentos:
    async def test_only_active_with_total(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)

        resp = await client_with_db.get(f"/api/v1/clients/{client.id}/omie/departamentos")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        codigos = [d["codigo"] for d in body["data"]]
        assert "800000009" not in codigos  # o inativo do mock
        assert body["total"] == len(codigos) == 4
        assert body["data"][0]["estrutura"] == "001"

    async def test_client_operator_of_the_tenant_reads(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        operator = await _seed_user(
            db_session,
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=client.id,
        )
        await _login(client_with_db, operator)
        resp = await client_with_db.get(f"/api/v1/clients/{client.id}/omie/departamentos")
        assert resp.status_code == 200, resp.text


class TestFornecedor:
    async def test_found(self, client_with_db: AsyncClient, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)

        resp = await client_with_db.get(
            f"/api/v1/clients/{client.id}/omie/fornecedores",
            params={"cnpj": "12345678000195"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {
            "codigo": 700_000_001,
            "razao_social": "PAPELARIA DEMO LTDA",
            "nome_fantasia": "Papelaria Demo",
        }

    async def test_not_found_is_typed_404_and_creates_nothing(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)
        before = dict(mock_client._MOCK_FORNECEDORES)

        resp = await client_with_db.get(
            f"/api/v1/clients/{client.id}/omie/fornecedores",
            params={"cnpj": "11.222.333/0001-81"},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "FORNECEDOR_NAO_ENCONTRADO"
        assert before == mock_client._MOCK_FORNECEDORES

    async def test_invalid_cnpj_is_400_without_echo(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)

        resp = await client_with_db.get(
            f"/api/v1/clients/{client.id}/omie/fornecedores",
            params={"cnpj": "12.345.678/0001-96"},
        )
        assert resp.status_code == 400, resp.text
        assert "12.345.678" not in resp.text


class TestFornecedorPorCodigo:
    """`?codigo=` (bloco C): o nome do fornecedor padrão gravado no cliente."""

    async def test_found_by_code_resolves_the_same_cadastro(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)

        resp = await client_with_db.get(
            f"/api/v1/clients/{client.id}/omie/fornecedores", params={"codigo": 700_000_001}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {
            "codigo": 700_000_001,
            "razao_social": "PAPELARIA DEMO LTDA",
            "nome_fantasia": None,
        }

    async def test_unknown_code_is_typed_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)

        url = f"/api/v1/clients/{client.id}/omie/fornecedores"
        params = {"codigo": MOCK_FORNECEDOR_EXCLUIDO_CODIGO}
        resp = await client_with_db.get(url, params=params)
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "FORNECEDOR_NAO_ENCONTRADO"
        # O cache negativo responde a 2ª busca sem perguntar à Omie de novo.
        again = await client_with_db.get(url, params=params)
        assert again.status_code == 404, again.text

    @pytest.mark.parametrize(
        "params",
        [{}, {"cnpj": "12345678000195", "codigo": "700000001"}],
        ids=["nenhum", "os-dois"],
    )
    async def test_cnpj_xor_codigo(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        params: dict[str, str],
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_omie_client(db_session, admin)
        await _login(client_with_db, admin)

        resp = await client_with_db.get(
            f"/api/v1/clients/{client.id}/omie/fornecedores", params=params
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


class TestClienteSoArquivo:
    @pytest.mark.parametrize(
        ("path", "params"),
        [("departamentos", None), ("fornecedores", {"cnpj": "12345678000195"})],
    )
    async def test_is_409_capacidade_ausente(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        params: dict[str, str] | None,
    ) -> None:
        monkeypatch.setattr(get_settings(), "LEGACY_CREDENTIALS_FALLBACK_ENABLED", False)
        admin = await _seed_user(db_session)
        client = Client(name="So arquivo", active=True, created_by=admin.id)
        db_session.add(client)
        await db_session.flush()
        db_session.add(
            ClientConnection(
                client_id=client.id,
                provider_type=ProviderType.ARQUIVO.value,
                label="Arquivo",
                status=ConnectionStatus.ATIVA.value,
            )
        )
        await db_session.flush()
        await _login(client_with_db, admin)

        resp = await client_with_db.get(f"/api/v1/clients/{client.id}/omie/{path}", params=params)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CAPACIDADE_AUSENTE"
