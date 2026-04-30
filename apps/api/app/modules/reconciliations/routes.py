"""Endpoints do módulo de conciliações.

S8 (BACK 6.2):
    - GET /api/v1/reconciliations/check-duplicate

S9+ amplia com criação assíncrona, listagem de entries, revisão e exportação.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    DbSessionDep,
    ManagerOrAdminDep,
    require_client_access,
)
from app.core.exceptions import ClientNotAccessibleError, NotFoundError
from app.modules.reconciliations.repository import ReconciliationRepository
from app.modules.reconciliations.schemas import (
    CheckDuplicateResponse,
    DuplicateCheckPayload,
)
from app.modules.reconciliations.service import ReconciliationService

router = APIRouter(prefix="/api/v1/reconciliations", tags=["reconciliations"])


def _get_reconciliation_service(db: DbSessionDep) -> ReconciliationService:
    """Provider para injeção do service em endpoints."""
    return ReconciliationService(ReconciliationRepository(db))


ReconciliationServiceDep = Annotated[ReconciliationService, Depends(_get_reconciliation_service)]

# Mês de referência aceita 01..12 (mesmo padrão da listagem do histórico em
# /api/v1/clients/{id}/reconciliations).
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# SHA-256 hex: 64 caracteres em [a-f0-9] (case-insensitive — alguns clientes
# enviam em maiúsculas, normalizamos depois antes de comparar com o DB).
_HASH_PATTERN = r"^[a-fA-F0-9]{64}$"


@router.get(
    "/check-duplicate",
    summary=(
        "Verifica se já existe sessão com (client, conta, mês, hash). "
        "RBAC: admin OU manager-da-carteira; cliente inacessível devolve 404 "
        "para não vazar a existência."
    ),
)
async def check_duplicate(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    service: ReconciliationServiceDep,
    client_id: Annotated[UUID, Query(description="UUID do cliente.")],
    omie_conta_id: Annotated[int, Query(ge=1, description="nCodCC do Omie (BigInteger no DB).")],
    month: Annotated[
        str,
        Query(
            pattern=_MONTH_PATTERN,
            description="Mês de referência no formato YYYY-MM.",
        ),
    ],
    file_hash: Annotated[
        str,
        Query(
            alias="hash",
            pattern=_HASH_PATTERN,
            description="SHA-256 hex (64 caracteres) do arquivo a ser conciliado.",
        ),
    ],
) -> CheckDuplicateResponse:
    # RBAC: reusa `require_client_access` programaticamente — diferente das
    # rotas com `{client_id}` no path, aqui o ID vem por query, então não dá
    # pra usar a dependency direto.
    #
    # Convertemos `ClientNotAccessibleError` (403) em `NotFoundError` (404)
    # propositalmente: para um manager que não enxerga o cliente, a resposta
    # deve ser indistinguível de "cliente não existe", evitando leak de
    # existência por probing. Cliente realmente inexistente já cai em 404 no
    # caminho normal de `require_client_access`.
    try:
        await require_client_access(client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError("Cliente não encontrado.") from exc

    # Hash em caso-insensitive no input; armazenado em hex lower-case (S2).
    # Normalizar antes de consultar evita falso negativo se cliente mandar
    # maiúsculas.
    reference_month = date(int(month[:4]), int(month[5:7]), 1)
    duplicate = await service.check_duplicate(
        client_id=client_id,
        omie_conta_id=omie_conta_id,
        reference_month=reference_month,
        file_hash=file_hash.lower(),
    )
    return CheckDuplicateResponse(data=DuplicateCheckPayload(duplicate=duplicate))
