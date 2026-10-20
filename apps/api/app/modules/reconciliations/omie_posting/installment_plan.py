"""As datas e o valor de cada parcela FUTURA de uma compra parcelada (86e3qhhtb).

Regra das respostas do Gabriel (19/10/2026), uma função só, sem I/O. É ela que a
revisão usa para mostrar a lista que a pessoa confere na gaveta e é ela que o
serviço usa ao criar os títulos, então tela e Omie não divergem:

  (a) **vencimento** da parcela `j`: o MESMO DIA da compra, mês a mês ("comprou
      dia 10 de setembro: 10 de outubro, 10 de novembro…"). Dia que não existe no
      mês (31 em novembro) é o último dia do mês;
  (c) **emissão e previsão** do título: no processo "na data da compra", a DATA DA
      FATURA em que a parcela cai, o vencimento da fatura corrente `k` meses
      depois; no processo "vencimento da fatura", o próprio vencimento da parcela.
      Sem vencimento da fatura gravado na sessão, cai no vencimento da parcela.

**Que data a linha da parcela traz depende do emissor**, e isso foi medido: na
fatura real da Cora (13/10/2026, período 05/09 a 05/10) a linha `3/6` vem com a
data ORIGINAL da compra (22/07); outros emissores imprimem a data da parcela. A
regra cobre os dois sem perguntar: a data da parcela corrente é a candidata mais
perto da fatura (`invoice_anchor`, a compra mais recente da conciliação), entre a
data da linha e a data da linha mais `N-1` meses. Na Cora: 22/07 está a 74 dias
da âncora, 22/09 a 12 — a parcela 3 é de 22/09, e a 4 vence em 22/10.

O valor é o da parcela corrente: parcela de cartão é valor unitário igual.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from app.db.models import CardPostingDateMode

if TYPE_CHECKING:
    from decimal import Decimal

    from app.modules.reconciliations.installments import Installment


@dataclass(frozen=True, slots=True)
class PlannedInstallment:
    """Uma parcela futura a provisionar: número, datas do título e valor (positivo)."""

    number: int
    total: int
    due_date: date
    issue_date: date
    amount: Decimal


def add_months(day: date, months: int) -> date:
    """`day` daqui a `months` meses, no mesmo dia; dia inexistente vira o último do mês."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _months_to_current(installment: Installment, line_date: date, invoice_anchor: date) -> int:
    """Quantos meses somar à data da linha para chegar à data da parcela corrente.

    `0` quando a linha já traz a data da parcela; `N-1` quando traz a da compra.
    Empate fica com a data da linha (o emissor que imprime a data da parcela).
    """
    shifted = installment.number - 1
    if shifted == 0:
        return 0
    as_printed = abs((line_date - invoice_anchor).days)
    as_original = abs((add_months(line_date, shifted) - invoice_anchor).days)
    return shifted if as_original < as_printed else 0


def current_installment_date(
    installment: Installment, line_date: date, invoice_anchor: date
) -> date:
    """A data da parcela `N` (a desta fatura), lida como a regra do cabeçalho manda."""
    return add_months(line_date, _months_to_current(installment, line_date, invoice_anchor))


def plan_future_installments(
    *,
    installment: Installment,
    line_date: date,
    invoice_anchor: date,
    amount: Decimal,
    process: str,
    invoice_due_date: date | None,
) -> list[PlannedInstallment]:
    """As parcelas `N+1` a `M`, em ordem. Lista vazia quando `N == M`.

    Args:
        installment: a parcela da linha (`N/M`).
        line_date: a data que a linha traz (da compra ou da parcela, conforme o
            emissor).
        invoice_anchor: a compra mais recente da conciliação, que marca o fim da
            fatura corrente.
        amount: o valor da parcela, POSITIVO.
        process: o processo do cartão da sessão (`CardPostingDateMode`).
        invoice_due_date: o vencimento da fatura corrente, quando gravado.
    """
    base = _months_to_current(installment, line_date, invoice_anchor)
    plan: list[PlannedInstallment] = []
    for k in range(1, installment.total - installment.number + 1):
        # Sempre a partir da data da LINHA: somar mês a mês sobre uma data já
        # encurtada (31/01 → 28/02) perderia o dia da compra nos meses seguintes.
        due = add_months(line_date, base + k)
        if process == CardPostingDateMode.INVOICE_DUE_DATE.value or invoice_due_date is None:
            issue = due
        else:
            issue = add_months(invoice_due_date, k)
        plan.append(
            PlannedInstallment(
                number=installment.number + k,
                total=installment.total,
                due_date=due,
                issue_date=issue,
                amount=amount,
            )
        )
    return plan
