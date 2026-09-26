"""Endpoints da BASE DE MOVIMENTOS (Sprint 12, BACK 12.2 — R0).

    - POST /api/v1/clients/{client_id}/movements/sync
    - GET  /api/v1/clients/{client_id}/movements/sync-state?competence=YYYY-MM

**Duas travas, ambas necessárias** (mesmo desenho da carteira, S11):

1. A MATRIZ: `sync_client_movements` (todos menos o `client_operator`) na
   sincronização, pelo guard AUDITADO (`require_client_permission`) — a negação do
   papel que alcança o cliente mas não pode sincronizar vira 1 linha `denied` em
   `access_audit`. A LEITURA do estado não pede permissão: quem alcança o cliente
   lê (PRD, R0).
2. O TENANT: `OpenClientDep` na escrita, `AccessibleClientDep` na leitura — o
   `client_id` do path só passa por `resolve_client_access`, e cliente ENCERRADO
   recusa a sincronização com 409 enquanto a leitura segue 200 (§4.12).

⚠️ **Ordem dos parâmetros importa.** `client` vem ANTES do guard de permissão:
o FastAPI resolve as dependências na ordem declarada, e o alcance (cross-tenant e
cross-org, com a trilha dele) tem de ser decidido antes da célula da matriz.

⚠️ **Competência malformada é 400 `VALIDATION_ERROR`, nunca 422** — validação de
FORMA é do handler global (convenção de 23/09/2026, §4.8).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    OpenClientDep,
    SettingsDep,
    SyncClientMovementsDep,
)
from app.modules.client_movements.competence import COMPETENCE_PATTERN, parse_competence
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.client_movements.schemas import (
    MovementsSyncEnvelope,
    MovementsSyncRequest,
    MovementsSyncResponse,
    MovementsSyncStateEnvelope,
    MovementsSyncStateResponse,
)
from app.modules.client_movements.service import ClientMovementsSyncService
from app.modules.clients.repository import ClientRepository

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/movements",
    tags=["client-movements"],
)


def _get_repository(db: DbSessionDep) -> ClientMovementsRepository:
    return ClientMovementsRepository(db)


RepositoryDep = Annotated[ClientMovementsRepository, Depends(_get_repository)]


def _get_sync_service(
    db: DbSessionDep, settings: SettingsDep, repository: RepositoryDep
) -> ClientMovementsSyncService:
    return ClientMovementsSyncService(
        db,
        repository=repository,
        clients=ClientRepository(db),
        settings=settings,
    )


SyncServiceDep = Annotated[ClientMovementsSyncService, Depends(_get_sync_service)]


@router.post(
    "/sync",
    summary=(
        "Sincroniza a base de movimentos REALIZADOS de uma competência (`YYYY-MM`) "
        "com a origem: todas as contas conhecidas do cliente, de qualquer tipo, do "
        "primeiro ao último dia do mês, serializando as chamadas por cliente. É a "
        "entrada do de-para — a prévia NÃO sincroniza sozinha. Requer a permissão "
        "`sync_client_movements` (plataforma, admin, gerente da carteira e gerente "
        "do cliente; o operador do cliente recebe 403 e a negação fica na trilha). "
        "Movimento que saiu da origem vira `ausente_na_origem`, nunca é apagado; "
        "movimento sem categoria é gravado e contado em `semCategoria`. Falha no "
        "meio preserva a última base íntegra e registra `syncFailedAt`. Cliente "
        "encerrado: 409. Cliente sem origem capaz: 409 `SEM_CONEXAO`, "
        "`ORIGEM_COM_ERRO` ou `CAPACIDADE_AUSENTE`; contas do cliente nunca "
        "sincronizadas: 409 `CONFLICT`. Competência malformada: 400."
    ),
)
async def sync_client_movements(
    client: OpenClientDep,
    _user: SyncClientMovementsDep,
    payload: MovementsSyncRequest,
    service: SyncServiceDep,
    repository: RepositoryDep,
) -> MovementsSyncEnvelope:
    competence = parse_competence(payload.competence)
    result = await service.sync(client, competence)
    state = await repository.get_sync_state(client.id, competence)
    return MovementsSyncEnvelope(data=MovementsSyncResponse.build(result, state))


@router.get(
    "/sync-state",
    summary=(
        "Estado da base de movimentos de UMA competência: `syncedAt` (última "
        "sincronização íntegra), `syncFailedAt` (última falha, se a mais recente "
        "falhou) e `neverSynced` — CAMPO explícito, nunca inferido de zero "
        "movimentos. Não fala com a origem e não pede permissão além de alcançar "
        "o cliente (o operador do cliente lê). Cliente encerrado continua legível. "
        "Competência malformada: 400."
    ),
)
async def get_client_movements_sync_state(
    client: AccessibleClientDep,
    repository: RepositoryDep,
    competence: Annotated[
        str,
        Query(
            pattern=COMPETENCE_PATTERN,
            description="Competência consultada, `YYYY-MM` (ex.: `2026-06`).",
        ),
    ],
) -> MovementsSyncStateEnvelope:
    month = parse_competence(competence)
    state = await repository.get_sync_state(client.id, month)
    return MovementsSyncStateEnvelope(data=MovementsSyncStateResponse.build(month, state))
