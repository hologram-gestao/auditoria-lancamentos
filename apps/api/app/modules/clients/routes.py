"""Endpoints CRUD de clientes BPO — S6 + S7.

S6 (BACK 3.1-3.5):
    - GET   /api/v1/clients                          (admin + manager)
    - POST  /api/v1/clients                          (admin + manager)
    - POST  /api/v1/clients/test-connection          (admin + manager)
    - PATCH /api/v1/clients/{id}/assign              (admin only)
    - PATCH /api/v1/clients/{id}                     (admin OR manager-da-carteira)

S7 (BACK 4.1-4.2):
    - GET   /api/v1/clients/{id}                     detalhe + cache L1
    - PATCH /api/v1/clients/{id}/sync-accounts       força sync ignorando TTL
    - GET   /api/v1/clients/{id}/reconciliations     histórico paginado

RBAC dispatch:
    - `require_admin` — assign.
    - `require_manager_or_admin` — list, create, test-connection.
    - `require_client_access(client_id)` — detalhe, sync-accounts,
      reconciliations, patch (já carrega o Client).

Ordem dos paths importa: o estático `/test-connection` precisa vir ANTES das
rotas com `/{id}` para o FastAPI não interpretá-lo como UUID inválido. Da
mesma forma, as rotas com sub-path (`/{id}/assign`, `/{id}/sync-accounts`,
`/{id}/reconciliations`) precisam vir ANTES da rota mais genérica
`PATCH /{id}` — FastAPI matcha por ordem de registro.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.dependencies import (
    AccessibleClientDep,
    AdminDep,
    DbSessionDep,
    ManagerOrAdminDep,
    SettingsDep,
)
from app.core.rate_limit import limiter, user_id_key_func
from app.db.models import UserRole
from app.modules.clients.repository import ClientRepository
from app.modules.clients.schemas import (
    AssignClientRequest,
    ClientDetailResponse,
    ClientListResponse,
    ClientResponse,
    CreateClientRequest,
    ReconciliationSessionListResponse,
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
    summary=(
        "Testa credenciais Omie sem persistir. Retorna ok+message para a UI. "
        "Rate limit: 30/min/usuário — chama Omie."
    ),
)
@limiter.limit("30/minute", key_func=user_id_key_func)
async def test_connection(
    request: Request,
    response: Response,
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
# PATCH /{id}/assign — admin reatribui cliente
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
# PATCH /{id}/sync-accounts — força sync ignorando TTL (S7 BACK 4.1)
# ----------------------------------------------------------------------
#
# Vem ANTES de `PATCH /{id}` para que o FastAPI matche o path estático
# primeiro — caso contrário, "sync-accounts" seria interpretado como UUID e
# a request cairia em update_client (com 422 do Pydantic UUID parser).


@router.patch(
    "/{client_id}/sync-accounts",
    summary=(
        "Força sincronização das contas Omie do cliente, ignorando o TTL do "
        "cache L1. Rate limit: 30/min/usuário — chama Omie."
    ),
)
@limiter.limit("30/minute", key_func=user_id_key_func)
async def sync_accounts(
    request: Request,
    response: Response,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> ClientDetailResponse:
    return await service.force_sync_accounts(client)


# ----------------------------------------------------------------------
# GET /{id}/reconciliations — histórico paginado (S7 BACK 4.2)
# ----------------------------------------------------------------------


@router.get(
    "/{client_id}/reconciliations",
    summary="Histórico de sessões de conciliação do cliente (paginado, filtros conta+mês).",
)
async def list_client_reconciliations(
    client: AccessibleClientDep,
    service: ClientServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, alias="pageSize")] = 10,
    omie_conta_id: Annotated[int | None, Query(alias="omie_conta_id", ge=1)] = None,
    month: Annotated[
        str | None,
        Query(
            alias="month",
            pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
            description="Filtro de mês no formato YYYY-MM.",
        ),
    ] = None,
) -> ReconciliationSessionListResponse:
    rows, pagination = await service.list_reconciliations(
        client.id,
        page=page,
        page_size=page_size,
        omie_conta_id=omie_conta_id,
        month=month,
    )
    return ReconciliationSessionListResponse(data=rows, pagination=pagination)


# ----------------------------------------------------------------------
# GET /{id} — detalhe + contas Omie do cache L1 (S7 BACK 4.1)
# ----------------------------------------------------------------------


@router.get(
    "/{client_id}",
    summary="Detalhe do cliente + contas Omie (cache L1 com TTL 24h).",
)
async def get_client(
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> ClientDetailResponse:
    return await service.get_client_detail_with_accounts(client)


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
