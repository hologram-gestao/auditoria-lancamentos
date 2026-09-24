"""A ingestão da carteira, ponta a ponta contra a origem e o banco (BACK 11.2).

O `respx` intercepta o TRANSPORTE, então o caminho exercitado é o de produção:
fallback datado da S9 sintetiza a conexão → adaptador → `OmieClient` → HTTP. É o
que permite afirmar três coisas que um mock de cliente não afirmaria:

  - **os filtros que saem no `param`**: a carteira NÃO manda
    `filtrar_conta_corrente` nem `filtrar_por_data_*` (o request é inspecionado);
  - **quantas chamadas** a origem recebe, e que nenhuma é concorrente;
  - que um título vencido há mais de 90 dias **entra** na carteira.

Além disso: os três 409 da taxonomia da S9, e a prova de que falha no meio
preserva a carteira anterior com os dois relógios no estado certo.
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
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import (
    ErrorCode,
    NoOriginConnectionError,
    OmieFaultError,
    OriginCapabilityMissingError,
)
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientTitle,
    OmieAccountCache,
    TitleStatus,
    TitleType,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.db.models.client_connection import ProviderType
from app.integrations.omie.client_locks import OriginClientLocks
from app.integrations.providers import omie_adapter, registry
from app.integrations.providers.base import Capability
from app.modules.client_titles.repository import ClientTitlesRepository
from app.modules.client_titles.service import ClientTitlesSyncService
from app.modules.clients.repository import ClientRepository
from app.modules.usage_events.schemas import UsageEventName

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

OMIE_PAGAR_URL = "https://app.omie.com.br/api/v1/financas/contapagar/"
OMIE_RECEBER_URL = "https://app.omie.com.br/api/v1/financas/contareceber/"

FAKE_APP_KEY = "titles-app-key-12345"
FAKE_APP_SECRET = "titles-app-secret-67890"

#: "Hoje" de referência dos dados plantados. Os vencimentos abaixo são escolhidos
#: contra ele, não contra o calendário de quem roda o teste.
HOJE = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _no_inter_call_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zera a pausa anti-rate-limit: 4 chamadas x 1,5 s por teste é gate mais
    lento sem prova nenhuma a mais. A serialização é provada pelo lock."""
    monkeypatch.setattr(omie_adapter, "_INTER_CALL_DELAY_SECONDS", 0)


def _titulo(
    omie_id: int,
    *,
    vencimento: date,
    valor: str = "100.00",
    conta: int = 2617722760,
    categoria: str = "2.04.94",
    fornecedor: int = 2624256082,
    documento: str = "00123/A",
    situacao: str = "ATRASADO",
    observacao: str = "COMPRA NO FORNECEDOR ACME LTDA",
) -> dict[str, Any]:
    """Um título na FORMA da resposta real, incluindo `observacao`.

    A `observacao` vai preenchida de propósito: ela carrega nome de fornecedor, e
    um dos testes prova que ela **não** chega ao banco.
    """
    return {
        "codigo_lancamento_omie": omie_id,
        "data_vencimento": vencimento.strftime("%d/%m/%Y"),
        "valor_documento": float(Decimal(valor)),
        "codigo_cliente_fornecedor": fornecedor,
        "codigo_categoria": categoria,
        "id_conta_corrente": conta,
        "numero_documento": documento,
        "status_titulo": situacao,
        "observacao": observacao,
    }


def _envelope(key: str, items: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "pagina": 1,
            "total_de_paginas": 1,
            "registros": len(items),
            "total_de_registros": len(items),
            key: items,
        },
    )


def _fault(code: str, message: str) -> httpx.Response:
    """A Omie responde erro com **HTTP 200** (§6.3) — o modo que mais engana."""
    return httpx.Response(200, json={"faultcode": code, "faultstring": message})


async def _seed_client(session: AsyncSession, *, name: str = "Cliente carteira") -> Client:
    """Cliente com as 4 colunas antigas preenchidas.

    O fallback datado da S9 sintetiza a conexão Omie em memória, então o caminho
    `resolve_capable_connection` → adaptador → `OmieClient` é o de produção — e o
    `respx` vê o request de verdade.
    """
    creator = User(
        name="Seed",
        email=f"titles-seed-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password("Senh@Carteira#1"),
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


async def _seed_client_without_origin(session: AsyncSession) -> Client:
    """Cliente da S9: existe, e não tem origem nenhuma."""
    creator = User(
        name="Seed",
        email=f"titles-noorigin-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password("Senh@Carteira#1"),
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


def _service(
    db: AsyncSession, *, locks: OriginClientLocks | None = None
) -> ClientTitlesSyncService:
    return ClientTitlesSyncService(
        db,
        repository=ClientTitlesRepository(db),
        clients=ClientRepository(db),
        settings=get_settings(),
        locks=locks or OriginClientLocks(),
    )


async def _titles(db: AsyncSession, client_id: Any) -> dict[str, ClientTitle]:
    repo = ClientTitlesRepository(db)
    rows, _ = await repo.list_for_client(client_id, today=HOJE, limit=500, offset=0)
    return {row.external_id: row for row in rows}


class TestIngestaoSemRecorte:
    @respx.mock
    async def test_traz_todas_as_contas_sem_competencia_e_persiste(
        self, db_session: AsyncSession
    ) -> None:
        client = await _seed_client(db_session)
        pagar = respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro", [_titulo(1001, vencimento=date(2026, 5, 27))]
            )
        )
        receber = respx.post(OMIE_RECEBER_URL).mock(
            return_value=_envelope(
                "conta_receber_cadastro", [_titulo(2001, vencimento=date(2026, 10, 30))]
            )
        )

        result = await _service(db_session).sync(client)

        assert result.total == 2
        assert result.titulos_pagar == 1
        assert result.titulos_receber == 1
        rows = await _titles(db_session, client.id)
        assert set(rows) == {"1001", "2001"}
        assert rows["1001"].title_type == TitleType.A_PAGAR.value
        assert rows["2001"].title_type == TitleType.A_RECEBER.value
        # 2 status x 2 cadastros = 4 chamadas, duas em cada endpoint.
        assert pagar.call_count == 2
        assert receber.call_count == 2

    @respx.mock
    async def test_o_param_enviado_nao_tem_filtro_de_conta_nem_de_data(
        self, db_session: AsyncSession
    ) -> None:
        """O recorte é o coração do R1 — e é observável no request.

        Se alguém reintroduzir o filtro de competência aqui, a carteira volta a
        perder o título de quatro meses atrás e nenhum outro teste percebe.
        """
        client = await _seed_client(db_session)
        route = respx.post(OMIE_PAGAR_URL).mock(return_value=_envelope("conta_pagar_cadastro", []))
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        await _service(db_session).sync(client)

        assert route.call_count == 2
        for call in route.calls:
            param = json.loads(call.request.content)["param"][0]
            assert "filtrar_conta_corrente" not in param
            assert "filtrar_por_data_de" not in param
            assert "filtrar_por_data_ate" not in param
            assert param["filtrar_por_status"] in {"ATRASADO", "AVENCER"}

    @respx.mock
    async def test_titulo_vencido_ha_mais_de_90_dias_entra_na_carteira(
        self, db_session: AsyncSession
    ) -> None:
        """A diferença que justifica a sprint, afirmada contra o banco.

        O vencimento de 27/05/2026 está 120 dias atrás de 24/09/2026 e **fora** da
        janela que a conciliação do mês corrente usaria (01/09 a 30/09).
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro", [_titulo(4242, vencimento=date(2026, 5, 27))]
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        result = await _service(db_session).sync(client)

        rows = await _titles(db_session, client.id)
        assert rows["4242"].due_date == date(2026, 5, 27)
        assert (HOJE - rows["4242"].due_date).days == 120
        janela_da_conciliacao = (date(2026, 9, 1), date(2026, 9, 30))
        assert not (janela_da_conciliacao[0] <= rows["4242"].due_date <= janela_da_conciliacao[1])
        assert result.vencidos == 1
        assert result.mais_antigo_dias >= 120

    @respx.mock
    async def test_a_observacao_da_origem_nao_chega_ao_banco(
        self, db_session: AsyncSession
    ) -> None:
        """§4.5 por VALOR, não por schema.

        A `observacao` da Omie ecoa nome de fornecedor. A varredura é sobre os
        valores reais gravados, que é o que pega alguém enfiando texto livre num
        campo de código.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro",
                [_titulo(1, vencimento=date(2026, 6, 1), observacao="PAGTO FORNECEDOR ACME LTDA")],
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        await _service(db_session).sync(client)

        row = (await _titles(db_session, client.id))["1"]
        gravado = [str(v) for v in row.__dict__.values() if v is not None]
        assert not any("ACME" in valor.upper() for valor in gravado)

    @respx.mock
    async def test_codigos_grandes_da_origem_cabem_no_banco(self, db_session: AsyncSession) -> None:
        """`2624256082` e `2617722760` — acima do teto de INTEGER.

        Com `Integer` no lugar de `BigInteger`, esta sincronização estoura no
        `INSERT`.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro", [_titulo(1, vencimento=date(2026, 6, 1))]
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        await _service(db_session).sync(client)

        row = (await _titles(db_session, client.id))["1"]
        assert row.supplier_code == 2624256082
        assert row.omie_conta_id == 2617722760


class TestRamoBQuandoAOrigemExigeAConta:
    @respx.mock
    async def test_recusa_5001_dispara_iteracao_pelas_contas_cacheadas(
        self, db_session: AsyncSession
    ) -> None:
        """Ramo (b) do R1, contra o transporte.

        A primeira chamada (sem conta) volta `faultstring` 5001 — HTTP 200 —, e a
        ingestão passa a iterar as contas que o cache do cliente já conhece.
        Nenhuma chamada nova à origem é feita para DESCOBRIR as contas: isso
        custaria justamente a requisição que o ramo (a) tenta economizar.
        """
        client = await _seed_client(db_session)
        db_session.add(
            OmieAccountCache(
                client_id=client.id,
                omie_conta_id=777,
                name="Conta Itau",
                bank_name="Itau",
                account_type="CC",
            )
        )
        await db_session.flush()

        def _responder(request: httpx.Request) -> httpx.Response:
            param = json.loads(request.content)["param"][0]
            if "filtrar_conta_corrente" not in param:
                return _fault("SOAP-ENV:Client-5001", "Tag nao faz parte da estrutura")
            return _envelope(
                "conta_pagar_cadastro",
                [_titulo(5001, vencimento=date(2026, 7, 1), conta=777)],
            )

        pagar = respx.post(OMIE_PAGAR_URL).mock(side_effect=_responder)
        respx.post(OMIE_RECEBER_URL).mock(side_effect=_responder)

        result = await _service(db_session).sync(client)

        assert result.total == 1
        rows = await _titles(db_session, client.id)
        assert set(rows) == {"5001"}
        assert rows["5001"].omie_conta_id == 777
        # a 1ª chamada (recusada) + as 2 por conta de cada status
        assert pagar.call_count >= 2

    @respx.mock
    async def test_falha_que_nao_e_5001_propaga_sem_iterar_contas(
        self, db_session: AsyncSession
    ) -> None:
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_fault("SOAP-ENV:Client-9999", "Instabilidade generica")
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        with pytest.raises(OmieFaultError):
            await _service(db_session).sync(client)


class TestTaxonomiaDeOrigem:
    async def test_cliente_sem_conexao_e_409_sem_conexao(self, db_session: AsyncSession) -> None:
        client = await _seed_client_without_origin(db_session)
        with pytest.raises(NoOriginConnectionError) as exc:
            await _service(db_session).sync(client)
        assert exc.value.status_code == 409
        assert exc.value.code is ErrorCode.SEM_CONEXAO

    async def test_provedor_sem_a_capacidade_e_409_capacidade_ausente(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provedor que não declara a capacidade não é 5xx nem tentativa.

        `capacidade_ausente` é o caso em que **não há nada a consertar** — e é
        por isso que a mensagem não manda reconectar.
        """
        client = await _seed_client(db_session)
        sem_carteira = omie_adapter.OMIE_CAPABILITIES - {Capability.LISTAR_TITULOS_EM_ABERTO}
        # O predicado da 09.2 consulta `capabilities_for`, que lê ESTE mapa —
        # então é ele que precisa deixar de declarar a capacidade.
        monkeypatch.setitem(registry._CAPABILITIES_BY_TYPE, ProviderType.OMIE.value, sem_carteira)

        with pytest.raises(OriginCapabilityMissingError) as exc:
            await _service(db_session).sync(client)
        assert exc.value.status_code == 409
        assert exc.value.code is ErrorCode.CAPACIDADE_AUSENTE

    async def test_os_409_nao_carimbam_falha_de_sincronizacao(
        self, db_session: AsyncSession
    ) -> None:
        """Não é a origem que falhou — é a configuração que não permite tentar.

        Carimbar `titles_sync_failed_at` aqui poria na tela "a sincronização
        falhou" para um cliente que nunca conectou nada.
        """
        client = await _seed_client_without_origin(db_session)
        repo = ClientTitlesRepository(db_session)

        with pytest.raises(NoOriginConnectionError):
            await _service(db_session).sync(client)

        assert await repo.get_sync_state(client.id) == (None, None)


class TestFalhaPreservaACarteiraAnterior:
    @respx.mock
    async def test_falha_no_meio_preserva_linhas_e_carimbos(self, db_session: AsyncSession) -> None:
        """R1: "nunca deixar carteira parcial visível como se fosse completa".

        Primeiro ciclo íntegro planta duas linhas e carimba sucesso. O segundo
        falha: as duas linhas continuam lá com o MESMO status, `titles_synced_at`
        continua no carimbo antigo e só `titles_sync_failed_at` muda.
        """
        client = await _seed_client(db_session)
        repo = ClientTitlesRepository(db_session)

        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro",
                [
                    _titulo(1, vencimento=date(2026, 6, 1)),
                    _titulo(2, vencimento=date(2026, 7, 1)),
                ],
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))
        await _service(db_session).sync(client)

        antes_linhas = await repo.count_for_client(client.id)
        primeiro_ok, primeira_falha = await repo.get_sync_state(client.id)
        assert primeiro_ok is not None
        assert primeira_falha is None

        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_fault("SOAP-ENV:Client-9999", "Instabilidade")
        )
        with pytest.raises(OmieFaultError):
            await _service(db_session).sync(client)

        assert await repo.count_for_client(client.id) == antes_linhas
        depois_ok, depois_falha = await repo.get_sync_state(client.id)
        assert depois_ok == primeiro_ok, "a falha NUNCA toca o carimbo de sucesso"
        assert depois_falha is not None

        # e nenhuma linha foi fechada pela tentativa que falhou
        rows = await _titles(db_session, client.id)
        assert {r.status for r in rows.values()} == {TitleStatus.EM_ABERTO.value}

    @respx.mock
    async def test_lista_vazia_da_origem_e_carteira_vazia_de_verdade(
        self, db_session: AsyncSession
    ) -> None:
        """`[]` significa "sem título em aberto", não "a leitura falhou".

        Quem converte erro do fornecedor em exceção é o `OmieClient` (a Omie
        responde HTTP 200 com `faultstring`), então tratar `[]` como falha
        fecharia a carteira inteira de quem teve uma credencial expirar.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(return_value=_envelope("conta_pagar_cadastro", []))
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        result = await _service(db_session).sync(client)

        assert result.total == 0
        assert result.mais_antigo_dias == 0
        synced_at, failed_at = await ClientTitlesRepository(db_session).get_sync_state(client.id)
        assert synced_at is not None, "carteira vazia é DIFERENTE de nunca sincronizou"
        assert failed_at is None


class TestIsolamentoEntreClientes:
    @respx.mock
    async def test_sincronizar_um_cliente_nao_encosta_na_carteira_do_outro(
        self, db_session: AsyncSession
    ) -> None:
        """Os dois clientes recebem o MESMO identificador de título da origem.

        É o caso que pega um `WHERE` esquecido: sem o filtro de tenant, o ciclo
        de um fecharia o título do outro.
        """
        a = await _seed_client(db_session, name="Cliente A")
        b = await _seed_client(db_session, name="Cliente B")
        repo = ClientTitlesRepository(db_session)

        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro", [_titulo(9999, vencimento=date(2026, 6, 1))]
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))
        await _service(db_session).sync(a)
        await _service(db_session).sync(b)

        # o cliente A perde o título; o B mantém o dele, com o MESMO external_id
        respx.post(OMIE_PAGAR_URL).mock(return_value=_envelope("conta_pagar_cadastro", []))
        await _service(db_session).sync(a)

        do_a = (await _titles(db_session, a.id))["9999"]
        do_b = (await _titles(db_session, b.id))["9999"]
        assert do_a.status == TitleStatus.AUSENTE_NA_ORIGEM.value
        assert do_b.status == TitleStatus.EM_ABERTO.value
        assert await repo.count_for_client(a.id) == 1
        assert await repo.count_for_client(b.id) == 1


class TestEventoCarteiraSincronizada:
    """BACK 11.3 — a métrica da sprint, contada em LINHAS de `usage_events`.

    Sem estes testes a sprint é inverificável: a leitura D+30 lê a última linha
    por `client_id`, e um evento que não é emitido (ou que é deduplicado) faz a
    cobertura parecer congelada no primeiro dia.
    """

    @staticmethod
    async def _eventos(db: AsyncSession, client_id: Any) -> list[UsageEvent]:
        rows = await db.execute(
            select(UsageEvent)
            .where(UsageEvent.event == UsageEventName.CARTEIRA_SINCRONIZADA.value)
            .order_by(UsageEvent.created_at.asc())
        )
        return [e for e in rows.scalars().all() if e.props.get("client_id") == str(client_id)]

    @respx.mock
    async def test_duas_sincronizacoes_geram_duas_linhas(self, db_session: AsyncSession) -> None:
        """O teste que CONTA AS LINHAS.

        Se o evento entrar em `DEDUPED_EVENT_NAMES`, a segunda linha desaparece e
        este teste fica vermelho — que é o ponto. A reprovação histórica da
        Sprint 4 foi exatamente o inverso: a chave divergia em um dos três
        lugares, o fail-soft engolia, e **nada** era gravado.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro",
                [
                    _titulo(1, vencimento=date(2026, 5, 27)),
                    _titulo(2, vencimento=date(2026, 8, 20)),
                ],
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(
            return_value=_envelope(
                "conta_receber_cadastro", [_titulo(3, vencimento=date(2026, 10, 30))]
            )
        )

        await _service(db_session).sync(client)
        await _service(db_session).sync(client)

        eventos = await self._eventos(db_session, client.id)
        assert len(eventos) == 2, "cada sincronização é uma linha — o evento NÃO tem dedup"
        for evento in eventos:
            assert evento.props["titulos_pagar"] == 2
            assert evento.props["titulos_receber"] == 1
            # dois vencidos (27/05 e 20/08); o de 30/10 ainda não venceu
            assert evento.props["vencidos"] == 2

    @respx.mock
    async def test_o_payload_tem_exatamente_as_cinco_chaves_do_prd(
        self, db_session: AsyncSession
    ) -> None:
        """Afirmação sobre as CHAVES, não sobre os valores.

        É o que pega alguém acrescentando "só para diagnosticar" um campo com
        nome de devedor — e um `grep` no payload gravado confirma que nenhum nome
        atravessou.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro",
                [
                    _titulo(
                        1,
                        vencimento=date(2026, 6, 1),
                        observacao="PAGTO FORNECEDOR ACME LTDA",
                    )
                ],
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        await _service(db_session).sync(client)

        evento = (await self._eventos(db_session, client.id))[0]
        assert set(evento.props) == {
            "client_id",
            "titulos_receber",
            "titulos_pagar",
            "vencidos",
            "mais_antigo_dias",
        }
        assert "ACME" not in json.dumps(evento.props).upper()
        # e a sessão fica NULA: carteira não pertence a conciliação nenhuma
        assert evento.session_id is None

    @respx.mock
    async def test_sincronizacao_que_falha_no_meio_nao_emite(
        self, db_session: AsyncSession
    ) -> None:
        """Teste negativo do R1: carteira parcial não conta como cobertura.

        Emitir aqui inflaria o numerador com uma carteira que a plataforma não
        tem — e a métrica passaria a medir tentativas, não cobertura.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_fault("SOAP-ENV:Client-9999", "Instabilidade")
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        with pytest.raises(OmieFaultError):
            await _service(db_session).sync(client)

        assert await self._eventos(db_session, client.id) == []

    async def test_os_409_de_configuracao_tambem_nao_emitem(self, db_session: AsyncSession) -> None:
        """Cliente sem origem não tem cobertura zero — não tem medição nenhuma."""
        client = await _seed_client_without_origin(db_session)
        with pytest.raises(NoOriginConnectionError):
            await _service(db_session).sync(client)
        assert await self._eventos(db_session, client.id) == []

    @respx.mock
    async def test_carteira_vazia_emite_com_zeros(self, db_session: AsyncSession) -> None:
        """Zero é resultado aqui: o cliente foi consultado e não tem título aberto.

        "Nunca sincronizou" é outra coisa, e mora em `clients.titles_synced_at`.
        """
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(return_value=_envelope("conta_pagar_cadastro", []))
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        await _service(db_session).sync(client)

        evento = (await self._eventos(db_session, client.id))[0]
        assert evento.props["titulos_pagar"] == 0
        assert evento.props["titulos_receber"] == 0
        assert evento.props["mais_antigo_dias"] == 0

    @respx.mock
    async def test_mais_antigo_dias_e_o_maior_atraso(self, db_session: AsyncSession) -> None:
        """É o número que a reunião usa para dizer "tem coisa de 7 meses aqui"."""
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro",
                [
                    _titulo(1, vencimento=date(2026, 9, 14)),  # 10 dias
                    _titulo(2, vencimento=date(2026, 5, 27)),  # 120 dias
                ],
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        result = await _service(db_session).sync(client)

        evento = (await self._eventos(db_session, client.id))[0]
        assert evento.props["mais_antigo_dias"] == result.mais_antigo_dias
        assert evento.props["mais_antigo_dias"] >= 120


class TestIdempotencia:
    @respx.mock
    async def test_duas_sincronizacoes_seguidas_deixam_o_mesmo_estado(
        self, db_session: AsyncSession
    ) -> None:
        """O que torna o comando em lote retomável de verdade."""
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro", [_titulo(1, vencimento=date(2026, 6, 1))]
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        primeiro = await _service(db_session).sync(client)
        segundo = await _service(db_session).sync(client)

        assert (primeiro.total, primeiro.fechados) == (1, 0)
        assert (segundo.total, segundo.fechados) == (1, 0)
        assert await ClientTitlesRepository(db_session).count_for_client(client.id) == 1

    @respx.mock
    async def test_o_carimbo_de_sucesso_avanca_a_cada_ciclo(self, db_session: AsyncSession) -> None:
        """Sem TTL: quem chama decide a cadência (job diário + botão manual)."""
        client = await _seed_client(db_session)
        respx.post(OMIE_PAGAR_URL).mock(return_value=_envelope("conta_pagar_cadastro", []))
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        primeiro = await _service(db_session).sync(client)
        segundo = await _service(db_session).sync(client)
        assert segundo.synced_at >= primeiro.synced_at
        assert segundo.synced_at <= datetime.now(UTC)
