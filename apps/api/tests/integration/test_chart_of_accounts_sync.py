"""Sincronização do plano de contas (Sprint 10 / BACK 10.2).

O que estes testes travam, na ordem dos critérios de aceite:

  - **um único caminho até a origem**: uma sincronização = UMA chamada
    (`respx` conta), e dentro da validade de 24h = ZERO;
  - `force=True` ("Sincronizar agora") ignora a validade E o cache de 6h;
  - categoria que some da origem vira `ausente_na_origem` e **não** é apagada;
  - categoria sem `dadosDRE.codigoDRE` fica com destino NULO — nunca inferido;
  - falha da origem **preserva** a última sincronização válida (linhas e
    carimbo de sucesso intactos) e registra a falha, sem escrita parcial;
  - cliente sem conexão capaz recebe o 409 da taxonomia da S9, nunca 5xx.

⚠️ O `respx` é a prova de "uma chamada": ele intercepta o TRANSPORTE. Contar
chamadas no `MockOmieClient` provaria menos — o mock nem chega a abrir socket,
então não distinguiria "reusou o caminho existente" de "fez uma segunda ida à
rede por outro caminho".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import (
    ErrorCode,
    NoOriginConnectionError,
    OmieAuthError,
    OmieFaultError,
)
from app.core.security import hash_password
from app.db.models import (
    ChartOfAccountsStatus,
    Client,
    ClientChartOfAccount,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.modules.client_chart_of_accounts.repository import ClientChartOfAccountsRepository
from app.modules.client_chart_of_accounts.service import ChartOfAccountsSyncService
from app.modules.omie_data.categorias_service import OmieCategoriasService
from app.modules.usage_events.repository import UsageEventRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

OMIE_CATEGORIAS_URL = "https://app.omie.com.br/api/v1/geral/categorias/"

FAKE_APP_KEY = "chart-app-key-12345"
FAKE_APP_SECRET = "chart-app-secret-67890"


def _categoria(
    codigo: str,
    *,
    descricao: str = "Categoria",
    superior: str = "0",
    inativa: str = "N",
    dre: str = "",
    conta_contabil: str = "",
    totalizadora: str = "N",
    transferencia: str = "N",
) -> dict[str, Any]:
    """Uma categoria na FORMA da resposta real: nada é omitido, é `''`/`{}`."""
    return {
        "codigo": codigo,
        "descricao": descricao,
        "categoria_superior": superior,
        "conta_inativa": inativa,
        "id_conta_contabil": conta_contabil,
        "tag_conta_contabil": "ROTULO DA CONTA" if conta_contabil else "",
        "totalizadora": totalizadora,
        "transferencia": transferencia,
        "nao_exibir": "N",
        "dadosDRE": (
            {
                "codigoDRE": dre,
                "descricaoDRE": "Nome da conta de demonstrativo",
                "nivelDRE": 3,
                "sinalDRE": "+",
            }
            if dre
            else {}
        ),
    }


def _omie_response(items: list[dict[str, Any]]) -> httpx.Response:
    """Envelope de `ListarCategorias`. `registros < 50` encerra a paginação."""
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


def _omie_fault(code: str, message: str) -> httpx.Response:
    """A Omie responde erro com **HTTP 200** (§6.3) — o modo que mais engana."""
    return httpx.Response(200, json={"faultcode": code, "faultstring": message})


#: O catálogo padrão dos testes: uma com destino e conta contábil, uma com
#: destino só, uma transferência SEM destino e uma inativa.
_CATALOGO = [
    _categoria("1.01.01", descricao="Vendas", superior="1.01", dre="1.01.01"),
    _categoria(
        "1.02.01",
        descricao="Rendimentos",
        superior="1.02",
        dre="1.11.02",
        conta_contabil="3.1.3.01.00003",
    ),
    _categoria("0.01", descricao="Transferência", totalizadora="S", transferencia="S"),
    _categoria("9.99", descricao="Antiga", inativa="S", dre="9.99"),
]


async def _seed_client(session: AsyncSession) -> Client:
    """Cliente com as 4 colunas antigas preenchidas.

    O fallback datado da S9 sintetiza a conexão Omie em memória, então o
    caminho `resolve_capable_connection` → adaptador → `OmieClient` é o de
    produção — e o `respx` vê o request de verdade.
    """
    creator = User(
        name="Seed",
        email=f"chart-seed-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password("Senh@PlanoDeContas#1"),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    session.add(creator)
    await session.flush()

    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name="Cliente do plano de contas",
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


async def _seed_client_without_origin(session: AsyncSession) -> Client:
    """Cliente da S9: existe, e não tem origem nenhuma."""
    creator = User(
        name="Seed",
        email=f"chart-noorigin-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password("Senh@PlanoDeContas#1"),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    session.add(creator)
    await session.flush()
    client = Client(name="Sem origem", active=True, created_by=creator.id)
    session.add(client)
    await session.flush()
    return client


def _service(db: AsyncSession) -> ChartOfAccountsSyncService:
    """Cache de categorias NOVO a cada serviço — senão a contagem vaza."""
    return ChartOfAccountsSyncService(
        db,
        repository=ClientChartOfAccountsRepository(db),
        categorias_service=OmieCategoriasService(OmieCategoriasCache()),
        settings=get_settings(),
    )


async def _rows(db: AsyncSession, client_id: Any) -> dict[str, ClientChartOfAccount]:
    stmt = select(ClientChartOfAccount).where(ClientChartOfAccount.client_id == client_id)
    return {row.category_code: row for row in (await db.execute(stmt)).scalars().all()}


#: A whitelist de chaves de `props` declarada no PRD — nem uma a mais.
_WHITELIST = {"client_id", "total_categorias", "ativas", "com_destino", "com_conta_contabil"}

#: O que NÃO pode aparecer na telemetria: nome de categoria, nome de conta de
#: demonstrativo, rótulo de conta contábil, CÓDIGO de categoria (a lista deles
#: reconstituiria o plano de contas) e o nome do cliente.
_NUNCA_NA_TELEMETRIA = (
    "Vendas",
    "Rendimentos",
    "Transferência",
    "Nome da conta de demonstrativo",
    "ROTULO DA CONTA",
    "1.01.01",
    "Cliente do plano de contas",
)


async def _usage_events(db: AsyncSession, client_id: Any) -> list[UsageEvent]:
    """As linhas de `plano_contas_sincronizado` do cliente, mais antiga primeiro."""
    stmt = (
        select(UsageEvent)
        .where(
            UsageEvent.event == "plano_contas_sincronizado",
            UsageEvent.props["client_id"].astext == str(client_id),
        )
        .order_by(UsageEvent.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


@pytest.mark.integration
class TestUmUnicoCaminhoAteAOrigem:
    @respx.mock
    async def test_uma_sincronizacao_e_uma_chamada(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        result = await _service(db_session).sync(client)

        assert route.call_count == 1, (
            f"{route.call_count} chamadas à origem numa sincronização — "
            "o desenho criou um segundo caminho de leitura"
        )
        assert result.from_origin is True
        assert result.coverage.total == 4
        assert result.coverage.ativas == 3
        assert result.coverage.com_destino == 2
        assert result.coverage.com_conta_contabil == 1
        assert result.coverage.com_destino + result.coverage.sem_destino == result.coverage.ativas

    @respx.mock
    async def test_dentro_da_validade_nao_chama_a_origem(self, db_session: AsyncSession) -> None:
        """24h: a segunda sincronização serve do armazenamento LOCAL."""
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))
        service = _service(db_session)

        primeira = await service.sync(client)
        # Serviço NOVO: o cache in-memory de 6h está vazio, então quem impede a
        # chamada só pode ser a validade de 24h da PERSISTÊNCIA.
        segunda = await _service(db_session).sync(client)

        assert route.call_count == 1, "a 2ª sincronização foi à origem dentro da validade"
        assert segunda.from_origin is False
        assert segunda.synced_at == primeira.synced_at
        assert segunda.coverage == primeira.coverage

    @respx.mock
    async def test_sincronizar_agora_ignora_a_validade(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)
        forcada = await _service(db_session).sync(client, force=True)

        assert route.call_count == 2
        assert forcada.from_origin is True

    @respx.mock
    async def test_force_ignora_tambem_o_cache_de_seis_horas(
        self, db_session: AsyncSession
    ) -> None:
        """No MESMO serviço (cache in-memory quente), `force` rebusca.

        É o cenário real de "criei a categoria no Omie agora": sem invalidar o
        cache de 6h, "Sincronizar agora" devolveria o catálogo velho e pareceria
        que o Omie não gravou.
        """
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))
        service = _service(db_session)

        await service.sync(client)
        await service.sync(client, force=True)

        assert route.call_count == 2


@pytest.mark.integration
class TestOQueAOrigemDiz:
    @respx.mock
    async def test_sem_destino_declarado_nao_e_inferido(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)

        rows = await _rows(db_session, client.id)
        transferencia = rows["0.01"]
        assert transferencia.dre_code is None, "destino foi INFERIDO para uma transferência"
        assert transferencia.transferencia is True
        assert transferencia.totalizadora is True
        assert transferencia.parent_code is None
        assert rows["1.01.01"].dre_code == "1.01.01"
        assert rows["1.01.01"].parent_code == "1.01"
        assert rows["1.02.01"].conta_contabil_code == "3.1.3.01.00003"

    @respx.mock
    async def test_nenhum_nome_de_categoria_vai_para_o_banco(
        self, db_session: AsyncSession
    ) -> None:
        """§4.5 provada no dado GRAVADO, não só no modelo."""
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)

        gravado = {
            valor
            for row in (await _rows(db_session, client.id)).values()
            for valor in (
                row.category_code,
                row.parent_code,
                row.dre_code,
                row.dre_sign,
                row.conta_contabil_code,
                row.status,
            )
        }
        for nome in (
            "Vendas",
            "Rendimentos",
            "Transferência",
            "Nome da conta de demonstrativo",
            "ROTULO DA CONTA",
        ):
            assert nome not in gravado, f"nome vazou para o banco: {nome!r}"

    @respx.mock
    async def test_categoria_inativa_e_gravada_como_inativa(self, db_session: AsyncSession) -> None:
        """Inativa NÃO some: ela existe no plano de contas e pode ter de-para."""
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)

        rows = await _rows(db_session, client.id)
        assert rows["9.99"].status == ChartOfAccountsStatus.INATIVA.value
        assert rows["1.01.01"].status == ChartOfAccountsStatus.ATIVA.value

    @respx.mock
    async def test_categoria_que_some_da_origem_vira_ausente_e_nao_e_apagada(
        self, db_session: AsyncSession
    ) -> None:
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL)
        route.mock(return_value=_omie_response(_CATALOGO))
        await _service(db_session).sync(client)

        # Segunda passada, com `1.01.01` fora do catálogo.
        sem_a_primeira = [c for c in _CATALOGO if c["codigo"] != "1.01.01"]
        route.mock(return_value=_omie_response(sem_a_primeira))
        result = await _service(db_session).sync(client, force=True)

        rows = await _rows(db_session, client.id)
        assert "1.01.01" in rows, "a categoria foi APAGADA — pode haver de-para apontando p/ ela"
        assert rows["1.01.01"].status == ChartOfAccountsStatus.AUSENTE_NA_ORIGEM.value
        assert rows["1.01.01"].dre_code == "1.01.01", "o vínculo antigo continua legível"
        assert result.marked_absent == 1
        # Ausente sai da cobertura das ATIVAS — é o denominador da métrica.
        assert result.coverage.total == 4
        assert result.coverage.ativas == 2

    @respx.mock
    async def test_categoria_que_volta_a_existir_volta_a_valer(
        self, db_session: AsyncSession
    ) -> None:
        """O inverso do teste acima: o upsert reabilita quem reapareceu."""
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL)
        route.mock(return_value=_omie_response([c for c in _CATALOGO if c["codigo"] != "1.01.01"]))
        await _service(db_session).sync(client)

        route.mock(return_value=_omie_response(_CATALOGO))
        await _service(db_session).sync(client, force=True)

        rows = await _rows(db_session, client.id)
        assert rows["1.01.01"].status == ChartOfAccountsStatus.ATIVA.value

    @respx.mock
    async def test_duas_sincronizacoes_iguais_nao_duplicam(self, db_session: AsyncSession) -> None:
        """A idempotência é da UNIQUE `(client_id, category_code)`."""
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)
        await _service(db_session).sync(client, force=True)

        assert len(await _rows(db_session, client.id)) == 4

    @respx.mock
    async def test_codigo_repetido_no_mesmo_lote_nao_derruba_a_sincronizacao(
        self, db_session: AsyncSession
    ) -> None:
        """Página repetida por instabilidade do fornecedor não é problema NOSSO.

        `ON CONFLICT DO UPDATE` com a mesma chave duas vezes no MESMO comando é
        erro do Postgres — sem a deduplicação, a sincronização inteira morreria.
        """
        client = await _seed_client(db_session)
        duplicado = [*_CATALOGO, _categoria("1.01.01", descricao="Vendas", dre="1.01.01")]
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(duplicado))

        result = await _service(db_session).sync(client)

        assert result.coverage.total == 4


@pytest.mark.integration
class TestFalhaPreservaOQueJaHavia:
    @respx.mock
    async def test_fault_da_origem_preserva_a_ultima_sincronizacao(
        self, db_session: AsyncSession
    ) -> None:
        """A Omie responde erro com HTTP 200 — e nada pode ser apagado por isso."""
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL)
        route.mock(return_value=_omie_response(_CATALOGO))
        boa = await _service(db_session).sync(client)

        route.mock(return_value=_omie_fault("SOAP-ENV:Client-101", "Erro do fornecedor"))
        # `SOAP-ENV:Client-101` é fault de AUTENTICAÇÃO: o client Omie o classifica
        # como `OmieAuthError`, não `OmieFaultError` (validação humana da S10).
        with pytest.raises((OmieFaultError, OmieAuthError)):
            await _service(db_session).sync(client, force=True)

        rows = await _rows(db_session, client.id)
        assert len(rows) == 4, "a falha apagou linhas da última sincronização válida"
        assert all(
            row.status != ChartOfAccountsStatus.AUSENTE_NA_ORIGEM.value for row in rows.values()
        )

        repo = ClientChartOfAccountsRepository(db_session)
        last_ok, last_failure = await repo.get_sync_state(client.id)
        assert last_ok == boa.synced_at, "o carimbo do último SUCESSO foi sobrescrito"
        assert last_failure is not None, "a falha não foi registrada"
        assert last_failure > boa.synced_at

    @respx.mock
    async def test_falha_na_primeira_sincronizacao_nao_grava_nada(
        self, db_session: AsyncSession
    ) -> None:
        """Nada pela metade: o erro acontece ANTES de qualquer escrita de linha."""
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(
            return_value=_omie_fault("SOAP-ENV:Client-5113", "Chave de acesso invalida")
        )

        with pytest.raises((OmieFaultError, OmieAuthError)):
            await _service(db_session).sync(client)

        assert await _rows(db_session, client.id) == {}
        last_ok, last_failure = await ClientChartOfAccountsRepository(db_session).get_sync_state(
            client.id
        )
        assert last_ok is None
        assert last_failure is not None

    @respx.mock
    async def test_sucesso_depois_de_falha_limpa_o_aviso(self, db_session: AsyncSession) -> None:
        """ "Falhou" é sobre a ÚLTIMA tentativa — senão o aviso fica pendurado."""
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL)
        route.mock(return_value=_omie_fault("SOAP-ENV:Client-101", "Erro"))
        # `SOAP-ENV:Client-101` é fault de AUTENTICAÇÃO: o client Omie o classifica
        # como `OmieAuthError`, não `OmieFaultError` (validação humana da S10).
        with pytest.raises((OmieFaultError, OmieAuthError)):
            await _service(db_session).sync(client)

        route.mock(return_value=_omie_response(_CATALOGO))
        await _service(db_session).sync(client)

        last_ok, last_failure = await ClientChartOfAccountsRepository(db_session).get_sync_state(
            client.id
        )
        assert last_ok is not None
        assert last_failure is None

    @respx.mock
    async def test_cliente_sem_conexao_capaz_recebe_409_da_taxonomia(
        self, db_session: AsyncSession
    ) -> None:
        """409 (estado esperado da configuração), nunca 5xx — e sem marcar falha.

        Não é a origem que falhou: é o cliente que não tem uma. Carimbar
        `sync_failed_at` aqui poria um aviso de erro numa tela cujo estado certo
        é "conecte uma origem".
        """
        client = await _seed_client_without_origin(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL)

        with pytest.raises(NoOriginConnectionError) as exc:
            await _service(db_session).sync(client)

        assert exc.value.status_code == 409
        assert exc.value.code == ErrorCode.SEM_CONEXAO
        assert route.call_count == 0, "tentou ir à rede sem origem configurada"
        last_ok, last_failure = await ClientChartOfAccountsRepository(db_session).get_sync_state(
            client.id
        )
        assert last_ok is None
        assert last_failure is None


@pytest.mark.integration
class TestValidadeDeVinteEQuatroHoras:
    @respx.mock
    async def test_carimbo_vencido_rebusca(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))
        await _service(db_session).sync(client)

        # Envelhece o carimbo em 25h — o TTL é 24h.
        await ClientChartOfAccountsRepository(db_session).mark_sync_succeeded(
            client.id, at=datetime.now(UTC) - timedelta(hours=25)
        )

        result = await _service(db_session).sync(client)

        assert route.call_count == 2
        assert result.from_origin is True

    @respx.mock
    async def test_origem_sem_categoria_nenhuma_ainda_respeita_o_ttl(
        self, db_session: AsyncSession
    ) -> None:
        """Cliente sem categorias não pode bater a origem a cada abertura de tela.

        É a armadilha que o `clients.omie_accounts_synced_at` já documenta: com
        o carimbo derivado de `MAX(synced_at)` das linhas, zero linha deixaria o
        MAX em NULL e o TTL nunca dispararia.
        """
        client = await _seed_client(db_session)
        route = respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response([]))

        primeira = await _service(db_session).sync(client)
        segunda = await _service(db_session).sync(client)

        assert route.call_count == 1
        assert primeira.coverage.total == 0
        assert segunda.from_origin is False


@pytest.mark.integration
class TestMetricaDaSprint:
    """BACK 10.4 — `plano_contas_sincronizado`, o evento sem o qual a sprint é
    inverificável.

    Baseline declarado: **0%** — não porque não se lia categoria, mas porque
    nada era persistido com destino. Alvo: **≥ 70% das ativas com destino**.
    A fórmula lê a ÚLTIMA linha por `client_id` no período.
    """

    @respx.mock
    async def test_n_sincronizacoes_geram_n_linhas(self, db_session: AsyncSession) -> None:
        """Sem dedup, e é requisito: a fórmula lê a ÚLTIMA linha por cliente.

        Se o evento entrasse na allow-list de dedup, a 2ª sincronização em
        diante sumiria e a leitura D+30 mediria a foto do primeiro dia para
        sempre — invisível, porque a linha existe.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        for _ in range(3):
            await _service(db_session).sync(client, force=True)

        eventos = await _usage_events(db_session, client.id)
        assert len(eventos) == 3, f"{len(eventos)} linhas para 3 sincronizações"

    @respx.mock
    async def test_props_sao_exatamente_a_whitelist_e_so_contagens(
        self, db_session: AsyncSession
    ) -> None:
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)

        props = (await _usage_events(db_session, client.id))[0].props
        assert set(props) == _WHITELIST
        assert props["client_id"] == str(client.id)
        assert props["total_categorias"] == 4
        assert props["ativas"] == 3
        assert props["com_destino"] == 2
        assert props["com_conta_contabil"] == 1

    @respx.mock
    async def test_nenhum_nome_de_categoria_nem_pii_nas_props(
        self, db_session: AsyncSession
    ) -> None:
        """O sink de métrica não pode reconstituir o desenho contábil do cliente."""
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)

        props = (await _usage_events(db_session, client.id))[0].props
        texto = json.dumps(props, ensure_ascii=False)
        for proibido in _NUNCA_NA_TELEMETRIA:
            assert proibido not in texto, f"vazou para a telemetria: {proibido!r}"
        # Toda prop é número, salvo o id do tenant.
        for chave, valor in props.items():
            if chave != "client_id":
                assert isinstance(valor, int), f"{chave} não é contagem"

    @respx.mock
    async def test_sincronizacao_dentro_da_validade_nao_emite(
        self, db_session: AsyncSession
    ) -> None:
        """O evento marca o FATO "foi à origem e persistiu", não "abriram a tela".

        Emitir no caminho servido do local inflaria o denominador com repetições
        idênticas e faria a leitura D+30 parecer movimento onde não houve.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        await _service(db_session).sync(client)
        await _service(db_session).sync(client)

        assert len(await _usage_events(db_session, client.id)) == 1

    @respx.mock
    async def test_falha_da_origem_nao_emite(self, db_session: AsyncSession) -> None:
        """Só sincronização BEM-SUCEDIDA entra na métrica."""
        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(
            return_value=_omie_fault("SOAP-ENV:Client-101", "Erro")
        )

        # `SOAP-ENV:Client-101` é fault de AUTENTICAÇÃO: o client Omie o classifica
        # como `OmieAuthError`, não `OmieFaultError` (validação humana da S10).
        with pytest.raises((OmieFaultError, OmieAuthError)):
            await _service(db_session).sync(client)

        assert await _usage_events(db_session, client.id) == []

    @respx.mock
    async def test_falha_da_telemetria_nao_derruba_nem_reverte_a_sincronizacao(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fail-soft de verdade: o plano de contas fica gravado mesmo assim.

        O `emit` engole a exceção e loga; o SAVEPOINT do repository impede que
        o erro marque a transação como abortada e leve junto as linhas que
        acabaram de ser escritas.
        """

        async def explode(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("sink de métrica fora do ar")

        monkeypatch.setattr(UsageEventRepository, "insert_ignore_duplicate", explode)

        client = await _seed_client(db_session)
        respx.post(OMIE_CATEGORIAS_URL).mock(return_value=_omie_response(_CATALOGO))

        result = await _service(db_session).sync(client)

        assert result.from_origin is True
        assert result.coverage.total == 4
        assert len(await _rows(db_session, client.id)) == 4
        last_ok, _ = await ClientChartOfAccountsRepository(db_session).get_sync_state(client.id)
        assert last_ok is not None, "a telemetria reverteu o carimbo da sincronização"
