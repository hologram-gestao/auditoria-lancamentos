"""Aviso da parcela FUTURA que não veio na fatura (86e3qhhtb, resposta (b) do Gabriel).

A parcela 9/10 foi provisionada pela fatura de setembro; a de novembro chega sem a
linha 9/10 (compra cancelada, estornada, renegociada). O título continua em aberto
no Omie e só a pessoa sabe o que fazer com ele: o sistema AVISA, com o id do
título, e nunca exclui nada.

**Quando uma parcela é avisada numa conciliação S** (mesmo cliente e mesmo cartão):

  - o título dela existe (`concluido`) e nenhuma linha a encontrou;
  - ela não foi criada pela própria S (a série nasce de uma linha de S, e as
    parcelas dela são futuras para S);
  - S é a fatura DELA: o vencimento cai na janela de um mês que termina na compra
    mais recente de S (`invoice_anchor`). Cada parcela é julgada só pela fatura que
    devia trazê-la; uma fatura posterior não avisa de parcela de um mês que não foi
    importado (o sistema só olha para frente, e o aviso não vira ruído mensal);
    a janela tem 45 dias e o valor casa a até 0,01 (o centavo da parcela arredondada);
  - nenhuma linha com o mesmo valor e o mesmo `N/M` existe no cartão (as colunas da
    extração, em qualquer conciliação ativa do cartão; e, só em S, também a
    descrição decifrada, para linha sem as colunas);
  - ela ainda não foi avisada (`missing_anomaly_id` nulo). Reprocessar S apaga as
    anomalias dela (FK `SET NULL`), e o aviso volta no processamento.

Roda no processamento (inclusive no reprocessamento) e no fim do lote de
lançamento. Só códigos, datas e valores no contexto da anomalia (cifrado como todo
contexto); nada de nome nem descrição em log (§4.5).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import exists, select

from app.core.crypto_service import (
    AAD_ANOMALY_CONTEXT,
    AAD_FILE_ENTRY_DESCRIPTION,
    field_locator,
)
from app.core.logging import get_logger
from app.db.models import (
    AnomalyDetectedBy,
    AnomalyType,
    InstallmentStep,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationOmiePostingInstallment,
    ReconciliationSession,
)
from app.modules.reconciliations.installments import parse_installment
from app.modules.reconciliations.omie_posting.repository import (
    INSTALLMENT_AMOUNT_TOLERANCE,
    invoice_anchor,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.crypto import ClientCipher

log = get_logger(__name__)

#: Código do tipo no catálogo (semeado pela migration `c8d1f4e6a2b9`).
ANOMALY_CODE_INSTALLMENT_MISSING = "parcela_nao_veio"

#: A janela de uma fatura, até o fim dela (`invoice_anchor`): um mês e meio. Mais
#: que um mês de propósito: com 31 dias, a parcela que vence entre a última compra
#: de uma fatura e o fechamento dela ficava fora das janelas das duas faturas.
#: A sobreposição não avisa duas vezes (`missing_anomaly_id`).
_INVOICE_WINDOW_DAYS = 45


def _brl(value: Decimal) -> str:
    """`1234.5` → `"1.234,50"` (só número, sem identificação)."""
    whole, _, cents = f"{value:.2f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{grouped},{cents}"


def _context(row: ReconciliationOmiePostingInstallment) -> str:
    return (
        f"Parcela {row.installment_number}/{row.installment_total} · título nº "
        f"{row.payable_id} no Omie · vencimento {row.due_date:%d/%m/%Y} · "
        f"valor R$ {_brl(row.amount)}"
    )


async def flag_missing_installments(
    db: AsyncSession,
    *,
    session: ReconciliationSession,
    cipher: ClientCipher | None,
) -> int:
    """Cria as anomalias `parcela_nao_veio` de S e devolve quantas. Só `flush`.

    Quem chama decide a transação (o job commita no fim do bloco; o lote de
    lançamento commita logo depois).
    """
    anchor = await invoice_anchor(db, session.id, session.invoice_due_date)
    if anchor is None:
        return 0
    inst = ReconciliationOmiePostingInstallment
    candidates = list(
        (
            await db.execute(
                select(inst)
                .join(ReconciliationOmiePosting, ReconciliationOmiePosting.id == inst.posting_id)
                .where(
                    inst.client_id == session.client_id,
                    inst.omie_conta_id == session.omie_conta_id,
                    inst.step == InstallmentStep.CONCLUIDO.value,
                    inst.claimed_by_file_entry_id.is_(None),
                    inst.missing_anomaly_id.is_(None),
                    inst.due_date <= anchor,
                    inst.due_date > anchor - timedelta(days=_INVOICE_WINDOW_DAYS),
                    ReconciliationOmiePosting.session_id != session.id,
                )
                .order_by(inst.due_date, inst.installment_number)
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return 0

    type_row = (
        await db.execute(
            select(AnomalyType.id, AnomalyType.active).where(
                AnomalyType.code == ANOMALY_CODE_INSTALLMENT_MISSING
            )
        )
    ).one_or_none()
    if type_row is None or not type_row.active or cipher is None:
        # Tipo desativado pela plataforma (S15), ou ausente: sem aviso, como os
        # outros tipos opcionais. Nunca derruba o processamento.
        log.warning(
            "missing_installments_skipped",
            session_id=str(session.id),
            reason="tipo_ausente_ou_inativo" if cipher is not None else "sem_cifra",
        )
        return 0

    fallback = await _pairs_from_descriptions(db, session=session, cipher=cipher)
    created = 0
    for row in candidates:
        if await _line_came(db, session=session, row=row, fallback=fallback):
            continue
        anomaly_id = uuid4()
        ct, iv = cipher.encrypt(_context(row), field_locator(AAD_ANOMALY_CONTEXT, anomaly_id))
        db.add(
            ReconciliationAnomaly(
                id=anomaly_id,
                session_id=session.id,
                anomaly_type_id=type_row.id,
                detected_by=AnomalyDetectedBy.AI.value,
                context_encrypted=ct,
                context_iv=iv,
            )
        )
        await db.flush()
        row.missing_anomaly_id = anomaly_id
        created += 1
    if created:
        await db.flush()
        log.info("missing_installments_flagged", session_id=str(session.id), count=created)
    return created


async def _line_came(
    db: AsyncSession,
    *,
    session: ReconciliationSession,
    row: ReconciliationOmiePostingInstallment,
    fallback: set[tuple[Decimal, int, int]],
) -> bool:
    """Alguma fatura do cartão trouxe a linha desta parcela (mesmo valor e `N/M`)?"""
    amount = -row.amount
    tol = INSTALLMENT_AMOUNT_TOLERANCE
    if any(
        abs(value - amount) <= tol and (n, m) == (row.installment_number, row.installment_total)
        for value, n, m in fallback
    ):
        return True
    entry = ReconciliationFileEntry
    sessions_of_card = select(ReconciliationSession.id).where(
        ReconciliationSession.client_id == session.client_id,
        ReconciliationSession.omie_conta_id == session.omie_conta_id,
        ReconciliationSession.deleted_at.is_(None),
    )
    came = await db.scalar(
        select(
            exists().where(
                entry.session_id.in_(sessions_of_card),
                entry.amount >= amount - tol,
                entry.amount <= amount + tol,
                entry.installment_number == row.installment_number,
                entry.installment_total == row.installment_total,
            )
        )
    )
    return bool(came)


async def _pairs_from_descriptions(
    db: AsyncSession,
    *,
    session: ReconciliationSession,
    cipher: ClientCipher,
) -> set[tuple[Decimal, int, int]]:
    """`(valor, N, M)` das linhas de S SEM as colunas, lido da descrição decifrada.

    Só em S (as linhas que podiam trazer a parcela desta fatura) e só débito: a
    leitura em memória custa uma decifragem por linha, nunca grava.
    """
    rows = (
        await db.execute(
            select(
                ReconciliationFileEntry.id,
                ReconciliationFileEntry.amount,
                ReconciliationFileEntry.description_encrypted,
                ReconciliationFileEntry.description_iv,
            ).where(
                ReconciliationFileEntry.session_id == session.id,
                ReconciliationFileEntry.installment_number.is_(None),
                ReconciliationFileEntry.amount < 0,
            )
        )
    ).all()
    pairs: set[tuple[Decimal, int, int]] = set()
    for entry_id, amount, ct, iv in rows:
        try:
            description = cipher.decrypt(
                ct, iv, field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry_id)
            )
        except Exception:  # noqa: S112  -- linha indecifrável não traz parcela legível
            continue
        pair = parse_installment(description)
        if pair is not None:
            pairs.add((Decimal(amount), pair.number, pair.total))
    return pairs
