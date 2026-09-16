"""Rotas do catálogo de categorias de cliente (86e34jd8m).

    - GET    /api/v1/client-categories            staff (plataforma, admin, manager)
    - POST   /api/v1/client-categories            quem gere o catálogo (matriz)
    - PATCH  /api/v1/client-categories/{id}       idem
    - DELETE /api/v1/client-categories/{id}       idem (409 se em uso)

Catálogo POR ORGANIZAÇÃO (D3 da camada de organizações): a tabela já carrega
`organization_id`; o filtro por org na leitura/escrita chega na task 86e36ecqz,
quando estas rotas saem de `NON_TENANT_ENDPOINTS`. Leitura é do staff todo —
o filtro da lista de clientes e o formulário do cliente precisam dele; escrita
segue a matriz (`MANAGE_CLIENT_CATEGORIES`, §4.9).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.core.dependencies import DbSessionDep, ManageClientCategoriesDep, StaffDep
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
    user: StaffDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryListResponse:
    del user
    return ClientCategoryListResponse(data=await service.list_categories())


@router.post(
    "",
    status_code=201,
    summary="Cria categoria (quem gere o catálogo). Nome único sem distinção de caixa.",
)
async def create_client_category(
    payload: ClientCategoryCreate,
    _actor: ManageClientCategoriesDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryItem:
    return await service.create_category(name=payload.name, tone=payload.tone)


@router.patch(
    "/{category_id}",
    summary="Atualiza nome e/ou tom (quem gere o catálogo).",
)
async def update_client_category(
    category_id: UUID,
    payload: ClientCategoryUpdate,
    _actor: ManageClientCategoriesDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryItem:
    return await service.update_category(category_id, name=payload.name, tone=payload.tone)


@router.delete(
    "/{category_id}",
    status_code=204,
    summary="Exclui categoria (quem gere o catálogo). 409 se houver clientes vinculados.",
)
async def delete_client_category(
    category_id: UUID,
    _actor: ManageClientCategoriesDep,
    service: ClientCategoryServiceDep,
) -> Response:
    await service.delete_category(category_id)
    return Response(status_code=204)
