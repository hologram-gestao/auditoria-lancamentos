"""Endpoints CRUD de clientes BPO — S6 (BACK 3.1 a 3.5).

Cobre:
    - GET   /api/v1/clients                          (admin + manager)
    - POST  /api/v1/clients                          (admin + manager)
    - POST  /api/v1/clients/test-connection          (admin + manager)
    - POST  /api/v1/clients/{id}/assign              (admin only)
    - PATCH /api/v1/clients/{id}                     (admin OR manager-da-carteira)

RBAC dispatch:
    - `require_admin` — assign.
    - `require_manager_or_admin` — list, create, test-connection.
    - `require_client_access(client_id)` — patch (já carrega o Client).

Ordem dos paths importa: o estático `/test-connection` precisa vir ANTES das
rotas com `/{id}` para o FastAPI não interpretá-lo como UUID inválido.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    AccessibleClientDep,
    AdminDep,
    DbSessionDep,
    ManagerOrAdminDep,
    SettingsDep,
)
from app.db.models import UserRole
from app.modules.clients.repository import ClientRepository
from app.modules.clients.schemas import (
    AssignClientRequest,
    ClientListResponse,
    ClientResponse,
    CreateClientRequest,
    TestConnectionRequest,
    TestConnectionResponse,
    UpdateClientRequest,
)
from app.modules.clients.service import ClientService

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])


def _get_client_service(db: DbSessionDep, settings: SettingsDep) -> ClientService:
    """Provider para injeção do service em endpoints."""
    return ClientService(ClientRepository(db), settings)


ClientServiceDep = Annotated[ClientService, Depends(_get_client_service)]


# ----------------------------------------------------------------------
# GET / — listar (RBAC: admin vê tudo; manager vê só a carteira)
# ----------------------------------------------------------------------


@router.get(
    "",
    summary="Listar clientes (paginado, busca por nome). Manager vê só carteira.",
)
async def list_clients(
    user: ManagerOrAdminDep,
    service: ClientServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> ClientListResponse:
    # Admin: filtro None (vê tudo). Manager: filtra pelo próprio user_id no
    # client_assignments — clientes de outros managers retornam 0 rows.
    manager_filter = None if user.role == UserRole.ADMIN.value else UUID(user.id)
    rows, pagination = await service.list_clients(
        page=page, page_size=page_size, search=search, manager_id_filter=manager_filter
    )
    return ClientListResponse(data=rows, pagination=pagination)


# ----------------------------------------------------------------------
# POST / — criar (admin OU manager — auto-assign)
# ----------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    summary="Cria cliente. Credenciais Omie criptografadas + auto-assign do criador.",
)
async def create_client(
    payload: CreateClientRequest,
    user: ManagerOrAdminDep,
    service: ClientServiceDep,
) -> ClientResponse:
    return await service.create_client(
        name=payload.name,
        omie_app_key=payload.omie_app_key,
        omie_app_secret=payload.omie_app_secret,
        current_user_id=UUID(user.id),
    )


# ----------------------------------------------------------------------
# POST /test-connection — valida credenciais sem persistir
# ----------------------------------------------------------------------


@router.post(
    "/test-connection",
    summary="Testa credenciais Omie sem persistir. Retorna ok+message para a UI.",
)
async def test_connection(
    payload: TestConnectionRequest,
    user: ManagerOrAdminDep,
    service: ClientServiceDep,
) -> TestConnectionResponse:
    # `user` existe apenas para acionar o RBAC dependency; não é usado no body.
    del user
    return await service.test_connection(
        omie_app_key=payload.omie_app_key,
        omie_app_secret=payload.omie_app_secret,
    )


# ----------------------------------------------------------------------
# POST /{id}/assign — admin reatribui cliente
# ----------------------------------------------------------------------


@router.patch(
    "/{client_id}/assign",
    summary="Reatribui cliente a outro gerente (admin-only).",
)
async def assign_client(
    client_id: UUID,
    payload: AssignClientRequest,
    admin: AdminDep,
    service: ClientServiceDep,
) -> ClientResponse:
    return await service.assign_client(
        client_id,
        new_user_id=payload.user_id,
        current_admin_id=UUID(admin.id),
    )


# ----------------------------------------------------------------------
# PATCH /{id} — atualizar (admin OU manager-da-carteira)
# ----------------------------------------------------------------------


@router.patch(
    "/{client_id}",
    summary="Atualiza nome, status ou credenciais. Manager: apenas clientes da carteira.",
)
async def update_client(
    payload: UpdateClientRequest,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> ClientResponse:
    # `client` já vem carregado e validado pelo `require_client_access` —
    # se o caller não tem acesso, a dependency lança 403 antes daqui.
    return await service.update_client(
        client,
        name=payload.name,
        active=payload.active,
        omie_app_key=payload.omie_app_key,
        omie_app_secret=payload.omie_app_secret,
    )
