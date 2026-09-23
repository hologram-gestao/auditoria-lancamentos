"""Testes de integração do CRUD de clientes BPO — cobre BACK 3.1 a 3.5.

Cenários cobertos (≥ 15):
    RBAC:
        - Não autenticado → 401.
        - Manager autenticado em /assign (admin-only) → 403.
        - Manager não vê cliente de outro manager (lista vazia + 403 no PATCH).

    GET /clients:
        - Admin lista todos com manager + count.
        - Manager lista apenas a própria carteira.
        - Paginação respeita pageSize.
        - Search filtra por nome (ILIKE case-insensitive).
        - Response NÃO inclui campos `*_encrypted`/`*_iv`.

    POST /clients:
        - Admin cria, credenciais persistem encriptadas no DB (verificação SQL direta).
        - Manager cria com auto-assign (passa a aparecer na sua listagem).
        - Cada credencial tem IV próprio (`omie_app_key_iv != omie_app_secret_iv`).

    POST /clients/test-connection:
        - 200 do Omie (mock) → ok=true.
        - faultstring auth do Omie → ok=false com mensagem PT-BR.
        - Timeout → ok=false com mensagem específica.
        - NÃO persiste cliente.

    PATCH /clients/{id}:
        - Admin edita qualquer cliente (nome / status).
        - Manager edita apenas da carteira (403 em outro).
        - Apenas omie_app_key sem secret → 400 IncompleteCredentials.
        - Ambos juntos → recriptografa, IVs novos diferentes dos anteriores.

    PATCH /clients/{id}/assign (86e390kz8 — define o RESPONSÁVEL):
        - Admin define outro responsável — o anterior CONTINUA vendo o cliente
          (carteira compartilhada; a remoção de acesso é ação própria, em
          test_client_managers.py).
        - Manager → 403.
        - User-alvo não-manager → 400.
        - User-alvo inativo → 400.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import ClientCipher, encrypt
from app.core.crypto_service import (
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    load_client_cipher,
)
from app.core.security import hash_password
from app.db.models import Client, ClientAssignment, ClientConnection, User, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient


# ----------------------------------------------------------------------
# Constantes / helpers
# ----------------------------------------------------------------------

ADMIN_EMAIL = "clients-admin@hologram.com.br"
MANAGER_A_EMAIL = "mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

OMIE_LISTAR_CLIENTES_URL = "https://app.omie.com.br/api/v1/geral/clientes/"

# App key/secret arbitrários — apenas strings inertes para teste, não credenciais.
FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    name: str = "Test User",
    active: bool = True,
) -> User:
    user = User(
        name=name,
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=active,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    manager: User | None = None,
) -> Client:
    """Cria cliente com credenciais encriptadas e (opcionalmente) assignment.

    Se `manager` for None, o cliente fica órfão — útil para testar resiliência
    da listagem (mas em fluxo normal o backend sempre cria o assignment).
    """
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()

    if manager is not None:
        assignment = ClientAssignment(
            client_id=client.id,
            user_id=manager.id,
            assigned_by=creator.id,
            is_primary=True,
        )
        session.add(assignment)
        await session.flush()
    return client


async def _login_as(client: AsyncClient, email: str) -> None:
    """Faz login no client (cookies persistem no jar para próximas requests)."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# RBAC — quem pode acessar
# ----------------------------------------------------------------------


class TestClientsRBAC:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 401

    async def test_manager_assign_returns_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target_client = await _seed_client(db_session, name="Cliente X", creator=admin, manager=mgr)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target_client.id}/assign",
            json={"user_id": str(mgr.id)},
        )
        assert resp.status_code == 403


# ----------------------------------------------------------------------
# GET /clients
# ----------------------------------------------------------------------


class TestListClients:
    async def test_admin_sees_all_with_manager_and_count(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        await _seed_client(db_session, name="Cliente A", creator=admin, manager=mgr_a)
        await _seed_client(db_session, name="Cliente B", creator=admin, manager=mgr_b)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pagination"]["total"] == 2
        names = {c["name"] for c in body["data"]}
        assert names == {"Cliente A", "Cliente B"}
        # Cada cliente expõe responsible_manager + reconciliation_count
        for row in body["data"]:
            assert "responsible_manager" in row
            assert row["responsible_manager"] is not None
            assert "reconciliation_count" in row
            assert row["reconciliation_count"] == 0
            # Nada de credencial vaza
            assert "omie_app_key_encrypted" not in row
            assert "omie_app_key_iv" not in row
            assert "omie_app_secret_encrypted" not in row
            assert "omie_app_secret_iv" not in row

    async def test_manager_sees_only_own_portfolio(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        await _seed_client(db_session, name="Da carteira de A", creator=admin, manager=mgr_a)
        await _seed_client(db_session, name="Da carteira de B", creator=admin, manager=mgr_b)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200
        body = resp.json()
        names = {c["name"] for c in body["data"]}
        assert names == {"Da carteira de A"}
        assert body["pagination"]["total"] == 1

    async def test_search_filters_by_name(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_client(db_session, name="Padaria do Zé", creator=admin, manager=mgr)
        await _seed_client(db_session, name="Mercado da Maria", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/clients?search=PADARIA")
        assert resp.status_code == 200
        names = {c["name"] for c in resp.json()["data"]}
        assert names == {"Padaria do Zé"}

    async def test_pagination_respects_page_size(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        for i in range(5):
            await _seed_client(db_session, name=f"Cliente {i}", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/clients?page=1&pageSize=2")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["pageSize"] == 2
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["totalPages"] == 3


# ----------------------------------------------------------------------
# POST /clients
# ----------------------------------------------------------------------


class TestCreateClient:
    @respx.mock
    async def test_admin_creates_with_encrypted_credentials(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S9 (BACK 09.4): a credencial vai para a CONEXÃO, não para `clients`.

        O que mudou: as 4 colunas antigas ficam NULAS e a origem nasce em
        `client_connections`, cifrada com o locator dela. O que NÃO mudou: nada
        de plaintext no banco, envelope `v1:` + DEK do cliente, e credencial
        nenhuma na resposta.

        O `respx` agora é obrigatório porque a criação **verifica a credencial
        contra o provedor antes de persistir** (09.3).
        """
        respx.post(OMIE_LISTAR_CLIENTES_URL).mock(
            return_value=httpx.Response(200, json={"clientes_cadastro": []})
        )
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/clients",
            json={
                "name": "Novo Cliente",
                "omie_app_key": FAKE_APP_KEY,
                "omie_app_secret": FAKE_APP_SECRET,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Novo Cliente"
        assert body["active"] is True
        assert body["origin_status"] == "ativa"
        # Credenciais NUNCA aparecem em response
        assert "omie_app_key_encrypted" not in body
        assert "omie_app_key" not in body
        assert "omie_app_secret" not in body

        client_id = body["id"]
        # IMPORTANTE: o client_with_db ainda mantém a transação aberta — preciso
        # buscar via a mesma session usada na fixture
        row = (await db_session.execute(select(Client).where(Client.id == client_id))).scalar_one()
        # As colunas ANTIGAS ficam nulas — a credencial mora na conexão agora.
        assert row.omie_app_key_encrypted is None
        assert row.omie_app_secret_encrypted is None
        # Mas a DEK foi provisionada pelo serviço de conexão.
        assert row.dek_wrapped is not None

        connection = (
            await db_session.execute(
                select(ClientConnection).where(ClientConnection.client_id == row.id)
            )
        ).scalar_one()
        assert connection.provider_type == "omie"
        assert connection.credentials_encrypted is not None
        assert connection.credentials_iv is not None
        # Nada de plaintext, e envelope versionado com DEK (não bare).
        assert FAKE_APP_KEY not in connection.credentials_encrypted
        assert FAKE_APP_SECRET not in connection.credentials_encrypted
        assert connection.credentials_encrypted.startswith("v1:")
        assert not ClientCipher.is_legacy(connection.credentials_encrypted)
        # Round-trip via o ClientCipher do cliente (DEK + AAD da CONEXÃO).
        cipher = await load_client_cipher(row, settings=get_settings())
        decifrado = json.loads(
            cipher.decrypt(
                connection.credentials_encrypted,
                connection.credentials_iv,
                field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
            )
        )
        assert decifrado == {"app_key": FAKE_APP_KEY, "app_secret": FAKE_APP_SECRET}

    async def test_manager_creates_with_auto_assign(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Sem credencial de propósito: o auto-assign não depende da origem —
        e desde a S9 o cliente pode nascer sem nenhuma."""
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.post("/api/v1/clients", json={"name": "Auto Assign"})
        assert resp.status_code == 201, resp.text
        client_id = resp.json()["id"]

        # Verifica que o assignment foi criado apontando para o próprio manager
        assignment = (
            await db_session.execute(
                select(ClientAssignment).where(ClientAssignment.client_id == client_id)
            )
        ).scalar_one()
        assert assignment.user_id == mgr.id
        assert assignment.assigned_by == mgr.id

        # E ele aparece na listagem do próprio manager
        list_resp = await client_with_db.get("/api/v1/clients")
        names = {c["name"] for c in list_resp.json()["data"]}
        assert "Auto Assign" in names

    async def test_create_rejects_empty_name(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post("/api/v1/clients", json={"name": ""})
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# POST /clients/test-connection
# ----------------------------------------------------------------------


def _omie_listar_clientes_response(payload: dict[str, Any]) -> httpx.Response:
    """Wrapper para construir a response esperada do Omie nos testes."""
    return httpx.Response(200, json=payload)


class TestTestConnection:
    @respx.mock
    async def test_ok_with_valid_credentials(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)
        respx.post(OMIE_LISTAR_CLIENTES_URL).mock(
            return_value=_omie_listar_clientes_response({"clientes_cadastro": []})
        )

        resp = await client_with_db.post(
            "/api/v1/clients/test-connection",
            json={
                "omie_app_key": FAKE_APP_KEY,
                "omie_app_secret": FAKE_APP_SECRET,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "sucesso" in body["message"].lower()

        # Não persistiu nenhum cliente
        rows = (await db_session.execute(select(Client))).all()
        assert rows == []

    @respx.mock
    async def test_credencial_mock_nao_vai_para_a_rede(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O "Testar conexão" da gaveta com credencial `FAKE_DEMO_OMIE_*` tem de
        cair no `MockOmieClient`, como `POST /connections` já caía.

        Defeito da validação humana da Sprint 9 (23/09/2026): a rota legada
        construía `OmieClient(...)` direto, fora do adaptador que decide entre o
        client real e o mock, e a gaveta saía para a rede com a credencial de
        demonstração. Sem NENHUMA rota registrada no respx, qualquer chamada
        HTTP aqui estoura `AllMockedAssertionError` — é isso que prova "sem rede".
        """
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/clients/test-connection",
            json={
                "omie_app_key": "FAKE_DEMO_OMIE_APP_KEY_TESTE",
                "omie_app_secret": "FAKE_DEMO_OMIE_APP_SECRET_TESTE",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert not respx.calls, "a credencial mock não pode gerar chamada HTTP"

    @respx.mock
    async def test_auth_error_returns_ok_false(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)
        respx.post(OMIE_LISTAR_CLIENTES_URL).mock(
            return_value=_omie_listar_clientes_response(
                {"faultstring": "App Key inválida", "faultcode": "SOAP-ENV:Client-101"}
            )
        )

        resp = await client_with_db.post(
            "/api/v1/clients/test-connection",
            json={
                "omie_app_key": "wrong-key",
                "omie_app_secret": "wrong-secret",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "inválidas" in body["message"].lower()

    @respx.mock
    async def test_timeout_returns_ok_false(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)
        respx.post(OMIE_LISTAR_CLIENTES_URL).mock(side_effect=httpx.TimeoutException("Omie lento"))

        resp = await client_with_db.post(
            "/api/v1/clients/test-connection",
            json={
                "omie_app_key": FAKE_APP_KEY,
                "omie_app_secret": FAKE_APP_SECRET,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "tempo esperado" in body["message"].lower()


# ----------------------------------------------------------------------
# PATCH /clients/{id}
# ----------------------------------------------------------------------


class TestUpdateClient:
    async def test_admin_updates_name_only(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Nome Antigo", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}",
            json={"name": "Nome Novo"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Nome Novo"

    async def test_manager_cannot_update_other_managers_client(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_b = await _seed_client(db_session, name="Do B", creator=admin, manager=mgr_b)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{cliente_b.id}",
            json={"name": "Tentando renomear"},
        )
        assert resp.status_code == 403
        # Confere que não houve mudança no DB
        await db_session.refresh(cliente_b)
        assert cliente_b.name == "Do B"

    async def test_credencial_no_patch_e_422_apontando_a_rota_de_conexao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S9 (BACK 09.3): o PATCH deixou de escrever credencial.

        Antes, mandar só a App Key era 400 `IncompleteCredentialsError` e mandar
        as duas recifrava as colunas antigas. Agora a origem é entidade própria
        (`client_connections`) e a escrita passa pelo serviço que valida contra
        o provedor ANTES de gravar — manter os dois caminhos seria manter duas
        verdades sobre a mesma credencial, e este não verificava nada.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="C", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}",
            json={"omie_app_key": "nova-key"},
        )
        assert resp.status_code == 422, resp.text
        assert "connections" in resp.text

    async def test_credencial_no_patch_nao_reescreve_as_colunas_antigas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="C", creator=admin, manager=mgr)
        antes = (
            target.omie_app_key_encrypted,
            target.omie_app_key_iv,
            target.omie_app_secret_encrypted,
            target.omie_app_secret_iv,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}",
            json={
                "omie_app_key": "nova-app-key-xyz",
                "omie_app_secret": "novo-secret-xyz",
            },
        )
        assert resp.status_code == 422, resp.text

        await db_session.refresh(target)
        assert (
            target.omie_app_key_encrypted,
            target.omie_app_key_iv,
            target.omie_app_secret_encrypted,
            target.omie_app_secret_iv,
        ) == antes

    async def test_admin_deactivates_via_active_field(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="C", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}",
            json={"active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False


# ----------------------------------------------------------------------
# PATCH /clients/{id}/assign
# ----------------------------------------------------------------------


class TestAssignClient:
    async def test_admin_sets_responsible_and_previous_manager_keeps_access(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O caso da Bruna (14/09/2026): trocar o responsável NÃO tira o acesso de ninguém.

        Antes, `/assign` sobrescrevia o `user_id` da linha única e o gerente
        anterior sumia da carteira em silêncio. Agora ele continua como
        colaborador; remover acesso é ação separada e explícita.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Reassign Me", creator=admin, manager=mgr_a)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}/assign",
            json={"user_id": str(mgr_b.id)},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["responsible_manager"]["id"] == str(mgr_b.id)
        assert resp.json()["manager_count"] == 2

        # Manager A CONTINUA vendo o cliente — agora como colaborador.
        await client_with_db.post("/api/v1/auth/logout")
        await _login_as(client_with_db, MANAGER_A_EMAIL)
        list_resp = await client_with_db.get("/api/v1/clients")
        rows = {c["name"]: c for c in list_resp.json()["data"]}
        assert "Reassign Me" in rows
        # E a lista mostra o RESPONSÁVEL (B), não quem está olhando.
        assert rows["Reassign Me"]["responsible_manager"]["id"] == str(mgr_b.id)

        detail = await client_with_db.get(f"/api/v1/clients/{target.id}/reconciliations")
        assert detail.status_code == 200, detail.text

    async def test_manager_assigning_returns_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="X", creator=admin, manager=mgr_a)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}/assign",
            json={"user_id": str(mgr_b.id)},
        )
        assert resp.status_code == 403

    async def test_assign_to_admin_target_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        another_admin = await _seed_user(
            db_session,
            email="other-admin@hologram.com.br",
            role=UserRole.ADMIN,
        )
        target = await _seed_client(db_session, name="C", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}/assign",
            json={"user_id": str(another_admin.id)},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_assign_to_inactive_manager_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_inactive = await _seed_user(
            db_session,
            email="inactive-mgr@hologram.com.br",
            role=UserRole.MANAGER,
            active=False,
        )
        target = await _seed_client(db_session, name="C", creator=admin, manager=mgr_a)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}/assign",
            json={"user_id": str(mgr_inactive.id)},
        )
        assert resp.status_code == 400

    async def test_assign_to_unknown_user_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="C", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/clients/{target.id}/assign",
            json={"user_id": str(uuid4())},
        )
        assert resp.status_code == 400
