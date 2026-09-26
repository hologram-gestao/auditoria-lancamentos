"""Rotas de organizações (BPOs/escritórios) — camada de organizações (86e36ecnp).

    - GET    /api/v1/organizations                  lista paginada com contagens
    - POST   /api/v1/organizations                  cria
    - GET    /api/v1/organizations/platform-admins  quem administra a plataforma
    - GET    /api/v1/organizations/{id}             detalhe
    - PATCH  /api/v1/organizations/{id}             renomeia; `active=false` suspende

Todas exigem `MANAGE_PLATFORM` (só `platform_admin`, D1 revisada). Não carregam
dado de cliente final: entram em `NON_TENANT_ENDPOINTS`, com o motivo. Um admin
de organização recebe 403 sem corpo que nomeie organização nenhuma.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import DbSessionDep, ManagePlatformDep
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.organizations.repository import OrganizationRepository
from app.modules.organizations.schemas import (
    OrganizationCreate,
    OrganizationItem,
    OrganizationListResponse,
    OrganizationUpdate,
    PlatformAdminListResponse,
)
from app.modules.organizations.service import OrganizationService
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def _get_service(db: DbSessionDep) -> OrganizationService:
    return OrganizationService(
        OrganizationRepository(db),
        usage_events=UsageEventService(UsageEventRepository(db)),
        mapping_catalog=MappingCatalogRepository(db),
    )


OrganizationServiceDep = Annotated[OrganizationService, Depends(_get_service)]


@router.get(
    "",
    summary="Lista organizações (paginado, busca por nome) com contagem de clientes e staff.",
)
async def list_organizations(
    _actor: ManagePlatformDep,
    service: OrganizationServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> OrganizationListResponse:
    rows, pagination = await service.list_organizations(
        page=page, page_size=page_size, search=search
    )
    return OrganizationListResponse(data=rows, pagination=pagination)


@router.post(
    "",
    status_code=201,
    summary="Cria organização (só plataforma). Nome único sem distinção de caixa.",
)
async def create_organization(
    payload: OrganizationCreate,
    _actor: ManagePlatformDep,
    service: OrganizationServiceDep,
) -> OrganizationItem:
    return await service.create_organization(name=payload.name)


# ⚠️ ANTES de `/{organization_id}`: o FastAPI casa as rotas na ORDEM em que são
# declaradas. Se a rota com parâmetro viesse primeiro, "platform-admins" seria
# lido como UUID e a resposta seria 422, não a lista.
@router.get(
    "/platform-admins",
    summary="Quem administra a plataforma (só plataforma). Lista curta, sem paginação.",
)
async def list_platform_admins(
    _actor: ManagePlatformDep,
    service: OrganizationServiceDep,
) -> PlatformAdminListResponse:
    """Os `platform_admin` do sistema.

    Existe porque NENHUMA outra tela os mostra: `GET /users` filtra
    `scope='system'` no próprio SELECT (anti-IDOR da 86e36ecar) e o
    `users_count` de cada organização conta só o staff dela — usuário de
    plataforma tem `organization_id` NULL e fica fora de todo total. Sem isto,
    nem a própria plataforma sabe quem são os pares dela.

    Só-leitura de propósito: entrar e sair da plataforma é pelo script
    (`promote_platform_admin.py`, decisão Q3), nunca por API.
    """
    return PlatformAdminListResponse(data=await service.list_platform_admins())


@router.get(
    "/{organization_id}",
    summary="Detalhe da organização (só plataforma).",
)
async def get_organization(
    organization_id: UUID,
    _actor: ManagePlatformDep,
    service: OrganizationServiceDep,
) -> OrganizationItem:
    return await service.get_organization(organization_id)


@router.patch(
    "/{organization_id}",
    summary=(
        "Renomeia e/ou suspende a organização (só plataforma). `active=false` derruba "
        "os usuários dela no request seguinte e bloqueia cliente novo; `true` reativa."
    ),
)
async def update_organization(
    organization_id: UUID,
    payload: OrganizationUpdate,
    _actor: ManagePlatformDep,
    service: OrganizationServiceDep,
) -> OrganizationItem:
    return await service.update_organization(
        organization_id, name=payload.name, active=payload.active
    )
