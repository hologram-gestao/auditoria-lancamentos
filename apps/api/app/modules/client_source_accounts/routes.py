"""Endpoints da conta do banco de cada conta de origem (Sprint 16, BACK 16.3 — R3/R5).

    - GET /api/v1/clients/{client_id}/source-accounts
    - PUT /api/v1/clients/{client_id}/source-accounts

**Duas travas** (desenho da S12/S14/16.1): a MATRIZ — `manage_client_accounting_chart`
(staff) na escrita, pelo guard AUDITADO (o `client_manager` lê 200 e escreve 403 com 1
`denied`); a LEITURA não pede permissão (`AccessibleClientDep`). O TENANT — o
`client_id` do path só passa por `resolve_client_access`; escrita em cliente encerrado
é 409 (`OpenClientDep`), leitura segue 200.

⚠️ `client` vem ANTES do guard: o alcance é decidido antes da célula da matriz.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    ManageClientAccountingChartDep,
    OpenClientDep,
    SettingsDep,
)
from app.modules.client_source_accounts.schemas import (
    SourceAccountBindingEnvelope,
    SourceAccountBindingPayload,
    SourceAccountBindingRequest,
    SourceAccountEntryResponse,
    SourceAccountListResponse,
)
from app.modules.client_source_accounts.service import SourceAccountBindingService

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/source-accounts",
    tags=["client-source-accounts"],
)


def _get_service(db: DbSessionDep, settings: SettingsDep) -> SourceAccountBindingService:
    return SourceAccountBindingService(db, settings=settings)


ServiceDep = Annotated[SourceAccountBindingService, Depends(_get_service)]


@router.get(
    "",
    summary=(
        "As contas de ORIGEM do cliente e a conta contábil do BANCO de cada uma (o lado "
        "fixo da partida no arquivo contábil): as contas distintas da base de movimentos, "
        "as já associadas e o slot da CONTA PADRÃO (`sourceAccountId: null`) — que cobre "
        "só as linhas SEM conta de origem, como o arquivo sem coluna de conta. Cada uma "
        "traz a conta do plano contábil associada (código e nome decifrado na leitura) "
        "ou `pending: true`. Conta de origem sem associação NUNCA cai na padrão: a "
        "materialização no destino `conta_contabil` é recusada (409) enquanto houver "
        "linha com alvo vinda dela. Visível a todo papel com acesso ao cliente; cliente "
        "encerrado continua legível."
    ),
)
async def list_source_accounts(
    client: AccessibleClientDep,
    service: ServiceDep,
) -> SourceAccountListResponse:
    entries = await service.list_entries(client)
    return SourceAccountListResponse(data=[SourceAccountEntryResponse.build(e) for e in entries])


@router.put(
    "",
    summary=(
        "Define ou TROCA a conta contábil do banco de uma conta de origem (ou do slot da "
        "conta padrão, com `sourceAccountId` nulo): uma conta ANALÍTICA e ATIVA do plano "
        "contábil do próprio cliente. É configuração (upsert): trocar NÃO altera "
        "materialização já feita — o código do banco de cada linha fica no snapshot. "
        "Conta de outro cliente: 404; sintética ou inativa: 422 "
        "`CONTA_CONTABIL_NAO_LANCAVEL`. Requer `manage_client_accounting_chart` "
        "(plataforma, admin e gerente da carteira; usuários do cliente recebem 403 e a "
        "negação fica na trilha). Cliente encerrado: 409. Forma inválida: 400."
    ),
)
async def put_source_account(
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    payload: SourceAccountBindingRequest,
    service: ServiceDep,
) -> SourceAccountBindingEnvelope:
    entry, created = await service.set_binding(
        client,
        actor=actor,
        source_type=payload.source_type,
        source_account_id=payload.source_account_id,
        accounting_account_id=payload.accounting_account_id,
    )
    return SourceAccountBindingEnvelope(
        data=SourceAccountBindingPayload(
            entry=SourceAccountEntryResponse.build(entry), created=created
        )
    )
