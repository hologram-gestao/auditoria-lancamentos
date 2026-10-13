"""Os cinco métodos do Contas a Pagar no `OmieClient` (86e3n70q0, bloco B).

O `param` que cada método monta é comparado com o `param` da FIXTURE REAL de
13/10/2026 (`tests/fixtures/omie/`): o método não pode mandar à Omie uma forma
diferente da que a captura provou. As respostas mockadas são as próprias
respostas capturadas.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.exceptions import OmieFaultError
from app.integrations.omie.client import (
    OMIE_CODE_NO_RECORDS,
    OmieClient,
    OmieCredentials,
    omie_error_code,
)
from app.integrations.omie.schemas import (
    ContaPagarCategoria,
    ContaPagarDistribuicao,
    IncluirClienteRequest,
    IncluirContaPagarRequest,
    LancarPagamentoRequest,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "omie"


def _fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((_FIXTURES / f"{name}.json").read_text("utf-8"))
    return payload


def _url(module: str, endpoint: str) -> str:
    return f"https://app.omie.com.br/api/v1/{module}/{endpoint}/"


def _sent_param(route: respx.Route, index: int = 0) -> dict[str, Any]:
    body = json.loads(route.calls[index].request.content)
    param: dict[str, Any] = body["param"][0]
    return param


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def client(settings: Settings) -> OmieClient:
    return OmieClient(
        credentials=OmieCredentials(app_key=SecretStr("k"), app_secret=SecretStr("s")),
        settings=settings,
    )


class TestOmieErrorCode:
    """O mesmo código chega por dois caminhos; os dois têm de dar o mesmo."""

    def test_from_the_omie_api_error_header(self) -> None:
        exc = OmieFaultError(
            "x", metadata={"omie_api_error": "5113 - Não existem registros para a página [1]!"}
        )
        assert omie_error_code(exc) == OMIE_CODE_NO_RECORDS

    def test_from_the_soap_faultcode(self) -> None:
        exc = OmieFaultError("x", metadata={"fault_code": "SOAP-ENV:Client-757"})
        assert omie_error_code(exc) == "757"

    def test_without_a_code_is_none(self) -> None:
        assert omie_error_code(OmieFaultError("x")) is None
        assert omie_error_code(OmieFaultError("x", metadata={"fault_code": "SOAP-ENV"})) is None

    def test_the_real_5113_message_parses(self) -> None:
        """O texto VERBATIM da captura (o header vem depois do prefixo do client)."""
        message = _fixture("listar_clientes_por_cnpj_inexistente.response")[
            "_adl_capture_exception_message"
        ]
        header = message.split(": ", 1)[1]
        assert omie_error_code(OmieFaultError("x", metadata={"omie_api_error": header})) == "5113"


class TestListarDepartamentos:
    @respx.mock
    async def test_paginates_with_the_captured_param(self, client: OmieClient) -> None:
        page1 = _fixture("listar_departamentos.response")
        page2 = {
            **page1,
            "pagina": 2,
            "total_de_paginas": 2,
            "departamentos": [
                {"codigo": "9", "descricao": "Último", "estrutura": "009", "inativo": "S"}
            ],
        }
        page1 = {**page1, "total_de_paginas": 2}
        route = respx.post(_url("geral", "departamentos")).mock(
            side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
        )
        items = await client.listar_departamentos()
        assert len(items) == len(page1["departamentos"]) + 1
        assert _sent_param(route, 0) == _fixture("listar_departamentos.request")["param"]
        assert _sent_param(route, 1)["pagina"] == 2
        assert items[-1].is_active is False


class TestListarClientesPorCnpj:
    @respx.mock
    async def test_found_sends_the_captured_param(self, client: OmieClient) -> None:
        captured = _fixture("listar_clientes_por_cnpj.request")["param"]
        route = respx.post(_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_fixture("listar_clientes_por_cnpj.response"))
        )
        found = await client.listar_clientes_por_cnpj(
            cnpj_cpf=captured["clientesFiltro"]["cnpj_cpf"]
        )
        assert _sent_param(route) == captured
        assert len(found) == 1
        assert found[0].codigo_cliente_omie == 3052086626
        assert found[0].is_active is True

    @respx.mock
    async def test_5113_is_not_found_not_an_error(self, client: OmieClient) -> None:
        respx.post(_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                # Header HTTP é ASCII no fake; o que o client lê é o código inicial.
                500,
                headers={"OmieAPI-Error": "5113 - Nao existem registros para a pagina [1]!"},
            )
        )
        assert await client.listar_clientes_por_cnpj(cnpj_cpf="99.999.001/0001-59") == []

    @respx.mock
    async def test_other_fault_still_raises(self, client: OmieClient) -> None:
        """ "Não consegui perguntar" não pode virar "não existe": só o 5113 é vazio."""
        respx.post(_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                200, json={"faultstring": "ERROR: outro", "faultcode": "SOAP-ENV:Client-5001"}
            )
        )
        with pytest.raises(OmieFaultError):
            await client.listar_clientes_por_cnpj(cnpj_cpf="99.999.001/0001-59")


class TestEscritas:
    @respx.mock
    async def test_incluir_cliente_sends_the_captured_shape(self, client: OmieClient) -> None:
        captured = _fixture("incluir_cliente.request")["param"]
        route = respx.post(_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json=_fixture("incluir_cliente.response"))
        )
        resp = await client.incluir_cliente(IncluirClienteRequest(**captured))
        assert _sent_param(route) == captured
        assert resp.codigo_cliente_omie == 3105843586

    @respx.mock
    async def test_incluir_conta_pagar_with_distribuicao(self, client: OmieClient) -> None:
        captured = _fixture("incluir_conta_pagar_distribuicao.request")["param"]
        route = respx.post(_url("financas", "contapagar")).mock(
            return_value=httpx.Response(
                200, json=_fixture("incluir_conta_pagar_distribuicao.response")
            )
        )
        request = IncluirContaPagarRequest(
            **{k: v for k, v in captured.items() if k != "distribuicao"},
            distribuicao=[
                ContaPagarDistribuicao(
                    c_cod_dep=d["cCodDep"],
                    n_val_dep=Decimal(str(d["nValDep"])),
                    n_per_dep=Decimal(str(d["nPerDep"])),
                )
                for d in captured["distribuicao"]
            ],
        )
        resp = await client.incluir_conta_pagar(request)
        assert _sent_param(route) == captured
        assert resp.codigo_lancamento_omie == 3105843602

    @respx.mock
    async def test_incluir_conta_pagar_with_two_categories(self, client: OmieClient) -> None:
        captured = _fixture("incluir_conta_pagar_duas_categorias.request")["param"]
        route = respx.post(_url("financas", "contapagar")).mock(
            return_value=httpx.Response(
                200, json=_fixture("incluir_conta_pagar_duas_categorias.response")
            )
        )
        request = IncluirContaPagarRequest(
            **{k: v for k, v in captured.items() if k != "categorias"},
            categorias=[
                ContaPagarCategoria(
                    codigo_categoria=c["codigo_categoria"],
                    valor=Decimal(str(c["valor"])),
                    percentual=Decimal(str(c["percentual"])),
                )
                for c in captured["categorias"]
            ],
        )
        await client.incluir_conta_pagar(request)
        assert _sent_param(route) == captured

    @respx.mock
    async def test_lancar_pagamento_sends_the_captured_shape(self, client: OmieClient) -> None:
        captured = _fixture("lancar_pagamento.request")["param"]
        route = respx.post(_url("financas", "contapagar")).mock(
            return_value=httpx.Response(200, json=_fixture("lancar_pagamento.response"))
        )
        resp = await client.lancar_pagamento(LancarPagamentoRequest(**captured))
        assert _sent_param(route) == captured
        assert resp.codigo_baixa == 3105843608
