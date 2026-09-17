"""Endpoints CRUD de clientes BPO — S6 + S7.

S6 (BACK 3.1-3.5):
    - GET   /api/v1/clients                          (admin + manager)
    - POST  /api/v1/clients                          (admin + manager)
    - POST  /api/v1/clients/test-connection          (admin + manager)
    - PATCH /api/v1/clients/{id}/assign              define o RESPONSÁVEL (admin only)
    - PATCH /api/v1/clients/{id}                     (admin OR manager-da-carteira)
    - DELETE /api/v1/clients/{id}                    exclusão definitiva (admin-only) — 86e34jd1d

S7 (BACK 4.1-4.2):
    - GET   /api/v1/clients/{id}                     detalhe + cache L1
    - PATCH /api/v1/clients/{id}/sync-accounts       força sync ignorando TTL
    - GET   /api/v1/clients/{id}/reconciliations     histórico paginado

86e34jd5a (favoritos, por usuário):
    - PUT    /api/v1/clients/{id}/favorite            marca favorito de quem pede
    - DELETE /api/v1/clients/{id}/favorite            desmarca

86e390kz8 (carteira compartilhada — N gerentes com acesso, UM responsável):
    - GET    /api/v1/clients/{id}/managers            quem tem acesso (admin-only)
    - POST   /api/v1/clients/{id}/managers            concede acesso a um gerente
    - DELETE /api/v1/clients/{id}/managers/{user_id}  remove o acesso (não o responsável)

RBAC dispatch:
    - `EditClientDep` (admin pela matriz) + tenant — assign, managers.
    - `StaffDep` — list, test-connection; `CreateClientDep` — create.
    - `require_client_access(client_id)` — detalhe, sync-accounts,
      reconciliations, patch (já carrega o Client).

Ordem dos paths importa: o estático `/test-connection` precisa vir ANTES das
rotas com `/{id}` para o FastAPI não interpretá-lo como UUID inválido. Da
mesma forma, as rotas com sub-path (`/{id}/assign`, `/{id}/sync-accounts`,
`/{id}/reconciliations`) precisam vir ANTES da rota mais genérica
`PATCH /{id}` — FastAPI matcha por ordem de registro.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.dependencies import (
    AccessibleClientDep,
    CreateClientDep,
    CurrentUserDep,
    DbSessionDep,
    EditClientDep,
    OpenClientDep,
    SettingsDep,
    StaffDep,
    SyncOmieAccountsDep,
)
from app.core.rate_limit import limiter, user_id_key_func
from app.modules.clients.repository import ClientRepository
from app.modules.clients.schemas import (
    AddClientManagerRequest,
    AssignClientRequest,
    ClientDetailResponse,
    ClientListResponse,
    ClientManagerListResponse,
    ClientResponse,
    CreateClientRequest,
    ReconciliationSessionListResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    UpdateClientRequest,
)
from app.modules.clients.service import ClientService
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

router = APIRouter(prefix="/api/v1/clients", tags=["clients"])


def _get_client_service(db: DbSessionDep, settings: SettingsDep) -> ClientService:
    """Provider para injeção do service em endpoints."""
    return ClientService(
        ClientRepository(db),
        settings,
        usage_events=UsageEventService(UsageEventRepository(db)),
    )


ClientServiceDep = Annotated[ClientService, Depends(_get_client_service)]


# ----------------------------------------------------------------------
# GET / — listar (RBAC: admin vê tudo; manager vê só a carteira)
# ----------------------------------------------------------------------


@router.get(
    "",
    summary="Listar clientes (paginado, busca por nome). Manager vê só carteira.",
)
async def list_clients(
    user: StaffDep,
    service: ClientServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    category_id: Annotated[
        UUID | None, Query(description="Filtra pela categoria do catálogo (86e34jd8m).")
    ] = None,
    organization_id: Annotated[
        UUID | None,
        Query(
            alias="organizationId",
            description="Plataforma: restringe a uma organização. Staff: só a própria.",
        ),
    ] = None,
) -> ClientListResponse:
    # O alcance (plataforma: tudo; admin: a própria organização; manager: a
    # carteira; cliente: o próprio tenant) entra no SELECT pelo `reach_filter`
    # do authz — a decisão única, derivada da LINHA do usuário, nunca da rota.
    rows, pagination = await service.list_clients(
        user=user,
        page=page,
        page_size=page_size,
        search=search,
        category_id=category_id,
        requested_organization_id=organization_id,
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
    user: CreateClientDep,
    service: ClientServiceDep,
) -> ClientResponse:
    return await service.create_client(
        name=payload.name,
        omie_app_key=payload.omie_app_key,
        omie_app_secret=payload.omie_app_secret,
        actor=user,
        # Só a plataforma escolhe; para o staff, o service confere contra a
        # LINHA do ator e recusa divergência (§3.15).
        requested_organization_id=payload.organization_id,
        category_id=payload.category_id,
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
    user: StaffDep,
    service: ClientServiceDep,
) -> TestConnectionResponse:
    # `user` existe apenas para acionar o RBAC dependency; não é usado no body.
    del user
    return await service.test_connection(
        omie_app_key=payload.omie_app_key,
        omie_app_secret=payload.omie_app_secret,
    )


# ----------------------------------------------------------------------
# Carteira compartilhada (86e390kz8): quem tem ACESSO e quem RESPONDE
# ----------------------------------------------------------------------
#
# Admin pela matriz (`EDIT_CLIENT` — a mesma célula de "editar dados do
# cliente", §4.9) + tenant pelo `AccessibleClientDep` (leitura) ou
# `OpenClientDep` (escrita: cliente ENCERRADO é 409). As quatro rotas entram
# na lista canônica como DETAIL_PK. Ordem: vêm ANTES de `GET /{id}` e
# `PATCH /{id}` — path estático depois do UUID.


@router.get(
    "/{client_id}/managers",
    summary="Quem tem acesso ao cliente: responsável + colaboradores (admin-only).",
)
async def list_client_managers(
    user: EditClientDep,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> ClientManagerListResponse:
    del user
    return ClientManagerListResponse(data=await service.list_client_managers(client.id))


@router.post(
    "/{client_id}/managers",
    status_code=201,
    summary=(
        "Concede ACESSO ao cliente a um gerente ativo (admin-only). Ninguém é removido. "
        "409 se o gerente já tem acesso; 400 se não é gerente ativo."
    ),
)
async def add_client_manager(
    payload: AddClientManagerRequest,
    user: EditClientDep,
    client: OpenClientDep,
    service: ClientServiceDep,
) -> ClientManagerListResponse:
    managers = await service.add_client_manager(
        client, user_id=payload.user_id, current_admin_id=UUID(user.id)
    )
    return ClientManagerListResponse(data=managers)


@router.delete(
    "/{client_id}/managers/{user_id}",
    summary=(
        "Remove o ACESSO de um gerente ao cliente (admin-only). 409 se ele é o "
        "responsável — defina outro responsável antes; 404 se não tinha acesso."
    ),
)
async def remove_client_manager(
    user_id: UUID,
    user: EditClientDep,
    client: OpenClientDep,
    service: ClientServiceDep,
) -> ClientManagerListResponse:
    del user
    return ClientManagerListResponse(
        data=await service.remove_client_manager(client, user_id=user_id)
    )


@router.patch(
    "/{client_id}/assign",
    summary=(
        "Define o gerente RESPONSÁVEL pelo cliente (admin-only). Não remove o acesso de "
        "ninguém: o responsável anterior continua como colaborador; se o alvo ainda não "
        "tinha acesso, passa a ter."
    ),
)
async def assign_client(
    payload: AssignClientRequest,
    user: EditClientDep,
    client: OpenClientDep,
    service: ClientServiceDep,
) -> ClientResponse:
    return await service.set_responsible_manager(
        client, user_id=payload.user_id, current_admin_id=UUID(user.id)
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
    user: SyncOmieAccountsDep,
    # 86e36pm1z — sync chama o Omie com as credenciais do cliente: encerrado
    # não tem credenciais (409 antes de tentar decifrar o que não existe).
    client: OpenClientDep,
    service: ClientServiceDep,
) -> ClientDetailResponse:
    # `user` aciona o guard da matriz (§4: gerente e operador do cliente PODEM
    # sincronizar contas); o tenant já foi validado pelo `AccessibleClientDep`.
    # O id vai junto para a response voltar com o `is_favorite` de quem pede —
    # o front grava esta response no cache do detalhe (86e34jd5a).
    return await service.force_sync_accounts(client, viewer_user_id=UUID(user.id))


# ----------------------------------------------------------------------
# PUT/DELETE /{id}/favorite — favorito POR USUÁRIO (86e34jd5a)
# ----------------------------------------------------------------------
#
# Preferência de quem opera, não edição do cliente: não passa pela matriz de
# `EDIT_CLIENT`. O que decide é o acesso ao tenant — `AccessibleClientDep`
# (resolve_client_access): quem enxerga o cliente pode favoritá-lo; cliente de
# outro tenant/carteira leva 403 + linha em `access_audit`, como nas demais
# rotas por PK. O `user_id` vem da LINHA do usuário autenticado (§3.15).


@router.put(
    "/{client_id}/favorite",
    summary="Marca o cliente como favorito do usuário autenticado (idempotente).",
)
async def favorite_client(
    user: CurrentUserDep,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> ClientResponse:
    return await service.set_favorite(client, user_id=UUID(user.id), favorite=True)


@router.delete(
    "/{client_id}/favorite",
    summary="Desmarca o favorito do usuário autenticado (idempotente).",
)
async def unfavorite_client(
    user: CurrentUserDep,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> ClientResponse:
    return await service.set_favorite(client, user_id=UUID(user.id), favorite=False)


# ----------------------------------------------------------------------
# GET /{id}/reconciliations — histórico paginado (S7 BACK 4.2)
# ----------------------------------------------------------------------


@router.get(
    "/{client_id}/reconciliations",
    summary=(
        "Lista de conciliações do cliente — a visão principal do cliente "
        "(ex-'Histórico'). Filtros combináveis com E: conta bancária "
        "(`omie_conta_id`), mês de referência (`month`, YYYY-MM) e status "
        "(`status`). O `status` usa o vocabulário do produto: `processing` "
        "(Em processamento) · `processed` (Processada — cobre `reviewing` e "
        "`done`) · `error` (Erro). Paginação `?page=&pageSize=` (padrão 20, "
        "máx. 100); `pagination.total` é a contagem COM os mesmos filtros, "
        "para o rodapé 'x-y de N'. Cada item traz conta, mês, status, nº de "
        "arquivos e os contadores. RBAC: admin OU manager-da-carteira."
    ),
)
async def list_client_reconciliations(
    client: AccessibleClientDep,
    user: CurrentUserDep,
    service: ClientServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    omie_conta_id: Annotated[int | None, Query(alias="omie_conta_id", ge=1)] = None,
    month: Annotated[
        str | None,
        Query(
            alias="month",
            pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
            description="Filtro de mês no formato YYYY-MM.",
        ),
    ] = None,
    status: Annotated[
        Literal["processing", "processed", "error"] | None,
        Query(
            description=(
                "Status no vocabulário do produto. `processed` cobre "
                "`reviewing` e `done` no banco. Valor fora da lista → 400."
            ),
        ),
    ] = None,
) -> ReconciliationSessionListResponse:
    rows, pagination = await service.list_reconciliations(
        client.id,
        page=page,
        page_size=page_size,
        omie_conta_id=omie_conta_id,
        month=month,
        status=status,
        # 86e2n39f1 — a máscara do autor ("Equipe {org}" p/ cliente vendo autor
        # de staff) decide pela LINHA de quem pede, no servidor (§4.9).
        viewer=user,
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
    user: CurrentUserDep,
    service: ClientServiceDep,
) -> ClientDetailResponse:
    return await service.get_client_detail_with_accounts(client, viewer_user_id=UUID(user.id))


# ----------------------------------------------------------------------
# PATCH /{id} — atualizar (admin OU manager-da-carteira)
# ----------------------------------------------------------------------


@router.patch(
    "/{client_id}",
    summary="Atualiza nome, status ou credenciais. Manager: apenas clientes da carteira.",
)
async def update_client(
    payload: UpdateClientRequest,
    user: EditClientDep,
    # 86e36pm1z — encerrado é terminal: nem nome nem credenciais são editáveis.
    client: OpenClientDep,
    service: ClientServiceDep,
) -> ClientResponse:
    # Matriz §4 — "Editar dados do cliente": SÓ admin. Papéis de cliente e
    # manager de sistema recebem 403 aqui (mudança de comportamento para o
    # manager, declarada no PRD).
    # `client` já vem carregado e validado pelo `require_client_access` —
    # se o caller não tem acesso, a dependency lança 403 antes daqui.
    return await service.update_client(
        client,
        name=payload.name,
        active=payload.active,
        omie_app_key=payload.omie_app_key,
        omie_app_secret=payload.omie_app_secret,
        viewer_user_id=UUID(user.id),
        # Tri-estado (86e34jd8m): só mexe na categoria se o campo veio no body.
        category_id=payload.category_id,
        category_set="category_id" in payload.model_fields_set,
    )


# ----------------------------------------------------------------------
# DELETE /{id} — exclusão DEFINITIVA (86e34jd1d)
# ----------------------------------------------------------------------
#
# Admin pela matriz (`EDIT_CLIENT` — mesma célula de "editar dados do
# cliente", §4.9) E tenant pelo `AccessibleClientDep`: entra na lista canônica
# de endpoints sensíveis como DETAIL_PK. 409 com conciliação em processamento.


@router.delete(
    "/{client_id}",
    status_code=204,
    summary=(
        "Exclui o cliente DEFINITIVAMENTE (admin-only): conciliações, usuários do "
        "cliente, glossário, credenciais e favoritos vão junto; trilhas de auditoria "
        "e eventos de uso ficam (só IDs). 409 se houver conciliação em processamento."
    ),
)
async def delete_client(
    user: EditClientDep,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> Response:
    del user
    await service.delete_client(client)
    return Response(status_code=204)


# ----------------------------------------------------------------------
# POST /{id}/close — encerramento com RETENÇÃO (86e36pm1z)
# ----------------------------------------------------------------------
#
# O irmão da exclusão, decidido pelo Lucas/Pedro (09/09/2026): apaga quem o
# cliente É (nome anonimizado, credenciais removidas, DEK destruída —
# crypto-shredding §4.1, usuários do tenant anonimizados) e MANTÉM o que
# aconteceu (conciliações, valores, datas, eventos de uso). Terminal: cliente
# que volta é cadastro novo. Admin pela matriz (`EDIT_CLIENT`) + tenant pelo
# `AccessibleClientDep`; DETAIL_PK na lista canônica. A exclusão total continua
# existindo (LGPD — o titular pode exigir apagamento completo).


@router.post(
    "/{client_id}/close",
    status_code=204,
    summary=(
        "ENCERRA o cliente com retenção (admin-only, terminal): anonimiza nome e "
        "usuários do tenant, remove credenciais Omie e destrói a chave de "
        "criptografia (conteúdo cifrado vira irrecuperável); conciliações, valores "
        "e trilhas FICAM, só-leitura. 409 se houver conciliação em processamento "
        "ou se o cliente já estiver encerrado."
    ),
)
async def close_client(
    user: EditClientDep,
    client: AccessibleClientDep,
    service: ClientServiceDep,
) -> Response:
    del user
    await service.close_client(client)
    return Response(status_code=204)
