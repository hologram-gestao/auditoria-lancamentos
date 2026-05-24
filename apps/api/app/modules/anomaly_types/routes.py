"""Rotas do módulo anomaly_types (BACK 9.10 + S15 BACK 11.1).

GET é compartilhado entre tela de revisão (manager+admin) e admin UI:
    - Sem `?page` → envelope simples `{ data: [...] }` (contrato legado;
      o wrapper do front auto-desempacota para array).
    - Com `?page` → envelope paginado `{ data, pagination }` (admin UI lê
      os dois campos explicitamente).

Mutações (PATCH/POST/DELETE) são admin-only. Manager autenticado nestas
rotas recebe 403. Catálogo é histórico — anomalias antigas continuam
visíveis mesmo se o tipo for desativado/excluído (FK ondelete=RESTRICT
no DB + bloqueio explícito no service).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.dependencies import AdminDep, DbSessionDep, ManagerOrAdminDep
from app.modules.anomaly_types.repository import AnomalyTypeRepository
from app.modules.anomaly_types.schemas import (
    AnomalyTypeCreate,
    AnomalyTypeItem,
    AnomalyTypeListPaginatedResponse,
    AnomalyTypeListResponse,
    AnomalyTypeUpdate,
)
from app.modules.anomaly_types.service import AnomalyTypeService

router = APIRouter(prefix="/api/v1/anomaly-types", tags=["anomaly-types"])


def _get_anomaly_type_service(db: DbSessionDep) -> AnomalyTypeService:
    return AnomalyTypeService(AnomalyTypeRepository(db))


AnomalyTypeServiceDep = Annotated[AnomalyTypeService, Depends(_get_anomaly_type_service)]


@router.get(
    "",
    summary=(
        "Lista tipos de anomalia. Sem `?page` retorna envelope legado "
        "`{data:[...]}`. Com `?page` retorna paginado `{data, pagination}`. "
        "Manager nunca vê inativos (silently filtered)."
    ),
)
async def list_anomaly_types(
    user: ManagerOrAdminDep,
    service: AnomalyTypeServiceDep,
    page: Annotated[int | None, Query(ge=1)] = None,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    include_inactive: Annotated[bool, Query(alias="include_inactive")] = False,
) -> AnomalyTypeListResponse | AnomalyTypeListPaginatedResponse:
    if page is None:
        rows = await service.list_all(role=user.role, include_inactive=include_inactive)
        return AnomalyTypeListResponse(
            data=[AnomalyTypeItem.model_validate(t) for t in rows],
        )

    rows, pagination = await service.list_paginated(
        role=user.role,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
    )
    return AnomalyTypeListPaginatedResponse(
        data=[AnomalyTypeItem.model_validate(t) for t in rows],
        pagination=pagination,
    )


@router.post(
    "",
    status_code=201,
    summary="Criar tipo custom (admin-only). Code é validado snake_case e único.",
)
async def create_anomaly_type(
    payload: AnomalyTypeCreate,
    _admin: AdminDep,
    service: AnomalyTypeServiceDep,
) -> AnomalyTypeItem:
    anomaly_type = await service.create_anomaly_type(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        severity=payload.severity,
        active=payload.active,
    )
    return AnomalyTypeItem.model_validate(anomaly_type)


@router.patch(
    "/{type_id}",
    summary="Atualizar tipo (admin-only). `code` é imutável.",
)
async def update_anomaly_type(
    type_id: UUID,
    payload: AnomalyTypeUpdate,
    _admin: AdminDep,
    service: AnomalyTypeServiceDep,
) -> AnomalyTypeItem:
    anomaly_type = await service.update_anomaly_type(
        type_id,
        name=payload.name,
        description=payload.description,
        severity=payload.severity,
        active=payload.active,
    )
    return AnomalyTypeItem.model_validate(anomaly_type)


@router.delete(
    "/{type_id}",
    status_code=204,
    summary=(
        "Excluir tipo (admin-only). 409 se houver anomalias referenciando — "
        "nesse caso, oriente a desativar via PATCH."
    ),
)
async def delete_anomaly_type(
    type_id: UUID,
    _admin: AdminDep,
    service: AnomalyTypeServiceDep,
) -> Response:
    await service.delete_anomaly_type(type_id)
    return Response(status_code=204)
