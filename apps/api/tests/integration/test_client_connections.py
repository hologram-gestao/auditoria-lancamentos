"""Endpoints das conexões de origem (Sprint 9, BACK 09.3 — R5).

Cobre, por ordem de risco:
    - **Nada é persistido antes do provedor aceitar**: credencial inválida
      deixa `client_connections` com ZERO linhas e não mexe na existente.
    - **Nenhuma resposta carrega credencial** — o JSON de cada rota é varrido
      pelo plaintext, pelo ciphertext e pelo IV.
    - Criação provisiona a DEK de um cliente que nasceu sem origem, cifra com o
      locator de `client_connections`, e o teste **decifra de volta**.
    - Casos negativos: `(tipo, rótulo)` repetido → 409 com
      `details.existingConnectionId` e sem PII; rótulo vazio → 422; 2ª do mesmo
      tipo com rótulo diferente → 201; tipo inexistente → 422.
    - Remoção é DELETE físico, e reconectar o mesmo par volta a funcionar.
    - Papel sem permissão → 403 e nenhuma linha criada.
    - As 4 ações de escrita geram 1 linha de auditoria cada, só com IDs.
    - `PATCH /clients/{id}` com credencial → 422 apontando a rota nova, e as
      colunas antigas ficam intactas.

A credencial usada é a do cliente-demo (`FAKE_DEMO_OMIE_`): o adaptador resolve
o `MockOmieClient` por dentro (BACK 09.2) e **nada toca a rede**. A recusa é
simulada com `respx`, que é o caminho da credencial real.

O cross-tenant (operador do tenant A contra o cliente B) está no parametrizado
de `test_sensitive_endpoints.py`, que lê as 5 rotas da lista canônica.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.crypto_service import (
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    load_client_cipher,
)
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    ClientConnection,
    ConnectionStatus,
    User,
    UserRole,
    UserScope,
)
from app.db.session import get_db_session
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
ADMIN_EMAIL = "admin-conn@hologram.com.br"
MANAGER_EMAIL = "manager-conn@hologram.com.br"
TENANT_MANAGER_EMAIL = "gerente@cliente-conn.com.br"
TENANT_OPERATOR_EMAIL = "operador@cliente-conn.com.br"

DEMO_CREDENTIALS = {
    "app_key": f"{FAKE_DEMO_KEY_PREFIX}KEY_1",
    "app_secret": f"{FAKE_DEMO_KEY_PREFIX}SECRET_1",
}
REAL_CREDENTIALS = {"app_key": "chave-real-recusada", "app_secret": "segredo-real-recusado"}
_OMIE_AUTH_FAULT = {
    "faultstring": "SOAP-ENV:Client-101: App Key inválido",
    "faultcode": "SOAP-ENV:Client-101",
}


def _omie_url(module: str, endpoint: str) -> str:
    return f"https://app.omie.com.br/api/v1/{module}/{endpoint}/"


@pytest.fixture(autouse=True)
def _no_mock_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.omie import mock_client

    for attr in (
        "_DELAY_LISTAR_CLIENTES_SECONDS",
        "_DELAY_LISTAR_CONTAS_SECONDS",
        "_DELAY_LISTAR_EXTRATO_SECONDS",
        "_DELAY_LISTAR_TITULOS_SECONDS",
    ):
        monkeypatch.setattr(mock_client, attr, 0.0)


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    user = User(
        name="Test User",
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


class _World:
    admin: User
    manager: User
    client: Client


async def _seed_world(db: AsyncSession, *, with_legacy_credentials: bool = False) -> _World:
    """Cliente SEM origem — o estado que a Sprint 9 criou — e o staff em volta."""
    w = _World()
    w.admin = await _seed_user(db, email=ADMIN_EMAIL, role=UserRole.ADMIN)
    w.manager = await _seed_user(db, email=MANAGER_EMAIL, role=UserRole.MANAGER)

    client = Client(name="Padaria do Bairro", active=True, created_by=w.admin.id)
    if with_legacy_credentials:
        hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
        ct_k, iv_k = encrypt("legado-app-key", hex_key)
        ct_s, iv_s = encrypt("legado-app-secret", hex_key)
        client.omie_app_key_encrypted = ct_k
        client.omie_app_key_iv = iv_k
        client.omie_app_secret_encrypted = ct_s
        client.omie_app_secret_iv = iv_s
    db.add(client)
    await db.flush()
    w.client = client

    db.add(
        ClientAssignment(
            client_id=client.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    await _seed_user(
        db,
        email=TENANT_MANAGER_EMAIL,
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=client.id,
    )
    await _seed_user(
        db,
        email=TENANT_OPERATOR_EMAIL,
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=client.id,
    )
    await db.flush()
    return w


async def _login_as(client: AsyncClient, email: str) -> int:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    return resp.status_code


async def _count(session: AsyncSession, stmt: object) -> int:
    return int((await session.execute(stmt)).scalar_one())  # type: ignore[arg-type]


async def _connections_of(session: AsyncSession, client_id: UUID) -> list[ClientConnection]:
    rows = await session.execute(
        select(ClientConnection).where(ClientConnection.client_id == client_id)
    )
    return list(rows.scalars().all())


async def _audited_conn_actions(session: AsyncSession, client_id: UUID) -> list[AccessAudit]:
    rows = await session.execute(
        select(AccessAudit).where(
            AccessAudit.client_id == client_id,
            AccessAudit.action.in_(["conn_create", "conn_test", "conn_update", "conn_delete"]),
        )
    )
    return list(rows.scalars().all())


def _base(client_id: UUID) -> str:
    return f"/api/v1/clients/{client_id}/connections"


@pytest.fixture
async def client_with_request_rollback(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Como `client_with_db`, mas com a POLÍTICA REAL de transação por request.

    A fixture padrão (`conftest.client_with_db`) injeta um override de
    `get_db_session` que é um gerador simples, **sem** o `except: rollback()` de
    `app/db/session.py`. Por isso ela não consegue provar nada sobre o que
    sobrevive a uma request que termina em exceção: tudo o que o handler deu
    `flush()` continua visível no teste, mesmo que em produção o rollback já o
    tivesse desfeito.

    Este override copia a política de produção — `yield` → `commit()`, exceção →
    `rollback()` e re-levanta — sobre a MESMA session da fixture. Como ela roda
    com `join_transaction_mode="create_savepoint"`, o `commit()` da aplicação
    libera o savepoint (o dado fica visível para o teste e morre no teardown) e
    o `rollback()` seguinte volta só até o savepoint NOVO, sem desfazê-lo. É
    exatamente a diferença que o defeito da 09.3 explorava.
    """

    async def _override() -> AsyncGenerator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


class TestCriarConexao:
    async def test_admin_conecta_origem_em_cliente_sem_credencial(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        # O cliente nasceu SEM DEK e SEM credencial — é o mundo da Sprint 9.
        assert w.client.dek_wrapped is None
        assert w.client.omie_app_key_encrypted is None

        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["provider_type"] == "omie"
        assert data["label"] == "Omie"  # rótulo padrão na PRIMEIRA do tipo
        assert data["status"] == "ativa"
        assert data["last_checked_at"] is not None
        assert set(data["capabilities"]) == {
            "verificar_credencial",
            "listar_contas",
            "listar_lancamentos",
            "escrever",
        }

        await db_session.refresh(w.client)
        # Criar conexão PROVISIONA a DEK do cliente que nasceu sem ela.
        assert w.client.dek_wrapped is not None

    async def test_credencial_cifrada_decifra_de_volta_com_o_locator_da_conexao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert resp.status_code == 201, resp.text

        await db_session.refresh(w.client)
        connection = (await _connections_of(db_session, w.client.id))[0]
        assert connection.credentials_encrypted is not None
        assert connection.credentials_iv is not None
        assert connection.credentials_encrypted.startswith("v1:")

        cipher = await load_client_cipher(w.client, settings=get_settings())
        plaintext = cipher.decrypt(
            connection.credentials_encrypted,
            connection.credentials_iv,
            field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
        )
        assert json.loads(plaintext) == DEMO_CREDENTIALS

    async def test_gerente_da_carteira_tambem_conecta(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A célula do manager é a razão de a permissão ser própria: ele cria
        cliente (✅) mas não edita (❌) — em `edit_client` ele não conectaria."""
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, MANAGER_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert resp.status_code == 201, resp.text

    async def test_segunda_do_mesmo_tipo_com_rotulo_diferente_e_aceita(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        primeira = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "Omie matriz", "credentials": DEMO_CREDENTIALS},
        )
        assert primeira.status_code == 201, primeira.text
        segunda = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "Omie filial", "credentials": DEMO_CREDENTIALS},
        )
        assert segunda.status_code == 201, segunda.text
        assert len(await _connections_of(db_session, w.client.id)) == 2

    async def test_tipo_e_rotulo_repetidos_devolvem_409_com_o_id_da_existente(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        primeira = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "Omie", "credentials": DEMO_CREDENTIALS},
        )
        assert primeira.status_code == 201
        existing_id = primeira.json()["data"]["id"]

        repetida = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "Omie", "credentials": DEMO_CREDENTIALS},
        )
        assert repetida.status_code == 409, repetida.text
        body = repetida.json()["error"]
        assert body["details"]["existingConnectionId"] == existing_id
        # Sem PII do cliente no corpo do erro (§3.15).
        assert "Padaria" not in json.dumps(body)
        assert len(await _connections_of(db_session, w.client.id)) == 1

    async def test_segunda_do_mesmo_tipo_sem_rotulo_e_recusada(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Sem rótulo, a segunda colidiria com o padrão e o 409 confundiria."""
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (
            await client_with_db.post(
                _base(w.client.id),
                json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
            )
        ).status_code == 201
        segunda = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert segunda.status_code in (400, 422), segunda.text

    async def test_rotulo_vazio_e_400_da_validacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "   ", "credentials": DEMO_CREDENTIALS},
        )
        # Convenção da casa: erro de validação de entrada é 400 `VALIDATION_ERROR`
        # com mensagem genérica (o handler global não ecoa input, 86e2rtxcm).
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert await _connections_of(db_session, w.client.id) == []

    async def test_tipo_inexistente_e_recusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "contabilix", "credentials": DEMO_CREDENTIALS},
        )
        assert resp.status_code in (400, 422), resp.text
        assert await _connections_of(db_session, w.client.id) == []

    async def test_credencial_faltando_chave_e_recusada(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": {"app_key": "so-metade"}},
        )
        assert resp.status_code in (400, 422), resp.text
        assert await _connections_of(db_session, w.client.id) == []


class TestNadaPersisteAntesDoProvedorAceitar:
    @respx.mock
    async def test_credencial_invalida_nao_cria_linha_nenhuma(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A prova mais importante da task: contagem ZERO depois da recusa."""
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_OMIE_AUTH_FAULT)
        )
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": REAL_CREDENTIALS},
        )
        assert resp.status_code >= 400, resp.text
        assert await _connections_of(db_session, w.client.id) == []
        # E nem a DEK foi provisionada — a cifra só acontece depois do "sim".
        await db_session.refresh(w.client)
        assert w.client.dek_wrapped is None

    @respx.mock
    async def test_patch_com_credencial_invalida_nao_muda_a_existente(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert criada.status_code == 201
        connection_id = criada.json()["data"]["id"]
        connection = (await _connections_of(db_session, w.client.id))[0]
        antes = (connection.credentials_encrypted, connection.credentials_iv, connection.status)

        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_OMIE_AUTH_FAULT)
        )
        resp = await client_with_db.patch(
            f"{_base(w.client.id)}/{connection_id}",
            json={"credentials": REAL_CREDENTIALS},
        )
        assert resp.status_code >= 400, resp.text

        await db_session.refresh(connection)
        assert (
            connection.credentials_encrypted,
            connection.credentials_iv,
            connection.status,
        ) == antes


class TestTestarConexao:
    async def test_sucesso_marca_ativa_e_carimba(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]
        connection = (await _connections_of(db_session, w.client.id))[0]
        connection.status = ConnectionStatus.ERRO.value
        await db_session.flush()

        resp = await client_with_db.post(f"{_base(w.client.id)}/{connection_id}/test")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "ativa"
        assert resp.json()["data"]["last_checked_at"] is not None

    @respx.mock
    async def test_credencial_recusada_marca_erro_e_preserva_o_ciphertext(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]
        connection = (await _connections_of(db_session, w.client.id))[0]
        # Troca a credencial gravada por uma REAL (sem o prefixo demo) para que
        # o teste passe pelo caminho de rede — que o respx recusa.
        await db_session.refresh(w.client)
        cipher = await load_client_cipher(w.client, settings=get_settings())
        envelope, iv = cipher.encrypt(
            json.dumps(REAL_CREDENTIALS, ensure_ascii=False, sort_keys=True),
            field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
        )
        connection.credentials_encrypted = envelope
        connection.credentials_iv = iv
        await db_session.flush()

        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_OMIE_AUTH_FAULT)
        )
        resp = await client_with_db.post(f"{_base(w.client.id)}/{connection_id}/test")
        assert resp.status_code >= 400, resp.text

        await db_session.refresh(connection)
        assert connection.status == ConnectionStatus.ERRO.value
        # Recusada NÃO é perdida: o envelope continua lá, byte a byte.
        assert connection.credentials_encrypted == envelope
        assert connection.credentials_iv == iv

    async def test_conexao_de_outro_cliente_e_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(f"{_base(w.client.id)}/{uuid4()}/test")
        assert resp.status_code == 404, resp.text

    @respx.mock
    async def test_marcacao_de_erro_sobrevive_ao_rollback_da_request(
        self, client_with_request_rollback: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A marcação `erro` + a auditoria precisam ser DURÁVEIS, não só flushadas.

        O `raise` do `test_connection` sobe até `get_db_session`, que dá
        `rollback()` e re-levanta. Com `flush()` no lugar do `commit()`, esta
        request deixaria a conexão `ativa` para sempre: a tela nunca mostraria
        "origem com erro" e nunca ofereceria reconectar. O teste roda sob
        `client_with_request_rollback` justamente porque a fixture padrão não
        aplica o rollback e passaria verde com o defeito.
        """
        w = await _seed_world(db_session)
        assert await _login_as(client_with_request_rollback, ADMIN_EMAIL) == 200
        criada = await client_with_request_rollback.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert criada.status_code == 201, criada.text
        connection_id = UUID(criada.json()["data"]["id"])

        # Credencial REAL na linha: é o caminho de rede, que o respx recusa.
        connection = (await _connections_of(db_session, w.client.id))[0]
        await db_session.refresh(w.client)
        cipher = await load_client_cipher(w.client, settings=get_settings())
        envelope, iv = cipher.encrypt(
            json.dumps(REAL_CREDENTIALS, ensure_ascii=False, sort_keys=True),
            field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
        )
        connection.credentials_encrypted = envelope
        connection.credentials_iv = iv
        await db_session.commit()

        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_OMIE_AUTH_FAULT)
        )
        resp = await client_with_request_rollback.post(f"{_base(w.client.id)}/{connection_id}/test")
        assert resp.status_code >= 400, resp.text

        # Releitura do ZERO (a sessão passou por um rollback): nada de cache de
        # identidade mascarando o estado real da linha.
        db_session.expunge_all()
        relida = (
            await db_session.execute(
                select(ClientConnection).where(ClientConnection.id == connection_id)
            )
        ).scalar_one()
        assert relida.status == ConnectionStatus.ERRO.value
        assert relida.credentials_encrypted == envelope

        testes_auditados = [
            row
            for row in await _audited_conn_actions(db_session, w.client.id)
            if row.action == "conn_test"
        ]
        assert len(testes_auditados) == 1


class TestAlterarERemover:
    async def test_renomeia_sem_mexer_na_credencial(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]
        connection = (await _connections_of(db_session, w.client.id))[0]
        antes = connection.credentials_encrypted

        resp = await client_with_db.patch(
            f"{_base(w.client.id)}/{connection_id}", json={"label": "Omie renomeada"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["label"] == "Omie renomeada"
        await db_session.refresh(connection)
        assert connection.credentials_encrypted == antes

    async def test_patch_vazio_e_recusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]
        resp = await client_with_db.patch(f"{_base(w.client.id)}/{connection_id}", json={})
        assert resp.status_code in (400, 422), resp.text

    async def test_remocao_e_fisica_e_reconectar_o_mesmo_par_funciona(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O motivo de a remoção divergir do soft delete padrão do repositório."""
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "Omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]

        apagada = await client_with_db.delete(f"{_base(w.client.id)}/{connection_id}")
        assert apagada.status_code == 200, apagada.text
        assert await _connections_of(db_session, w.client.id) == []

        recriada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "label": "Omie", "credentials": DEMO_CREDENTIALS},
        )
        assert recriada.status_code == 201, recriada.text
        assert recriada.json()["data"]["id"] != connection_id


class TestPermissaoETenant:
    async def test_operador_do_cliente_recebe_403_e_nada_e_criado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, TENANT_OPERATOR_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert resp.status_code == 403, resp.text
        assert await _connections_of(db_session, w.client.id) == []
        # O guard da MATRIZ nega por PAPEL, não por tenant: nenhuma linha
        # `denied` (essa é do caminho cross-tenant).
        assert (
            await _count(
                db_session,
                select(func.count(AccessAudit.id)).where(AccessAudit.action == "denied"),
            )
            == 0
        )

    async def test_gerente_do_cliente_tambem_recebe_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, TENANT_MANAGER_EMAIL) == 200
        resp = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert resp.status_code == 403, resp.text

    async def test_usuario_do_cliente_le_o_estado_da_origem(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A leitura é liberada de propósito: o operador precisa saber se a
        origem está ativa antes de rodar uma conciliação."""
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (
            await client_with_db.post(
                _base(w.client.id),
                json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
            )
        ).status_code == 201

        assert await _login_as(client_with_db, TENANT_OPERATOR_EMAIL) == 200
        resp = await client_with_db.get(_base(w.client.id))
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]["connections"]) == 1

    async def test_sem_login_e_401(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert (await client_with_db.get(_base(w.client.id))).status_code == 401


class TestClienteEncerrado:
    async def test_toda_escrita_de_conexao_e_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]
        assert (
            await client_with_db.post(f"/api/v1/clients/{w.client.id}/close")
        ).status_code == 204

        base = _base(w.client.id)
        bloqueadas = [
            client_with_db.post(
                base, json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS}
            ),
            client_with_db.post(f"{base}/{connection_id}/test"),
            client_with_db.patch(f"{base}/{connection_id}", json={"label": "Nova"}),
            client_with_db.delete(f"{base}/{connection_id}"),
        ]
        for coro in bloqueadas:
            resp = await coro
            assert resp.status_code == 409, resp.text
            assert "encerrado" in resp.json()["error"]["userMessage"]

    async def test_encerrar_apaga_as_conexoes_na_mesma_transacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A DEK morre no encerramento — a credencial cifrada da origem vira
        ciphertext morto e não deve ficar (§4.12)."""
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (
            await client_with_db.post(
                _base(w.client.id),
                json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
            )
        ).status_code == 201
        assert len(await _connections_of(db_session, w.client.id)) == 1

        assert (
            await client_with_db.post(f"/api/v1/clients/{w.client.id}/close")
        ).status_code == 204

        assert await _connections_of(db_session, w.client.id) == []
        await db_session.refresh(w.client)
        assert w.client.dek_wrapped is None


class TestNenhumaRespostaCarregaCredencial:
    async def test_varre_o_json_de_cada_rota(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        assert criada.status_code == 201
        connection_id = criada.json()["data"]["id"]
        connection = (await _connections_of(db_session, w.client.id))[0]
        segredos = [
            DEMO_CREDENTIALS["app_key"],
            DEMO_CREDENTIALS["app_secret"],
            connection.credentials_encrypted,
            connection.credentials_iv,
        ]

        respostas = [
            criada,
            await client_with_db.get(_base(w.client.id)),
            await client_with_db.post(f"{_base(w.client.id)}/{connection_id}/test"),
            await client_with_db.patch(
                f"{_base(w.client.id)}/{connection_id}", json={"label": "Outra"}
            ),
            # Erro também não vaza.
            await client_with_db.post(
                _base(w.client.id),
                json={"provider_type": "omie", "label": "Outra", "credentials": DEMO_CREDENTIALS},
            ),
            await client_with_db.delete(f"{_base(w.client.id)}/{connection_id}"),
        ]
        for resp in respostas:
            corpo = resp.text
            for segredo in segredos:
                assert segredo
                assert segredo not in corpo, f"{resp.request.url} vazou credencial"
            assert "credentials" not in corpo


class TestAuditoria:
    async def test_cada_acao_grava_uma_linha_so_com_ids(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        criada = await client_with_db.post(
            _base(w.client.id),
            json={"provider_type": "omie", "credentials": DEMO_CREDENTIALS},
        )
        connection_id = criada.json()["data"]["id"]
        await client_with_db.post(f"{_base(w.client.id)}/{connection_id}/test")
        await client_with_db.patch(f"{_base(w.client.id)}/{connection_id}", json={"label": "Nova"})
        await client_with_db.delete(f"{_base(w.client.id)}/{connection_id}")

        rows = await _audited_conn_actions(db_session, w.client.id)
        assert sorted(r.action for r in rows) == [
            "conn_create",
            "conn_delete",
            "conn_test",
            "conn_update",
        ]
        for row in rows:
            assert row.user_id == w.admin.id
            assert row.user_scope == UserScope.SYSTEM.value
            assert row.actor_organization_id is not None
            # SÓ IDs: nada de nome do cliente, rótulo ou credencial na trilha.
            assert "Padaria" not in row.rota
            assert DEMO_CREDENTIALS["app_key"] not in row.rota

        # E a listagem NÃO audita (navegação no próprio tenant não infla §4.7).
        await client_with_db.get(_base(w.client.id))
        assert len(await _audited_conn_actions(db_session, w.client.id)) == 4


class TestPatchDoClienteRecusaCredencial:
    async def test_422_apontando_a_rota_de_conexao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session, with_legacy_credentials=True)
        antes = (
            w.client.omie_app_key_encrypted,
            w.client.omie_app_key_iv,
            w.client.omie_app_secret_encrypted,
            w.client.omie_app_secret_iv,
        )
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200

        resp = await client_with_db.patch(
            f"/api/v1/clients/{w.client.id}",
            json={"omie_app_key": "nova-chave", "omie_app_secret": "novo-segredo"},
        )
        assert resp.status_code == 422, resp.text
        assert "connections" in resp.text

        # As colunas ANTIGAS ficam intactas — o PATCH não escreveu nada.
        await db_session.refresh(w.client)
        assert (
            w.client.omie_app_key_encrypted,
            w.client.omie_app_key_iv,
            w.client.omie_app_secret_encrypted,
            w.client.omie_app_secret_iv,
        ) == antes

    async def test_patch_de_nome_continua_funcionando(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session, with_legacy_credentials=True)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.patch(
            f"/api/v1/clients/{w.client.id}", json={"name": "Padaria renomeada"}
        )
        assert resp.status_code == 200, resp.text
        # O PATCH devolve o `ClientResponse` sem envelope, como o GET do detalhe.
        assert resp.json()["name"] == "Padaria renomeada"

    async def test_so_uma_das_credenciais_tambem_e_422(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Antes isto era 400 `IncompleteCredentialsError`; agora o caminho
        inteiro acabou, então a resposta é a mesma dos dois campos."""
        w = await _seed_world(db_session, with_legacy_credentials=True)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.patch(
            f"/api/v1/clients/{w.client.id}", json={"omie_app_key": "so-a-chave"}
        )
        assert resp.status_code == 422, resp.text
