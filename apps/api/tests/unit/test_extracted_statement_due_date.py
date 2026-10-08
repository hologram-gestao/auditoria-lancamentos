"""Vencimento da fatura no parse (86e3n70p0): só cartão, nunca inventado, sobrevive aos blocos."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedStatementBlock
from app.modules.reconciliations.parse_chunking import merge_statements
from app.modules.reconciliations.schemas import ParseResponse


def _payload(account_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "bank_name": "Cora",
        "account_type": account_type,
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "opening_balance": 0,
        "closing_balance": 1164.22,
        "transactions": [{"date": "2026-09-08", "description": "COMPRA", "amount": -591.91}],
        **extra,
    }


@pytest.mark.unit
class TestVencimentoNoSchema:
    def test_fatura_de_cartao_traz_o_vencimento(self) -> None:
        statement = ExtractedStatement.model_validate(
            _payload("credit_card", invoice_due_date="2026-10-10")
        )
        assert statement.invoice_due_date == date(2026, 10, 10)

    def test_sem_vencimento_no_documento_fica_none(self) -> None:
        statement = ExtractedStatement.model_validate(_payload("credit_card"))
        assert statement.invoice_due_date is None

    @pytest.mark.parametrize("account_type", ["checking", "investment"])
    def test_fora_do_cartao_o_vencimento_e_descartado(self, account_type: str) -> None:
        statement = ExtractedStatement.model_validate(
            _payload(account_type, invoice_due_date="2026-10-10")
        )
        assert statement.invoice_due_date is None

    def test_data_em_formato_local_falha_alto(self) -> None:
        with pytest.raises(ValueError, match="invoice_due_date"):
            ExtractedStatement.model_validate(
                _payload("credit_card", invoice_due_date="10/10/2026")
            )

    def test_parse_response_devolve_o_vencimento(self) -> None:
        statement = ExtractedStatement.model_validate(
            _payload("credit_card", invoice_due_date="2026-10-10")
        )
        body = ParseResponse(
            data=statement,
            checksum={  # type: ignore[arg-type]
                "ok": True,
                "applicable": True,
                "account_type": "credit_card",
                "expected": "0",
                "computed": "0",
                "difference": "0",
                "tolerance": "0.01",
            },
            file_hash="a" * 64,
        ).model_dump(mode="json")
        assert body["data"]["invoice_due_date"] == "2026-10-10"


def _block(due: str | None) -> ExtractedStatementBlock:
    extra = {"invoice_due_date": due} if due is not None else {}
    return ExtractedStatementBlock.model_validate(_payload("credit_card", **extra))


@pytest.mark.unit
class TestVencimentoNaJuncaoDosBlocos:
    def test_vale_o_primeiro_bloco_que_trouxe(self) -> None:
        merged = merge_statements([_block(None), _block("2026-10-10"), _block(None)])
        assert merged.invoice_due_date == date(2026, 10, 10)

    def test_nenhum_bloco_trouxe(self) -> None:
        assert merge_statements([_block(None), _block(None)]).invoice_due_date is None

    def test_divergencia_nao_derruba_e_loga_so_numeros(self) -> None:
        with capture_logs() as logs:
            merged = merge_statements([_block("2026-10-10"), _block("2026-10-11")])
        assert merged.invoice_due_date == date(2026, 10, 10)
        evento = next(e for e in logs if e["event"] == "parse_invoice_due_date_divergence")
        assert evento["distinct"] == 2
        assert "2026-10" not in str(evento)
