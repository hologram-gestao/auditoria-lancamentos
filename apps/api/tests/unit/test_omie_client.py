"""Testes unitários do OmieClient e DTOs.

Sem credenciais Omie sandbox disponíveis (Pedro, 25/04/2026), todos os
cenários são mockados com `respx`. Snapshots de payloads baseiam-se na
documentação oficial em `Docs/documentation/6. Integração com API do Omie-*.md`.

Cobertura:
    - DTOs: parse de data BR, valor com sinal, alias camelCase.
    - call(): sucesso (200 ok), faultstring auth → OmieAuthError,
      faultstring genérico → OmieFaultError, timeout → OmieTimeoutError
      após esgotar retries, 5xx com retry e eventual sucesso, 5xx persistente.
    - Paginação: para corretamente quando página retorna < page_size.
    - Métodos tipados: listar_contas_correntes converte items, listar_extrato
      aplica parse de data e signed_amount, contas_pagar/receber paginam.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.exceptions import OmieAuthError, OmieFaultError, OmieServerError, OmieTimeoutError
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.schemas import (
    ContaCorrente,
    LancamentoExtrato,
    OmieEntryNatureza,
    OmieTituloStatus,
    TituloAPagarReceber,
)

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def credentials() -> OmieCredentials:
    return OmieCredentials(
        app_key=SecretStr("fake-app-key"),
        app_secret=SecretStr("fake-app-secret"),
    )


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
async def client(credentials: OmieCredentials, settings: Settings) -> OmieClient:
    return OmieClient(credentials=credentials, settings=settings)


# ----------------------------------------------------------------------
# DTOs
# ----------------------------------------------------------------------


class TestSchemas:
    def test_conta_corrente_camelcase_alias(self) -> None:
        raw = {
            "nCodCC": 12345,
            "descricao": "Sicredi 91263-1",
            "codigo_banco": "748",
            "tipo_conta_corrente": "CC",
        }
        cc = ContaCorrente.model_validate(raw)
        assert cc.n_cod_cc == 12345
        assert cc.descricao == "Sicredi 91263-1"
        assert cc.codigo_banco == "748"
        assert cc.tipo == "CC"

    def test_lancamento_extrato_parse_brazilian_date(self) -> None:
        raw = {
            "nCodLancamento": 99,
            "cNatureza": "C",
            "dDataLancamento": "15/01/2026",
            "nValorDocumento": "1234.56",
            "cObservacoes": "Pagamento",
            "cSituacao": "Conciliado",
        }
        lanc = LancamentoExtrato.model_validate(raw)
        assert lanc.d_data_lancamento == date(2026, 1, 15)
        assert lanc.n_valor_documento == Decimal("1234.56")

    def test_lancamento_extrato_supplier_and_category_properties(self) -> None:
        """Properties resolvem o par (razão/fantasia) e (descrição/código) com fallback."""
        raw = {
            "nCodLancamento": 99,
            "cNatureza": "D",
            "dDataLancamento": "15/01/2026",
            "nValorDocumento": "100.00",
            "cSituacao": "Conciliado",
            "cRazCliente": "FORNECEDOR ABC LTDA",
            "cDesCliente": "ABC",
            "cDesCategoria": "Despesas com energia",
            "cCodCategoria": "DE",
        }
        lanc = LancamentoExtrato.model_validate(raw)
        assert lanc.supplier == "FORNECEDOR ABC LTDA"  # razão preferida
        assert lanc.category == "Despesas com energia"  # descrição preferida

        # Sem razão social, cai pro nome fantasia.
        raw_fallback = {**raw, "cRazCliente": None, "cDesCategoria": None}
        lanc_fb = LancamentoExtrato.model_validate(raw_fallback)
        assert lanc_fb.supplier == "ABC"
        assert lanc_fb.category == "DE"

    def test_signed_amount_credito_positive(self) -> None:
        lanc = LancamentoExtrato.model_validate(
            {
                "nCodLancamento": 1,
                "cNatureza": "C",
                "dDataLancamento": "01/01/2026",
                "nValorDocumento": "100.00",
                "cObservacoes": "x",
                "cSituacao": "Conciliado",
            }
        )
        assert lanc.signed_amount == Decimal("100.00")

    def test_signed_amount_debito_negative(self) -> None:
        lanc = LancamentoExtrato.model_validate(
            {
                "nCodLancamento": 1,
                "cNatureza": "D",
                "dDataLancamento": "01/01/2026",
                "nValorDocumento": "100.00",
                "cObservacoes": "x",
                "cSituacao": "Conciliado",
            }
        )
        assert lanc.signed_amount == Decimal("-100.00")

    def test_titulo_parse_data_vencimento(self) -> None:
        # Campos refletem o response real da Omie: `codigo_cliente_fornecedor`
        # (int) e `codigo_categoria` (str20) — `nome_fornecedor` e
        # `descricao_categoria` NÃO existem (auditoria CRÍTICO-5).
        raw = {
            "codigo_lancamento_omie": 42,
            "data_vencimento": "31/03/2026",
            "valor_documento": "550.00",
            "codigo_cliente_fornecedor": 1234,
            "codigo_categoria": "DE",
            "observacao": "ACME — Despesa Operacional",
            "status_titulo": "ATR",
        }
        t = TituloAPagarReceber.model_validate(raw)
        assert t.data_vencimento == date(2026, 3, 31)
        assert t.valor_documento == Decimal("550.00")
        assert t.codigo_cliente_fornecedor == 1234
        assert t.codigo_categoria == "DE"

    def test_invalid_date_raises(self) -> None:
        with pytest.raises(ValueError, match="Data Omie inválida"):
            LancamentoExtrato.model_validate(
                {
                    "nCodLancamento": 1,
                    "cNatureza": "C",
                    "dDataLancamento": "2026-01-01",  # ISO em vez de DD/MM/YYYY
                    "nValorDocumento": "10",
                    "cObservacoes": "x",
                    "cSituacao": "Conciliado",
                }
            )


# ----------------------------------------------------------------------
# OmieClient.call — fluxo geral
# ----------------------------------------------------------------------


def _omie_url(module: str, endpoint: str) -> str:
    """URL canônica esperada pelo client."""
    return f"https://app.omie.com.br/api/v1/{module}/{endpoint}/"


class TestCallSuccess:
    @respx.mock
    async def test_returns_response_dict(self, client: OmieClient) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json={"lista_clientes": [{"id": 1}]})
        )
        result = await client.call(
            module="geral",
            endpoint="clientes",
            call_name="ListarClientes",
            param={"pagina": 1},
        )
        assert result == {"lista_clientes": [{"id": 1}]}

    @respx.mock
    async def test_credentials_in_request_body(self, client: OmieClient) -> None:
        route = respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await client.call(
            module="geral",
            endpoint="clientes",
            call_name="ListarClientes",
            param={"pagina": 1},
        )
        assert route.called
        sent_body = route.calls.last.request.content.decode()
        assert "fake-app-key" in sent_body
        assert "fake-app-secret" in sent_body
        assert "ListarClientes" in sent_body


# ----------------------------------------------------------------------
# Faultstring → exceções
# ----------------------------------------------------------------------


class TestCallFault:
    @respx.mock
    async def test_auth_faultstring_raises_OmieAuthError(  # noqa: N802
        self, client: OmieClient
    ) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "faultstring": "App Key inválida",
                    "faultcode": "SOAP-ENV:Client-101",
                },
            )
        )
        with pytest.raises(OmieAuthError):
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )

    @respx.mock
    async def test_generic_faultstring_raises_OmieFaultError(  # noqa: N802
        self, client: OmieClient
    ) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                200,
                json={"faultstring": "Erro de validação no parâmetro X", "faultcode": "999"},
            )
        )
        with pytest.raises(OmieFaultError, match="validação"):
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )


# ----------------------------------------------------------------------
# Retry em 5xx e timeout
# ----------------------------------------------------------------------


class TestCallRetry:
    @respx.mock
    async def test_5xx_then_200_succeeds(self, client: OmieClient) -> None:
        responses = [
            httpx.Response(500, json={"err": "boom"}),
            httpx.Response(503, json={"err": "again"}),
            httpx.Response(200, json={"ok": True}),
        ]
        respx.post(_omie_url("geral", "clientes")).mock(side_effect=responses)
        result = await client.call(
            module="geral",
            endpoint="clientes",
            call_name="ListarClientes",
            param={"pagina": 1},
        )
        assert result == {"ok": True}

    @respx.mock
    async def test_5xx_persistent_raises_OmieServerError(self, client: OmieClient) -> None:  # noqa: N802
        """5xx repetidos sem header `OmieAPI-Error` → infra Omie instável; cai
        em `OmieServerError` (não `OmieTimeoutError`, que é específico de
        falta de resposta HTTP)."""
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(500, json={"err": "always"})
        )
        with pytest.raises(OmieServerError):
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )

    @respx.mock
    async def test_5xx_with_omie_api_error_header_raises_fault_without_retry(
        self, client: OmieClient
    ) -> None:
        """Quando a Omie responde 500 com header `OmieAPI-Error`, o erro é
        permanente (ex: tag inválida). Não fazer retry — só queima rate-limit
        e dispara o "Consumo redundante detectado". Foi exatamente o caso
        observado em 19/05/2026 (`filtrar_por_conta_corrente`)."""
        # httpx serializa headers como ASCII; o Omie real envia UTF-8 bruto,
        # mas pra esse teste basta uma versão sem acento que ainda casa em
        # "5001" e exercita o caminho de header presente.
        route = respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                500,
                headers={
                    "OmieAPI-Error": (
                        "5001 - Tag [FILTRAR_POR_CONTA_CORRENTE] nao faz parte da "
                        "estrutura do tipo complexo [lcpListarRequest]"
                    )
                },
                json={"err": "soap"},
            )
        )
        with pytest.raises(OmieFaultError) as exc_info:
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )
        assert "5001" in str(exc_info.value)
        # DoD: 1 so request — nada de retry x 3.
        assert route.call_count == 1

    @respx.mock
    async def test_timeout_raises_OmieTimeoutError(  # noqa: N802
        self, client: OmieClient
    ) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            side_effect=httpx.TimeoutException("network slow")
        )
        with pytest.raises(OmieTimeoutError):
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )

    @respx.mock
    async def test_unexpected_status_raises_OmieFaultError(  # noqa: N802
        self, client: OmieClient
    ) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(404, json={"err": "not found"})
        )
        with pytest.raises(OmieFaultError, match="404"):
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )

    @respx.mock
    async def test_invalid_json_raises_OmieFaultError(  # noqa: N802
        self, client: OmieClient
    ) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, content=b"<html>not-json</html>")
        )
        with pytest.raises(OmieFaultError, match="não-JSON"):
            await client.call(
                module="geral",
                endpoint="clientes",
                call_name="ListarClientes",
                param={"pagina": 1},
            )


# ----------------------------------------------------------------------
# Métodos tipados
# ----------------------------------------------------------------------


class TestListarContasCorrentes:
    @respx.mock
    async def test_returns_typed_items(self, client: OmieClient) -> None:
        respx.post(_omie_url("geral", "contacorrente")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "ListarContasCorrentes": [
                        {
                            "nCodCC": 1,
                            "descricao": "Sicredi",
                            "codigo_banco": "748",
                            "tipo_conta_corrente": "CC",
                        },
                        {
                            "nCodCC": 2,
                            "descricao": "Cartão",
                            "codigo_banco": "341",
                            "tipo_conta_corrente": "CA",
                        },
                    ],
                },
            )
        )
        contas = await client.listar_contas_correntes()
        assert len(contas) == 2
        assert all(isinstance(c, ContaCorrente) for c in contas)
        assert contas[0].n_cod_cc == 1
        assert contas[1].tipo == "CA"

    @respx.mock
    async def test_paginates_until_short_page(self, client: OmieClient) -> None:
        page1 = {"ListarContasCorrentes": [_make_cc(i) for i in range(100)]}
        page2 = {"ListarContasCorrentes": [_make_cc(i) for i in range(100, 150)]}
        respx.post(_omie_url("geral", "contacorrente")).mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        contas = await client.listar_contas_correntes()
        assert len(contas) == 150


def _make_cc(idx: int) -> dict[str, Any]:
    return {
        "nCodCC": idx,
        "descricao": f"Conta {idx}",
        "codigo_banco": "001",
        "tipo_conta_corrente": "CC",
    }


class TestListarExtrato:
    @respx.mock
    async def test_returns_typed_with_signed_amount(self, client: OmieClient) -> None:
        respx.post(_omie_url("financas", "extrato")).mock(
            return_value=httpx.Response(
                200,
                json={
                    # Chave canônica do envelope `eccListarExtratoResponse`.
                    # Guard contra regressão: a v1 do código usava "extrato",
                    # que NÃO existe no response real.
                    "listaMovimentos": [
                        {
                            "nCodLancamento": 10,
                            "cNatureza": "D",
                            "dDataLancamento": "10/01/2026",
                            "nValorDocumento": "500.00",
                            "cObservacoes": "Pagamento fornecedor",
                            "cSituacao": "Conciliado",
                        },
                        {
                            "nCodLancamento": 11,
                            "cNatureza": "C",
                            "dDataLancamento": "12/01/2026",
                            "nValorDocumento": "300.00",
                            "cObservacoes": "Recebimento",
                            "cSituacao": "Conciliado",
                        },
                    ]
                },
            )
        )
        items = await client.listar_extrato(
            n_cod_cc=42,
            data_inicial=date(2026, 1, 1),
            data_final=date(2026, 1, 31),
        )
        assert len(items) == 2
        assert items[0].signed_amount == Decimal("-500.00")
        assert items[1].signed_amount == Decimal("300.00")
        assert items[0].c_natureza == OmieEntryNatureza.DEBITO.value


class TestListarTitulos:
    @respx.mock
    async def test_contas_pagar_filtra_status_e_pagina(self, client: OmieClient) -> None:
        page1 = {"conta_pagar_cadastro": [_make_titulo(i) for i in range(50)]}
        page2 = {"conta_pagar_cadastro": [_make_titulo(i) for i in range(50, 73)]}  # < 50 → fim
        route = respx.post(_omie_url("financas", "contapagar")).mock(
            side_effect=[
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        items = await client.listar_contas_pagar(
            conta_corrente_id=10,
            data_de=date(2026, 1, 1),
            data_ate=date(2026, 1, 31),
            status=OmieTituloStatus.ATRASADO,
        )
        assert len(items) == 73
        assert all(isinstance(t, TituloAPagarReceber) for t in items)
        # Confere que status e nome correto do filtro foram enviados no body
        body = route.calls[0].request.content.decode()
        assert "ATRASADO" in body
        assert "filtrar_conta_corrente" in body
        # `filtrar_por_conta_corrente` (com `_por_`) seria rejeitado com erro
        # 5001 pela Omie — guard explícito contra regressão.
        assert "filtrar_por_conta_corrente" not in body

    @respx.mock
    async def test_contas_receber_chama_endpoint_correto(self, client: OmieClient) -> None:
        route = respx.post(_omie_url("financas", "contareceber")).mock(
            return_value=httpx.Response(200, json={"conta_receber_cadastro": []})
        )
        items = await client.listar_contas_receber(
            conta_corrente_id=10,
            data_de=date(2026, 1, 1),
            data_ate=date(2026, 1, 31),
            status=OmieTituloStatus.AVENCER,
        )
        assert items == []
        assert route.called


def _make_titulo(idx: int) -> dict[str, Any]:
    return {
        "codigo_lancamento_omie": idx,
        "data_vencimento": "20/01/2026",
        "valor_documento": "100.00",
        "codigo_cliente_fornecedor": 9000 + idx,
        "codigo_categoria": "DE",
        "status_titulo": "ATR",
    }


# ----------------------------------------------------------------------
# Test connection mínimo
# ----------------------------------------------------------------------


class TestListarClientesMinimal:
    @respx.mock
    async def test_returns_dict_on_success(self, client: OmieClient) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(200, json={"clientes_cadastro": []})
        )
        result = await client.listar_clientes_minimal()
        assert result == {"clientes_cadastro": []}

    @respx.mock
    async def test_invalid_credentials_raises_OmieAuthError(  # noqa: N802
        self, client: OmieClient
    ) -> None:
        respx.post(_omie_url("geral", "clientes")).mock(
            return_value=httpx.Response(
                200,
                json={
                    "faultstring": "App key não autorizada",
                    "faultcode": "SOAP-ENV:Client-101",
                },
            )
        )
        with pytest.raises(OmieAuthError):
            await client.listar_clientes_minimal()
