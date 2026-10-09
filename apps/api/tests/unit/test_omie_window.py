"""A janela única do Omie (86e3n70p0) — os dois modos travados.

`omie_window_for_session` é a decisão que processamento, cache da qualificação,
revisão, export e `omie_data` consultam (§5.3). O modo compra tem de ser o cálculo
de antes, bit a bit; o modo vencimento, o lote da fatura e nada mais.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.reconciliations.processing.matcher import DATE_DIVERGENCE_RANGE
from app.modules.reconciliations.processing.omie_window import (
    invoice_lot_date,
    omie_window_for_session,
)

pytestmark = pytest.mark.unit

SETEMBRO = (date(2026, 7, 22), date(2026, 10, 1))
VENCIMENTO = date(2026, 10, 10)


def _window(account_type: str, mode: str | None, due: date | None) -> tuple[date, date]:
    return omie_window_for_session(
        account_type=account_type,
        card_posting_date_mode=mode,
        invoice_due_date=due,
        period_start=SETEMBRO[0],
        period_end=SETEMBRO[1],
    )


class TestModoCompra:
    @pytest.mark.parametrize(
        ("account_type", "mode", "due"),
        [
            ("checking", None, None),
            ("investment", None, None),
            ("credit_card", None, None),  # sessão antiga: NULL lê como compra
            ("credit_card", "purchase_date", VENCIMENTO),  # vencimento é informativo
            ("credit_card", "purchase_date", None),
            # Conta corrente nunca entra no modo lote, nem com o valor gravado.
            ("checking", "invoice_due_date", VENCIMENTO),
        ],
    )
    def test_e_o_periodo_ampliado_de_sempre(
        self, account_type: str, mode: str | None, due: date | None
    ) -> None:
        assert _window(account_type, mode, due) == (date(2026, 7, 19), date(2026, 10, 4))
        assert (
            invoice_lot_date(
                account_type=account_type, card_posting_date_mode=mode, invoice_due_date=due
            )
            is None
        )

    def test_a_margem_e_o_range_fixo_do_matcher(self) -> None:
        start, end = _window("checking", None, None)
        assert (SETEMBRO[0] - start).days == DATE_DIVERGENCE_RANGE
        assert (end - SETEMBRO[1]).days == DATE_DIVERGENCE_RANGE


class TestModoVencimento:
    def test_e_o_lote_da_fatura_e_ignora_o_periodo_das_compras(self) -> None:
        # Compras de 22/07 a 01/10; o lote está em 10/10. A janela é o lote.
        assert _window("credit_card", "invoice_due_date", VENCIMENTO) == (
            date(2026, 10, 7),
            date(2026, 10, 13),
        )
        assert (
            invoice_lot_date(
                account_type="credit_card",
                card_posting_date_mode="invoice_due_date",
                invoice_due_date=VENCIMENTO,
            )
            == VENCIMENTO
        )

    def test_o_lote_anterior_fica_fora(self) -> None:
        start, _ = _window("credit_card", "invoice_due_date", VENCIMENTO)
        assert date(2026, 9, 10) < start

    def test_sem_vencimento_cai_no_processo_de_sempre(self) -> None:
        # Defesa: a criação recusa (422) e o banco também (CHECK de coerência).
        assert _window("credit_card", "invoice_due_date", None) == (
            date(2026, 7, 19),
            date(2026, 10, 4),
        )
