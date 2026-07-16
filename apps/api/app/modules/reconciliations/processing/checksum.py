"""Checksum de saldos do parse (BACK 02.3) — a defesa contra parse incompleto.

Função PURA (sem I/O) sobre o `ExtractedStatement` — testável sem DB nem
Anthropic. É a rede que pega o que o truncamento deixar passar: se linhas
sumiram ou um valor foi adulterado, a identidade não fecha.

Identidades (tolerância R$ 0,01, aritmética Decimal — CLAUDE.md §4.4/§5.1):

    Conta corrente (identidade universal do extrato bancário):
        saldo_inicial + Σ(amount) == saldo_final

    Cartão (⚠️ depende de S-1 — semântica contábil do BPO, não testada):
        Σ(amount, exceto is_payment, invertendo o sinal de débito) == total
        onde total = closing_balance (total da fatura) declarado.

    Conta aplicação (`investment`): NÃO VERIFICÁVEL — ver abaixo.

`amount` já vem com sinal aritmético (crédito +, débito -). Numa fatura de
cartão as compras/encargos são débitos (negativos) e estornos são créditos
(positivos); o total da fatura é positivo. Por isso invertemos o sinal da soma
(`-Σ`): compras viram positivas e somam ao total, estornos reduzem. Ex.:
compras -1200, encargos -30, estorno +50, pagamento +3000 (is_payment, excluído)
→ `-(-1200-30+50)` = 1180 == total.

⚠️ **`investment` não fecha por construção.** O prompt de extração manda NÃO
emitir IOF, IR nem rendimento como transações separadas (`prompts.py`, regra
14) — eles entram no saldo sem virar movimentação. Logo
`inicial + Σ != final` mesmo num parse PERFEITO. Aplicar aqui a regra de conta
corrente bloquearia conciliações válidas (falso positivo), que é o oposto do
objetivo. Por isso `applicable=False` e `ok=True`: os números continuam sendo
calculados (informativos), mas não barram a prévia.
"""

from __future__ import annotations

from decimal import Decimal

from app.integrations.anthropic.schemas import ExtractedStatement
from app.modules.reconciliations.schemas import ChecksumResult

# Tolerância de valor: R$ 0,01, hard-coded (CLAUDE.md §5.1). Mesma do matcher.
CHECKSUM_TOLERANCE = Decimal("0.01")


def _fmt(value: Decimal) -> str:
    """Formata Decimal com 2 casas (o front re-localiza para pt-BR)."""
    return f"{value:.2f}"


def compute_checksum(statement: ExtractedStatement) -> ChecksumResult:
    """Calcula o checksum de saldos do statement extraído.

    Não levanta exceção — devolve `ChecksumResult` com `ok` e, quando falha,
    uma `reason` acionável em PT-BR para o front bloquear a prévia.
    """
    transactions = statement.transactions
    expected = statement.closing_balance

    if statement.account_type == "credit_card":
        # Débitos são negativos; o total da fatura é positivo → inverte o sinal.
        computed = -sum(
            (tx.amount for tx in transactions if not tx.is_payment),
            start=Decimal("0"),
        )
    else:
        # `checking` e `investment` compartilham a reconstrução por saldo; só o
        # `checking` a usa como VEREDITO (ver `applicable` abaixo).
        computed = statement.opening_balance + sum(
            (tx.amount for tx in transactions), start=Decimal("0")
        )

    difference = expected - computed
    within_tolerance = abs(difference) <= CHECKSUM_TOLERANCE

    # `investment`: rendimento/IOF/IR não são emitidos como transação, então a
    # identidade não fecha nem num parse perfeito → não é veredito.
    applicable = statement.account_type != "investment"
    ok = True if not applicable else within_tolerance

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
        applicable=applicable,
        account_type=statement.account_type,
        expected=expected,
        computed=computed,
        difference=difference,
        tolerance=CHECKSUM_TOLERANCE,
        reason=reason,
    )
