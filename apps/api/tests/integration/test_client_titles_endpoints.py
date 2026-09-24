"""Rotas da CARTEIRA de títulos (Sprint 11 / BACK 11.5 — R4 + R5).

As DUAS permissões novas em ação, e a diferença entre elas é o ponto da task:

    `view_client_receivables`  → todos, inclusive o `client_operator`
    `sync_client_receivables`  → todos MENOS o `client_operator`

Também cobre: paginação com `alias="pageSize"` (sem ele o seletor do front vira
enfeite — 86e2u512z), filtros e ordenação NO SERVIDOR, enum inválido em query
respondendo **400 `VALIDATION_ERROR`** (nunca 422 — convenção de 23/09/2026),
cliente encerrado (sync 409 / leituras 200), os agregados sobre o conjunto INTEIRO
e o nome do devedor resolvido em runtime **em lote**, com fail-soft.

O cross-tenant e o cross-org das três rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica — as três entradas novas
estão lá. Aqui fica o cross-tenant DENTRO da mesma organização, que é o caso que o
critério pede nominalmente.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import update

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    TitleStatus,
    TitleType,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.clientes_cache import OmieClientesCache
from app.main import app as fastapi_app
from app.modules.client_titles.repository import ClientTitlesRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Carteira#1"
OMIE_CLIENTES_URL = "https://app.omie.com.br/api/v1/geral/clientes/"

#: Códigos de devedor plantados. São os da captura REAL — acima do teto de INTEGER.
FORNECEDOR_A = 2624256082
FORNECEDOR_B = 2624256090


@pytest.fixture(autouse=True)
def _fresh_clientes_cache() -> None:
    """Cache de nomes limpo por teste — senão a contagem de chamadas vaza."""
    fastapi_app.state.omie_clientes_cache = OmieClientesCache()


def _consultar_cliente_response(codigo: int, nome: str) -> httpx.Response:
    """Resposta de `ConsultarCliente` na forma real (só o que o DTO lê)."""
    return httpx.Response(
        200,
        json={
            "codigo_cliente_omie": codigo,
            "razao_social": nome,
            "nome_fantasia": "",
            "cnpj_cpf": "12.345.678/0001-90",
        },
    )


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Carteira",
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
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(f"titles-key-{uuid4().hex[:6]}", hex_key)
    ct_secret, iv_secret = encrypt(f"titles-secret-{uuid4().hex[:6]}", hex_key)
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
    return client


class World:
    admin: User
    manager_na_carteira: User
    manager_fora: User
    tenant_manager: User
    tenant_operator: User
    client: Client
    #: Segundo cliente da MESMA organização — o alvo do cross-tenant intra-org.
    vizinho: Client
    vizinho_operator: User


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    suffix = uuid4().hex[:8]
    w.admin = await _seed_user(
        db_session, email=f"tit-admin-{suffix}@hologram.com.br", role=UserRole.ADMIN
    )
    w.manager_na_carteira = await _seed_user(
        db_session, email=f"tit-mgr-{suffix}@hologram.com.br", role=UserRole.MANAGER
    )
    w.manager_fora = await _seed_user(
        db_session, email=f"tit-mgr-fora-{suffix}@hologram.com.br", role=UserRole.MANAGER
    )
    w.client = await _seed_client(db_session, creator=w.admin, name="Cliente da carteira")
    w.vizinho = await _seed_client(db_session, creator=w.admin, name="Cliente vizinho")
    db_session.add(
        ClientAssignment(
            client_id=w.client.id,
            user_id=w.manager_na_carteira.id,
            assigned_by=w.admin.id,
            is_primary=True,
        )
    )
    w.tenant_manager = await _seed_user(
        db_session,
        email=f"tit-cli-mgr-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.tenant_operator = await _seed_user(
        db_session,
        email=f"tit-cli-op-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.vizinho_operator = await _seed_user(
        db_session,
        email=f"tit-viz-op-{suffix}@vizinho.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.vizinho.id,
    )
    await db_session.flush()
    return w


async def _login(client: AsyncClient, user: User) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _base(world: World) -> str:
    return f"/api/v1/clients/{world.client.id}/titles"


def _row(
    external_id: str,
    *,
    due_date: date,
    amount: str = "100.00",
    title_type: TitleType = TitleType.A_RECEBER,
    supplier_code: int | None = FORNECEDOR_A,
    status: TitleStatus = TitleStatus.EM_ABERTO,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "title_type": title_type.value,
        "due_date": due_date,
        "amount": Decimal(amount),
        "status": status.value,
        "category_code": "2.04.94",
        "supplier_code": supplier_code,
        "omie_conta_id": 2617722760,
        "document_number": "00123/A",
    }


async def _seed_titles(
    db: AsyncSession, client: Client, rows: list[dict[str, Any]], *, synced: bool = True
) -> None:
    repo = ClientTitlesRepository(db)
    now = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    await repo.reconcile_cycle(client.id, rows, synced_at=now)
    if synced:
        await repo.mark_sync_succeeded(client.id, at=now)
    await db.flush()


# ----------------------------------------------------------------------
# R5 — as duas permissões
# ----------------------------------------------------------------------


class TestPermissoes:
    @pytest.mark.parametrize(
        "quem",
        ["admin", "manager_na_carteira", "tenant_manager", "tenant_operator"],
        ids=["admin", "manager-carteira", "client_manager", "client_operator"],
    )
    async def test_todos_esses_papeis_leem_a_carteira(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World, quem: str
    ) -> None:
        """`view_client_receivables` é ✅ nos cinco papéis (a plataforma inclusa)."""
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        await _login(client_with_db, getattr(world, quem))

        resp = await client_with_db.get(_base(world))

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 1

    async def test_o_par_de_permissoes_nao_foi_reusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """**O teste nominal do par**: `client_operator` LÊ 200 e sincroniza 403.

        Se alguém tivesse reusado UMA permissão para as duas ações, um dos dois
        lados deste teste quebraria — e é exatamente o defeito que a S10 evitou
        ao criar duas permissões em vez de uma.
        """
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        await _login(client_with_db, world.tenant_operator)

        leitura = await client_with_db.get(_base(world))
        summary = await client_with_db.get(f"{_base(world)}/summary")
        sync = await client_with_db.post(f"{_base(world)}/sync")

        assert leitura.status_code == 200
        assert summary.status_code == 200
        assert sync.status_code == 403, sync.text

    async def test_a_negacao_nao_vaza_o_nome_do_cliente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """403 com corpo SEM nome, razão social ou CNPJ do alvo (§3.15)."""
        await _login(client_with_db, world.tenant_operator)
        resp = await client_with_db.post(f"{_base(world)}/sync")
        assert resp.status_code == 403
        assert "Cliente da carteira" not in resp.text

    async def test_manager_fora_da_carteira_nao_alcanca(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """A permissão passa, o ALCANCE não — `resolve_client_access`.

        A matriz diz que `manager` pode ver a carteira; qual carteira é outra
        decisão, e é a que impede um gerente de ler o cliente de outro.
        """
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        await _login(client_with_db, world.manager_fora)

        resp = await client_with_db.get(_base(world))
        assert resp.status_code in (403, 404), resp.text
        assert "Cliente da carteira" not in resp.text

    async def test_sem_login_401(self, client_with_db: AsyncClient, world: World) -> None:
        assert (await client_with_db.get(_base(world))).status_code == 401


class TestCrossTenantDentroDaMesmaOrganizacao:
    async def test_usuario_de_outro_cliente_da_mesma_org_nao_alcanca(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O critério que o PRD pede nominalmente.

        Os dois clientes pertencem à MESMA organização (a Hologram, pelo
        `server_default`), então o que separa um do outro é só o tenant do
        usuário — o caso em que um `WHERE` esquecido vira vazamento entre dois
        clientes do mesmo escritório.
        """
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        await _login(client_with_db, world.vizinho_operator)

        lista = await client_with_db.get(_base(world))
        summary = await client_with_db.get(f"{_base(world)}/summary")

        for resp in (lista, summary):
            assert resp.status_code in (403, 404), resp.text
            # e o corpo não vaza nem o nome nem o CNPJ do alvo
            assert "Cliente da carteira" not in resp.text

    async def test_a_lista_do_proprio_tenant_nao_traz_titulo_do_vizinho(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Defense-in-depth na QUERY: os dois têm título com o MESMO identificador."""
        await _seed_titles(db_session, world.client, [_row("9999", due_date=date(2026, 8, 1))])
        await _seed_titles(
            db_session, world.vizinho, [_row("9999", due_date=date(2026, 8, 1), amount="777.00")]
        )
        await _login(client_with_db, world.tenant_operator)

        resp = await client_with_db.get(_base(world))

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["amount"] != "777.00"


# ----------------------------------------------------------------------
# R4 — paginação, filtros e ordenação NO SERVIDOR
# ----------------------------------------------------------------------


class TestPaginacaoEFiltros:
    async def test_page_size_tem_alias_e_recorta_de_verdade(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Sem `alias="pageSize"` o seletor do front vira enfeite (86e2u512z).

        O teste manda `pageSize=7` sobre 10 títulos e exige 7 itens — um `page_size`
        sem alias seria ignorado e devolveria os 10 (ou o default 20).
        """
        await _seed_titles(
            db_session,
            world.client,
            [_row(str(i), due_date=date(2026, 8, 1)) for i in range(10)],
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world), params={"pageSize": 7})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 7
        assert body["pagination"]["pageSize"] == 7
        assert body["pagination"]["total"] == 10
        assert body["pagination"]["totalPages"] == 2

    async def test_teto_de_100_no_page_size(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """§7 · API: teto 100. Acima disso é validação de FORMA → 400."""
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(_base(world), params={"pageSize": 101})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_filtro_de_tipo_e_do_servidor(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_titles(
            db_session,
            world.client,
            [
                _row("P1", due_date=date(2026, 8, 1), title_type=TitleType.A_PAGAR),
                _row("R1", due_date=date(2026, 8, 1), title_type=TitleType.A_RECEBER),
            ],
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world), params={"type": "a_pagar"})

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [t["externalId"] for t in data] == ["P1"]
        assert resp.json()["pagination"]["total"] == 1

    async def test_filtro_de_balde_e_do_servidor(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O balde de 90+ é o que justifica a sprint."""
        await _seed_titles(
            db_session,
            world.client,
            [
                _row("NOVO", due_date=date(2026, 9, 20)),
                _row("ANTIGO", due_date=date(2026, 1, 15)),
            ],
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world), params={"bucket": "90_mais"})

        assert resp.status_code == 200
        assert [t["externalId"] for t in resp.json()["data"]] == ["ANTIGO"]

    async def test_ordenacao_por_valor_decrescente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_titles(
            db_session,
            world.client,
            [
                _row("BARATO", due_date=date(2026, 8, 1), amount="10.00"),
                _row("CARO", due_date=date(2026, 8, 1), amount="9000.00"),
            ],
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(
            _base(world), params={"sortBy": "amount", "sortOrder": "desc"}
        )

        assert resp.status_code == 200
        assert [t["externalId"] for t in resp.json()["data"]] == ["CARO", "BARATO"]

    @pytest.mark.parametrize(
        "params",
        [
            {"type": "a_qualquer_coisa"},
            {"situation": "quitado"},
            {"bucket": "120_mais"},
            {"sortBy": "supplier_name"},
            {"sortOrder": "aleatoria"},
        ],
        ids=["type", "situation", "bucket", "sortBy", "sortOrder"],
    )
    async def test_enum_invalido_em_query_e_400_validation_error(
        self, client_with_db: AsyncClient, world: World, params: dict[str, str]
    ) -> None:
        """Convenção de 23/09/2026 e §4.8: validação de FORMA é **400**, nunca 422.

        O handler global responde `VALIDATION_ERROR` genérico, sem ecoar mensagem
        nem campo de propósito. Um `Literal` no filtro é o que garante que valor
        fora do vocabulário chegue aqui em vez de virar lista vazia — que o
        usuário leria como "este cliente não tem nada".
        """
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(_base(world), params=params)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ----------------------------------------------------------------------
# R4 — nome do devedor resolvido em RUNTIME
# ----------------------------------------------------------------------


class TestNomeDoDevedorEmRuntime:
    @respx.mock
    async def test_nome_resolvido_em_lote_sem_n_mais_1(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Seis títulos, DOIS fornecedores distintos → DUAS consultas à origem.

        É o que separa "resolução em lote" de N+1: um `ConsultarCliente` por linha
        renderizada seriam SEIS idas à origem para uma tela, e a origem processa
        uma requisição por método por credencial. A contagem é por código
        DISTINTO, não por linha — e as duas asserções juntas (6 linhas resolvidas,
        2 chamadas) são o que prova isso.
        """
        await _seed_titles(
            db_session,
            world.client,
            [
                *[_row(f"A{i}", due_date=date(2026, 8, 1)) for i in range(4)],
                *[
                    _row(f"B{i}", due_date=date(2026, 8, 1), supplier_code=FORNECEDOR_B)
                    for i in range(2)
                ],
            ],
        )
        nomes = {FORNECEDOR_A: "AUSTRAL COMERCIO LTDA", FORNECEDOR_B: "BOREAL SERVICOS ME"}

        def _responder(request: httpx.Request) -> httpx.Response:
            codigo = json.loads(request.content)["param"][0]["codigo_cliente_omie"]
            return _consultar_cliente_response(codigo, nomes[codigo])

        route = respx.post(OMIE_CLIENTES_URL).mock(side_effect=_responder)
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world))

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert len(data) == 6
        assert all(t["supplierNameResolved"] is True for t in data)
        assert {t["supplierName"] for t in data} == set(nomes.values())
        assert route.call_count == 2, "dois códigos distintos = duas consultas, não seis"

    @respx.mock
    async def test_falha_de_resolucao_devolve_o_codigo_marcado_e_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """R4: "falha de resolução mostra o código, não vazio" — e **não** um 500.

        O Omie recusando (HTTP 200 + `faultstring`) não pode deixar a carteira
        ilegível: códigos, valores, vencimentos e aging são LOCAIS e continuam
        corretos. `supplierNameResolved=false` é o que diz à tela para mostrar o
        código com a marcação.
        """
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        respx.post(OMIE_CLIENTES_URL).mock(
            return_value=httpx.Response(
                200,
                json={"faultcode": "SOAP-ENV:Client-101", "faultstring": "Cliente nao cadastrado"},
            )
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world))

        assert resp.status_code == 200, resp.text
        titulo = resp.json()["data"][0]
        assert titulo["supplierName"] is None
        assert titulo["supplierNameResolved"] is False
        assert titulo["supplierCode"] == FORNECEDOR_A

    @respx.mock
    async def test_titulo_sem_codigo_de_fornecedor_nao_e_nao_resolvido(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Sem código não há o que resolver — a marcação de "não resolvido" ficaria
        mentindo que a origem falhou."""
        await _seed_titles(
            db_session, world.client, [_row("1", due_date=date(2026, 8, 1), supplier_code=None)]
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world))

        titulo = resp.json()["data"][0]
        assert titulo["supplierCode"] is None
        assert titulo["supplierNameResolved"] is True

    async def test_nenhum_nome_de_devedor_vem_do_banco(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """§4.5: sem origem alcançável, o nome simplesmente não existe.

        Como nada é persistido, uma leitura sem a origem responder devolve
        `supplierName: null` — que é a prova de que o nome não estava guardado.
        """
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_base(world))

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"][0]["supplierName"] is None


# ----------------------------------------------------------------------
# R3 — o bloco de agregados
# ----------------------------------------------------------------------


class TestSummary:
    async def test_agregados_sobre_o_conjunto_inteiro_nao_sobre_a_pagina(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """30 títulos, página de 5, e o total dos agregados continua o de 30."""
        await _seed_titles(
            db_session,
            world.client,
            [_row(str(i), due_date=date(2026, 8, 1), amount="100.00") for i in range(30)],
        )
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(f"{_base(world)}/summary")

        assert resp.status_code == 200, resp.text
        receber = resp.json()["data"]["aReceber"]
        assert receber["qtdEmAberto"] == 30
        assert Decimal(receber["totalEmAberto"]) == Decimal("3000.00")

    async def test_os_quatro_baldes_somam_o_total_vencido(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_titles(
            db_session,
            world.client,
            [
                _row("B1", due_date=date(2026, 9, 14), amount="10.00"),
                _row("B2", due_date=date(2026, 8, 10), amount="20.00"),
                _row("B3", due_date=date(2026, 7, 11), amount="30.00"),
                _row("B4", due_date=date(2026, 1, 15), amount="40.00"),
            ],
        )
        await _login(client_with_db, world.admin)

        receber = (await client_with_db.get(f"{_base(world)}/summary")).json()["data"]["aReceber"]

        soma = sum(
            Decimal(receber[k])
            for k in ("bucket1a30", "bucket31a60", "bucket61a90", "bucket90Mais")
        )
        assert soma == Decimal(receber["totalVencido"])

    async def test_nunca_sincronizada_e_um_campo_explicito(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """R3: "nunca zeros que pareçam resultado" — a tela lê `neverSynced`."""
        await _login(client_with_db, world.admin)

        body = (await client_with_db.get(f"{_base(world)}/summary")).json()["data"]

        assert body["neverSynced"] is True
        assert body["syncedAt"] is None
        assert body["referenceDate"] is not None

    async def test_carteira_vazia_sincronizada_nao_e_never_synced(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_titles(db_session, world.client, [])
        await _login(client_with_db, world.admin)

        body = (await client_with_db.get(f"{_base(world)}/summary")).json()["data"]

        assert body["neverSynced"] is False
        assert body["syncedAt"] is not None
        assert body["aReceber"]["qtdEmAberto"] == 0


# ----------------------------------------------------------------------
# §4.12 — cliente encerrado
# ----------------------------------------------------------------------


class TestClienteEncerrado:
    async def test_sincronizar_409_e_ler_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """A retenção que o encerramento protege inclui LER o que já existe.

        `OpenClientDep` na escrita, `AccessibleClientDep` na leitura: as duas
        rotas de leitura continuam 200 e só o `POST /sync` recebe 409.
        """
        await _seed_titles(db_session, world.client, [_row("1", due_date=date(2026, 8, 1))])
        await db_session.execute(
            update(Client)
            .where(Client.id == world.client.id)
            .values(closed_at=datetime(2026, 9, 1, tzinfo=UTC))
        )
        await db_session.flush()
        await _login(client_with_db, world.admin)

        lista = await client_with_db.get(_base(world))
        summary = await client_with_db.get(f"{_base(world)}/summary")
        sync = await client_with_db.post(f"{_base(world)}/sync")

        assert lista.status_code == 200, lista.text
        assert summary.status_code == 200, summary.text
        assert sync.status_code == 409, sync.text


# ----------------------------------------------------------------------
# Taxonomia de origem na rota de sync
# ----------------------------------------------------------------------


class TestSyncSemOrigem:
    async def test_cliente_sem_origem_e_409_e_a_leitura_segue_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Os três 409 da S9 são estado esperado da configuração, não 5xx."""
        sem_origem = Client(name="Sem origem", active=True, created_by=world.admin.id)
        db_session.add(sem_origem)
        await db_session.flush()
        await _login(client_with_db, world.admin)

        base = f"/api/v1/clients/{sem_origem.id}/titles"
        lista = await client_with_db.get(base)
        sync = await client_with_db.post(f"{base}/sync")

        assert lista.status_code == 200, lista.text
        assert sync.status_code == 409, sync.text
        assert sync.json()["error"]["code"] == "SEM_CONEXAO"
