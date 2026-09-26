"""Rotas do catálogo de destinos e alvos do de-para (Sprint 12, BACK 12.3 — R1).

    - GET    /api/v1/mapping-destinations                              quem pertence à org
    - POST   /api/v1/mapping-destinations                              manage_mapping_catalog
    - PATCH  /api/v1/mapping-destinations/{destination_id}             manage_mapping_catalog
    - GET    /api/v1/mapping-destinations/{destination_id}/targets     quem pertence à org
    - POST   /api/v1/mapping-destinations/{destination_id}/targets     manage_mapping_catalog (lote)
    - PATCH  /api/v1/mapping-destinations/{destination_id}/targets/{target_id}
    - DELETE /api/v1/mapping-destinations/{destination_id}/targets/{target_id}

**Catálogo POR ORGANIZAÇÃO.** A leitura é de quem pertence à organização — staff
E usuários de cliente dela, porque o `client_manager` escolhe alvo ao decidir o
de-para; a plataforma lê todas (com `?organizationId=` opcional). A escrita é
`manage_mapping_catalog` (plataforma e admin — decisão do planejador, ADR-074-BE).
Destino ou alvo de outra organização é **404**, nunca o dado. Organização suspensa:
escrita 409.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.core.dependencies import CurrentUserDep, DbSessionDep, ManageMappingCatalogDep
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.schemas import (
    MappingDestinationCreate,
    MappingDestinationEnvelope,
    MappingDestinationListResponse,
    MappingDestinationUpdate,
    MappingTargetBatchCreate,
    MappingTargetBatchResponse,
    MappingTargetEnvelope,
    MappingTargetListResponse,
    MappingTargetUpdate,
)
from app.modules.mapping_catalog.service import MappingCatalogService

router = APIRouter(prefix="/api/v1/mapping-destinations", tags=["mapping-catalog"])


def _get_service(db: DbSessionDep) -> MappingCatalogService:
    return MappingCatalogService(MappingCatalogRepository(db))


ServiceDep = Annotated[MappingCatalogService, Depends(_get_service)]


@router.get(
    "",
    summary=(
        "Lista os destinos do de-para da organização de quem pede (a plataforma vê "
        "todas; `organizationId` restringe). Cada organização nasce com os cinco "
        "destinos do PRD. Leitura de quem pertence à organização — staff e "
        "usuários de cliente dela."
    ),
)
async def list_mapping_destinations(
    user: CurrentUserDep,
    service: ServiceDep,
    organization_id: Annotated[
        UUID | None,
        Query(
            alias="organizationId",
            description="Plataforma: restringe a uma organização. Staff: só a própria.",
        ),
    ] = None,
) -> MappingDestinationListResponse:
    return MappingDestinationListResponse(
        data=await service.list_destinations(viewer=user, requested_organization_id=organization_id)
    )


@router.post(
    "",
    status_code=201,
    summary=(
        "Cria um destino na organização do ator (a plataforma escolhe, obrigatório). "
        "O tipo é um slug; um por organização (409 se repetido). Organização "
        "suspensa: 409. Requer `manage_mapping_catalog`."
    ),
)
async def create_mapping_destination(
    payload: MappingDestinationCreate,
    actor: ManageMappingCatalogDep,
    service: ServiceDep,
) -> MappingDestinationEnvelope:
    return MappingDestinationEnvelope(
        data=await service.create_destination(
            actor=actor,
            destination_type=payload.type,
            name=payload.name,
            requested_organization_id=payload.organization_id,
        )
    )


@router.patch(
    "/{destination_id}",
    summary=(
        "Edita nome e/ou situação do destino (o tipo não muda). Destino desativado "
        "não recebe decisão nova. 404 fora da própria organização; 409 se a "
        "organização estiver suspensa."
    ),
)
async def update_mapping_destination(
    destination_id: UUID,
    payload: MappingDestinationUpdate,
    actor: ManageMappingCatalogDep,
    service: ServiceDep,
) -> MappingDestinationEnvelope:
    return MappingDestinationEnvelope(
        data=await service.update_destination(
            destination_id, actor=actor, name=payload.name, active=payload.active
        )
    )


@router.get(
    "/{destination_id}/targets",
    summary=(
        "Lista os alvos de um destino, por código (paginado: `page`/`pageSize`, "
        "máximo 100). Filtros: `active` e `codePrefix`. 404 fora da própria "
        "organização."
    ),
)
async def list_mapping_targets(
    destination_id: UUID,
    user: CurrentUserDep,
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100).")
    ] = 20,
    active: Annotated[bool | None, Query(description="Só ativos / só inativos.")] = None,
    code_prefix: Annotated[
        str | None,
        Query(alias="codePrefix", max_length=50, description="Código começando por."),
    ] = None,
) -> MappingTargetListResponse:
    data, pagination = await service.list_targets(
        destination_id,
        viewer=user,
        page=page,
        page_size=page_size,
        active=active,
        code_prefix=code_prefix,
    )
    return MappingTargetListResponse(data=data, pagination=pagination)


@router.post(
    "/{destination_id}/targets",
    status_code=201,
    summary=(
        "Cria alvos EM LOTE (código + nome), atômico: código repetido no lote ou já "
        "existente no destino recusa tudo com 409 listando os códigos. Até 500 por "
        "chamada. Requer `manage_mapping_catalog`."
    ),
)
async def create_mapping_targets(
    destination_id: UUID,
    payload: MappingTargetBatchCreate,
    actor: ManageMappingCatalogDep,
    service: ServiceDep,
) -> MappingTargetBatchResponse:
    return MappingTargetBatchResponse(
        data=await service.create_targets(destination_id, actor=actor, targets=payload.targets)
    )


@router.patch(
    "/{destination_id}/targets/{target_id}",
    summary=(
        "Edita nome e/ou situação do alvo — desativar é permitido mesmo com decisões "
        "apontando para ele. O código não muda."
    ),
)
async def update_mapping_target(
    destination_id: UUID,
    target_id: UUID,
    payload: MappingTargetUpdate,
    actor: ManageMappingCatalogDep,
    service: ServiceDep,
) -> MappingTargetEnvelope:
    return MappingTargetEnvelope(
        data=await service.update_target(
            destination_id, target_id, actor=actor, name=payload.name, active=payload.active
        )
    )


@router.delete(
    "/{destination_id}/targets/{target_id}",
    status_code=204,
    summary=(
        "Apaga um alvo SEM decisões apontando para ele. Referenciado por qualquer "
        "decisão: 409 — desative em vez de excluir."
    ),
)
async def delete_mapping_target(
    destination_id: UUID,
    target_id: UUID,
    actor: ManageMappingCatalogDep,
    service: ServiceDep,
) -> Response:
    await service.delete_target(destination_id, target_id, actor=actor)
    return Response(status_code=204)
