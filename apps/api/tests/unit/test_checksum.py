"""Testes do checksum de saldos (BACK 02.3).

Cobre as duas identidades (conta corrente e cartão) e os casos negativos que
a sprint exige: fatura adulterada (anti-tautologia) e parse que perdeu linhas
(rede do truncamento). ⚠️ O caminho de cartão depende de S-1 (assumida, não
testada) — aqui verificamos a MECÂNICA do checksum sob a suposição declarada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction
from app.modules.reconciliations.processing.checksum import compute_checksum


def _tx(amount: str, *, is_payment: bool = False, desc: str = "mov") -> ExtractedTransaction:
    return ExtractedTransaction(
        date=date(2026, 4, 2),
        description=desc,
        amount=Decimal(amount),
        balance=None,
        is_payment=is_payment,
    )


def _checking(txs: list[ExtractedTransaction], *, opening: str, closing: str) -> ExtractedStatement:
    return ExtractedStatement(
        bank_name="Sicredi",
        account_type="checking",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        opening_balance=Decimal(opening),
        closing_balance=Decimal(closing),
        transactions=txs,
    )


def _card(txs: list[ExtractedTransaction], *, opening: str, closing: str) -> ExtractedStatement:
    return ExtractedStatement(
        bank_name="Nubank",
        account_type="credit_card",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        opening_balance=Decimal(opening),
        closing_balance=Decimal(closing),
        transactions=txs,
    )


class TestChecking:
    def test_balanced_passes(self) -> None:
        stmt = _checking([_tx("-500.00"), _tx("734.56")], opening="1000.00", closing="1234.56")
        result = compute_checksum(stmt)
        assert result.ok is True
        assert result.reason is None
        assert result.computed == Decimal("1234.56")
        assert result.expected == Decimal("1234.56")

    def test_within_tolerance_passes(self) -> None:
        # Diferença de 0,01 exatamente ainda fecha.
        stmt = _checking([_tx("-500.00"), _tx("734.55")], opening="1000.00", closing="1234.56")
        assert compute_checksum(stmt).ok is True

    def test_adulterated_line_is_blocked(self) -> None:
        # Anti-tautologia: um valor adulterado NÃO passa.
        stmt = _checking([_tx("-600.00"), _tx("734.56")], opening="1000.00", closing="1234.56")
        result = compute_checksum(stmt)
        assert result.ok is False
        assert result.reason is not None
        assert "não fecha" in result.reason
        assert result.difference == Decimal("100.00")

    def test_missing_line_is_blocked(self) -> None:
        # Rede do truncamento: uma linha perdida quebra o checksum.
        stmt = _checking([_tx("-500.00")], opening="1000.00", closing="1234.56")
        result = compute_checksum(stmt)
        assert result.ok is False
        assert result.computed == Decimal("500.00")


class TestCreditCard:
    def test_balanced_with_payment_excluded(self) -> None:
        # Exemplo do PRD: fatura anterior 3000 paga; compras 1200, encargos 30,
        # estorno 50. Total da fatura = 1180. Pagamento é is_payment (excluído).
        stmt = _card(
            [
                _tx("-1200.00", desc="COMPRA"),
                _tx("-30.00", desc="ENCARGO"),
                _tx("50.00", desc="ESTORNO"),
                _tx("3000.00", is_payment=True, desc="PAGAMENTO FATURA"),
            ],
            opening="3000.00",
            closing="1180.00",
        )
        result = compute_checksum(stmt)
        assert result.ok is True, result.reason
        assert result.computed == Decimal("1180.00")

    def test_payment_not_marked_would_break(self) -> None:
        # Se o pagamento NÃO fosse marcado is_payment, entraria na soma e
        # quebraria — prova que a marcação é o que faz o checksum fechar.
        stmt = _card(
            [
                _tx("-1200.00"),
                _tx("-30.00"),
                _tx("50.00"),
                _tx("3000.00", is_payment=False, desc="PAGAMENTO NAO MARCADO"),
            ],
            opening="3000.00",
            closing="1180.00",
        )
        assert compute_checksum(stmt).ok is False

    def test_adulterated_card_line_is_blocked(self) -> None:
        stmt = _card(
            [_tx("-1300.00"), _tx("-30.00"), _tx("50.00")],
            opening="3000.00",
            closing="1180.00",
        )
        result = compute_checksum(stmt)
        assert result.ok is False
        assert result.account_type == "credit_card"

    def test_missing_card_line_is_blocked(self) -> None:
        # Estorno some (truncamento) → soma muda → bloqueia.
        stmt = _card([_tx("-1200.00"), _tx("-30.00")], opening="3000.00", closing="1180.00")
        assert compute_checksum(stmt).ok is False


class TestIsPaymentSchema:
    def test_defaults_false_when_absent(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "x", "amount": "-10.00"}
        )
        assert tx.is_payment is False

    def test_parsed_when_present(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-04-01", "description": "PGTO", "amount": "100", "is_payment": True}
        )
        assert tx.is_payment is True


def _investment(
    txs: list[ExtractedTransaction], *, opening: str, closing: str
) -> ExtractedStatement:
    return ExtractedStatement(
        bank_name="BTG",
        account_type="investment",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        opening_balance=Decimal(opening),
        closing_balance=Decimal(closing),
        transactions=txs,
    )


class TestInvestmentNotApplicable:
    """Conta aplicação não é verificável por identidade de saldo.

    O prompt manda NÃO emitir IOF, IR nem rendimento como transação (regra 14):
    eles entram no saldo sem virar movimentação. Logo `inicial + Σ != final`
    mesmo num parse PERFEITO — aplicar a regra de conta corrente aqui
    bloquearia conciliações válidas.
    """

    def test_rendimento_nao_lancado_nao_bloqueia(self) -> None:
        # Aplicação de 500 sobre saldo 1000; o extrato fecha em 1530 porque
        # rendeu 30 — rendimento que, por regra, não vira transação.
        stmt = _investment([_tx("500")], opening="1000", closing="1530")
        result = compute_checksum(stmt)

        assert result.applicable is False
        assert result.ok is True  # não bloqueia
        assert result.reason is None
        # Os números seguem honestos: computed é a reconstrução real.
        assert result.computed == Decimal("1500")
        assert result.expected == Decimal("1530")
        assert result.difference == Decimal("30")

    def test_investment_nunca_bloqueia_mesmo_com_diferenca_grande(self) -> None:
        stmt = _investment([_tx("10")], opening="0", closing="99999")
        result = compute_checksum(stmt)

        assert result.applicable is False
        assert result.ok is True
        assert result.reason is None

    def test_checking_e_card_permanecem_aplicaveis(self) -> None:
        # Guarda contra regressão: só `investment` sai do veredito.
        assert (
            compute_checksum(_checking([_tx("10")], opening="0", closing="10")).applicable is True
        )
        assert compute_checksum(_card([_tx("-10")], opening="0", closing="10")).applicable is True
