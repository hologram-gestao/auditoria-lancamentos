"""Endpoints de usuários DO CLIENTE, escopados ao tenant (Sprint 5 / R5).

    - GET    /api/v1/clients/{client_id}/users                    (paginado)
    - POST   /api/v1/clients/{client_id}/users                    (senha inicial)
    - GET    /api/v1/clients/{client_id}/users/{user_id}
    - PATCH  /api/v1/clients/{client_id}/users/{user_id}
    - POST   /api/v1/clients/{client_id}/users/{user_id}/activate
    - POST   /api/v1/clients/{client_id}/users/{user_id}/deactivate

Reusa o `UserService`/`UserRepository` da §8 — estendidos, não duplicados. O
módulo é separado apenas por PREFIXO de rota; a lógica mora no mesmo service.

**Duas travas, ambas necessárias:**

1. `ManageClientUsersDep` — a MATRIZ (§4): gerente do cliente e admin do
   sistema ✅; operador do cliente e gerente do sistema ❌ (403).
2. `AccessibleClientDep` — o TENANT: o `client_id` do path só passa se for o
   tenant da linha do usuário (ou a carteira/admin, para a equipe Hologram).
   É a função única do BACK 05.3 (`resolve_client_access`).

E, dentro do service, a terceira: toda operação por `user_id` resolve o alvo com
`AND client_id = <tenant>` **no SELECT** (`get_by_id_in_tenant`) — sem isso, um
gerente editaria/desativaria usuário de outro tenant, ou um admin do sistema
(que tem `client_id IS NULL`), forjando o `user_id` na URL (IDOR).

`password_hash` nunca sai em response: o `ClientUserResponse` não tem o campo.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    ManageClientUsersDep,
)
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import (
    ClientUserListResponse,
    ClientUserResponse,
    CreateClientUserRequest,
    UpdateClientUserRequest,
)
from app.modules.users.service import UserService

router = APIRouter(prefix="/api/v1/clients/{client_id}/users", tags=["client-users"])


def _get_user_service(db: DbSessionDep) -> UserService:
    return UserService(UserRepository(db))


UserServiceDep = Annotated[UserService, Depends(_get_user_service)]


@router.get(
    "",
    summary=(
        "Lista os usuários DO tenant (paginado, busca por nome ou e-mail). "
        "Visível ao gerente do cliente e à equipe do sistema; operador do "
        "cliente recebe 403. Usuário de outro tenant nunca aparece."
    ),
)
async def list_client_users(
    actor: ManageClientUsersDep,
    client: AccessibleClientDep,
    service: UserServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> ClientUserListResponse:
    # `actor` aciona o guard da matriz; `client` já veio validado contra o tenant.
    del actor
    rows, pagination = await service.list_client_users(
        client_id=client.id, page=page, page_size=page_size, search=search
    )
    return ClientUserListResponse(
        data=[ClientUserResponse.model_validate(u) for u in rows],
        pagination=pagination,
    )


@router.post(
    "",
    status_code=201,
    summary=(
        "Cria usuário do cliente com senha inicial (mínimo 10 caracteres, hash "
        "bcrypt). O papel aceita apenas client_manager/client_operator; "
        "`client_id` e `scope` são fixados pelo servidor a partir do tenant da "
        "rota e não são aceitos no body. E-mail já em uso devolve 409."
    ),
)
async def create_client_user(
    payload: CreateClientUserRequest,
    actor: ManageClientUsersDep,
    client: AccessibleClientDep,
    service: UserServiceDep,
) -> ClientUserResponse:
    del actor
    user = await service.create_client_user(
        client_id=client.id,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        role=payload.role,
    )
    return ClientUserResponse.model_validate(user)


@router.get(
    "/{user_id}",
    summary=(
        "Detalhe de um usuário DO tenant. `user_id` de outro tenant (ou de um "
        "usuário da equipe Hologram) devolve 404 — o SELECT já filtra por "
        "client_id."
    ),
)
async def get_client_user(
    user_id: UUID,
    actor: ManageClientUsersDep,
    client: AccessibleClientDep,
    service: UserServiceDep,
) -> ClientUserResponse:
    del actor
    user = await service.get_client_user(client_id=client.id, user_id=user_id)
    return ClientUserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    summary=(
        "Atualiza nome, e-mail ou papel de um usuário DO tenant (PATCH "
        "parcial). O papel aceita apenas client_manager/client_operator."
    ),
)
async def update_client_user(
    user_id: UUID,
    payload: UpdateClientUserRequest,
    actor: ManageClientUsersDep,
    client: AccessibleClientDep,
    service: UserServiceDep,
) -> ClientUserResponse:
    del actor
    user = await service.update_client_user(
        client_id=client.id,
        user_id=user_id,
        name=payload.name,
        email=payload.email,
        role=payload.role,
    )
    return ClientUserResponse.model_validate(user)


@router.post(
    "/{user_id}/deactivate",
    summary=(
        "Desativa um usuário do tenant. O acesso é revogado no PRÓXIMO request "
        "(o middleware relê `active` a cada chamada). Ninguém desativa a si mesmo."
    ),
)
async def deactivate_client_user(
    user_id: UUID,
    actor: ManageClientUsersDep,
    client: AccessibleClientDep,
    service: UserServiceDep,
) -> ClientUserResponse:
    user = await service.set_client_user_active(
        client_id=client.id,
        user_id=user_id,
        active=False,
        current_user_id=UUID(actor.id),
    )
    return ClientUserResponse.model_validate(user)


@router.post(
    "/{user_id}/activate",
    summary="Reativa um usuário do tenant previamente desativado.",
)
async def activate_client_user(
    user_id: UUID,
    actor: ManageClientUsersDep,
    client: AccessibleClientDep,
    service: UserServiceDep,
) -> ClientUserResponse:
    user = await service.set_client_user_active(
        client_id=client.id,
        user_id=user_id,
        active=True,
        current_user_id=UUID(actor.id),
    )
    return ClientUserResponse.model_validate(user)
