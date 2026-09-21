"""Endpoints CRUD de usuários de STAFF da organização.

Cobre BACK 2.1 do backlog:
    - GET  /api/v1/users?page&pageSize&search    (paginado)
    - POST /api/v1/users                          (criação)
    - PATCH /api/v1/users/{id}                    (update parcial)
    - POST /api/v1/users/{id}/activate            (reativa)
    - POST /api/v1/users/{id}/deactivate          (soft delete)
    - POST /api/v1/users/{id}/transfer              (muda de organização — só plataforma)

Toda rota exige a permissão `MANAGE_ORG_USERS` da matriz (plataforma e admin da
organização); manager autenticado recebe 403. A EXCEÇÃO é `transfer`, que exige
`MANAGE_PLATFORM`: mover gente entre organizações é escrita cross-org por
definição, e o admin de uma organização não alcança a outra (86e3bvbfx). A listagem e o alvo por PK são
SÓ staff (`scope='system'`) da organização do observador — plataforma e
usuários de cliente nunca aparecem nem são alcançados por aqui (anti-IDOR); a
criação carimba a organização do ator. A plataforma escolhe a organização
(`?organizationId=` na lista, `organization_id` no body da criação —
obrigatório para ela); para o admin, o mesmo parâmetro só pode ser a própria
org (outro valor é 403, nunca ignorado). `?role=` alimenta o seletor de
gerentes do front. `platform_admin` não entra em whitelist nenhuma: forjá-lo
no body é erro de validação.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import DbSessionDep, ManageOrgUsersDep, ManagePlatformDep
from app.db.models import SystemUserRole, UserRole
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService
from app.modules.users.repository import StaffRow, UserRepository
from app.modules.users.schemas import (
    CreateUserRequest,
    TransferUserRequest,
    UpdateUserRequest,
    UserListResponse,
    UserResponse,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _get_user_service(db: DbSessionDep) -> UserService:
    return UserService(
        UserRepository(db),
        usage_events=UsageEventService(UsageEventRepository(db)),
    )


UserServiceDep = Annotated[UserService, Depends(_get_user_service)]


def _to_response(row: StaffRow) -> UserResponse:
    return UserResponse.from_staff(row.user, organization_name=row.organization_name)


@router.get(
    "",
    summary="Listar staff (paginado, busca por nome ou e-mail; filtros de organização e papel).",
)
async def list_users(
    admin: ManageOrgUsersDep,
    service: UserServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    organization_id: Annotated[
        UUID | None,
        Query(
            alias="organizationId",
            description="Plataforma: restringe a uma organização. Admin: só a própria.",
        ),
    ] = None,
    role: Annotated[
        SystemUserRole | None, Query(description="Filtra pelo papel (admin ou manager).")
    ] = None,
) -> UserListResponse:
    rows, pagination = await service.list_users(
        viewer=admin,
        page=page,
        page_size=page_size,
        search=search,
        requested_organization_id=organization_id,
        role=UserRole(role) if role is not None else None,
    )
    return UserListResponse(data=[_to_response(r) for r in rows], pagination=pagination)


@router.post(
    "",
    status_code=201,
    summary="Criar usuário com senha inicial. Email deve ser único.",
)
async def create_user(
    payload: CreateUserRequest,
    admin: ManageOrgUsersDep,
    service: UserServiceDep,
) -> UserResponse:
    row = await service.create_user(
        viewer=admin,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        # `SystemUserRole` é a whitelist do REQUEST (admin/manager); o service
        # trabalha com o enum completo. Conversão explícita, sem string mágica.
        role=UserRole(payload.role),
        # Só a plataforma escolhe; para o admin, o service confere contra a
        # LINHA do ator e recusa divergência (§3.15).
        requested_organization_id=payload.organization_id,
    )
    return _to_response(row)


@router.get(
    "/{user_id}",
    summary="Buscar usuário por ID.",
)
async def get_user(
    user_id: UUID,
    admin: ManageOrgUsersDep,
    service: UserServiceDep,
) -> UserResponse:
    return _to_response(await service.get_user(user_id, viewer=admin))


@router.patch(
    "/{user_id}",
    summary="Atualizar campos do usuário (PATCH parcial).",
)
async def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    admin: ManageOrgUsersDep,
    service: UserServiceDep,
) -> UserResponse:
    row = await service.update_user(
        user_id,
        viewer=admin,
        name=payload.name,
        email=payload.email,
        role=UserRole(payload.role) if payload.role is not None else None,
    )
    return _to_response(row)


@router.post(
    "/{user_id}/deactivate",
    summary="Desativar usuário. Admin não pode desativar a si mesmo.",
)
async def deactivate_user(
    user_id: UUID,
    admin: ManageOrgUsersDep,
    service: UserServiceDep,
) -> UserResponse:
    return _to_response(await service.set_user_active(user_id, active=False, viewer=admin))


@router.post(
    "/{user_id}/activate",
    summary="Reativar usuário previamente desativado.",
)
async def activate_user(
    user_id: UUID,
    admin: ManageOrgUsersDep,
    service: UserServiceDep,
) -> UserResponse:
    return _to_response(await service.set_user_active(user_id, active=True, viewer=admin))


@router.post(
    "/{user_id}/transfer",
    summary="Transfere o staff para outra organização (só plataforma).",
)
async def transfer_user(
    user_id: UUID,
    payload: TransferUserRequest,
    platform: ManagePlatformDep,
    service: UserServiceDep,
) -> UserResponse:
    """Move admin ou gerente de uma organização para outra sem apagar e recriar.

    Apagar e recriar não é alternativa: o e-mail é único no sistema e a linha
    não pode ser apagada se a pessoa criou cliente ou conciliação (FK
    RESTRICT). As regras (responsável de cliente aberto recusa; colaborador e
    favoritos cross-org saem; encerrado e histórico ficam; papel não muda)
    estão em `UserService.transfer_user`.
    """
    row = await service.transfer_user(
        user_id, viewer=platform, organization_id=payload.organization_id
    )
    return _to_response(row)
