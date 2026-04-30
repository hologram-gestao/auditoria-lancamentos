"""Lógica de negócio do módulo de conciliações.

S8 (BACK 6.2): verificação de duplicata pré-criação de sessão (Doc §11.3 V3).
O front chama esta rota antes de fazer o POST de criação para avisar o
analista cedo, sem precisar enfrentar o 409 do upload real.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.core.logging import get_logger
from app.modules.reconciliations.repository import ReconciliationRepository

logger = get_logger(__name__)


class ReconciliationService:
    """Operações de domínio sobre conciliações."""

    def __init__(self, repository: ReconciliationRepository) -> None:
        self._repo = repository

    async def check_duplicate(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        reference_month: date,
        file_hash: str,
    ) -> bool:
        """Retorna True se já existe sessão com a tupla idempotente.

        O caller (route) é responsável pelo RBAC sobre `client_id` antes de
        chamar esta função; aqui é apenas uma consulta sem efeitos colaterais.
        Loga apenas o prefixo de 8 chars do hash — o valor completo é PII de
        higiene (não permite identificar o conteúdo do arquivo, mas evita
        deixar correlação fácil entre logs).
        """
        duplicate = await self._repo.exists_session_with_idempotency_key(
            client_id=client_id,
            omie_conta_id=omie_conta_id,
            reference_month=reference_month,
            file_hash=file_hash,
        )
        logger.info(
            "reconciliation_check_duplicate",
            client_id=str(client_id),
            omie_conta_id=omie_conta_id,
            month=reference_month.isoformat(),
            hash_prefix=file_hash[:8],
            duplicate=duplicate,
        )
        return duplicate
