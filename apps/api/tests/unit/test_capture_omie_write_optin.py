"""Opt-in da captura de ESCRITA do `IncluirLancCC` (BACK 07.1).

A captura de escrita cria **movimento financeiro real** na contabilidade de um
cliente (a Omie não tem sandbox — CLAUDE.md §10). Estes testes travam o
comportamento que impede isso de acontecer por acidente:

  - sem `OMIE_CAPTURE_ALLOW_WRITE`, **nenhum POST de escrita** sai;
  - a chave `cCodIntLanc` respeita `string20` e é recusada quando não cabe;
  - o `cTipo` (valor `DIN` do PRD é palpite — S-1) **não** é enviado.

Nada aqui toca a rede: o `OmieClient` é substituído por um espião que registra
as chamadas. Um teste que precisasse da Omie real não seria um teste.
"""

from __future__ import annotations

from typing import Any

import pytest
from scripts import capture_omie_fixtures as capture

from app.integrations.omie.schemas import IncluirLancCCRequest

_READ_ONLY_ENV = {
    "OMIE_CAPTURE_APP_KEY": "fake-key",
    "OMIE_CAPTURE_APP_SECRET": "fake-secret",
    "OMIE_CAPTURE_CONTA_ID": "4321",
    "OMIE_CAPTURE_PERIODO_INICIAL": "01/04/2026",
    "OMIE_CAPTURE_PERIODO_FINAL": "30/04/2026",
}

_WRITE_ENV = {
    "OMIE_CAPTURE_ALLOW_WRITE": "1",
    "OMIE_CAPTURE_COD_CATEG": "1.01.01",
    "OMIE_CAPTURE_COD_INT_LANC": "ADL0701CAP1",
}

_ALL_CAPTURE_VARS = (
    *_READ_ONLY_ENV,
    *_WRITE_ENV,
    "OMIE_CAPTURE_DATA_LANC",
    "OMIE_CAPTURE_VALOR_LANC",
)


class _SpyOmieClient:
    """Substituto do `OmieClient` que registra `call_name` em vez de ir à rede."""

    calls: list[str] = []  # noqa: RUF012 - espião de teste, estado de classe é intencional

    def __init__(self, *_args: object, **_kwargs: object) -> None: ...

    async def __aenter__(self) -> _SpyOmieClient:
        return self

    async def __aexit__(self, *_exc: object) -> None: ...

    async def call(self, *, call_name: str, **_kwargs: Any) -> dict[str, Any]:
        type(self).calls.append(call_name)
        return {"listaMovimentos": []}


@pytest.fixture
def _clean_capture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_CAPTURE_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def spy_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> type[_SpyOmieClient]:
    _SpyOmieClient.calls = []
    monkeypatch.setattr(capture, "OmieClient", _SpyOmieClient)
    monkeypatch.setattr(capture, "_FIXTURES_DIR", tmp_path)
    return _SpyOmieClient


@pytest.mark.unit
class TestWriteCaptureOptIn:
    @pytest.mark.parametrize("raw", ["", "0", "false", "no", "off", "sim", " "])
    def test_disabled_for_anything_but_explicit_yes(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("OMIE_CAPTURE_ALLOW_WRITE", raw)
        assert capture._write_capture_enabled() is False

    def test_disabled_when_var_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OMIE_CAPTURE_ALLOW_WRITE", raising=False)
        assert capture._write_capture_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Yes"])
    def test_enabled_only_for_affirmative_values(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv("OMIE_CAPTURE_ALLOW_WRITE", raw)
        assert capture._write_capture_enabled() is True

    @pytest.mark.usefixtures("_clean_capture_env")
    async def test_no_write_post_without_opt_in(
        self, monkeypatch: pytest.MonkeyPatch, spy_client: type[_SpyOmieClient]
    ) -> None:
        """Sem a env var, `IncluirLancCC` NÃO aparece entre as chamadas feitas."""
        for key, value in _READ_ONLY_ENV.items():
            monkeypatch.setenv(key, value)
        # Credenciais de escrita presentes de propósito: só a ausência do
        # ALLOW_WRITE deve bastar para não postar nada.
        monkeypatch.setenv("OMIE_CAPTURE_COD_CATEG", _WRITE_ENV["OMIE_CAPTURE_COD_CATEG"])
        monkeypatch.setenv("OMIE_CAPTURE_COD_INT_LANC", _WRITE_ENV["OMIE_CAPTURE_COD_INT_LANC"])

        await capture._main()

        assert "IncluirLancCC" not in spy_client.calls, (
            "captura de escrita rodou sem OMIE_CAPTURE_ALLOW_WRITE — "
            "isso cria lançamento REAL no Omie."
        )
        assert "ListarExtrato" in spy_client.calls, "as capturas de leitura deveriam ter rodado"

    @pytest.mark.usefixtures("_clean_capture_env")
    async def test_opt_in_posts_twice_with_same_key(
        self, monkeypatch: pytest.MonkeyPatch, spy_client: type[_SpyOmieClient]
    ) -> None:
        """Com opt-in: DOIS POSTs (prova de idempotência) + readback do extrato."""
        for key, value in {**_READ_ONLY_ENV, **_WRITE_ENV}.items():
            monkeypatch.setenv(key, value)

        await capture._main()

        assert spy_client.calls.count("IncluirLancCC") == 2, (
            "a captura precisa de DOIS POSTs do mesmo cCodIntLanc — "
            "uma resposta só não demonstra idempotência (S-1)."
        )
        # 4 capturas de leitura + 1 readback do extrato após a escrita.
        assert spy_client.calls.count("ListarExtrato") == 2


@pytest.mark.unit
class TestIncluirLancCCCaptureRequest:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, value in {**_READ_ONLY_ENV, **_WRITE_ENV}.items():
            monkeypatch.setenv(key, value)

    def test_request_is_built_from_the_dto(self) -> None:
        request = capture._build_incluir_lanc_cc_request()
        assert isinstance(request, IncluirLancCCRequest)
        param = request.model_dump(by_alias=True, exclude_none=True, mode="json")
        # As chaves enviadas são exatamente as declaradas — é o laço que o gate
        # da fixture fecha (nada de dict escrito à mão divergindo do DTO).
        assert set(param) <= IncluirLancCCRequest.omie_param_aliases()
        assert param["nCodCC"] == 4321
        assert param["cNatureza"] == "D"
        assert param["cCodIntLanc"] == "ADL0701CAP1"

    def test_ctipo_is_not_sent(self) -> None:
        """`DIN` é palpite (S-1) — mandar um valor inventado poderia fazer a
        Omie recusar a chamada e transformar a captura numa prova falsa."""
        param = capture._build_incluir_lanc_cc_request().model_dump(
            by_alias=True, exclude_none=True, mode="json"
        )
        assert "cTipo" not in param

    def test_cod_int_lanc_over_20_chars_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OMIE_CAPTURE_COD_INT_LANC", "X" * 21)
        with pytest.raises(SystemExit, match="string20"):
            capture._build_incluir_lanc_cc_request()

    def test_missing_categoria_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OMIE_CAPTURE_COD_CATEG")
        with pytest.raises(SystemExit, match="OMIE_CAPTURE_COD_CATEG"):
            capture._build_incluir_lanc_cc_request()

    def test_amount_defaults_to_the_smallest_possible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OMIE_CAPTURE_VALOR_LANC", raising=False)
        request = capture._build_incluir_lanc_cc_request()
        assert str(request.n_valor_lanc) == "0.01"
