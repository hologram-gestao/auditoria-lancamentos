"""Checksum de saldos do parse (BACK 02.3) — a defesa contra parse incompleto.

Função PURA (sem I/O) sobre o `ExtractedStatement` — testável sem DB nem
Anthropic. É a rede que pega o que o truncamento (BACK 02.1) deixar passar:
se linhas sumiram ou um valor foi adulterado, a identidade não fecha.

Identidades (tolerancia R$ 0,01, aritmetica Decimal — CLAUDE.md 3.4/5.1):

    Conta corrente (identidade universal do extrato bancario):
        saldo_inicial + soma(amount) == saldo_final

    Cartao (⚠️ depende de S-1 — semantica contabil do BPO, nao testada):
        soma(amount, exceto is_payment, invertendo o sinal de debito) == total
        onde total = closing_balance (total da fatura) declarado.

`amount` ja vem com sinal aritmetico (credito +, debito -). Numa fatura de
cartao as compras/encargos sao debitos (negativos) e estornos sao creditos
(positivos); o total da fatura e positivo. Por isso invertemos o sinal da soma
(`-soma`): compras viram positivas e somam ao total, estornos reduzem. Ex.:
compras -1200, encargos -30, estorno +50, pagamento +3000 (is_payment, excluido)
-> `-(-1200-30+50)` = 1180 == total.
"""

from __future__ import annotations

from decimal import Decimal

from app.integrations.anthropic.schemas import ExtractedStatement
from app.modules.reconciliations.schemas import ChecksumResult

# Tolerância de valor: R$ 0,01, hard-coded (CLAUDE.md §5.1). Mesma do matcher.
CHECKSUM_TOLERANCE = Decimal("0.01")


def _fmt(value: Decimal) -> str:
    """Formata Decimal como R$ com 2 casas (o front pode re-localizar)."""
    return f"{value:.2f}"


def compute_checksum(statement: ExtractedStatement) -> ChecksumResult:
    """Calcula o checksum de saldos do statement extraído.

    Não levanta exceção — devolve `ChecksumResult` com `ok` e, quando falha,
    uma `reason` acionável em PT-BR para o front bloquear a prévia.
    """
    if statement.account_type == "checking":
        computed = statement.opening_balance + sum(
            (tx.amount for tx in statement.transactions), start=Decimal("0")
        )
        expected = statement.closing_balance
    else:  # credit_card
        non_payment_sum = sum(
            (tx.amount for tx in statement.transactions if not tx.is_payment),
            start=Decimal("0"),
        )
        # Débitos são negativos; o total da fatura é positivo → inverte o sinal.
        computed = -non_payment_sum
        expected = statement.closing_balance

    difference = expected - computed
    ok = abs(difference) <= CHECKSUM_TOLERANCE

    reason: str | None = None
    if not ok:
        if statement.account_type == "checking":
            reason = (
                f"O extrato não fecha: saldo inicial R$ {_fmt(statement.opening_balance)} "
                f"mais as movimentações dá R$ {_fmt(computed)}, mas o saldo final "
                f"declarado é R$ {_fmt(expected)} (diferença de "
                f"R$ {_fmt(abs(difference))}). Pode haver transação faltando ou valor "
                "incorreto — revise antes de conciliar."
            )
        else:
            reason = (
                "A fatura não fecha: a soma das movimentações (excluindo pagamentos "
                f"da fatura anterior) dá R$ {_fmt(computed)}, mas o total declarado é "
                f"R$ {_fmt(expected)} (diferença de R$ {_fmt(abs(difference))}). Pode "
                "haver lançamento faltando ou valor incorreto — revise antes de conciliar."
            )

    return ChecksumResult(
        ok=ok,
        account_type=statement.account_type,
        expected=expected,
        computed=computed,
        difference=difference,
        tolerance=CHECKSUM_TOLERANCE,
        reason=reason,
    )
