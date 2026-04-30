"""Acesso ao DB do módulo de conciliações.

S8 (BACK 6.2): apenas a verificação de duplicata via chave de idempotência.
A query depende do UNIQUE `uq_recon_sessions_idempotency` em
`reconciliation_sessions(client_id, omie_conta_id, reference_month, file_hash)`
— para qualquer combinação dos 4 campos, no máximo 1 registro existe.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ReconciliationSession


class ReconciliationRepository:
    """Operações de leitura/escrita sobre `reconciliation_sessions`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_session_with_idempotency_key(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        reference_month: date,
        file_hash: str,
    ) -> bool:
        """Retorna True se já existe sessão com a tupla idempotente.

        Não carrega a `ReconciliationSession` inteira: seleciona apenas o `id`
        com `LIMIT 1` para que o Postgres responda direto pelo índice da
        UNIQUE — gasto de I/O constante e mínimo.
        """
        stmt = (
            select(ReconciliationSession.id)
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.omie_conta_id == omie_conta_id,
                ReconciliationSession.reference_month == reference_month,
                ReconciliationSession.file_hash == file_hash,
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
