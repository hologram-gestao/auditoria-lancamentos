"""Nenhuma rota dependente de origem vira 5xx sem origem (S9, BACK 09.6 — R6).

O defeito que este arquivo existe para impedir: com cliente sem conexão, a
linha `load_client_cipher(client)` estourava `CryptoError` e o handler global
devolvia **500**. Para o usuário isso é "o sistema quebrou"; a verdade é "falta
conectar uma origem", que é **409 acionável**.

Cobre, por família de rota, os três códigos da taxonomia:

    sem_conexao        — cliente sem nenhuma conexão
    origem_com_erro    — tem conexão, nenhuma ativa
    capacidade_ausente — tem ativa, e o tipo não faz aquilo

E a exceção deliberada: `GET /clients/{id}` responde **200 sempre**.

⚠️ O fallback da 09.5 fica DESLIGADO nestes testes (`monkeypatch` na flag) —
com ele ligado, um cliente com credencial nas colunas antigas operaria, e o
cenário "sem origem" não seria testável. Os clientes aqui nascem sem credencial
nenhuma, então o fallback nem teria o que sintetizar; desligar é cinto e
suspensório.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    ClientConnection,
    ConnectionStatus,
    ProviderType,
    ReconciliationFile,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
ADMIN_EMAIL = "admin-origem@hologram.com.br"

#: Os três códigos da taxonomia — nenhum outro é aceitável nestas rotas.
ORIGIN_CODES = {"SEM_CONEXAO", "ORIGEM_COM_ERRO", "CAPACIDADE_AUSENTE"}


@pytest.fixture(autouse=True)
def _fallback_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem o fallback da janela, para o cenário "sem origem" ser real."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LEGACY_CREDENTIALS_FALLBACK_ENABLED", False)


async def _seed_admin(session: AsyncSession) -> User:
    user = User(
        name="Admin",
        email=ADMIN_EMAIL,
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client_sem_origem(session: AsyncSession, admin: User) -> Client:
    """Cliente pleno e SEM nenhuma origem — o mundo da Sprint 9."""
    client = Client(name="Sem origem", active=True, created_by=admin.id)
    session.add(client)
    await session.flush()
    session.add(
        ClientAssignment(
            client_id=client.id, user_id=admin.id, assigned_by=admin.id, is_primary=True
        )
    )
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession, client: Client, admin: User
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=admin.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=f"{uuid4().hex}{uuid4().hex}",
        status="reviewing",
        balance_start=0,
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    session.add(
        ReconciliationFile(
            session_id=sess.id, file_hash=f"{uuid4().hex}{uuid4().hex}", status="parsed"
        )
    )
    await session.flush()
    return sess


async def _login(client: AsyncClient) -> int:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PLAIN_PASSWORD}
    )
    return resp.status_code


def _assert_origin_409(resp: object) -> None:
    """A asserção que este arquivo inteiro existe para fazer."""
    status = resp.status_code  # type: ignore[attr-defined]
    assert status == 409, f"{resp.request.url} devolveu {status}: {resp.text}"  # type: ignore[attr-defined]
    body = resp.json()["error"]  # type: ignore[attr-defined]
    assert body["code"] in ORIGIN_CODES, f"código inesperado: {body['code']}"
    assert body["userMessage"]


class TestSemConexao:
    async def test_sync_de_contas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        assert await _login(client_with_db) == 200

        resp = await client_with_db.patch(f"/api/v1/clients/{client.id}/sync-accounts")
        _assert_origin_409(resp)
        assert resp.json()["error"]["code"] == "SEM_CONEXAO"

    async def test_criacao_de_conciliacao_recusa_antes_de_gravar(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O 409 sai ANTES da sessão: criar e falhar no job deixaria lixo."""
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        assert await _login(client_with_db) == 200

        antes = int(
            (await db_session.execute(select(func.count(ReconciliationSession.id)))).scalar_one()
        )
        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json={
                "client_id": str(client.id),
                "omie_conta_id": 42,
                "reference_month": "2026-04",
                "files": [],
            },
        )
        # 409 da taxonomia (ou 422 da validação do payload, que vem antes) —
        # o que NÃO pode é 5xx.
        assert resp.status_code < 500, resp.text
        depois = int(
            (await db_session.execute(select(func.count(ReconciliationSession.id)))).scalar_one()
        )
        assert depois == antes

    async def test_lancamentos_disponiveis_na_revisao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        sess = await _seed_session(db_session, client, admin)
        assert await _login(client_with_db) == 200

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/available-omie-entries")
        _assert_origin_409(resp)

    async def test_export(self, client_with_db: AsyncClient, db_session: AsyncSession) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        sess = await _seed_session(db_session, client, admin)
        assert await _login(client_with_db) == 200

        # A rota é POST. O GET que vivia aqui recebia 405 e o `< 500` engolia
        # — 405 é menor que 500 —, então o export nunca tinha sido exercitado
        # por esta bateria (follow-up 86e3dc16c; o gate de URLs do QA compara o
        # path, não o método). Com o verbo certo e a sessão em `reviewing`
        # (exportável), nada legítimo vem antes do resolvedor de origem: a
        # rota carrega a sessão, o cliente, grava a trilha de export e SÓ
        # ENTÃO pede a conexão capaz. Cliente sem origem é o 409 da taxonomia.
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/export")
        _assert_origin_409(resp)

    async def test_categorias_do_omie(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        sess = await _seed_session(db_session, client, admin)
        assert await _login(client_with_db) == 200

        # O router é `APIRouter(prefix="/api/v1/omie")` e o parâmetro é
        # `session_id` (sem alias). O path errado que estava aqui dava 404, e a
        # asserção `< 500` engolia isso — 404 é menor que 500. Asserção que não
        # distingue "rota respondeu" de "rota não existe" não pode ser a única
        # guardiã de um path, então ela subiu para o que o caso realmente afirma:
        # o 409 da taxonomia, como os vizinhos.
        resp = await client_with_db.get(
            "/api/v1/omie/categorias", params={"session_id": str(sess.id)}
        )
        _assert_origin_409(resp)

    async def test_lancamento_no_omie(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        sess = await _seed_session(db_session, client, admin)
        assert await _login(client_with_db) == 200

        # `cod_categoria` é obrigatório no schema: sem ele a validação do body
        # devolvia 422 ANTES de o handler rodar, e o `< 500` que vivia aqui
        # passava sem nunca exercitar a origem (follow-up 86e3dc16c). Com o body
        # válido, a rota carrega a sessão e o cliente, carrega o cipher (sem
        # DEK é `None`, não erro) e pede a capacidade ESCREVER antes do lote:
        # cliente sem origem é o 409 da taxonomia, e o `file_entry_id`
        # inventado nunca chega a ser consultado.
        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/omie-postings",
            json={"lines": [{"file_entry_id": str(uuid4()), "cod_categoria": "2.01.03"}]},
        )
        _assert_origin_409(resp)


class TestOrigemComErro:
    async def test_conexao_inativa_devolve_origem_com_erro(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        db_session.add(
            ClientConnection(
                client_id=client.id,
                provider_type=ProviderType.OMIE.value,
                label="Omie",
                status=ConnectionStatus.ERRO.value,
            )
        )
        await db_session.flush()
        assert await _login(client_with_db) == 200

        resp = await client_with_db.patch(f"/api/v1/clients/{client.id}/sync-accounts")
        _assert_origin_409(resp)
        assert resp.json()["error"]["code"] == "ORIGEM_COM_ERRO"


class TestCapacidadeAusente:
    async def test_tipo_ativo_sem_a_capacidade(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hoje o Omie declara as 4 capacidades, então o caso só existe com um
        tipo que declare menos — o cenário do 2º provedor. Em vez de inventar
        um adaptador de mentira no `app/`, o registry é reduzido só aqui."""
        from app.integrations.providers.base import Capability
        from app.modules.client_connections import capability as capability_module

        monkeypatch.setattr(
            capability_module,
            "capabilities_for",
            lambda _tipo: frozenset({Capability.VERIFICAR_CREDENCIAL}),
        )
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        db_session.add(
            ClientConnection(
                client_id=client.id,
                provider_type=ProviderType.OMIE.value,
                label="Omie",
                status=ConnectionStatus.ATIVA.value,
            )
        )
        await db_session.flush()
        assert await _login(client_with_db) == 200

        resp = await client_with_db.patch(f"/api/v1/clients/{client.id}/sync-accounts")
        _assert_origin_409(resp)
        assert resp.json()["error"]["code"] == "CAPACIDADE_AUSENTE"


class TestDetalheEhExcecao:
    async def test_detalhe_responde_200_sem_origem(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A exceção deliberada (ADR-044-BE): sem isso o parceiro não conseguiria
        nem ABRIR a tela do cliente que acabou de cadastrar."""
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        assert await _login(client_with_db) == 200

        resp = await client_with_db.get(f"/api/v1/clients/{client.id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["origin_status"] == "sem_origem"
        assert resp.json()["accounts"] == []
        assert resp.json()["accounts_synced_at"] is None

    async def test_listagem_de_clientes_responde_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        await _seed_client_sem_origem(db_session, admin)
        assert await _login(client_with_db) == 200

        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200, resp.text

    async def test_listagem_de_conexoes_responde_200_vazia(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        assert await _login(client_with_db) == 200

        resp = await client_with_db.get(f"/api/v1/clients/{client.id}/connections")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["connections"] == []


class TestCarimboPorConexao:
    async def test_sincronizar_uma_nao_carimba_a_outra(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S9 (BACK 09.6): duas contas no mesmo ERP são duas conexões.

        Com o carimbo por CLIENTE (o de antes), sincronizar a primeira marcaria
        a segunda como fresca e ela nunca sincronizaria — um bug que só
        apareceria no cliente que tem duas contas.
        """
        from app.db.models import OmieAccountCache
        from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX

        admin = await _seed_admin(db_session)
        client = await _seed_client_sem_origem(db_session, admin)
        assert await _login(client_with_db) == 200

        credenciais = {
            "app_key": f"{FAKE_DEMO_KEY_PREFIX}KEY_X",
            "app_secret": f"{FAKE_DEMO_KEY_PREFIX}SECRET_X",
        }
        base = f"/api/v1/clients/{client.id}/connections"
        primeira = await client_with_db.post(
            base,
            json={"provider_type": "omie", "label": "Omie matriz", "credentials": credenciais},
        )
        segunda = await client_with_db.post(
            base,
            json={"provider_type": "omie", "label": "Omie filial", "credentials": credenciais},
        )
        assert {primeira.status_code, segunda.status_code} == {201}

        conexoes = (
            (
                await db_session.execute(
                    select(ClientConnection).where(ClientConnection.client_id == client.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(conexoes) == 2
        # Nenhuma sincronizou ainda.
        assert all(c.accounts_synced_at is None for c in conexoes)

        # O sync manual resolve a PRIMEIRA capaz (ordem determinística do
        # repositório: por tipo e rótulo).
        resp = await client_with_db.patch(f"/api/v1/clients/{client.id}/sync-accounts")
        assert resp.status_code == 200, resp.text

        for conexao in conexoes:
            await db_session.refresh(conexao)
        carimbadas = [c for c in conexoes if c.accounts_synced_at is not None]
        assert len(carimbadas) == 1, "o carimbo vazou para a outra conexão"
        # Primeira na ordem `(provider_type, label)` — "Omie filial" < "Omie matriz".
        assert carimbadas[0].label == "Omie filial"

        # E as linhas do cache apontam para a conexão sincronizada.
        cache = (
            (
                await db_session.execute(
                    select(OmieAccountCache).where(OmieAccountCache.client_id == client.id)
                )
            )
            .scalars()
            .all()
        )
        assert cache
        assert {row.connection_id for row in cache} == {carimbadas[0].id}


class TestCacheHitNaoResolveOrigem:
    """`/omie-data/lancamentos` em cache HIT não constrói client (retrabalho R1).

    A migração da 09.6 tinha invertido o contrato desta rota: o client era
    construído ANTES da chamada e entregue como `lambda: omie_client`. O serviço
    só invoca a fábrica no MISS — e o `aclose()` mora no `finally` logo abaixo
    dela. Em cache HIT, portanto, o `OmieClient` (com o `httpx.AsyncClient`
    dentro) nascia e nunca era fechado, o unwrap da DEK no KMS passava a
    acontecer em toda request, e o 409 de origem estourava numa request que o
    cache resolveria sozinho.

    Este é o teste de ROTA — o companheiro unitário
    (`tests/unit/test_omie_lancamentos_cache_hit.py`) prova o outro lado: que o
    serviço só chama a fábrica no miss, e que ela e a do endpoint de categorias
    têm a MESMA assinatura.
    """

    async def test_cache_quente_responde_200_sem_tocar_na_origem(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from decimal import Decimal

        from app.integrations.omie.lancamento_cache import OmieLancamentoData
        from app.main import app as fastapi_app
        from app.modules.omie_data import routes as omie_data_routes

        admin = await _seed_admin(db_session)
        # Cliente SEM origem de propósito: se a rota resolvesse a conexão no
        # hit, este cenário devolveria 409 — a terceira consequência descrita
        # na reprovação.
        client = await _seed_client_sem_origem(db_session, admin)
        sess = await _seed_session(db_session, client, admin)
        assert await _login(client_with_db) == 200

        # Cache quente: escrita direta no L1 (o singleton de `app.state`, criado
        # em `create_app` e não no lifespan). Popular pela API exigiria rede.
        cache = fastapi_app.state.omie_lancamento_cache
        cache._l1[(client.id, 4242)] = OmieLancamentoData(
            omie_id=4242,
            transaction_date=date(2026, 4, 10),
            description="Compra em cache",
            amount=Decimal("10.00"),
            supplier=None,
            category=None,
            status="conciliado",
        )

        async def _nao_deveria_resolver(*args: object, **kwargs: object) -> object:
            raise AssertionError("cache hit resolveu a origem")

        monkeypatch.setattr(omie_data_routes, "build_capable_client", _nao_deveria_resolver)

        resp = await client_with_db.get(
            "/api/v1/omie/lancamentos",
            params={"ids": "4242", "session_id": str(sess.id)},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 1
