"""Rotas do catálogo de categorias de cliente (86e34jd8m + 86e36ecqz).

    - GET    /api/v1/client-categories            staff (plataforma, admin, manager)
    - POST   /api/v1/client-categories            quem gere o catálogo (matriz)
    - PATCH  /api/v1/client-categories/{id}       idem
    - DELETE /api/v1/client-categories/{id}       idem (409 se em uso)

Catálogo POR ORGANIZAÇÃO (D3 da camada de organizações): a leitura é o
catálogo da organização da LINHA do observador (plataforma: todas, com
`?organizationId=` opcional), a categoria nasce na organização do ator (a
plataforma escolhe, obrigatório) e o alvo por PK de outra organização é 404.
Leitura é do staff todo — o filtro da lista de clientes e o formulário do
cliente precisam dele; escrita segue a matriz (`MANAGE_CLIENT_CATEGORIES`,
§4.9). As quatro rotas estão na lista canônica de endpoints sensíveis.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

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
    organization_id: Annotated[
        UUID | None,
        Query(
            alias="organizationId",
            description="Plataforma: restringe a uma organização. Staff: só a própria.",
        ),
    ] = None,
) -> ClientCategoryListResponse:
    return ClientCategoryListResponse(
        data=await service.list_categories(viewer=user, requested_organization_id=organization_id)
    )


@router.post(
    "",
    status_code=201,
    summary=(
        "Cria categoria na organização do ator (a plataforma escolhe). "
        "Nome único sem distinção de caixa dentro da organização."
    ),
)
async def create_client_category(
    payload: ClientCategoryCreate,
    actor: ManageClientCategoriesDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryItem:
    return await service.create_category(
        actor=actor,
        name=payload.name,
        tone=payload.tone,
        requested_organization_id=payload.organization_id,
    )


@router.patch(
    "/{category_id}",
    summary="Atualiza nome e/ou tom (quem gere o catálogo). 404 fora da própria organização.",
)
async def update_client_category(
    category_id: UUID,
    payload: ClientCategoryUpdate,
    actor: ManageClientCategoriesDep,
    service: ClientCategoryServiceDep,
) -> ClientCategoryItem:
    return await service.update_category(
        category_id, viewer=actor, name=payload.name, tone=payload.tone
    )


@router.delete(
    "/{category_id}",
    status_code=204,
    summary=(
        "Exclui categoria (quem gere o catálogo). 409 se houver clientes vinculados; "
        "404 fora da própria organização."
    ),
)
async def delete_client_category(
    category_id: UUID,
    actor: ManageClientCategoriesDep,
    service: ClientCategoryServiceDep,
) -> Response:
    await service.delete_category(category_id, viewer=actor)
    return Response(status_code=204)
