"""Rotas do plano de contas do cliente (Sprint 10 / BACK 10.3 — R3 + R4).

As DUAS permissões novas em ação, e a diferença entre elas é o ponto da task:

    `view_client_chart_of_accounts`  → todos, inclusive o `client_operator`
    `sync_client_chart_of_accounts`  → todos MENOS o `client_operator`

Também cobre: paginação com `alias="pageSize"` (sem ele o seletor do front vira
enfeite — 86e2u512z), filtros por situação e hierarquia, busca por CÓDIGO (não
por nome), a cobertura calculada no servidor sobre o conjunto INTEIRO, cliente
encerrado (sync 409 / leitura 200) e o nome resolvido em runtime com fail-soft.

O cross-tenant e o cross-org destas três rotas rodam na bateria dos três
atacantes (`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import func, update

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@PlanoDeContas#1"
OMIE_CATEGORIAS_URL = "https://app.omie.com.br/api/v1/geral/categorias/"

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "omie" / "listar_categorias.response.json"
)

#: A resposta REAL, 50 categorias da conta Hologram.
#:
#: ⚠️ Os números de cobertura aqui **não** são os do exemplo do PRD
#: ("50 · 50 ativas · 37 · 13 · 6"). Conferidos contra a fixture: 4 categorias
#: têm `conta_inativa='S'`, então são **46 ativas**; 37 e 6 são contagens sobre
#: o TOTAL, e sobre as ativas dão 33 e 5. O `semDestino` bate: 46 - 33 = 13.
#: Registrado em `.claude/memory/decisions.md` (ADR-061-BE).
FIXTURE_TOTAL = 50
FIXTURE_ATIVAS = 46
FIXTURE_COM_DESTINO = 33
FIXTURE_SEM_DESTINO = 13
FIXTURE_COM_CONTA_CONTABIL = 5


def _fixture_items() -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    items = payload["categoria_cadastro"]
    assert isinstance(items, list)
    return items


def _omie_response(items: list[dict[str, Any]]) -> httpx.Response:
    """`registros < 50` encerra a paginação; a fixture tem exatamente 50, então
    a segunda página é pedida e devolvida vazia — é o comportamento real."""
    return httpx.Response(
        200,
        json={
            "pagina": 1,
            "total_de_paginas": 1,
            "registros": len(items),
            "total_de_registros": len(items),
            "categoria_cadastro": items,
        },
    )


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Cache de categorias limpo por teste — senão a contagem de chamadas vaza."""
    fastapi_app.state.omie_categorias_cache = OmieCategoriasCache()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Plano de contas",
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
    ct_key, iv_key = encrypt("chart-app-key", hex_key)
    ct_secret, iv_secret = encrypt("chart-app-secret", hex_key)
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


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    suffix = uuid4().hex[:8]
    w.admin = await _seed_user(
        db_session, email=f"coa-admin-{suffix}@hologram.com.br", role=UserRole.ADMIN
    )
    w.manager_na_carteira = await _seed_user(
        db_session, email=f"coa-mgr-{suffix}@hologram.com.br", role=UserRole.MANAGER
    )
    w.manager_fora = await _seed_user(
        db_session, email=f"coa-mgr-fora-{suffix}@hologram.com.br", role=UserRole.MANAGER
    )
    w.client = await _seed_client(db_session, creator=w.admin, name="Cliente do plano")
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
        email=f"coa-cli-mgr-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.tenant_operator = await _seed_user(
        db_session,
        email=f"coa-cli-op-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    await db_session.flush()
    return w


async def _login(client: AsyncClient, user: User) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _base(world: World) -> str:
    return f"/api/v1/clients/{world.client.id}/chart-of-accounts"


async def _sync_fixture(client_with_db: AsyncClient, world: World) -> httpx.Response:
    """Popula o plano de contas do cliente com a resposta REAL."""
    respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_fixture_items()))
    return await client_with_db.post(f"{_base(world)}/sync")


@pytest.mark.integration
class TestAsDuasPermissoes:
    @respx.mock
    async def test_operador_do_cliente_le_mas_nao_sincroniza(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """O teste nominal do R4: as duas células que diferem entre si.

        Reusar `sync_omie_accounts` (de todos) daria 200 nos dois; reusar
        `manage_client_categories` (admin-only) daria 403 nos dois. É o par que
        prova que nenhuma das duas foi reusada.
        """
        await _login(client_with_db, world.admin)
        assert (await _sync_fixture(client_with_db, world)).status_code == 200

        await _login(client_with_db, world.tenant_operator)
        leitura = await client_with_db.get(_base(world))
        cobertura = await client_with_db.get(f"{_base(world)}/coverage")
        sincronizar = await client_with_db.post(f"{_base(world)}/sync")

        assert leitura.status_code == 200, leitura.text
        assert cobertura.status_code == 200, cobertura.text
        assert sincronizar.status_code == 403, sincronizar.text
        assert sincronizar.json()["error"]["userMessage"]
        # A negativa não vaza o alvo.
        assert world.client.name not in sincronizar.text

    @respx.mock
    async def test_gerente_do_cliente_sincroniza(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.tenant_manager)
        resp = await _sync_fixture(client_with_db, world)
        assert resp.status_code == 200, resp.text

    @respx.mock
    async def test_gerente_fora_da_carteira_e_negado_nas_tres(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """A permissão passa (o papel tem a célula); o ALCANCE não.

        É `resolve_client_access`, não a matriz — e é por isso que "(carteira)"
        não é célula.
        """
        await _login(client_with_db, world.manager_fora)
        for resp in (
            await client_with_db.get(_base(world)),
            await client_with_db.get(f"{_base(world)}/coverage"),
            await client_with_db.post(f"{_base(world)}/sync"),
        ):
            assert resp.status_code in (403, 404), resp.text
            assert world.client.name not in resp.text

    @respx.mock
    async def test_gerente_da_carteira_alcanca(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.manager_na_carteira)
        resp = await _sync_fixture(client_with_db, world)
        assert resp.status_code == 200, resp.text

    async def test_sem_login_e_401(self, client_with_db: AsyncClient, world: World) -> None:
        assert (await client_with_db.get(_base(world))).status_code == 401


@pytest.mark.integration
class TestCoberturaSobreOConjuntoInteiro:
    @respx.mock
    async def test_as_cinco_contagens_da_fixture_real(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        sync = await _sync_fixture(client_with_db, world)
        assert sync.status_code == 200, sync.text

        resp = await client_with_db.get(f"{_base(world)}/coverage")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        assert data["total"] == FIXTURE_TOTAL
        assert data["ativas"] == FIXTURE_ATIVAS
        assert data["comDestino"] == FIXTURE_COM_DESTINO
        assert data["semDestino"] == FIXTURE_SEM_DESTINO
        assert data["comContaContabil"] == FIXTURE_COM_CONTA_CONTABIL
        # A identidade que a tela promete.
        assert data["comDestino"] + data["semDestino"] == data["ativas"]
        assert data["syncedAt"] is not None
        assert data["syncFailedAt"] is None

    @respx.mock
    async def test_a_cobertura_nao_muda_com_a_paginacao(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Ela é sobre o CLIENTE, não sobre a página — a razão de ser rota própria."""
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        primeira = await client_with_db.get(f"{_base(world)}/coverage")
        lista = await client_with_db.get(_base(world), params={"page": 1, "pageSize": 5})
        segunda = await client_with_db.get(f"{_base(world)}/coverage")

        assert len(lista.json()["data"]) == 5
        assert primeira.json() == segunda.json()

    async def test_cliente_que_nunca_sincronizou_tem_estado_vazio(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Estado vazio da tela: zeros e `syncedAt` nulo — não é erro."""
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(f"{_base(world)}/coverage")

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["syncedAt"] is None
        assert data["syncFailedAt"] is None


@pytest.mark.integration
class TestListaFiltrosEBusca:
    @respx.mock
    async def test_paginacao_respeita_o_alias_pagesize(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Sem `alias="pageSize"` o parâmetro é descartado SEM erro e o servidor
        devolve o default — o seletor de itens por página vira enfeite
        (86e2u512z). A prova é o tamanho da página mudando de verdade."""
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        resp = await client_with_db.get(_base(world), params={"pageSize": 7})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 7
        assert body["pagination"]["pageSize"] == 7
        assert body["pagination"]["total"] == FIXTURE_TOTAL
        assert body["pagination"]["totalPages"] == 8  # ceil(50 / 7)

    @respx.mock
    async def test_paginas_nao_se_repetem(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        p1 = await client_with_db.get(_base(world), params={"page": 1, "pageSize": 10})
        p2 = await client_with_db.get(_base(world), params={"page": 2, "pageSize": 10})

        codigos1 = [item["categoryCode"] for item in p1.json()["data"]]
        codigos2 = [item["categoryCode"] for item in p2.json()["data"]]
        assert len(codigos1) == len(codigos2) == 10
        assert set(codigos1).isdisjoint(codigos2)
        assert codigos1 == sorted(codigos1), "ordenação instável quebraria a paginação"

    @respx.mock
    async def test_busca_e_por_codigo(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        resp = await client_with_db.get(_base(world), params={"code": "1.01"})
        assert resp.status_code == 200, resp.text
        codigos = [item["categoryCode"] for item in resp.json()["data"]]
        assert codigos, "a busca por prefixo real não achou nada"
        assert all("1.01" in codigo for codigo in codigos)

    @respx.mock
    async def test_busca_por_nome_nao_existe_e_curinga_e_literal(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Duas coisas que o servidor NÃO faz.

        Nome não é campo de busca (nem está no banco, §4.5). E `%` no termo
        procura o caractere: sem escapar, buscar `%` listaria o plano inteiro e
        pareceria que a busca não filtra.
        """
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        por_nome = await client_with_db.get(_base(world), params={"code": "Transferência"})
        assert por_nome.json()["data"] == []

        curinga = await client_with_db.get(_base(world), params={"code": "%"})
        assert curinga.json()["data"] == []

    @respx.mock
    async def test_filtro_por_situacao_e_por_hierarquia(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        ativas = await client_with_db.get(_base(world), params={"status": "ativa", "pageSize": 100})
        assert ativas.json()["pagination"]["total"] == FIXTURE_ATIVAS

        filhas = await client_with_db.get(_base(world), params={"parentCode": "1.01"})
        assert filhas.status_code == 200, filhas.text
        assert all(item["parentCode"] == "1.01" for item in filhas.json()["data"])

    @respx.mock
    async def test_situacao_fora_do_vocabulario_e_400_da_validacao(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """`Literal` no schema: 422 automático, nunca lista vazia silenciosa."""
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(_base(world), params={"status": "arquivada"})
        # Convenção da casa: validação de entrada é 400 `VALIDATION_ERROR` genérico
        # (o handler global não ecoa input, 86e2rtxcm).
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    @respx.mock
    async def test_pagesize_acima_do_teto_e_400_da_validacao(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await client_with_db.get(_base(world), params={"pageSize": 500})).status_code == 400


@pytest.mark.integration
class TestNomeVemDoCacheEmRuntime:
    @respx.mock
    async def test_nome_e_resolvido_e_nao_persistido(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        resp = await client_with_db.get(_base(world), params={"code": "1.01.01"})
        item = next(i for i in resp.json()["data"] if i["categoryCode"] == "1.01.01")
        assert item["name"] == "BPO Controller - RB"
        # O nome da conta de DEMONSTRATIVO é outro, apesar do código igual —
        # é o motivo de os dois mapas serem separados.
        assert item["dreCode"] == "1.01.01"
        assert item["dreName"] == "Receita Bruta de Vendas"

    @respx.mock
    async def test_origem_indisponivel_devolve_a_lista_com_nome_nulo(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Fail-soft: código, destino e cobertura são LOCAIS e continuam certos.

        Propagar o erro deixaria o plano de contas ilegível justamente quando o
        usuário mais precisa dele — a credencial expirou, e o que ele tem de ver
        é a lista com o aviso, não uma tela de erro.
        """
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)
        fastapi_app.state.omie_categorias_cache = OmieCategoriasCache()
        respx.post(OMIE_CATEGORIAS_URL).mock(
            return_value=httpx.Response(
                200, json={"faultcode": "SOAP-ENV:Client-101", "faultstring": "Erro"}
            )
        )

        resp = await client_with_db.get(_base(world), params={"code": "1.01.01"})

        assert resp.status_code == 200, resp.text
        item = next(i for i in resp.json()["data"] if i["categoryCode"] == "1.01.01")
        assert item["name"] is None
        assert item["dreCode"] == "1.01.01", "o código é LOCAL e não depende da origem"


@pytest.mark.integration
class TestClienteEncerrado:
    @respx.mock
    async def test_sincronizar_e_409_e_ler_continua_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """§4.12: escrita em cliente encerrado é 409; o histórico continua legível."""
        await _login(client_with_db, world.admin)
        await _sync_fixture(client_with_db, world)

        await db_session.execute(
            update(Client).where(Client.id == world.client.id).values(closed_at=func.now())
        )
        await db_session.flush()

        sincronizar = await client_with_db.post(f"{_base(world)}/sync")
        leitura = await client_with_db.get(_base(world))
        cobertura = await client_with_db.get(f"{_base(world)}/coverage")

        assert sincronizar.status_code == 409, sincronizar.text
        assert leitura.status_code == 200, leitura.text
        assert cobertura.json()["data"]["total"] == FIXTURE_TOTAL
