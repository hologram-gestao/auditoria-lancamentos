"""A janela do Omie de uma conciliação — UMA decisão para os cinco pontos (§5.3).

Antes desta função, cada consumidor recalculava `período ± DATE_DIVERGENCE_RANGE`
por conta própria: o processamento (`fetch_realized`), o cache da qualificação, a
tela de revisão (`/available-omie-entries`), o export e o detalhe de lançamento
(`omie_data`). Com o modo "vencimento da fatura" do cartão (86e3n70p0) a janela
deixa de ser só o período ampliado, e cinco cópias da regra virariam cinco
oportunidades de o matcher ver uma coisa e a tela mostrar outra.

Funções puras: sem I/O, sem ORM. Cada caller passa a BASE de período que já usava
(as linhas do arquivo no job, o período da sessão na revisão e no export, o mês no
`omie_data`), e o modo compra devolve exatamente o cálculo de antes sobre ela.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.db.models import CardPostingDateMode, SessionAccountType
from app.modules.reconciliations.processing.matcher import DATE_DIVERGENCE_RANGE


def invoice_lot_date(
    *,
    account_type: str,
    card_posting_date_mode: str | None,
    invoice_due_date: date | None,
) -> date | None:
    """A data do LOTE da fatura quando o cruzamento é pelo vencimento; senão `None`.

    Só devolve data para sessão de cartão gravada no modo `invoice_due_date` COM o
    vencimento (a criação recusa o modo sem a data, 422, e o banco também, pelo
    CHECK de coerência). Qualquer outra combinação (conta corrente, sessão antiga
    com o modo NULL, modo `purchase_date`) é o processo de sempre: `None`.
    """
    if account_type != SessionAccountType.CREDIT_CARD.value:
        return None
    if card_posting_date_mode != CardPostingDateMode.INVOICE_DUE_DATE.value:
        return None
    return invoice_due_date


def omie_window_for_session(
    *,
    account_type: str,
    card_posting_date_mode: str | None,
    invoice_due_date: date | None,
    period_start: date,
    period_end: date,
) -> tuple[date, date]:
    """`(início, fim)` inclusivos da janela consultada no Omie para a sessão.

    - Modo compra (e tudo que não é cartão no modo vencimento): o período ampliado
      em `DATE_DIVERGENCE_RANGE` nas duas pontas — o cálculo de sempre.
    - Modo vencimento: `[vencimento - 3, vencimento + 3]`. As compras da fatura
      estão no Omie em LOTE na data do vencimento; a data da compra (22/07 de uma
      parcela 3/6 numa fatura que vence em 10/10) não diz onde o lote está. Os 3
      dias cobrem vencimento que cai em fim de semana e é lançado no dia útil.
    """
    lot = invoice_lot_date(
        account_type=account_type,
        card_posting_date_mode=card_posting_date_mode,
        invoice_due_date=invoice_due_date,
    )
    margin = timedelta(days=DATE_DIVERGENCE_RANGE)
    if lot is not None:
        return lot - margin, lot + margin
    return period_start - margin, period_end + margin
