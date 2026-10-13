"""Opt-in de escrita e invariantes do `scripts/capture_omie_payables.py` (86e3n70q0).

A captura do Contas a Pagar cria registro REAL na contabilidade da Hologram. Estes
testes travam, sem rede, o que impede isso de acontecer por acidente: passo de
escrita sem `OMIE_CAPTURE_ALLOW_WRITE=1` não faz POST nenhum; `cleanup`, `estorno` e
as releituras tardias nunca entram na sequência padrão; chave de integração acima do
limite da Omie é recusada antes de sair; o `param` vai do DTO, com dinheiro como
NÚMERO JSON.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from scripts import capture_omie_payables as capture

from app.integrations.omie.schemas import ContaPagarCategoria, IncluirContaPagarRequest
from tests.unit.test_omie_fixtures import _flatten_param_keys

_WRITE_STEPS = (
    "incluir_cliente",
    "incluir_conta_pagar",
    "incluir_conta_pagar_duas_categorias",
    "incluir_conta_pagar_distribuicao",
    "lancar_pagamento",
    "estorno",
    "cleanup",
)


class _SpyClient:
    calls: list[str] = []  # noqa: RUF012 - espião de teste, estado de classe é intencional

    def __init__(self, *_args: object, **_kwargs: object) -> None: ...

    async def __aenter__(self) -> _SpyClient:
        return self

    async def __aexit__(self, *_exc: object) -> None: ...

    async def call(self, *, call_name: str, **_kwargs: Any) -> dict[str, Any]:
        type(self).calls.append(call_name)
        return {}


@pytest.fixture
def capture_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> type[_SpyClient]:
    _SpyClient.calls = []
    monkeypatch.setattr(capture, "OmieClient", _SpyClient)
    monkeypatch.setattr(capture, "_FIXTURES_DIR", tmp_path)
    monkeypatch.setattr(capture, "_PAUSA_ENTRE_CHAMADAS_SECONDS", 0)
    for var in ("OMIE_CAPTURE_ALLOW_WRITE", "OMIE_CAPTURE_ESTORNO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OMIE_CAPTURE_APP_KEY", "fake-key")
    monkeypatch.setenv("OMIE_CAPTURE_APP_SECRET", "fake-secret")
    monkeypatch.setenv("OMIE_CAPTURE_CONTA_ID", "4321")
    monkeypatch.setenv("OMIE_CAPTURE_COD_CATEG", "2.01.90")
    monkeypatch.setenv("OMIE_CAPTURE_COD_CATEG_2", "2.01.01")
    monkeypatch.setenv("OMIE_CAPTURE_DEP_1", "1")
    monkeypatch.setenv("OMIE_CAPTURE_DEP_2", "2")
    monkeypatch.setenv("OMIE_CAPTURE_DATA_HOJE", "13/10/2026")
    return _SpyClient


@pytest.mark.unit
class TestWriteOptIn:
    @pytest.mark.parametrize("step", _WRITE_STEPS)
    @pytest.mark.asyncio
    async def test_write_step_without_opt_in_makes_no_call(
        self, capture_env: type[_SpyClient], step: str
    ) -> None:
        with pytest.raises(SystemExit):
            await capture.run_steps([step])
        assert capture_env.calls == [], f"{step} chamou a Omie sem OMIE_CAPTURE_ALLOW_WRITE"

    def test_default_sequence_never_includes_cleanup_or_estorno(self) -> None:
        assert set(capture._DEFAULT_SEQUENCE).isdisjoint(
            {"cleanup", "estorno", "readback_credito", "readback_limpeza"}
        )
        assert capture._DEFAULT_SEQUENCE[0] == "listar_departamentos"
        assert set(capture._DEFAULT_SEQUENCE) <= set(capture.STEPS)

    def test_estorno_also_needs_its_own_flag(
        self, capture_env: type[_SpyClient], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OMIE_CAPTURE_ALLOW_WRITE", "1")
        with pytest.raises(SystemExit, match="OMIE_CAPTURE_ESTORNO"):
            capture.asyncio.run(capture.run_steps(["estorno"]))
        assert capture_env.calls == []


@pytest.mark.unit
class TestRequestShape:
    def test_integration_key_over_the_omie_limit_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OMIE_CAPTURE_KEY_PREFIX", "X" * 10)
        monkeypatch.setenv("OMIE_CAPTURE_DATA_HOJE", "13/10/2026")
        assert len(capture._chave("BX-A", max_len=20)) <= 20
        with pytest.raises(SystemExit, match="limite 20"):
            capture._chave("BAIXA-DO-TITULO-A", max_len=20)

    def test_param_comes_from_the_dto_with_json_numbers(self) -> None:
        request = IncluirContaPagarRequest(
            codigo_lancamento_integracao="K",
            codigo_cliente_fornecedor=1,
            data_vencimento="12/11/2026",
            valor_documento=Decimal("0.02"),
            id_conta_corrente=4321,
            categorias=[
                ContaPagarCategoria(
                    codigo_categoria="2.01.90", valor=Decimal("0.01"), percentual=Decimal("50")
                )
            ],
        )
        param = capture._param(request)
        assert param["valor_documento"] == 0.02
        assert isinstance(param["valor_documento"], float)
        assert param["categorias"][0]["valor"] == 0.01
        assert "codigo_categoria" not in param  # exclude_none: o que não foi dado não vai
        assert set(_flatten_param_keys(param)) <= IncluirContaPagarRequest.omie_param_aliases()
