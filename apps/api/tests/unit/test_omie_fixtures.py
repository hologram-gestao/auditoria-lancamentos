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
        # valida o schema das linhas que produção de fato parseia.
        lancamentos = [raw for raw in items if raw.get("nCodLancamento") is not None]
        assert lancamentos, "fixture de extrato sem nenhum lançamento de verdade — recapture"
        # Se a resposta real divergir do schema, model_validate LEVANTA → FALHA.
        for raw in lancamentos:
            LancamentoExtrato.model_validate(raw)

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
