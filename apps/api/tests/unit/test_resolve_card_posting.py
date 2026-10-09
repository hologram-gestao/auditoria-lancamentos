"""Qual processo do cartão a sessão grava (86e3n70p0) — `resolve_card_posting`."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.exceptions import InvoiceDueDateRequiredError
from app.db.models import CardPostingDateMode
from app.modules.reconciliations.service import resolve_card_posting

pytestmark = pytest.mark.unit

DUE = date(2026, 10, 10)


def test_conta_corrente_nao_grava_modo_nem_vencimento() -> None:
    assert resolve_card_posting(
        account_type="checking",
        client_mode="invoice_due_date",
        requested_mode=CardPostingDateMode.INVOICE_DUE_DATE,
        invoice_due_date=DUE,
    ) == (None, None)


def test_cartao_sem_troca_usa_o_modo_do_cliente() -> None:
    assert resolve_card_posting(
        account_type="credit_card",
        client_mode="invoice_due_date",
        requested_mode=None,
        invoice_due_date=DUE,
    ) == ("invoice_due_date", DUE)


def test_troca_pontual_vence_o_cliente() -> None:
    assert resolve_card_posting(
        account_type="credit_card",
        client_mode="invoice_due_date",
        requested_mode=CardPostingDateMode.PURCHASE_DATE,
        invoice_due_date=None,
    ) == ("purchase_date", None)


def test_modo_compra_grava_o_vencimento_como_informativo() -> None:
    assert resolve_card_posting(
        account_type="credit_card",
        client_mode="purchase_date",
        requested_mode=None,
        invoice_due_date=DUE,
    ) == ("purchase_date", DUE)


def test_modo_vencimento_sem_data_e_422_tipado() -> None:
    with pytest.raises(InvoiceDueDateRequiredError) as exc:
        resolve_card_posting(
            account_type="credit_card",
            client_mode="purchase_date",
            requested_mode=CardPostingDateMode.INVOICE_DUE_DATE,
            invoice_due_date=None,
        )
    assert exc.value.status_code == 422
    assert exc.value.code.value == "VENCIMENTO_DA_FATURA_OBRIGATORIO"
