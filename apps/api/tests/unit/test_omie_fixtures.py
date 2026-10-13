"""Testes contra fixtures REAIS da Omie (BACK 02.7 · leitura · BACK 07.1 · escrita).

⚠️ **S-3 (ASSUMIDA — NÃO TESTADA / RISCO):** os nomes de campo da Omie podem
estar errados (já quebrou em prod). Estes testes rodam o schema ATUAL contra a
RESPOSTA REAL capturada — se divergir, FALHAM (o "teste negativo" que registra a
divergência). **Mock escrito à mão não conta** (confirmaria a invenção).

⚠️ **S-1 (AINDA ABERTA):** a FORMA aninhada do contrato de ESCRITA
(`IncluirLancCC`) foi corroborada pela RECUSA real do formato plano em
21/08/2026; nomes internos de `cabecalho`/`detalhes`, obrigatoriedade,
representação do sinal (não há `cNatureza` na escrita) e unicidade de
`cCodIntLanc` seguem pendentes de uma captura ACEITA. Os testes de escrita
abaixo só ficam verdes contra a fixture real gravada por
`scripts/capture_omie_fixtures.py` com `OMIE_CAPTURE_ALLOW_WRITE=1`.

Enquanto não houver fixture real (captura exige credencial Omie autorizada +
rede — ver `tests/fixtures/omie/README.md`), cada teste é **skipado** com uma
mensagem que aponta o script de captura. NÃO fabricamos fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.integrations.omie.client import is_extrato_summary_row
from app.integrations.omie.schemas import (
    CategoriaOmie,
    ContaCorrente,
    IncluirLancCCRequest,
    IncluirLancCCResponse,
    LancamentoExtrato,
    TituloAPagarReceber,
)

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "omie"

_CAPTURE_HINT = (
    "Fixture real ausente. Rode `uv run python -m scripts.capture_omie_fixtures` "
    "com credencial Omie autorizada (ver tests/fixtures/omie/README.md). S-3."
)

_WRITE_CAPTURE_HINT = (
    "Fixture real de ESCRITA ausente (S-1: nomes internos de cabecalho/"
    "detalhes, obrigatoriedade, representação do sinal e unicidade de "
    "cCodIntLanc do IncluirLancCC seguem SEM captura ACEITA). Rode "
    "`OMIE_CAPTURE_ALLOW_WRITE=1 uv run python -m "
    "scripts.capture_omie_fixtures` com credencial Omie autorizada e exclua o "
    "lançamento depois (ver tests/fixtures/omie/README.md)."
)

# Chaves de credencial que NUNCA podem aparecer numa fixture commitada.
_SECRET_KEYS = ("app_key", "app_secret")


def _load_json(name: str) -> dict[str, Any] | None:
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"fixture {name}.json não é um objeto JSON"
    return loaded


def _load_response(name: str) -> dict[str, Any] | None:
    return _load_json(f"{name}.response")


def _flatten_param_keys(param: dict[str, Any], prefix: str = "") -> Iterator[str]:
    """Achata as chaves de um `param` aninhado em caminhos pontilhados.

    Mesmo formato de `IncluirLancCCRequest.omie_param_aliases()`
    (`cabecalho`, `cabecalho.nCodCC`, ...) — é o que permite comparar o
    request capturado com o DTO aninhado chave a chave.
    """
    for key, value in param.items():
        path = f"{prefix}{key}"
        yield path
        if isinstance(value, dict):
            yield from _flatten_param_keys(value, f"{path}.")


@pytest.mark.unit
class TestOmieRealFixtures:
    """Cada teste valida o schema atual contra a resposta real, quando existir."""

    def test_listar_extrato_matches_schema(self) -> None:
        resp = _load_response("listar_extrato")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("listaMovimentos")
        assert isinstance(items, list), "envelope de ListarExtrato sem `listaMovimentos`"
        # O Omie mistura linhas-resumo de saldo ("SALDO ANTERIOR/POSTERIOR")
        # no array — sem `nCodLancamento`, sem `cNatureza`, sem `cSituacao`.
        # Não são lançamentos; produção as filtra ANTES do parse
        # (`omie/client.listar_extrato`, caso Austral 20/05/2026) e a fixture
        # real da captura de 21/08/2026 confirma que elas existem. O gate
        # valida o schema das linhas que produção de fato parseia — com o
        # MESMO predicado de produção, para os dois não divergirem.
        lancamentos = [raw for raw in items if not is_extrato_summary_row(raw)]
        assert lancamentos, "fixture de extrato sem nenhum lançamento de verdade — recapture"
        # Se a resposta real divergir do schema, model_validate LEVANTA → FALHA.
        for raw in lancamentos:
            LancamentoExtrato.model_validate(raw)

    def test_listar_extrato_conta_corrente_matches_schema(self) -> None:
        """Conta CORRENTE real (BB, captura de 28/09/2026, anonimizada).

        Duas diferenças contra a fixture do cartão, e as duas derrubaram ou
        poderiam derrubar produção:

        - as linhas de saldo trazem `nCodLancamento` — um CONTADOR 1, 2, 3…,
          não um ID —, então o filtro antigo ("sem `nCodLancamento`") as
          deixava passar e o parse morria por falta de `cNatureza`
          (5 sessões em erro no Laticínio, 28/09/2026);
        - a natureza é `P`/`R` com valor JÁ sinalizado, como no cartão — não
          `D`/`C` com valor absoluto, como a doc diz para conta corrente.
        """
        resp = _load_response("listar_extrato_conta_corrente")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        assert resp["cCodTipo"] == "CC"
        items = resp["listaMovimentos"]

        resumo = [raw for raw in items if is_extrato_summary_row(raw)]
        lancamentos = [raw for raw in items if not is_extrato_summary_row(raw)]
        assert len(resumo) == 12
        assert all(raw.get("nCodLancamento") is not None for raw in resumo), (
            "a forma que motivou o predicado é linha de saldo COM nCodLancamento"
        )
        assert len(lancamentos) == 113

        parsed = [LancamentoExtrato.model_validate(raw) for raw in lancamentos]
        for entry in parsed:
            if entry.c_natureza == "P":
                assert entry.signed_amount < 0
            else:
                assert entry.c_natureza == "R"
                assert entry.signed_amount > 0

    def test_listar_extrato_has_no_pagination(self) -> None:
        resp = _load_response("listar_extrato")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        # O código assume que ListarExtrato NÃO pagina. Se a resposta real
        # trouxer marcadores de paginação, o contrato mudou → FALHA (registra).
        assert "total_de_paginas" not in resp, (
            "ListarExtrato trouxe `total_de_paginas` — passou a paginar! "
            "Ajuste omie/client.listar_extrato (ver README)."
        )
        assert "pagina" not in resp, "ListarExtrato trouxe `pagina` — passou a paginar!"

    def test_listar_contas_correntes_matches_schema(self) -> None:
        resp = _load_response("listar_contas_correntes")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("ListarContasCorrentes")
        assert isinstance(items, list), "envelope sem `ListarContasCorrentes`"
        for raw in items:
            ContaCorrente.model_validate(raw)

    def test_listar_contas_pagar_matches_schema(self) -> None:
        resp = _load_response("listar_contas_pagar")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("conta_pagar_cadastro")
        assert isinstance(items, list), "envelope sem `conta_pagar_cadastro`"
        for raw in items:
            TituloAPagarReceber.model_validate(raw)

    def test_listar_contas_receber_matches_schema(self) -> None:
        resp = _load_response("listar_contas_receber")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("conta_receber_cadastro")
        assert isinstance(items, list), "envelope sem `conta_receber_cadastro`"
        for raw in items:
            TituloAPagarReceber.model_validate(raw)

    def test_listar_categorias_matches_schema(self) -> None:
        """BACK 07.3 — `cCodCateg` é obrigatório no lançamento; se o nome do
        campo estiver errado, o combobox de classificação nasce vazio."""
        resp = _load_response("listar_categorias")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("categoria_cadastro")
        assert isinstance(items, list), (
            "envelope de ListarCategorias sem `categoria_cadastro` — a chave do "
            "array veio da doc, não de uma resposta. Ajuste "
            "omie/client.listar_categorias."
        )
        for raw in items:
            CategoriaOmie.model_validate(raw)

    def test_listar_categorias_carrega_o_plano_de_contas(self) -> None:
        """BACK 10.1 — os campos NOVOS do contrato, contra a resposta REAL.

        A razão de existir da Sprint 10 é o vínculo categoria → conta de
        demonstrativo que a origem já preenche. Se `dadosDRE.codigoDRE` for
        lido com outro nome (ou não for lido), a tabela nova nasce com 100% de
        "sem destino declarado" e a métrica da sprint fica travada em 0% sem
        nada quebrar — o modo de falha silencioso que este teste existe para
        pegar.

        Os números são os da fixture: 37 de 50 com destino, 6 de 50 com conta
        contábil.
        """
        resp = _load_response("listar_categorias")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("categoria_cadastro")
        assert isinstance(items, list)

        categorias = [CategoriaOmie.model_validate(raw) for raw in items]
        assert len(categorias) == 50, "a fixture capturada tem 50 categorias"

        com_destino = [c for c in categorias if c.destino_code is not None]
        com_conta_contabil = [c for c in categorias if c.conta_contabil_code is not None]
        assert len(com_destino) == 37, (
            "37 de 50 categorias da resposta real trazem `dadosDRE.codigoDRE`. "
            "Divergiu = o nome do campo mudou ou o DTO parou de lê-lo, e o "
            "plano de contas nasceria sem destino nenhum."
        )
        assert len(com_conta_contabil) == 6, "6 de 50 trazem `id_conta_contabil`"

        # O bloco `dadosDRE` vem sempre presente — vazio (`{}`) nas 13 sem destino.
        assert all("dadosDRE" in raw for raw in items)
        assert len(categorias) - len(com_destino) == 13

        # Hierarquia e flags são lidas de verdade, não ficam todas no default.
        assert any(c.parent_code is not None for c in categorias), (
            "nenhuma categoria com pai — `categoria_superior` não está sendo lida"
        )
        assert any(c.is_transferencia for c in categorias)
        assert any(c.is_totalizadora for c in categorias)

        # Nível e sinal do demonstrativo vêm preenchidos onde há destino.
        assert all(c.dados_dre.nivel_dre is not None for c in com_destino)
        assert {c.dados_dre.sinal_dre for c in com_destino} <= {"+", "-"}

    def test_transferencia_da_fixture_nao_tem_destino(self) -> None:
        """Ausência de destino é INFORMAÇÃO, não buraco — e é verificável.

        A categoria `0.01` (Transferência) é totalizadora e de transferência na
        resposta real, e por definição contábil não tem conta de demonstrativo
        própria. Se um dia alguém "consertar" isso inferindo um destino, este
        teste cai.
        """
        resp = _load_response("listar_categorias")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("categoria_cadastro")
        assert isinstance(items, list)

        transferencia = next(
            CategoriaOmie.model_validate(raw) for raw in items if raw.get("codigo") == "0.01"
        )
        assert transferencia.is_transferencia
        assert transferencia.is_totalizadora
        assert transferencia.destino_code is None
        assert transferencia.parent_code is None, "`categoria_superior='0'` é raiz, não um código"


@pytest.mark.unit
class TestFixturesCarryNoSecrets:
    """Nenhuma fixture commitada pode conter credencial da Omie.

    Roda mesmo sem fixture nenhuma (varre o diretório): o dia em que alguém
    gravar um request com `app_key` dentro, o teste acusa antes do commit.
    """

    def test_no_request_fixture_contains_credentials(self) -> None:
        offenders: list[str] = []
        for path in sorted(_FIXTURES_DIR.glob("*.json")):
            raw = path.read_text(encoding="utf-8")
            offenders.extend(f"{path.name}:{key}" for key in _SECRET_KEYS if f'"{key}"' in raw)
        assert not offenders, "Fixture com credencial Omie dentro — NÃO commitar: " + ", ".join(
            offenders
        )


@pytest.mark.unit
class TestIncluirLancCCRealFixture:
    """Gate anti-invenção do contrato de ESCRITA (S-1 · BACK 07.1).

    Sem a fixture, cada teste SKIPA nomeando S-1 — nunca verde silencioso.
    Com a fixture, o DTO é confrontado com a chamada que a Omie de fato
    aceitou; qualquer divergência de nome ou tipo FALHA.
    """

    def test_request_fixture_keys_match_dto_aliases(self) -> None:
        req = _load_json("incluir_lanc_cc.request")
        if req is None:
            pytest.skip(_WRITE_CAPTURE_HINT)
        assert req.get("call_name") == "IncluirLancCC", (
            "fixture de escrita não é do IncluirLancCC — recapture."
        )
        param = req.get("param")
        assert isinstance(param, dict), "fixture de escrita sem `param`"
        declared = IncluirLancCCRequest.omie_param_aliases()
        unknown = set(_flatten_param_keys(param)) - declared
        assert not unknown, (
            "A chamada capturada enviou chaves que o DTO não declara "
            f"({sorted(unknown)}) — o DTO e a chamada real divergiram. "
            "Corrija IncluirLancCCRequest ou recapture."
        )

    def test_response_matches_schema(self) -> None:
        resp = _load_response("incluir_lanc_cc")
        if resp is None:
            pytest.skip(_WRITE_CAPTURE_HINT)
        refusal = resp.get("faultstring") or resp.get("_adl_capture_exception_message")
        assert refusal is None, (
            "A fixture gravada é uma RECUSA da Omie — evidência S-1 válida, "
            f"mas não é um contrato aceito: {refusal!r}. Corrija "
            "IncluirLancCCRequest e RECAPTURE antes de qualquer lançamento."
        )
        # Divergência de nome/tipo de campo LEVANTA → FALHA (é o ponto do gate).
        IncluirLancCCResponse.model_validate(resp)

    def test_repeat_post_documents_idempotency(self) -> None:
        """O 2º POST do MESMO `cCodIntLanc` — a evidência que 1 POST não dá.

        Este teste **não** exige que a Omie imponha unicidade: o que ele exige
        é que a resposta do 2º POST exista e seja *legível*, e que o resultado
        (impôs / não impôs) fique registrado no repositório em vez de assumido.
        Se a Omie NÃO impuser, o teste falha com a mensagem que manda tratar o
        caso — é dinheiro duplicado na contabilidade do cliente.
        """
        first = _load_response("incluir_lanc_cc")
        repeat = _load_response("incluir_lanc_cc_repeat")
        if first is None or repeat is None:
            pytest.skip(_WRITE_CAPTURE_HINT)

        rejected = "faultstring" in repeat or "_adl_capture_exception_type" in repeat
        if rejected:
            return  # Omie impôs a unicidade — defesa secundária confirmada.

        first_id = IncluirLancCCResponse.model_validate(first).n_cod_lanc
        second_id = IncluirLancCCResponse.model_validate(repeat).n_cod_lanc
        assert first_id == second_id, (
            "S-1 REFUTADA: o 2º POST do mesmo cCodIntLanc criou um lançamento "
            f"NOVO ({first_id} → {second_id}). A Omie NÃO impõe unicidade — a "
            "dedup do ADL (BACK 07.2) é a única defesa e nenhum caminho pode "
            "reenviar sem consultar o estado próprio antes."
        )

    def test_readback_confirms_sign_convention(self) -> None:
        """O request só mostra o que MANDAMOS; o extrato mostra o que a Omie ENTENDEU.

        Convenção OBSERVADA (captura de 21/08/2026, conta de CARTÃO):
        enviamos `nValorLanc` ABSOLUTO com categoria de despesa e o extrato
        devolveu o lançamento com natureza **'P'** (pagamento — não o 'D' da
        doc) e `nValorDocumento` **JÁ NEGATIVO**. A invariante que este teste
        trava é a que importa para dinheiro: valor absoluto enviado →
        `signed_amount` NEGATIVO (débito) do MESMO montante. Se uma recaptura
        mostrar outra direção, a montagem da BACK 07.4 (e o bloqueio de
        estorno) precisam ser revistos com essa evidência.

        A linha recém-criada também volta SEM `cSituacao` — o model_validate
        abaixo só passa com o campo opcional (evidência da mesma captura).
        """
        readback = _load_json("incluir_lanc_cc.readback")
        created = _load_response("incluir_lanc_cc")
        request = _load_json("incluir_lanc_cc.request")
        if readback is None or created is None or request is None:
            pytest.skip(_WRITE_CAPTURE_HINT)

        created_id = IncluirLancCCResponse.model_validate(created).n_cod_lanc
        movimentos = readback.get("listaMovimentos") or []
        matches = [
            LancamentoExtrato.model_validate(raw)
            for raw in movimentos
            if raw.get("nCodLancamento") == created_id
        ]
        assert matches, (
            f"O lançamento {created_id} criado pela captura não apareceu no "
            "extrato relido — recapture (ou o nCodLanc da resposta não é o "
            "mesmo ID que o extrato usa, o que também é divergência de contrato)."
        )
        entry = matches[0]
        sent = Decimal(str(request["param"]["cabecalho"]["nValorLanc"]))
        assert entry.signed_amount == -sent, (
            "S-1 REFUTADA (sinal): enviamos valor ABSOLUTO com categoria de "
            f"despesa e o extrato devolveu signed_amount={entry.signed_amount} "
            f"(esperado {-sent}, débito). A direção do lançamento difere da "
            "observada em 21/08/2026 — corrija a montagem da BACK 07.4."
        )
        assert entry.c_natureza == "P", (
            f"Natureza observada mudou: era 'P' (cartão, 21/08/2026), veio "
            f"{entry.c_natureza!r}. Se for 'D' com valor absoluto, "
            "`signed_amount` continua correto; registre a nova evidência aqui."
        )


# ----------------------------------------------------------------------
# Contas a Pagar — captura real de 13/10/2026 (task 86e3n70q0)
# ----------------------------------------------------------------------
#
# Diferente das classes acima, estes testes FALHAM se a fixture sumir: ela já foi
# capturada e commitada (conta da Hologram, cartão Inter, R$ 0,01), então ausência
# é regressão do repositório, não "captura ainda pendente".

from app.integrations.omie.schemas import (  # noqa: E402 - bloco próprio da captura
    CancelarPagamentoRequest,
    CancelarPagamentoResponse,
    ClienteChaveRequest,
    ClienteOmie,
    ClienteStatusResponse,
    ContaPagarChaveRequest,
    ContaPagarStatusResponse,
    DepartamentoOmie,
    ExcluirLancCCRequest,
    ExcluirLancCCResponse,
    IncluirClienteRequest,
    IncluirContaPagarRequest,
    LancarPagamentoRequest,
    LancarPagamentoResponse,
    ListarClientesPorCnpjRequest,
    OmieWriteParam,
)

_PAYABLES_CAPTURE = "captura real de 13/10/2026 (scripts/capture_omie_payables.py)"


def _require(name: str) -> dict[str, Any]:
    loaded = _load_json(name)
    assert loaded is not None, f"fixture {name}.json ausente — era da {_PAYABLES_CAPTURE}"
    return loaded


def _require_list(name: str) -> list[dict[str, Any]]:
    path = _FIXTURES_DIR / f"{name}.json"
    assert path.exists(), f"fixture {name}.json ausente — era da {_PAYABLES_CAPTURE}"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


def _refusal_message(resp: dict[str, Any]) -> str:
    message = resp.get("_adl_capture_exception_message")
    assert isinstance(message, str), "fixture esperada como RECUSA não tem a faultstring"
    return message


def _assert_request_matches_dto(name: str, call_name: str, dto: type[OmieWriteParam]) -> dict:
    req = _require(f"{name}.request")
    assert req.get("call_name") == call_name
    param = req["param"]
    assert isinstance(param, dict)
    unknown = set(_flatten_param_keys(param)) - dto.omie_param_aliases()
    assert not unknown, (
        f"{name}: a chamada ACEITA enviou chaves que {dto.__name__} não declara "
        f"({sorted(unknown)}) — DTO e chamada real divergiram."
    )
    return param


def _extrato_lancamentos(name: str) -> list[LancamentoExtrato]:
    resp = _require(f"{name}.response")
    return [
        LancamentoExtrato.model_validate(raw)
        for raw in resp["listaMovimentos"]
        if not is_extrato_summary_row(raw)
    ]


@pytest.mark.unit
class TestContasAPagarRealFixtures:
    """Gate anti-invenção das chamadas do Contas a Pagar, contra as respostas REAIS."""

    # --- 1. departamentos -------------------------------------------------

    def test_listar_departamentos_matches_schema(self) -> None:
        req = _require("listar_departamentos.request")
        assert req["call_name"] == "ListarDepartamentos"
        resp = _require("listar_departamentos.response")
        itens = resp["departamentos"]
        assert isinstance(itens, list)
        assert len(itens) == 50
        assert {"pagina", "total_de_paginas", "registros", "total_de_registros"} <= set(resp)
        for raw in itens:
            DepartamentoOmie.model_validate(raw)

    def test_listar_departamentos_nao_traz_nivel_totalizador(self) -> None:
        """A doc promete `nivel_totalizador`; a resposta real não o traz em nenhum dos 50.

        Sem ele, sintético e analítico só se distinguem pela convenção de nome do
        cliente — por isso o departamento do rateio é sempre escolhido, nunca inferido.
        """
        itens = _require("listar_departamentos.response")["departamentos"]
        assert all("nivel_totalizador" not in raw for raw in itens)
        assert all("estrutura" in raw for raw in itens)

    # --- 2. fornecedor por CNPJ --------------------------------------------

    def test_listar_clientes_por_cnpj_existente(self) -> None:
        param = _assert_request_matches_dto(
            "listar_clientes_por_cnpj", "ListarClientes", ListarClientesPorCnpjRequest
        )
        resp = _require("listar_clientes_por_cnpj.response")
        cadastros = resp["clientes_cadastro"]
        assert resp["total_de_registros"] == 1
        assert len(cadastros) == 1
        cliente = ClienteOmie.model_validate(cadastros[0])
        assert cliente.display_name
        assert cadastros[0]["cnpj_cpf"] == param["clientesFiltro"]["cnpj_cpf"]

    def test_listar_clientes_por_cnpj_inexistente_e_fault_5113(self) -> None:
        """CNPJ sem cadastro NÃO é lista vazia: é fault `5113 - Não existem registros`."""
        _assert_request_matches_dto(
            "listar_clientes_por_cnpj_inexistente", "ListarClientes", ListarClientesPorCnpjRequest
        )
        message = _refusal_message(_require("listar_clientes_por_cnpj_inexistente.response"))
        assert "5113" in message
        assert "Não existem registros" in message
        history = _require_list("listar_clientes_por_cnpj_inexistente.refusals")
        assert len(history) == 1

    # --- 3. fornecedor de teste --------------------------------------------

    def test_incluir_cliente_matches_schema_and_needs_email(self) -> None:
        param = _assert_request_matches_dto(
            "incluir_cliente", "IncluirCliente", IncluirClienteRequest
        )
        assert param["email"], "o e-mail é obrigatório na prática (1040)"
        resp = ClienteStatusResponse.model_validate(_require("incluir_cliente.response"))
        assert resp.codigo_status == "0"
        assert resp.codigo_cliente_integracao == param["codigo_cliente_integracao"]
        # 1ª tentativa, SEM e-mail, foi recusada: a recusa é evidência e fica no histórico.
        history = _require_list("incluir_cliente.refusals")
        assert len(history) == 1
        assert "email" not in history[0]["param"]
        assert "1040" in history[0]["_adl_capture_exception_message"]

    def test_incluir_cliente_is_idempotent_on_codigo_cliente_integracao(self) -> None:
        first = ClienteStatusResponse.model_validate(_require("incluir_cliente.response"))
        repeat = _require("incluir_cliente_repeat.response")
        assert "_adl_capture_exception_type" not in repeat, "o 2º POST não foi recusado"
        second = ClienteStatusResponse.model_validate(repeat)
        assert second.codigo_cliente_omie == first.codigo_cliente_omie
        assert second.codigo_status == "0"

    # --- 4. títulos ------------------------------------------------------------

    def test_incluir_conta_pagar_matches_schema(self) -> None:
        param = _assert_request_matches_dto(
            "incluir_conta_pagar", "IncluirContaPagar", IncluirContaPagarRequest
        )
        assert param["valor_documento"] == 0.01
        resp = ContaPagarStatusResponse.model_validate(_require("incluir_conta_pagar.response"))
        assert resp.codigo_status == "0"
        assert resp.codigo_lancamento_integracao == param["codigo_lancamento_integracao"]

    def test_incluir_conta_pagar_is_idempotent_on_codigo_lancamento_integracao(self) -> None:
        first = ContaPagarStatusResponse.model_validate(_require("incluir_conta_pagar.response"))
        repeat = _require("incluir_conta_pagar_repeat.response")
        assert "_adl_capture_exception_type" not in repeat
        second = ContaPagarStatusResponse.model_validate(repeat)
        assert second.codigo_lancamento_omie == first.codigo_lancamento_omie

    def test_incluir_conta_pagar_duas_categorias_aceita_lista(self) -> None:
        """Subtask 10: um título com DUAS categorias (R$ 0,02 = 0,01 + 0,01) é aceito."""
        param = _assert_request_matches_dto(
            "incluir_conta_pagar_duas_categorias", "IncluirContaPagar", IncluirContaPagarRequest
        )
        assert "codigo_categoria" not in param
        assert len(param["categorias"]) == 2
        assert all(
            {"codigo_categoria", "valor", "percentual"} <= set(c) for c in param["categorias"]
        )
        resp = ContaPagarStatusResponse.model_validate(
            _require("incluir_conta_pagar_duas_categorias.response")
        )
        assert resp.codigo_status == "0"

    def test_incluir_conta_pagar_distribuicao_aceita_valor_e_percentual(self) -> None:
        """Rateio por departamento: `distribuicao[{cCodDep, nValDep, nPerDep}]` aceito."""
        param = _assert_request_matches_dto(
            "incluir_conta_pagar_distribuicao", "IncluirContaPagar", IncluirContaPagarRequest
        )
        assert len(param["distribuicao"]) == 2
        assert all({"cCodDep", "nValDep", "nPerDep"} <= set(d) for d in param["distribuicao"])
        resp = ContaPagarStatusResponse.model_validate(
            _require("incluir_conta_pagar_distribuicao.response")
        )
        assert resp.codigo_status == "0"

    # --- 5. baixa e releituras --------------------------------------------------

    def test_lancar_pagamento_matches_schema(self) -> None:
        param = _assert_request_matches_dto(
            "lancar_pagamento", "LancarPagamento", LancarPagamentoRequest
        )
        resp = LancarPagamentoResponse.model_validate(_require("lancar_pagamento.response"))
        assert resp.codigo_status == "0"
        assert resp.liquidado == "S"
        assert resp.valor_baixado == Decimal(str(param["valor"]))
        assert resp.codigo_lancamento == param["codigo_lancamento"]
        assert resp.codigo_baixa_integracao == param["codigo_baixa_integracao"]

    def test_lancar_pagamento_repeat_devolve_a_mesma_baixa(self) -> None:
        first = LancarPagamentoResponse.model_validate(_require("lancar_pagamento.response"))
        repeat = _require("lancar_pagamento_repeat.response")
        assert "_adl_capture_exception_type" not in repeat
        second = LancarPagamentoResponse.model_validate(repeat)
        assert second.codigo_baixa == first.codigo_baixa, "o 2º envio criou OUTRA baixa"

    def test_consultar_conta_pagar_apos_baixa(self) -> None:
        _assert_request_matches_dto(
            "consultar_conta_pagar", "ConsultarContaPagar", ContaPagarChaveRequest
        )
        raw = _require("consultar_conta_pagar.response")
        titulo = TituloAPagarReceber.model_validate(raw)
        assert titulo.status_titulo == "PAGO"
        # Enviamos só `codigo_categoria`; a Omie devolve também a lista com 100%.
        assert raw["categorias"] == [
            {"codigo_categoria": raw["codigo_categoria"], "percentual": 100, "valor": 0.01}
        ]
        assert raw["codigo_lancamento_integracao"] == "HOSCAP1013-CP-A"

    def test_listar_contas_pagar_apos_baixa_mostra_os_tres_titulos(self) -> None:
        req = _require("listar_contas_pagar_apos_baixa.request")
        assert req["call_name"] == "ListarContasPagar"
        assert "filtrar_cliente" in req["param"]
        resp = _require("listar_contas_pagar_apos_baixa.response")
        titulos = resp["conta_pagar_cadastro"]
        assert resp["total_de_registros"] == 3
        assert len(titulos) == 3
        for raw in titulos:
            TituloAPagarReceber.model_validate(raw)
        por_chave = {raw["codigo_lancamento_integracao"]: raw for raw in titulos}
        assert por_chave["HOSCAP1013-CP-A"]["status_titulo"] == "PAGO"
        assert por_chave["HOSCAP1013-CP-B"]["status_titulo"] == "A VENCER"
        assert len(por_chave["HOSCAP1013-CP-B"]["categorias"]) == 2
        dist = por_chave["HOSCAP1013-CP-C"]["distribuicao"]
        assert len(dist) == 2
        assert all({"cCodDep", "cDesDep", "nPerDep", "nValDep"} <= set(d) for d in dist)

    def test_listar_extrato_baixa_mostra_a_baixa_como_pagamento(self) -> None:
        """A baixa vira linha do extrato do cartão: natureza `P`, valor já negativo,
        `cOrigem='Conta Paga'`, com ID PRÓPRIO (nem o do título, nem o da baixa) e
        sem `cCodIntLanc`."""
        baixa = LancarPagamentoResponse.model_validate(_require("lancar_pagamento.response"))
        lancamentos = _extrato_lancamentos("listar_extrato_baixa")
        assert len(lancamentos) == 1
        linha = lancamentos[0]
        assert linha.c_natureza == "P"
        assert linha.signed_amount == Decimal("-0.01")
        assert linha.n_cod_lancamento not in {baixa.codigo_lancamento, baixa.codigo_baixa}
        assert linha.n_cod_lanc_relac == linha.n_cod_lancamento
        assert linha.c_cod_int_lanc is None
        raw = _require("listar_extrato_baixa.response")["listaMovimentos"]
        assert any(m.get("cOrigem") == "Conta Paga" for m in raw)

    # --- 7. crédito no IncluirLancCC (épico 86e2xweaf, item 2) -----------------

    def test_incluir_lanc_cc_credito_via_categoria_de_receita_e_aceito(self) -> None:
        param = _assert_request_matches_dto(
            "incluir_lanc_cc_credito_receita", "IncluirLancCC", IncluirLancCCRequest
        )
        assert param["cabecalho"]["nValorLanc"] == 0.01
        resp = IncluirLancCCResponse.model_validate(
            _require("incluir_lanc_cc_credito_receita.response")
        )
        assert resp.c_cod_status == "0"

    def test_incluir_lanc_cc_valor_negativo_e_recusado_106(self) -> None:
        """`nValorLanc` negativo NÃO representa crédito: fault `106 - ... maior ou igual que zero`."""
        req = _require("incluir_lanc_cc_credito_negativo.request")
        assert req["param"]["cabecalho"]["nValorLanc"] == -0.01
        message = _refusal_message(_require("incluir_lanc_cc_credito_negativo.response"))
        assert "106" in message
        assert "nValorLanc" in message
        history = _require_list("incluir_lanc_cc_credito_negativo.refusals")
        assert len(history) == 1

    def test_listar_extrato_credito_mostra_natureza_r(self) -> None:
        """Categoria de RECEITA + valor absoluto aterrissa como CRÉDITO (`R`, positivo)."""
        criado = IncluirLancCCResponse.model_validate(
            _require("incluir_lanc_cc_credito_receita.response")
        )
        lancamentos = {
            e.n_cod_lancamento: e for e in _extrato_lancamentos("listar_extrato_credito")
        }
        credito = lancamentos[criado.n_cod_lanc]
        assert credito.c_natureza == "R"
        assert credito.signed_amount == Decimal("0.01")
        raw = _require("listar_extrato_credito.response")["listaMovimentos"]
        origem = next(m["cOrigem"] for m in raw if m.get("nCodLancamento") == criado.n_cod_lanc)
        assert origem == "Crédito em Conta Corrente"

    # --- 6. limpeza ----------------------------------------------------------------

    def test_cancelar_pagamento_matches_schema(self) -> None:
        param = _assert_request_matches_dto(
            "cancelar_pagamento", "CancelarPagamento", CancelarPagamentoRequest
        )
        baixa = LancarPagamentoResponse.model_validate(_require("lancar_pagamento.response"))
        assert param["codigo_baixa"] == baixa.codigo_baixa
        resp = CancelarPagamentoResponse.model_validate(_require("cancelar_pagamento.response"))
        assert resp.codigo_status == "0"
        assert resp.codigo_baixa == baixa.codigo_baixa
        # A Omie ecoa o `codigo_baixa_integracao` PREENCHIDO com espaços (string20).
        assert resp.codigo_baixa_integracao is not None
        assert resp.codigo_baixa_integracao != resp.codigo_baixa_integracao.strip()
        assert resp.codigo_baixa_integracao.strip() == baixa.codigo_baixa_integracao

    @pytest.mark.parametrize(
        ("criado", "excluido"),
        [
            ("incluir_conta_pagar", "excluir_conta_pagar"),
            ("incluir_conta_pagar_duas_categorias", "excluir_conta_pagar_duas_categorias"),
            ("incluir_conta_pagar_distribuicao", "excluir_conta_pagar_distribuicao"),
        ],
    )
    def test_excluir_conta_pagar_matches_schema(self, criado: str, excluido: str) -> None:
        titulo = ContaPagarStatusResponse.model_validate(_require(f"{criado}.response"))
        param = _assert_request_matches_dto(excluido, "ExcluirContaPagar", ContaPagarChaveRequest)
        assert param["codigo_lancamento_omie"] == titulo.codigo_lancamento_omie
        resp = ContaPagarStatusResponse.model_validate(_require(f"{excluido}.response"))
        assert resp.codigo_status == "0"
        assert resp.codigo_lancamento_omie == titulo.codigo_lancamento_omie

    def test_excluir_lanc_cc_matches_schema(self) -> None:
        criado = IncluirLancCCResponse.model_validate(
            _require("incluir_lanc_cc_credito_receita.response")
        )
        param = _assert_request_matches_dto(
            "excluir_lanc_cc_credito_receita", "ExcluirLancCC", ExcluirLancCCRequest
        )
        assert param["nCodLanc"] == criado.n_cod_lanc
        resp = ExcluirLancCCResponse.model_validate(
            _require("excluir_lanc_cc_credito_receita.response")
        )
        assert resp.c_cod_status == "0"
        assert resp.n_cod_lanc == criado.n_cod_lanc

    def test_excluir_cliente_recusado_por_dependencia_21(self) -> None:
        """Fornecedor que já teve título NÃO sai pela API: `21 - ... existem outras
        informações que dependem deste registro` — ficou para apagar à mão."""
        _assert_request_matches_dto("excluir_cliente", "ExcluirCliente", ClienteChaveRequest)
        message = _refusal_message(_require("excluir_cliente.response"))
        assert message.startswith("5xx permanente em ExcluirCliente")
        assert "21 - " in message
        history = _require_list("excluir_cliente.refusals")
        assert len(history) == 1

    def test_listar_extrato_apos_limpeza_so_tem_linhas_de_saldo(self) -> None:
        resp = _require("listar_extrato_apos_limpeza.response")
        assert all(is_extrato_summary_row(raw) for raw in resp["listaMovimentos"])
        assert _extrato_lancamentos("listar_extrato_apos_limpeza") == []
