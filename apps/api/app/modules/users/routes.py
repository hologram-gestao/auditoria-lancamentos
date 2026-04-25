"""Endpoints CRUD de usuários — admin-only.

Cobre BACK 2.1 do backlog:
    - GET  /api/v1/users?page&pageSize&search    (paginado)
    - POST /api/v1/users                          (criação)
    - PATCH /api/v1/users/{id}                    (update parcial)
    - POST /api/v1/users/{id}/activate            (reativa)
    - POST /api/v1/users/{id}/deactivate          (soft delete)

Toda rota exige `require_admin` (RBAC). Manager autenticado recebe 403.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import AdminDep, DbSessionDep
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import (
    CreateUserRequest,
    UpdateUserRequest,
    UserListResponse,
    UserResponse,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _get_user_service(db: DbSessionDep) -> UserService:
    return UserService(UserRepository(db))


UserServiceDep = Annotated[UserService, Depends(_get_user_service)]


@router.get(
    "",
    summary="Listar usuários (paginado, busca por nome ou e-mail).",
)
async def list_users(
    admin: AdminDep,
    service: UserServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> UserListResponse:
    rows, pagination = await service.list_users(page=page, page_size=page_size, search=search)
    return UserListResponse(
        data=[UserResponse.model_validate(u) for u in rows],
        pagination=pagination,
    )


@router.post(
    "",
    status_code=201,
    summary="Criar usuário com senha inicial. Email deve ser único.",
)
async def create_user(
    payload: CreateUserRequest,
    admin: AdminDep,
    service: UserServiceDep,
) -> UserResponse:
    user = await service.create_user(
        name=payload.name,
        email=payload.email,
        password=payload.password,
        role=payload.role,
    )
    return UserResponse.model_validate(user)


@router.get(
    "/{user_id}",
    summary="Buscar usuário por ID.",
)
async def get_user(
    user_id: UUID,
    admin: AdminDep,
    service: UserServiceDep,
) -> UserResponse:
    user = await service.get_user(user_id)
    return UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    summary="Atualizar campos do usuário (PATCH parcial).",
)
async def update_user(
    user_id: UUID,
    payload: UpdateUserRequest,
    admin: AdminDep,
    service: UserServiceDep,
) -> UserResponse:
    user = await service.update_user(
        user_id,
        current_user_id=UUID(admin.id),
        name=payload.name,
        email=payload.email,
        role=payload.role,
    )
    return UserResponse.model_validate(user)


@router.post(
    "/{user_id}/deactivate",
    summary="Desativar usuário. Admin não pode desativar a si mesmo.",
)
async def deactivate_user(
    user_id: UUID,
    admin: AdminDep,
    service: UserServiceDep,
) -> UserResponse:
    user = await service.set_user_active(user_id, active=False, current_user_id=UUID(admin.id))
    return UserResponse.model_validate(user)


@router.post(
    "/{user_id}/activate",
    summary="Reativar usuário previamente desativado.",
)
async def activate_user(
    user_id: UUID,
    admin: AdminDep,
    service: UserServiceDep,
) -> UserResponse:
    user = await service.set_user_active(user_id, active=True, current_user_id=UUID(admin.id))
    return UserResponse.model_validate(user)
