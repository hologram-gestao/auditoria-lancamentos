"""Acesso ao DB para o módulo de gestão de tipos de anomalia (S15 BACK 11.1).

Pequenas queries — repositório fica fino, regras de negócio (RBAC, conflitos,
imutabilidade do `code`) ficam no service.

A ordenação custom de severidade (`critical → moderate → info`) é reproduzida
aqui no nível do SQL — duplicada do `review/repository.py` propositalmente
para evitar dependência cruzada entre módulos (anomaly_types não importa
nada de reconciliations).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import asc, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnomalySeverity, AnomalyType, ReconciliationAnomaly

# Severidade tem ordem semântica, não alfabética. CASE inline é mais barato
# que adicionar uma coluna `severity_rank` e manter sincronizada.
SEVERITY_ORDER_CASE = case(
    {
        AnomalySeverity.CRITICAL.value: 1,
        AnomalySeverity.MODERATE.value: 2,
        AnomalySeverity.INFO.value: 3,
    },
    value=AnomalyType.severity,
    else_=99,
)


class AnomalyTypeRepository:
    """Operações de leitura/escrita sobre `anomaly_types`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_all(self, *, include_inactive: bool) -> list[AnomalyType]:
        """Lista TODOS os tipos (sem paginação), ordenados por severity + name.

        Mantém o contrato legado da tela de revisão (`AnomalyTypeListResponse`).
        """
        stmt = select(AnomalyType)
        if not include_inactive:
            stmt = stmt.where(AnomalyType.active.is_(True))
        stmt = stmt.order_by(SEVERITY_ORDER_CASE, asc(AnomalyType.name))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        include_inactive: bool,
    ) -> tuple[Sequence[AnomalyType], int]:
        """Lista paginada — usada quando o cliente passa `?page=...`.

        Returns:
            Tupla `(rows, total_count)`. Total é a contagem ANTES da paginação,
            necessária para `totalPages` no response.
        """
        base = select(AnomalyType)
        count_base = select(func.count()).select_from(AnomalyType)
        if not include_inactive:
            base = base.where(AnomalyType.active.is_(True))
            count_base = count_base.where(AnomalyType.active.is_(True))

        base = base.order_by(SEVERITY_ORDER_CASE, asc(AnomalyType.name))
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        total = (await self._session.execute(count_base)).scalar_one()
        rows = (await self._session.execute(base)).scalars().all()
        return rows, int(total)

    async def get_by_id(self, type_id: UUID) -> AnomalyType | None:
        result = await self._session.execute(select(AnomalyType).where(AnomalyType.id == type_id))
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> AnomalyType | None:
        result = await self._session.execute(select(AnomalyType).where(AnomalyType.code == code))
        return result.scalar_one_or_none()

    async def count_anomalies_using_type(self, type_id: UUID) -> int:
        """Conta `reconciliation_anomalies` que referenciam este tipo.

        Usada antes de DELETE para decidir entre 409 (em uso) vs 204 (órfão).
        """
        stmt = select(func.count(ReconciliationAnomaly.id)).where(
            ReconciliationAnomaly.anomaly_type_id == type_id,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # ------------------------------ WRITE -----------------------------

    async def add(self, anomaly_type: AnomalyType) -> None:
        """Insere/atualiza e flush + refresh.

        Refresh é necessário para carregar `created_at`/`updated_at` populados
        server-side via `func.now()` — sem ele, a serialização Pydantic
        explode em `MissingGreenlet` ao acessar esses campos.
        """
        self._session.add(anomaly_type)
        await self._session.flush()
        await self._session.refresh(anomaly_type)

    async def delete(self, anomaly_type: AnomalyType) -> None:
        await self._session.delete(anomaly_type)
        await self._session.flush()
