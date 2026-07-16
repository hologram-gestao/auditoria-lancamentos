"""Testes contra fixtures REAIS da Omie (BACK 02.7).

⚠️ **S-3 (ASSUMIDA — NÃO TESTADA / RISCO):** os nomes de campo da Omie podem
estar errados (já quebrou em prod). Estes testes rodam o schema ATUAL contra a
RESPOSTA REAL capturada — se divergir, FALHAM (o "teste negativo" que registra a
divergência). **Mock escrito à mão não conta** (confirmaria a invenção).

Enquanto não houver fixture real (captura exige credencial Omie autorizada +
rede — ver `tests/fixtures/omie/README.md`), cada teste é **skipado** com uma
mensagem que aponta o script de captura. NÃO fabricamos fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.integrations.omie.schemas import (
    ContaCorrente,
    LancamentoExtrato,
    TituloAPagarReceber,
)

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "omie"

_CAPTURE_HINT = (
    "Fixture real ausente. Rode `uv run python -m scripts.capture_omie_fixtures` "
    "com credencial Omie autorizada (ver tests/fixtures/omie/README.md). S-3."
)


def _load_response(name: str) -> dict | None:
    path = _FIXTURES_DIR / f"{name}.response.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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
