"""Testes do evento de instrumentação `parse_concluido` (BACK 02.2).

Cobre os DOIS caminhos exigidos pela sprint: parse OK e parse truncado
(`AnthropicTruncatedError`). Verifica os campos EXATOS do evento e a ausência
de PII. `capture_logs` intercepta o event_dict cru passado ao logger.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import structlog

from app.core.exceptions import AnthropicTruncatedError
from app.integrations.anthropic.client import ExtractionResult
from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction
from app.modules.reconciliations.parse_service import ParseService

pytestmark = pytest.mark.asyncio

_PDF_BYTES = b"%PDF-1.7\n" + b"conteudo-fake-do-pdf\n" * 4

_EXACT_EVENT_KEYS = {
    "session_id",
    "input_tokens",
    "output_tokens",
    "stop_reason",
    "n_transacoes",
    "file_bytes",
    "modelo",
}

# Nenhuma destas substrings pode aparecer nas CHAVES do evento (anti-PII).
_PII_KEY_SUBSTRINGS = ("description", "descricao", "content", "filename", "nome", "bank")


def _statement() -> ExtractedStatement:
    return ExtractedStatement(
        bank_name="Sicredi",
        account_type="checking",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        opening_balance=Decimal("1000.00"),
        closing_balance=Decimal("1200.00"),
        transactions=[
            ExtractedTransaction(
                date=date(2026, 4, 2),
                description="Pagamento fornecedor X",
                amount=Decimal("-500.00"),
                balance=None,
            ),
            ExtractedTransaction(
                date=date(2026, 4, 3),
                description="Recebimento cliente Y",
                amount=Decimal("700.00"),
                balance=None,
            ),
        ],
    )


class _FakeAnthropicOk:
    async def extract_movements(self, **_kwargs: Any) -> ExtractionResult:
        return ExtractionResult(
            statement=_statement(),
            stop_reason="tool_use",
            input_tokens=1500,
            output_tokens=2400,
            model="claude-sonnet-4-5",
        )


class _FakeAnthropicTruncated:
    async def extract_movements(self, **_kwargs: Any) -> ExtractionResult:
        raise AnthropicTruncatedError(
            "truncou",
            input_tokens=1800,
            output_tokens=32000,
            model="claude-sonnet-4-5",
        )


def _find_parse_event(logs: list[dict[str, Any]]) -> dict[str, Any]:
    events = [e for e in logs if e.get("event") == "parse_concluido"]
    assert len(events) == 1, f"esperava 1 parse_concluido, veio {len(events)}"
    return events[0]


def _assert_no_pii(event: dict[str, Any]) -> None:
    for key in event:
        low = key.lower()
        assert not any(sub in low for sub in _PII_KEY_SUBSTRINGS), f"chave PII no evento: {key}"


class TestParseConcluidoEvent:
    async def test_emitted_on_success_with_exact_fields(self) -> None:
        service = ParseService(_FakeAnthropicOk())  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as logs:
            stmt = await service.parse_statement(
                file_bytes=_PDF_BYTES,
                filename="extrato.pdf",
                max_upload_bytes=20 * 1024 * 1024,
            )
        assert stmt.bank_name == "Sicredi"
        event = _find_parse_event(logs)
        # log_level/event são metadados do structlog; o resto são os campos EXATOS.
        payload_keys = set(event) - {"event", "log_level"}
        assert payload_keys == _EXACT_EVENT_KEYS
        assert event["input_tokens"] == 1500
        assert event["output_tokens"] == 2400
        assert event["stop_reason"] == "tool_use"
        assert event["n_transacoes"] == 2
        assert event["file_bytes"] == len(_PDF_BYTES)
        assert event["modelo"] == "claude-sonnet-4-5"
        assert event["session_id"] is None
        _assert_no_pii(event)

    async def test_emitted_on_truncation_path(self) -> None:
        service = ParseService(_FakeAnthropicTruncated())  # type: ignore[arg-type]
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(AnthropicTruncatedError),
        ):
            await service.parse_statement(
                file_bytes=_PDF_BYTES,
                filename="extrato.pdf",
                max_upload_bytes=20 * 1024 * 1024,
            )
        event = _find_parse_event(logs)
        payload_keys = set(event) - {"event", "log_level"}
        assert payload_keys == _EXACT_EVENT_KEYS
        assert event["stop_reason"] == "max_tokens"
        assert event["output_tokens"] == 32000
        # Truncou → nada extraído, nada gravado.
        assert event["n_transacoes"] == 0
        assert event["file_bytes"] == len(_PDF_BYTES)
        assert event["modelo"] == "claude-sonnet-4-5"
        _assert_no_pii(event)

    async def test_mock_path_also_emits(self) -> None:
        service = ParseService(
            _FakeAnthropicOk(),  # type: ignore[arg-type]
            mock_enabled=True,
            mock_delay_seconds=0.0,
        )
        with structlog.testing.capture_logs() as logs:
            await service.parse_statement(
                file_bytes=_PDF_BYTES,
                filename="extrato.pdf",
                max_upload_bytes=20 * 1024 * 1024,
            )
        event = _find_parse_event(logs)
        payload_keys = set(event) - {"event", "log_level"}
        assert payload_keys == _EXACT_EVENT_KEYS
        assert event["modelo"] == "mock-demo"
        assert event["input_tokens"] is None
        assert event["n_transacoes"] > 0
