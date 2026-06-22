"""Testes unitários do `AnthropicClient` e `ExtractedStatement` (S9 BACK 7.1).

Sem chave Anthropic real disponível em CI, todos os cenários mockam o
`AsyncAnthropic` via a injeção do construtor. Validamos:

- Schemas: coerção Decimal, validação de data, sinal aritmético.
- Caminho feliz: tool_use com payload válido vira `ExtractedStatement`.
- Edge cases: free-text sem tool_use, validação Pydantic falhando (datas
  PT-BR, transactions vazio).
- Erros do SDK: timeout, auth, 5xx (retry + persistente).
- Construção do user content: PDF → document base64; outros → text block.
"""

from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)
from pydantic import SecretStr, ValidationError

from app.core.exceptions import (
    AnthropicAuthError,
    AnthropicParseError,
    AnthropicTimeoutError,
)
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.anthropic.prompts import SYSTEM_PROMPT
from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction
from app.integrations.anthropic.tools import EXTRACT_MOVEMENTS_TOOL, EXTRACT_MOVEMENTS_TOOL_NAME

# ----------------------------------------------------------------------
# Helpers / fakes
# ----------------------------------------------------------------------


_FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _ToolUseBlock:
    """Espelha `anthropic.types.ToolUseBlock` no que o client lê."""

    def __init__(self, *, name: str, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.id = "toolu_test"
        self.input = payload


class _TextBlock:
    """Espelha `anthropic.types.TextBlock`."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Message:
    """Mensagem com `.content` lista de blocks — formato consumido pelo client."""

    def __init__(self, blocks: list[Any]) -> None:
        self.content = blocks


class _FakeMessages:
    """Fake do `client.messages` capturando kwargs e devolvendo respostas."""

    def __init__(self, *, side_effect: Any | list[Any]) -> None:
        self._side_effect = side_effect
        self.calls: list[dict[str, Any]] = []
        self._iter: list[Any] = list(side_effect) if isinstance(side_effect, list) else []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._side_effect, list):
            if not self._iter:
                raise RuntimeError("Fake esgotou o side_effect")
            value = self._iter.pop(0)
        else:
            value = self._side_effect
        if isinstance(value, BaseException):
            raise value
        return value


class _FakeAnthropic:
    """Fake do `AsyncAnthropic` — único atributo usado é `messages`."""

    def __init__(self, *, side_effect: Any | list[Any]) -> None:
        self.messages = _FakeMessages(side_effect=side_effect)


def _valid_payload() -> dict[str, Any]:
    """Payload típico do tool_use — usado em vários testes."""
    return {
        "bank_name": "Sicredi",
        "account_type": "checking",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "opening_balance": "1000.00",
        "closing_balance": "1234.56",
        "transactions": [
            {
                "date": "2026-04-02",
                "description": "Pagamento fornecedor X",
                "amount": "-500.00",
                "balance": "500.00",
            },
            {
                "date": "2026-04-15",
                "description": "Recebimento cliente Y",
                "amount": 734.56,  # float vindo da Anthropic
                "balance": None,
            },
        ],
    }


def _card_invoice_payload() -> dict[str, Any]:
    """Payload de fatura de cartão (BACK 1.5): parcelas individuais, estorno com
    amount positivo, encargo (IOF) como linha separada negativa, sem pagamento."""
    return {
        "bank_name": "Nubank",
        "account_type": "credit_card",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "opening_balance": "0.00",
        "closing_balance": "-450.00",
        "transactions": [
            {"date": "2026-04-05", "description": "TENIS LOJA X 1/3", "amount": "-100.00"},
            {"date": "2026-04-05", "description": "TENIS LOJA X 2/3", "amount": "-100.00"},
            {"date": "2026-04-05", "description": "TENIS LOJA X 3/3", "amount": "-100.00"},
            {"date": "2026-04-10", "description": "ESTORNO COMPRA Y", "amount": "50.00"},
            {"date": "2026-04-30", "description": "IOF", "amount": "-200.00"},
        ],
    }


def _ok_message() -> _Message:
    return _Message([_ToolUseBlock(name=EXTRACT_MOVEMENTS_TOOL_NAME, payload=_valid_payload())])


def _make_client(fake: _FakeAnthropic) -> AnthropicClient:
    return AnthropicClient(
        api_key=SecretStr("sk-ant-fake"),
        model="claude-test",
        timeout=10.0,
        anthropic_client=fake,
    )


# ----------------------------------------------------------------------
# Schemas — coerções e validações
# ----------------------------------------------------------------------


class TestCardInvoiceExtraction:
    """FASE 1 / BACK 1.5 — a extração de fatura de cartão.

    A validação final com fatura REAL é deferida (item 6 do checklist) até o
    cliente mandar uma; aqui cobrimos o shape da saída (parcelas individuais,
    estorno positivo, encargo separado) e a presença das regras no prompt/tool.
    """

    def test_card_payload_parses_with_card_shape(self) -> None:
        stmt = ExtractedStatement.model_validate(_card_invoice_payload())
        assert stmt.account_type == "credit_card"
        # Parcelas: 3 linhas individuais com valor unitário (não agrupadas).
        parcelas = [t for t in stmt.transactions if "/3" in t.description]
        assert len(parcelas) == 3
        assert all(t.amount == Decimal("-100.00") for t in parcelas)
        # Estorno: crédito com amount positivo.
        estorno = next(t for t in stmt.transactions if "ESTORNO" in t.description)
        assert estorno.amount == Decimal("50.00")
        # Encargo (IOF): transação separada, negativa.
        iof = next(t for t in stmt.transactions if t.description == "IOF")
        assert iof.amount == Decimal("-200.00")

    def test_system_prompt_cobre_regras_de_fatura(self) -> None:
        p = SYSTEM_PROMPT.lower()
        assert "parcela" in p
        assert "estorno" in p
        assert "iof" in p
        assert "juros" in p
        assert "multa" in p
        # Regra de não incluir o pagamento da fatura.
        assert "pagamento" in p

    def test_tool_transactions_description_menciona_cartao(self) -> None:
        desc = EXTRACT_MOVEMENTS_TOOL["input_schema"]["properties"]["transactions"][
            "description"
        ].lower()
        assert "parcela" in desc
        assert "estorno" in desc


class TestExtractedStatementSchema:
    def test_valid_payload_parses(self) -> None:
        stmt = ExtractedStatement.model_validate(_valid_payload())
        assert stmt.bank_name == "Sicredi"
        assert stmt.account_type == "checking"
        assert stmt.period_start == date(2026, 4, 1)
        assert stmt.opening_balance == Decimal("1000.00")
        assert len(stmt.transactions) == 2

    def test_amount_float_coerced_to_decimal_via_str(self) -> None:
        """Float chega do JSON e VIRA Decimal via str() — sem ruído binário."""
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "x", "amount": 100.10}
        )
        assert tx.amount == Decimal("100.10")
        assert isinstance(tx.amount, Decimal)

    def test_amount_int_coerced_to_decimal(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "x", "amount": 100}
        )
        assert tx.amount == Decimal("100")

    def test_amount_string_coerced_to_decimal(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "x", "amount": "100.50"}
        )
        assert tx.amount == Decimal("100.50")

    def test_negative_amount_preserved(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "x", "amount": "-50.00"}
        )
        assert tx.amount == Decimal("-50.00")

    def test_balance_can_be_null(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "x", "amount": "100", "balance": None}
        )
        assert tx.balance is None

    def test_brazilian_date_rejected(self) -> None:
        """Modelo desobedeceu o system prompt — Pydantic explode aqui."""
        bad = _valid_payload()
        bad["transactions"][0]["date"] = "01/04/2026"
        with pytest.raises(ValidationError):
            ExtractedStatement.model_validate(bad)

    def test_empty_transactions_rejected(self) -> None:
        bad = _valid_payload()
        bad["transactions"] = []
        with pytest.raises(ValidationError):
            ExtractedStatement.model_validate(bad)

    def test_invalid_account_type_rejected(self) -> None:
        bad = _valid_payload()
        bad["account_type"] = "savings"
        with pytest.raises(ValidationError):
            ExtractedStatement.model_validate(bad)

    def test_invalid_amount_string_rejected(self) -> None:
        bad = _valid_payload()
        bad["transactions"][0]["amount"] = "abc"
        with pytest.raises(ValidationError):
            ExtractedStatement.model_validate(bad)


# ----------------------------------------------------------------------
# AnthropicClient — caminho feliz
# ----------------------------------------------------------------------


class TestExtractMovementsHappyPath:
    async def test_returns_validated_statement(self) -> None:
        fake = _FakeAnthropic(side_effect=_ok_message())
        client = _make_client(fake)

        stmt = await client.extract_movements(
            content=b"%PDF-1.7\n...",
            mime_type="application/pdf",
            document_kind="extrato em PDF",
        )

        assert isinstance(stmt, ExtractedStatement)
        assert stmt.bank_name == "Sicredi"
        assert stmt.transactions[0].amount == Decimal("-500.00")
        assert stmt.transactions[1].amount == Decimal("734.56")
        # SDK foi chamado com tool_choice forçado e schema correto
        assert len(fake.messages.calls) == 1
        kwargs = fake.messages.calls[0]
        assert kwargs["tool_choice"] == {
            "type": "tool",
            "name": EXTRACT_MOVEMENTS_TOOL_NAME,
        }
        assert kwargs["model"] == "claude-test"

    async def test_pdf_sent_as_base64_document(self) -> None:
        fake = _FakeAnthropic(side_effect=_ok_message())
        client = _make_client(fake)

        pdf_bytes = b"%PDF-1.7\n%fake-content"
        await client.extract_movements(
            content=pdf_bytes,
            mime_type="application/pdf",
            document_kind="x",
        )

        msg = fake.messages.calls[0]["messages"][0]
        blocks = msg["content"]
        # 1º bloco: document base64; 2º: text instruction
        assert blocks[0]["type"] == "document"
        assert blocks[0]["source"]["type"] == "base64"
        assert blocks[0]["source"]["media_type"] == "application/pdf"
        decoded = base64.b64decode(blocks[0]["source"]["data"])
        assert decoded == pdf_bytes
        assert blocks[1]["type"] == "text"

    async def test_csv_sent_as_text_block(self) -> None:
        fake = _FakeAnthropic(side_effect=_ok_message())
        client = _make_client(fake)

        csv_bytes = b"data,desc,valor\n2026-04-01,Pagamento,-100.00\n"
        await client.extract_movements(
            content=csv_bytes,
            mime_type="text/csv",
            document_kind="x",
        )

        blocks = fake.messages.calls[0]["messages"][0]["content"]
        assert blocks[0]["type"] == "text"
        assert "Pagamento" in blocks[0]["text"]

    async def test_model_override(self) -> None:
        fake = _FakeAnthropic(side_effect=_ok_message())
        client = _make_client(fake)

        await client.extract_movements(
            content=b"%PDF-",
            mime_type="application/pdf",
            document_kind="x",
            model="claude-opus-override",
        )
        assert fake.messages.calls[0]["model"] == "claude-opus-override"


# ----------------------------------------------------------------------
# Tool use ausente / inválido
# ----------------------------------------------------------------------


class TestToolUseEdgeCases:
    async def test_no_tool_use_raises_parse_error(self) -> None:
        """Modelo respondeu free-text — fallback que NUNCA deveria acontecer
        com tool_choice forçado, mas garantimos a mensagem clara."""
        fake = _FakeAnthropic(side_effect=_Message([_TextBlock("desculpa, não sei extrair")]))
        client = _make_client(fake)

        with pytest.raises(AnthropicParseError, match="Modelo não chamou"):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )

    async def test_brazilian_date_in_tool_input_raises_parse_error(self) -> None:
        """Modelo desobedeceu instruções de data — schema rejeita; client mapeia."""
        bad = _valid_payload()
        bad["transactions"][0]["date"] = "31/03/2026"
        fake = _FakeAnthropic(
            side_effect=_Message([_ToolUseBlock(name=EXTRACT_MOVEMENTS_TOOL_NAME, payload=bad)])
        )
        client = _make_client(fake)

        with pytest.raises(AnthropicParseError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )

    async def test_empty_transactions_raises_parse_error(self) -> None:
        bad = _valid_payload()
        bad["transactions"] = []
        fake = _FakeAnthropic(
            side_effect=_Message([_ToolUseBlock(name=EXTRACT_MOVEMENTS_TOOL_NAME, payload=bad)])
        )
        client = _make_client(fake)
        with pytest.raises(AnthropicParseError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )

    async def test_wrong_tool_name_ignored_raises_parse_error(self) -> None:
        """Bloco tool_use com nome diferente do esperado — tratamos como
        ausência da tool real."""
        fake = _FakeAnthropic(
            side_effect=_Message([_ToolUseBlock(name="outra_tool", payload=_valid_payload())])
        )
        client = _make_client(fake)
        with pytest.raises(AnthropicParseError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )


# ----------------------------------------------------------------------
# Erros do SDK
# ----------------------------------------------------------------------


def _api_status_error(status: int) -> APIStatusError:
    return APIStatusError(
        message=f"HTTP {status}",
        response=httpx.Response(status, request=_FAKE_REQUEST),
        body={"error": {"type": "test"}},
    )


class TestSdkErrorMapping:
    async def test_timeout_raises_anthropic_timeout(self) -> None:
        fake = _FakeAnthropic(side_effect=APITimeoutError(_FAKE_REQUEST))
        client = _make_client(fake)
        with pytest.raises(AnthropicTimeoutError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )

    async def test_auth_error_raises_anthropic_auth(self) -> None:
        fake = _FakeAnthropic(
            side_effect=AuthenticationError(
                message="invalid x-api-key",
                response=httpx.Response(401, request=_FAKE_REQUEST),
                body={"error": {"type": "authentication_error"}},
            )
        )
        client = _make_client(fake)
        with pytest.raises(AnthropicAuthError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )

    async def test_5xx_then_200_succeeds(self) -> None:
        """Retry de 1 tentativa basta — segunda chamada retorna OK."""
        fake = _FakeAnthropic(
            side_effect=[_api_status_error(500), _ok_message()],
        )
        client = _make_client(fake)
        stmt = await client.extract_movements(
            content=b"%PDF-",
            mime_type="application/pdf",
            document_kind="x",
        )
        assert stmt.bank_name == "Sicredi"
        assert len(fake.messages.calls) == 2

    async def test_5xx_persistent_raises_timeout(self) -> None:
        """Após esgotar retry, mapeia para AnthropicTimeoutError (UX
        equivalente: 'tente novamente')."""
        fake = _FakeAnthropic(
            side_effect=[_api_status_error(500), _api_status_error(503)],
        )
        client = _make_client(fake)
        with pytest.raises(AnthropicTimeoutError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )

    async def test_connection_error_then_success(self) -> None:
        fake = _FakeAnthropic(
            side_effect=[
                APIConnectionError(message="net fail", request=_FAKE_REQUEST),
                _ok_message(),
            ],
        )
        client = _make_client(fake)
        stmt = await client.extract_movements(
            content=b"%PDF-",
            mime_type="application/pdf",
            document_kind="x",
        )
        assert stmt.bank_name == "Sicredi"

    async def test_4xx_non_auth_raises_parse_error(self) -> None:
        """413 (payload too large) ou 400 (request mal formado) não passa por
        retry — vira ParseError com mensagem genérica."""
        fake = _FakeAnthropic(side_effect=_api_status_error(413))
        client = _make_client(fake)
        with pytest.raises(AnthropicParseError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )


# ----------------------------------------------------------------------
# Configuração ausente
# ----------------------------------------------------------------------


class TestMissingApiKey:
    async def test_empty_key_without_injected_client_raises_auth(self) -> None:
        """Sem chave configurada e sem fake injetado: erro mapeado para
        AnthropicAuthError ANTES de qualquer chamada de rede."""
        client = AnthropicClient(
            api_key=SecretStr(""),
            model="claude-test",
            timeout=1.0,
            anthropic_client=None,
        )
        with pytest.raises(AnthropicAuthError):
            await client.extract_movements(
                content=b"%PDF-",
                mime_type="application/pdf",
                document_kind="x",
            )
