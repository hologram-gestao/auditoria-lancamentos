"""Estado da intenção de lançamento no Omie, por linha da revisão (86e3n70q0, bloco C).

UM helper para as duas abas que oferecem "Lançar no Omie" (Movimentações e
Anomalias): as duas mostram o mesmo selo e decidem a mesma ação, então leem o
estado pela mesma função. Duas leituras divergiriam no dia em que uma ganhasse
um filtro que a outra não tem.

**Uma query por página**, nunca N+1: recebe os `file_entry_id` da página e lê as
intenções de todos de uma vez.

**`client_id` no `WHERE`** (§3.15): a rota já resolveu o alcance à sessão, e a
tabela tem `client_id` desnormalizado justamente para toda leitura filtrar por
ele sem depender de um JOIN que alguém esqueça (§4.11). Uma linha de outro
tenant com o mesmo `file_entry_id` é impossível pela FK, mas o filtro é a regra
da casa, não uma otimização.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db.models import ReconciliationOmiePosting
from app.modules.reconciliations.review.schemas import OmiePostingState

if TYPE_CHECKING:
    from collections.abc import Collection
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def posting_state_from_row(row: ReconciliationOmiePosting) -> OmiePostingState:
    """A linha de `reconciliation_omie_postings` como a tela a lê (só códigos e ids)."""
    return OmiePostingState(
        kind=row.kind,
        status=row.status,
        step=row.step,
        attempts=row.attempts,
        supplier_code=row.supplier_code,
        payable_id=row.payable_id,
        payment_id=row.payment_id,
        omie_lancamento_id=row.omie_lancamento_id,
        error_code=row.error_code,
        error_message=row.error_message,
    )


async def load_omie_posting_states(
    session: AsyncSession,
    *,
    client_id: UUID,
    file_entry_ids: Collection[UUID],
) -> dict[UUID, OmiePostingState]:
    """`file_entry_id` → estado da intenção. Linha sem intenção fica FORA do mapa."""
    if not file_entry_ids:
        return {}
    stmt = select(ReconciliationOmiePosting).where(
        ReconciliationOmiePosting.client_id == client_id,
        ReconciliationOmiePosting.file_entry_id.in_(list(file_entry_ids)),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {row.file_entry_id: posting_state_from_row(row) for row in rows}
