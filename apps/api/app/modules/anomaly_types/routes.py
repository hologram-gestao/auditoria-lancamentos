"""Rotas do módulo anomaly_types (BACK 9.10).

Catálogo é pequeno (~8 itens fixos hoje, expansível em S15) e mudou pouca
vez no MVP — sem paginação, sem filtro de busca. Ordem custom de severity
(critical → moderate → info) já vem do SQL via `SEVERITY_ORDER_CASE`.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DbSessionDep, ManagerOrAdminDep
from app.modules.anomaly_types.schemas import (
    AnomalyTypeItem,
    AnomalyTypeListResponse,
)
from app.modules.reconciliations.review.repository import ReviewRepository

router = APIRouter(prefix="/api/v1/anomaly-types", tags=["anomaly-types"])


@router.get(
    "",
    summary=(
        "Lista tipos de anomalia ATIVOS, ordenados por severity (critical → "
        "moderate → info) e nome. Sem paginação — catálogo pequeno."
    ),
)
async def list_anomaly_types(
    _user: ManagerOrAdminDep,
    db: DbSessionDep,
) -> AnomalyTypeListResponse:
    """Qualquer manager/admin pode ler — não há dados sensíveis por cliente."""
    repo = ReviewRepository(db)
    rows = await repo.list_active_anomaly_types()
    items = [
        AnomalyTypeItem(
            id=row.id,
            code=row.code,
            name=row.name,
            description=row.description,
            severity=row.severity,
        )
        for row in rows
    ]
    return AnomalyTypeListResponse(data=items)
