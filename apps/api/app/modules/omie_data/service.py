"""Serviço de leitura cached de lançamentos Omie (BACK 9.2).

Decisão pragmática: por que `session_id` em vez de `client_id` no endpoint?
O Omie não tem endpoint by-id (`Consultar1LancamentoPorId`). A única forma
de buscar é `ListarExtrato(omie_conta_id, periodo)`. Como o caller precisa
saber `omie_conta_id` e o período, e tudo isso já está na sessão, exigimos
`session_id` — o cache é resolvido implicitamente pelo `client_id` da sessão.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.modules.omie_data.schemas import OmieLancamentoItem
from app.modules.reconciliations.review.repository import ReviewRepository
from app.modules.reconciliations.review.service import _month_bounds

if TYPE_CHECKING:
    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.lancamento_cache import OmieLancamentoCache


logger = get_logger(__name__)


class OmieLancamentoService:
    """Resolve um lote de IDs Omie via cache hierárquico (L1 + L2 + refetch)."""

    def __init__(
        self,
        repository: ReviewRepository,
        cache: OmieLancamentoCache,
    ) -> None:
        self._repo = repository
        self._cache = cache

    async def fetch_lancamentos(
        self,
        *,
        session_id: UUID,
        omie_ids: list[int],
        omie_client_factory: Callable[[], OmieClient],
    ) -> list[OmieLancamentoItem]:
        """Resolve IDs solicitados — L1 → L2 → re-fetch via extrato.

        Args:
            session_id: para resolver `client_id`, `omie_conta_id`, período.
            omie_ids: lista deduplicada de IDs (caller já saneou).
            omie_client_factory: callable() → OmieClient. Permite que o
                provider injete um client já construído sem precisar que o
                serviço conheça a Settings/Client model.

        Returns:
            Lista de items na ordem em que foram solicitados; IDs não
            encontrados nem após re-fetch são SILENCIOSAMENTE excluídos
            (Doc §14: melhor item ausente que erro hard quando a lista
            tem outros IDs válidos). Caller pode comparar
            `len(omie_ids) - len(result)` para alertar a UI.
        """
        sess = await self._repo.get_session(session_id)
        if sess is None:
            raise NotFoundError("Sessão de conciliação não encontrada.")

        # Primeira passada: cache lookup-only.
        cached = await self._cache.get_many(
            client_id=sess.client_id,
            omie_ids=omie_ids,
        )

        missing = [oid for oid in omie_ids if oid not in cached]
        if missing:
            # Falta IDs → popula via extrato. UMA chamada ao Omie pra todo
            # o período da sessão. Quando o usuário abre /omie-entries pela
            # 1ª vez, esse caminho aquece o cache pra toda a sessão.
            period_start, period_end = _month_bounds(sess.reference_month)
            expanded_start, expanded_end = self._repo.expand_period(
                period_start, period_end, sess.date_tolerance_days
            )
            omie_client = omie_client_factory()
            try:
                populated = await self._cache.populate_from_extrato(
                    client_id=sess.client_id,
                    omie_client=omie_client,
                    omie_conta_id=sess.omie_conta_id,
                    period_start=expanded_start,
                    period_end=expanded_end,
                )
            finally:
                await omie_client.aclose()
            for oid in missing:
                if oid in populated:
                    cached[oid] = populated[oid]

        items: list[OmieLancamentoItem] = []
        for oid in omie_ids:
            data = cached.get(oid)
            if data is None:
                continue
            items.append(
                OmieLancamentoItem(
                    omie_id=data.omie_id,
                    transaction_date=data.transaction_date,
                    description=data.description,
                    supplier=data.supplier,
                    category=data.category,
                    amount=data.amount,
                    status=data.status,
                )
            )

        logger.info(
            "omie_lancamentos_fetched",
            session_id=str(session_id),
            requested=len(omie_ids),
            resolved=len(items),
        )
        return items
