"""Testes contra fixtures REAIS da Omie (BACK 02.7 · leitura · BACK 07.1 · escrita).

⚠️ **S-3 (ASSUMIDA — NÃO TESTADA / RISCO):** os nomes de campo da Omie podem
estar errados (já quebrou em prod). Estes testes rodam o schema ATUAL contra a
RESPOSTA REAL capturada — se divergir, FALHAM (o "teste negativo" que registra a
divergência). **Mock escrito à mão não conta** (confirmaria a invenção).

⚠️ **S-1 (ASSUMIDA — NÃO TESTADA / RISCO):** o contrato de ESCRITA
(`IncluirLancCC`) — nomes de campo, convenção de sinal (`nValorLanc` absoluto +
`cNatureza`) e unicidade de `cCodIntLanc` — é suposição documentada, não fato.
Os testes de escrita abaixo só ficam verdes contra a fixture real gravada por
`scripts/capture_omie_fixtures.py` com `OMIE_CAPTURE_ALLOW_WRITE=1`.

Enquanto não houver fixture real (captura exige credencial Omie autorizada +
rede — ver `tests/fixtures/omie/README.md`), cada teste é **skipado** com uma
mensagem que aponta o script de captura. NÃO fabricamos fixtures.
"""

from __future__ import annotations

import json
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
    "Fixture real de ESCRITA ausente (S-1: o contrato do IncluirLancCC — nomes "
    "de campo, sinal absoluto+cNatureza e unicidade de cCodIntLanc — segue "
    "ASSUMIDO, NÃO TESTADO). Rode `OMIE_CAPTURE_ALLOW_WRITE=1 uv run python -m "
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


@pytest.mark.unit
class TestOmieRealFixtures:
    """Cada teste valida o schema atual contra a resposta real, quando existir."""

    def test_listar_extrato_matches_schema(self) -> None:
        resp = _load_response("listar_extrato")
        if resp is None:
            pytest.skip(_CAPTURE_HINT)
        items = resp.get("listaMovimentos")
        assert isinstance(items, list), "envelope de ListarExtrato sem `listaMovimentos`"
        # Se a resposta real divergir do schema, model_validate LEVANTA → FALHA.
        for raw in items:
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
        unknown = set(param) - declared
        assert not unknown, (
            "A chamada capturada enviou chaves que o DTO não declara "
            f"({sorted(unknown)}) — o DTO e a chamada real divergiram. "
            "Corrija IncluirLancCCRequest ou recapture."
        )

    def test_response_matches_schema(self) -> None:
        resp = _load_response("incluir_lanc_cc")
        if resp is None:
            pytest.skip(_WRITE_CAPTURE_HINT)
        assert "faultstring" not in resp, (
            "A captura de escrita gravou um faultstring — a Omie RECUSOU o "
            f"request. O contrato assumido está errado: {resp.get('faultstring')!r}. "
            "Corrija IncluirLancCCRequest antes de qualquer lançamento."
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

        A captura lança um débito (`cNatureza='D'`) com `nValorLanc` absoluto e
        relê o extrato do dia. Se a convenção de escrita fosse outra (ex.: valor
        com sinal), o lançamento voltaria como crédito ou com valor negativo —
        e é isso que este teste recusa.
        """
        readback = _load_json("incluir_lanc_cc.readback")
        created = _load_response("incluir_lanc_cc")
        if readback is None or created is None:
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
        assert entry.c_natureza == "D", (
            "S-1 REFUTADA (sinal): enviamos cNatureza='D' com valor absoluto e "
            f"o Omie registrou cNatureza={entry.c_natureza!r}. A convenção de "
            "escrita difere da leitura — corrija a montagem da BACK 07.4."
        )
        assert entry.n_valor_documento > 0, (
            "S-1 REFUTADA (valor): o extrato devolveu valor não-positivo "
            f"({entry.n_valor_documento}) para um lançamento enviado com valor "
            "absoluto."
        )
