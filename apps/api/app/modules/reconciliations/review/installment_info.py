"""A parcela `N/M` de uma linha da revisão e as parcelas que viriam depois (86e3qhhtb).

UM lugar para Movimentações e Anomalias (as duas abrem a gaveta "Lançar no
Omie"). A parcela é a da regra única (`reconciliations/installments.py`: colunas
da extração ou a descrição já decifrada pela revisão) e as datas das futuras são
as de `installment_plan.plan_future_installments`, a MESMA função que cria os
títulos: o que a pessoa confere na gaveta é o que vai ao Omie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.db.models import CardPostingDateMode, SessionAccountType
from app.modules.reconciliations.installments import entry_installment
from app.modules.reconciliations.omie_posting.installment_plan import plan_future_installments
from app.modules.reconciliations.omie_posting.repository import invoice_anchor
from app.modules.reconciliations.review.schemas import (
    FileEntryInstallment,
    FutureInstallmentPreview,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import ReconciliationFileEntry, ReconciliationSession

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class InstallmentContext:
    """O que a previsão das parcelas precisa saber da conciliação (lido uma vez)."""

    is_card: bool
    process: str
    invoice_due_date: date | None
    anchor: date | None


async def load_installment_context(
    db: AsyncSession, session: ReconciliationSession
) -> InstallmentContext:
    """Lê a âncora da fatura só em conciliação de cartão (fora dela não há parcela a provisionar)."""
    is_card = session.account_type == SessionAccountType.CREDIT_CARD.value
    return InstallmentContext(
        is_card=is_card,
        process=session.card_posting_date_mode or CardPostingDateMode.PURCHASE_DATE.value,
        invoice_due_date=session.invoice_due_date,
        anchor=await invoice_anchor(db, session.id) if is_card else None,
    )


def installment_info(
    entry: ReconciliationFileEntry,
    description: str | None,
    context: InstallmentContext | None,
) -> FileEntryInstallment | None:
    """`null` para compra à vista; o par e, numa compra do cartão, as futuras."""
    pair = entry_installment(entry, description)
    if pair is None:
        return None
    futuras: list[FutureInstallmentPreview] = []
    if (
        context is not None
        and context.is_card
        and context.anchor is not None
        and pair.has_future
        and Decimal(entry.amount) < 0
    ):
        plan = plan_future_installments(
            installment=pair,
            line_date=entry.transaction_date,
            invoice_anchor=context.anchor,
            amount=abs(Decimal(entry.amount)).quantize(_CENT, rounding=ROUND_HALF_UP),
            process=context.process,
            invoice_due_date=context.invoice_due_date,
        )
        futuras = [
            FutureInstallmentPreview(
                numero=p.number,
                total=p.total,
                vencimento=p.due_date,
                emissao=p.issue_date,
                valor=p.amount,
            )
            for p in plan
        ]
    return FileEntryInstallment(numero=pair.number, total=pair.total, futuras=futuras)
