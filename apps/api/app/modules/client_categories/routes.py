"""Rotas do catálogo de categorias de cliente (86e34jd8m).

    - GET    /api/v1/client-categories            equipe Hologram (admin + manager)
    - POST   /api/v1/client-categories            admin-only
    - PATCH  /api/v1/client-categories/{id}       admin-only
    - DELETE /api/v1/client-categories/{id}       admin-only (409 se em uso)

Catálogo é configuração GLOBAL (sem dado de cliente): entra em
`NON_TENANT_ENDPOINTS`, como os tipos de anomalia. Leitura é da equipe toda —
o filtro da lista de clientes e o formulário do cliente precisam dele; escrita
é admin, como toda configuração do sistema (§4.9).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.core.dependencies import AdminDep, DbSessionDep, ManagerOrAdminDep
from app.modules.client_categories.repository import ClientCategoryRepository
from app.modules.client_categories.schemas import (
    ClientCategoryCreate,
    ClientCategoryItem,
    ClientCategoryListResponse,
    ClientCategoryUpdate,
)
from app.modules.client_categories.service import ClientCategoryService

router = APIRouter(prefix="/api/v1/client-categories", tags=["client-categories"])


def _get_service(db: DbSessionDep) -> ClientCategoryService:
    return ClientCategoryService(ClientCategoryRepository(db))


ClientCategoryServiceDep = Annotated[ClientCategoryService, Depends(_get_service)]


@router.get(
    "",
    summary="Lista o catálogo de categorias de cliente com a contagem de clientes por categoria.",
)
async def list_client_categories(
    user: ManagerOrAdminDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryListResponse:
    del user
    return ClientCategoryListResponse(data=await service.list_categories())


@router.post(
    "",
    status_code=201,
    summary="Cria categoria (admin-only). Nome único sem distinção de caixa.",
)
async def create_client_category(
    payload: ClientCategoryCreate,
    _admin: AdminDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryItem:
    return await service.create_category(name=payload.name, tone=payload.tone)


@router.patch(
    "/{category_id}",
    summary="Atualiza nome e/ou tom (admin-only).",
)
async def update_client_category(
    category_id: UUID,
    payload: ClientCategoryUpdate,
    _admin: AdminDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryItem:
    return await service.update_category(category_id, name=payload.name, tone=payload.tone)


@router.delete(
    "/{category_id}",
    status_code=204,
    summary="Exclui categoria (admin-only). 409 se houver clientes vinculados.",
)
async def delete_client_category(
    category_id: UUID,
    _admin: AdminDep,
    service: ClientCategoryServiceDep,
) -> Response:
    await service.delete_category(category_id)
    return Response(status_code=204)
