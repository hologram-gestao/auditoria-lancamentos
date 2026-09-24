"""A capacidade `listar_titulos_em_aberto` e os DOIS ramos do R1 (BACK 11.2).

Três coisas ficam travadas aqui, e a terceira é a que importa mais:

1. **o contrato** — a capacidade existe no enum fechado, no adaptador e nos dois
   mapas do registry; o DTO neutro não tem campo de nome;
2. **os dois ramos** — a listagem sem filtro de conta, e a iteração serial por
   conta quando a origem recusa a chamada sem ela (`faultstring` com HTTP 200);
3. **a EVIDÊNCIA REAL** — o ramo (a) não é palpite: a captura em
   `tests/fixtures/omie/listar_contas_pagar.request.json` é uma chamada aceita
   **sem** `filtrar_conta_corrente` e **sem** `filtrar_por_data_*`, e a resposta
   dela traz `id_conta_corrente`. Os testes afirmam isso lendo a fixture, não
   repetindo o que a doc diz — que já errou 3 vezes neste repositório.

O client do Omie é substituído por um duplo que **conta as chamadas**: o que está
sob teste é o RECORTE (quais filtros vão no `param`) e a SERIALIZAÇÃO, e os dois
são observáveis na sequência de chamadas. Um `respx` aqui provaria menos e
custaria um servidor.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import OmieAuthError, OmieFaultError, ProviderAuthError
from app.db.models.client_connection import ProviderType
from app.db.models.client_title import TitleStatus, TitleType
from app.integrations.omie.schemas import OmieTituloStatus, TituloAPagarReceber
from app.integrations.providers import omie_adapter
from app.integrations.providers.base import (
    Capability,
    OriginProvider,
    ProviderOpenTitle,
    ProviderTitleKind,
)
from app.integrations.providers.omie_adapter import OMIE_CAPABILITIES, OmieProvider
from app.integrations.providers.registry import capabilities_for
from app.modules.client_titles.schemas import client_title_row, title_status_from_situation
from app.modules.client_titles.service import TITLE_KIND_TO_TYPE

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "omie"

_CAPTURE_HINT = (
    "Fixture real ausente. Rode `uv run python -m scripts.capture_omie_fixtures` "
    "com credencial Omie autorizada (ver tests/fixtures/omie/README.md)."
)

#: O faultcode **como a Omie devolve**, com o prefixo SOAP — não o número solto.
#: Verificado no transporte em 24/09/2026 (`omie_call_fault call=ListarContasPagar
#: fault_code=SOAP-ENV:Client-5001`), e coerente com o `_AUTH_FAULT_CODES` do
#: client, que já casa `soap-env:client-101`.
_FAULT_CODE_REAL_TAG_INVALIDA = "SOAP-ENV:Client-5001"


@pytest.fixture(autouse=True)
def _no_inter_call_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zera a pausa anti-rate-limit — **só** no teste.

    `_INTER_CALL_DELAY_SECONDS` vale 1,5 s em produção porque a Omie recusa duas
    chamadas do mesmo método com a mesma credencial em sequência. Aqui ela não
    protege nada e custaria ~6 s por teste (4 chamadas), quase um minuto somado
    no gate. O que estes testes verificam é a ORDEM e a SERIALIZAÇÃO, e nenhuma
    das duas depende da duração: `test_nunca_ha_chamadas_concorrentes…` prova a
    serialização pelo pico de chamadas em voo, que continua 1 com pausa zero
    porque o `await` do duplo cede o controle do mesmo jeito.
    """
    monkeypatch.setattr(omie_adapter, "_INTER_CALL_DELAY_SECONDS", 0)


def _load(name: str) -> dict[str, Any] | None:
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return None
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


# ----------------------------------------------------------------------
# Duplo do OmieClient — conta chamadas e registra os filtros recebidos
# ----------------------------------------------------------------------


@dataclass
class _Call:
    method: str
    conta_corrente_id: int | None
    status: OmieTituloStatus


@dataclass
class _FakeOmieClient:
    """Duplo que registra CADA chamada e pode recusar a listagem sem conta.

    `reject_without_account=True` reproduz a recusa real da Omie: **HTTP 200 com
    `faultstring`**, que o `OmieClient` converte em `OmieFaultError` com o
    `fault_code` no `metadata`. É assim que o ramo (b) é disparado.
    """

    pagar: dict[int | None, list[TituloAPagarReceber]] = field(default_factory=dict)
    receber: dict[int | None, list[TituloAPagarReceber]] = field(default_factory=dict)
    reject_without_account: bool = False
    auth_error: bool = False
    calls: list[_Call] = field(default_factory=list)
    concurrent_peak: int = 0
    _in_flight: int = 0

    async def _listar(
        self,
        method: str,
        bucket: dict[int | None, list[TituloAPagarReceber]],
        conta_corrente_id: int | None,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        self._in_flight += 1
        self.concurrent_peak = max(self.concurrent_peak, self._in_flight)
        try:
            self.calls.append(_Call(method, conta_corrente_id, status))
            if self.auth_error:
                raise OmieAuthError("credencial recusada")
            if self.reject_without_account and conta_corrente_id is None:
                raise OmieFaultError(
                    "Fault em ListarContasPagar: Tag não faz parte da estrutura",
                    # ⚠️ O faultcode REAL, com o prefixo SOAP. O duplo usava
                    # `"5001"` solto e por isso este teste passava verde sobre um
                    # predicado que nunca desviava em produção (reprovação de
                    # 24/09/2026). Duplo com valor mais bonito que o da origem é
                    # um teste que prova o duplo.
                    metadata={"fault_code": _FAULT_CODE_REAL_TAG_INVALIDA},
                )
            # Cede o controle: se alguém paralelizar as chamadas, `concurrent_peak`
            # passa de 1 e o teste de serialização quebra.
            await asyncio.sleep(0)
            return bucket.get(conta_corrente_id, [])
        finally:
            self._in_flight -= 1

    async def listar_contas_pagar(
        self,
        *,
        conta_corrente_id: int | None = None,
        data_de: date | None = None,
        data_ate: date | None = None,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        assert data_de is None, "a carteira NÃO filtra por competência (R1)"
        assert data_ate is None, "a carteira NÃO filtra por competência (R1)"
        return await self._listar("pagar", self.pagar, conta_corrente_id, status)

    async def listar_contas_receber(
        self,
        *,
        conta_corrente_id: int | None = None,
        data_de: date | None = None,
        data_ate: date | None = None,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        assert data_de is None, "a carteira NÃO filtra por competência (R1)"
        assert data_ate is None, "a carteira NÃO filtra por competência (R1)"
        return await self._listar("receber", self.receber, conta_corrente_id, status)

    async def aclose(self) -> None:
        return None


def _titulo(
    omie_id: int,
    *,
    due: date,
    valor: str = "100.00",
    conta: int | None = 2617722760,
    categoria: str | None = "2.04.94",
    fornecedor: int | None = 2624256082,
    documento: str | None = "00123/A",
    situacao: str = "ATRASADO",
) -> TituloAPagarReceber:
    return TituloAPagarReceber.model_validate(
        {
            "codigo_lancamento_omie": omie_id,
            "data_vencimento": due.strftime("%d/%m/%Y"),
            "valor_documento": Decimal(valor),
            "codigo_cliente_fornecedor": fornecedor,
            "codigo_categoria": categoria,
            "id_conta_corrente": conta,
            "numero_documento": documento,
            "status_titulo": situacao,
        }
    )


def _provider_with(client: _FakeOmieClient) -> OmieProvider:
    """`OmieProvider` com o duplo no lugar do client, sem construir credencial.

    `object.__new__` de propósito: o `__init__` do adaptador constrói o client
    real a partir de `Settings`, e o que está sob teste aqui é a lógica de
    recorte — não a fábrica, que tem teste próprio desde a S9.
    """
    provider = object.__new__(OmieProvider)
    provider._client = client  # type: ignore[assignment]
    return provider


# ----------------------------------------------------------------------
# 1. O contrato
# ----------------------------------------------------------------------


class TestContratoDaCapacidade:
    def test_capacidade_esta_no_enum_fechado(self) -> None:
        assert Capability.LISTAR_TITULOS_EM_ABERTO.value == "listar_titulos_em_aberto"

    def test_o_omie_declara_a_capacidade(self) -> None:
        assert Capability.LISTAR_TITULOS_EM_ABERTO in OMIE_CAPABILITIES

    def test_o_registry_responde_a_capacidade_sem_credencial(self) -> None:
        """É por aqui que a resposta de conexão monta `capabilities` (09.3).

        O `_CAPABILITIES_BY_TYPE` do registry aponta para `OMIE_CAPABILITIES` —
        este teste é o que garante que os dois mapas continuam concordando, em
        vez de a coincidência ser suposta.
        """
        assert Capability.LISTAR_TITULOS_EM_ABERTO in capabilities_for(ProviderType.OMIE.value)

    def test_o_adaptador_cumpre_o_protocolo(self) -> None:
        """`runtime_checkable`: a operação nova entrou no Protocol E no adaptador."""
        provider = _provider_with(_FakeOmieClient())
        assert isinstance(provider, OriginProvider)
        assert hasattr(provider, "list_open_titles")

    def test_listar_lancamentos_e_capacidade_separada(self) -> None:
        """Extrato e carteira são coisas diferentes — um provedor pode ter só uma."""
        assert Capability.LISTAR_TITULOS_EM_ABERTO is not Capability.LISTAR_LANCAMENTOS


class TestDtoNeutroSoTemCodigo:
    def test_nenhum_campo_de_nome_no_dto(self) -> None:
        """§4.5: razão social e descrição de categoria são runtime, não DTO.

        `observacao` também fica fora: é texto livre em que a Omie ecoa a
        descrição da compra (§3.16), e é por aí que nome de fornecedor entraria
        no banco sem ninguém decidir isso.
        """
        campos = set(ProviderOpenTitle.model_fields)
        for proibido in (
            "name",
            "nome",
            "supplier_name",
            "nome_fornecedor",
            "razao_social",
            "description",
            "descricao",
            "category_name",
            "observacao",
            "observation",
        ):
            assert proibido not in campos, proibido

    def test_o_inventario_do_dto_e_fechado(self) -> None:
        assert set(ProviderOpenTitle.model_fields) == {
            "external_id",
            "kind",
            "due_date",
            "amount",
            "situation",
            "category_code",
            "supplier_code",
            "account_external_id",
            "document_number",
        }

    def test_valor_e_decimal_nunca_float(self) -> None:
        titulo = ProviderOpenTitle(
            external_id="1",
            kind=ProviderTitleKind.A_PAGAR,
            due_date=date(2026, 1, 1),
            amount=Decimal("10.50"),
        )
        assert isinstance(titulo.amount, Decimal)

    def test_o_vocabulario_de_tipo_bate_com_o_do_banco(self) -> None:
        """O DTO não importa modelo de banco — então a coincidência é vigiada.

        Renomear um dos dois lados sem o outro faria `client_title_row` gravar um
        valor que o CHECK do banco recusa, e o erro apareceria só na primeira
        sincronização real.
        """
        assert {k.value for k in ProviderTitleKind} == {t.value for t in TitleType}
        assert TITLE_KIND_TO_TYPE == {
            ProviderTitleKind.A_PAGAR: TitleType.A_PAGAR,
            ProviderTitleKind.A_RECEBER: TitleType.A_RECEBER,
        }


# ----------------------------------------------------------------------
# 2. Os dois ramos do R1
# ----------------------------------------------------------------------


class TestRamoASemFiltroDeConta:
    async def test_lista_sem_filtro_de_conta_e_sem_competencia(self) -> None:
        client = _FakeOmieClient(
            pagar={None: [_titulo(1, due=date(2026, 5, 1))]},
            receber={None: [_titulo(2, due=date(2026, 6, 1))]},
        )
        titles = await _provider_with(client).list_open_titles()

        assert {t.external_id for t in titles} == {"1", "2"}
        # NENHUMA chamada levou filtro de conta — é o ramo (a).
        assert all(call.conta_corrente_id is None for call in client.calls)

    async def test_as_quatro_chamadas_cobrem_os_dois_cadastros_e_os_dois_status(
        self,
    ) -> None:
        """A pagar x a receber x atrasado x a vencer. Nenhuma a mais, nenhuma a menos."""
        client = _FakeOmieClient()
        await _provider_with(client).list_open_titles()

        assert len(client.calls) == 4
        assert {(c.method, c.status) for c in client.calls} == {
            ("pagar", OmieTituloStatus.ATRASADO),
            ("receber", OmieTituloStatus.ATRASADO),
            ("pagar", OmieTituloStatus.AVENCER),
            ("receber", OmieTituloStatus.AVENCER),
        }

    async def test_o_mesmo_endpoint_nunca_e_chamado_duas_vezes_seguidas(self) -> None:
        """A colisão real da Omie é por MÉTODO por credencial (`1880`).

        Alternar PAGAR/RECEBER é a mesma proteção que a conciliação já aplica —
        copiada, não redescoberta.
        """
        client = _FakeOmieClient()
        await _provider_with(client).list_open_titles()

        metodos = [call.method for call in client.calls]
        for anterior, seguinte in itertools.pairwise(metodos):
            assert anterior != seguinte, metodos

    async def test_nunca_ha_chamadas_concorrentes_do_mesmo_metodo(self) -> None:
        """Serialização provada por observação: o pico de chamadas em voo é 1.

        `asyncio.gather` sobre estes métodos devolveria `8020`/`1880` da origem —
        e o duplo cede o controle no meio de cada chamada, então qualquer
        paralelismo faria o pico passar de 1.
        """
        client = _FakeOmieClient()
        await _provider_with(client).list_open_titles()
        assert client.concurrent_peak == 1

    async def test_titulo_vencido_ha_mais_de_90_dias_volta(self) -> None:
        """A diferença que justifica a sprint.

        A leitura da conciliação recorta `reference_month`..último dia do mês; a
        carteira não manda filtro de data nenhum (o duplo **afirma** isso), então
        o título de 27/05 volta numa ingestão de setembro.
        """
        antigo = _titulo(4242, due=date(2026, 5, 27))
        client = _FakeOmieClient(pagar={None: [antigo]})
        titles = await _provider_with(client).list_open_titles()

        assert [t.external_id for t in titles] == ["4242"]
        assert (date(2026, 9, 24) - titles[0].due_date).days > 90

    async def test_dedup_por_identificador(self) -> None:
        """Um "Atrasado" pode voltar nas duas passadas de status.

        Sem dedup, o `ON CONFLICT DO UPDATE` receberia o mesmo par
        `(cliente, identificador)` duas vezes no MESMO comando e o Postgres
        recusaria com `cannot affect row a second time` — matando a
        sincronização inteira por um problema que não é do cliente.
        """
        repetido = _titulo(7, due=date(2026, 4, 1))
        client = _FakeOmieClient(pagar={None: [repetido, repetido]})
        titles = await _provider_with(client).list_open_titles()
        assert [t.external_id for t in titles] == ["7"]


class TestRamoBIteracaoPorConta:
    async def test_origem_que_recusa_sem_conta_dispara_iteracao_serial(self) -> None:
        """A Omie recusa respondendo HTTP 200 + `faultstring` 5001.

        O resultado tem de ser o MESMO do ramo (a) — muda só o custo em
        requisições.
        """
        client = _FakeOmieClient(
            reject_without_account=True,
            pagar={111: [_titulo(1, due=date(2026, 5, 1), conta=111)]},
            receber={222: [_titulo(2, due=date(2026, 6, 1), conta=222)]},
        )
        titles = await _provider_with(client).list_open_titles(
            known_account_external_ids=["111", "222"]
        )

        assert {t.external_id for t in titles} == {"1", "2"}
        # 4 chamadas do ramo (a) tentadas (a 1ª recusa desvia) + 4 por conta.
        por_conta = [c for c in client.calls if c.conta_corrente_id is not None]
        assert {c.conta_corrente_id for c in por_conta} == {111, 222}
        assert len(por_conta) == 8

    async def test_o_ramo_b_tambem_e_serial(self) -> None:
        client = _FakeOmieClient(reject_without_account=True)
        await _provider_with(client).list_open_titles(known_account_external_ids=["1", "2", "3"])
        assert client.concurrent_peak == 1

    async def test_conta_nao_numerica_e_pulada_sem_derrubar_a_ingestao(self) -> None:
        """`nCodCC` é inteiro; cache com outro formato é dado velho, não outage."""
        client = _FakeOmieClient(
            reject_without_account=True,
            pagar={9: [_titulo(1, due=date(2026, 5, 1), conta=9)]},
        )
        titles = await _provider_with(client).list_open_titles(
            known_account_external_ids=["nao-numerica", "9"]
        )
        assert [t.external_id for t in titles] == ["1"]

    async def test_falha_que_nao_e_tag_invalida_propaga(self) -> None:
        """Só o `5001` desvia para o ramo (b).

        Tratar qualquer `faultstring` como "preciso do filtro de conta"
        transformaria credencial expirada em 78 páginas x N contas de
        requisições inúteis antes de falhar de novo.
        """
        client = _FakeOmieClient()

        async def _sempre_falha(**_: Any) -> list[TituloAPagarReceber]:
            raise OmieFaultError("Fault: instabilidade", metadata={"fault_code": "9999"})

        client.listar_contas_pagar = _sempre_falha  # type: ignore[method-assign]
        with pytest.raises(OmieFaultError):
            await _provider_with(client).list_open_titles(known_account_external_ids=["1"])
        # nenhuma iteração por conta aconteceu
        assert all(call.conta_corrente_id is None for call in client.calls)

    async def test_credencial_recusada_vira_erro_neutro(self) -> None:
        """A camada de cima não conhece `OmieAuthError` — ela conhece o contrato."""
        client = _FakeOmieClient(auth_error=True)
        with pytest.raises(ProviderAuthError):
            await _provider_with(client).list_open_titles()


class TestPredicadoDaRecusaDeTag:
    """O gatilho do ramo (b), testado DIRETO e com o valor real da origem.

    Por que um teste unitário do predicado, e não só o caso de integração: o caso
    de integração só falha para quem tem Docker, e foi exatamente por isso que o
    defeito de 24/09/2026 chegou ao review — o predicado comparava `"5001"` por
    igualdade contra `SOAP-ENV:Client-5001`, nunca dava verdadeiro, e o ramo (b)
    era código MORTO. Aqui ele roda em qualquer máquina, em microssegundos.
    """

    @staticmethod
    def _fault(code: str | None) -> OmieFaultError:
        return OmieFaultError(
            "Fault em ListarContasPagar: Tag não faz parte da estrutura",
            metadata={"fault_code": code},
        )

    def test_o_faultcode_real_da_omie_desvia(self) -> None:
        """`SOAP-ENV:Client-5001` — a forma que o transporte de fato entrega."""
        assert OmieProvider._is_tag_rejection(self._fault(_FAULT_CODE_REAL_TAG_INVALIDA)) is True

    def test_o_numero_solto_tambem_desvia(self) -> None:
        """Se a origem algum dia devolver só o número, o ramo (b) segue valendo."""
        assert OmieProvider._is_tag_rejection(self._fault("5001")) is True

    @pytest.mark.parametrize(
        "code",
        [
            "SOAP-ENV:Client-8020",  # o lock de lançamento — NÃO é "preciso da conta"
            "SOAP-ENV:Client-101",  # credencial inválida: propaga como auth
            "SOAP-ENV:Client-1880",  # rate limit por método: propaga e retenta
            "SOAP-ENV:Client-15001",  # termina em "5001" e NÃO é o 5001
            "9999",
            "",
            None,
        ],
    )
    def test_o_que_nao_e_5001_nao_desvia(self, code: str | None) -> None:
        """O predicado é ESTREITO de propósito.

        Tratar qualquer falha como "preciso do filtro de conta" gastaria 78 páginas
        x N contas por uma credencial expirada, antes de falhar de novo. O
        `15001` está na lista porque é o que um `endswith` solto deixaria passar.
        """
        assert OmieProvider._is_tag_rejection(self._fault(code)) is False


# ----------------------------------------------------------------------
# 3. A evidência REAL (fixtures capturadas)
# ----------------------------------------------------------------------


class TestEvidenciaDaCapturaReal:
    """O ramo (a) não é palpite — a captura real foi feita exatamente assim.

    Estes testes **leem a fixture**. Se um dia a captura for refeita e a Omie
    passar a exigir o filtro de conta, eles falham aqui em vez de a carteira
    silenciosamente perder títulos em produção.
    """

    def test_a_chamada_capturada_nao_tem_filtro_de_conta_nem_de_data(self) -> None:
        for name in ("listar_contas_pagar", "listar_contas_receber"):
            request = _load(f"{name}.request")
            if request is None:
                pytest.skip(_CAPTURE_HINT)
            param = request["param"]
            assert "filtrar_conta_corrente" not in param, name
            assert "filtrar_por_data_de" not in param, name
            assert "filtrar_por_data_ate" not in param, name

    def test_a_captura_sem_filtro_devolveu_paginas_de_resultado(self) -> None:
        """Prova de que a chamada foi ACEITA, não recusada com `faultstring`."""
        for name, key in (
            ("listar_contas_pagar", "conta_pagar_cadastro"),
            ("listar_contas_receber", "conta_receber_cadastro"),
        ):
            response = _load(f"{name}.response")
            if response is None:
                pytest.skip(_CAPTURE_HINT)
            assert "faultstring" not in response, name
            assert response["total_de_registros"] > 0, name
            assert isinstance(response[key], list), name
            assert response[key], name

    def test_id_conta_corrente_existe_na_resposta_real(self) -> None:
        """É o que permite persistir a conta sem iterar conta a conta.

        Se este campo não existisse, `omie_conta_id` só poderia ser preenchido
        no ramo (b) — e no ramo (a) a coluna nasceria nula para todo mundo.
        """
        for name, key in (
            ("listar_contas_pagar", "conta_pagar_cadastro"),
            ("listar_contas_receber", "conta_receber_cadastro"),
        ):
            response = _load(f"{name}.response")
            if response is None:
                pytest.skip(_CAPTURE_HINT)
            items = response[key]
            assert all("id_conta_corrente" in item for item in items), name
            parsed = [TituloAPagarReceber.model_validate(item) for item in items]
            assert all(t.id_conta_corrente is not None for t in parsed), name

    def test_os_codigos_reais_estouram_integer(self) -> None:
        """Por que `supplier_code` e `omie_conta_id` são `BigInteger` no banco."""
        response = _load("listar_contas_pagar.response")
        if response is None:
            pytest.skip(_CAPTURE_HINT)
        teto_int32 = 2_147_483_647
        items = response["conta_pagar_cadastro"]
        assert any(item["codigo_cliente_fornecedor"] > teto_int32 for item in items)
        assert any(item["id_conta_corrente"] > teto_int32 for item in items)

    def test_a_traducao_de_um_titulo_real_nao_leva_nome_nem_observacao(self) -> None:
        """Varredura por VALOR sobre a fixture inteira, não por schema.

        A checagem por valor pega o que a inspeção de campos não pega: alguém
        enfiando texto livre num campo de código.
        """
        response = _load("listar_contas_pagar.response")
        if response is None:
            pytest.skip(_CAPTURE_HINT)

        for item in response["conta_pagar_cadastro"]:
            titulo = TituloAPagarReceber.model_validate(item)
            dto = ProviderOpenTitle(
                external_id=str(titulo.codigo_lancamento_omie),
                kind=ProviderTitleKind.A_PAGAR,
                due_date=titulo.data_vencimento,
                amount=abs(titulo.valor_documento),
                situation=titulo.status_titulo,
                category_code=titulo.codigo_categoria,
                supplier_code=str(titulo.codigo_cliente_fornecedor),
                account_external_id=str(titulo.id_conta_corrente),
                document_number=titulo.numero_documento,
            )
            row = client_title_row(dto)
            observacao = item.get("observacao") or ""
            if observacao:
                assert observacao not in row.values()
            # a linha gravada não carrega o rótulo de situação do terceiro
            assert titulo.status_titulo not in row.values()


# ----------------------------------------------------------------------
# 4. A tradução para linha da carteira
# ----------------------------------------------------------------------


class TestTraducaoParaLinha:
    def test_as_chaves_sao_exatamente_as_colunas_do_upsert(self) -> None:
        """Chave a mais estoura o `INSERT`; a menos deixa o default no lugar do dado."""
        row = client_title_row(
            ProviderOpenTitle(
                external_id="99",
                kind=ProviderTitleKind.A_RECEBER,
                due_date=date(2026, 3, 1),
                amount=Decimal("1.00"),
            )
        )
        assert set(row) == {
            "external_id",
            "title_type",
            "due_date",
            "amount",
            "status",
            "category_code",
            "supplier_code",
            "omie_conta_id",
            "document_number",
        }

    def test_titulo_lido_do_conjunto_aberto_nasce_em_aberto(self) -> None:
        assert title_status_from_situation("ATRASADO") is TitleStatus.EM_ABERTO
        assert title_status_from_situation("A VENCER") is TitleStatus.EM_ABERTO
        assert title_status_from_situation("") is TitleStatus.EM_ABERTO

    def test_liquidado_so_sai_dos_rotulos_declarados(self) -> None:
        """Rótulo desconhecido NÃO vira liquidação.

        Inferir pagamento de um rótulo que a plataforma não conhece seria
        afirmar uma quitação que ninguém verificou.
        """
        for rotulo in ("PAGO", "pago", " RECEBIDO ", "LIQUIDADO"):
            assert title_status_from_situation(rotulo) is TitleStatus.LIQUIDADO
        for rotulo in ("CANCELADO", "PAGTO_PARCIAL", "qualquer coisa"):
            assert title_status_from_situation(rotulo) is TitleStatus.EM_ABERTO

    def test_codigo_nao_numerico_vira_none_em_vez_de_derrubar(self) -> None:
        row = client_title_row(
            ProviderOpenTitle(
                external_id="1",
                kind=ProviderTitleKind.A_PAGAR,
                due_date=date(2026, 1, 1),
                amount=Decimal("1.00"),
                supplier_code="abc",
                account_external_id="",
            )
        )
        assert row["supplier_code"] is None
        assert row["omie_conta_id"] is None

    def test_valor_vai_em_absoluto_sem_sinal(self) -> None:
        """`kind` já diz se é obrigação ou direito.

        Dois campos dizendo a mesma coisa é um deles podendo discordar — e a
        soma do aging passaria a depender de qual o consumidor olhou.
        """
        row = client_title_row(
            ProviderOpenTitle(
                external_id="1",
                kind=ProviderTitleKind.A_PAGAR,
                due_date=date(2026, 1, 1),
                amount=Decimal("250.00"),
            )
        )
        assert row["amount"] == Decimal("250.00")
