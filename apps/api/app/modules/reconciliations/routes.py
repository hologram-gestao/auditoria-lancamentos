"""Endpoints do módulo de conciliações.

S8 (BACK 6.2):
    - GET /api/v1/reconciliations/check-duplicate
S9 (BACK 7.1):
    - POST /api/v1/reconciliations/parse

S10+ amplia com criação assíncrona, listagem de entries, revisão e exportação.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    DbSessionDep,
    ManagerOrAdminDep,
    require_client_access,
)
from app.core.exceptions import (
    ClientNotAccessibleError,
    NotFoundError,
    ValidationAppError,
)
from app.integrations.anthropic.client import AnthropicClient
from app.modules.reconciliations.parse_service import ParseService
from app.modules.reconciliations.repository import ReconciliationRepository
from app.modules.reconciliations.schemas import (
    CheckDuplicateResponse,
    DuplicateCheckPayload,
    ParseResponse,
)
from app.modules.reconciliations.service import ReconciliationService

router = APIRouter(prefix="/api/v1/reconciliations", tags=["reconciliations"])


def _get_reconciliation_service(db: DbSessionDep) -> ReconciliationService:
    """Provider para injeção do service em endpoints."""
    return ReconciliationService(ReconciliationRepository(db))


ReconciliationServiceDep = Annotated[ReconciliationService, Depends(_get_reconciliation_service)]


def _get_anthropic_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AnthropicClient:
    """Provider para o `AnthropicClient`.

    Construção barata (sem I/O até a 1ª chamada de `messages.create`). Em
    testes, o override é trocado por um fake via `dependency_overrides`.
    """
    return AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.ANTHROPIC_MODEL_DEFAULT,
        timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
    )


AnthropicClientDep = Annotated[AnthropicClient, Depends(_get_anthropic_client)]


def _get_parse_service(anthropic: AnthropicClientDep) -> ParseService:
    """Provider para o `ParseService` (BACK 7.1)."""
    return ParseService(anthropic)


ParseServiceDep = Annotated[ParseService, Depends(_get_parse_service)]

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


@router.post(
    "/parse",
    summary=(
        "Extrai movimentações do arquivo via IA (Claude). Stateless: nada é "
        "persistido aqui — a sessão será criada por POST /reconciliations "
        "(S10) após o usuário confirmar o preview. RBAC: admin OU "
        "manager-da-carteira; cliente inacessível devolve 404."
    ),
)
async def parse_statement(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    parser: ParseServiceDep,
    client_id: Annotated[UUID, Form(description="UUID do cliente.")],
    file: Annotated[UploadFile, File(description="Extrato/fatura: PDF, CSV ou XLSX.")],
) -> ParseResponse:
    # RBAC programático — mesmo padrão do check_duplicate. Manager fora da
    # carteira recebe 404 para não vazar existência (CLAUDE.md §3.11).
    try:
        await require_client_access(client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError("Cliente não encontrado.") from exc

    # Lê todo o conteúdo em memória. UploadFile usa SpooledTemporaryFile
    # internamente — abaixo do threshold (1 MB) fica em RAM; acima disso o
    # FastAPI/Starlette spool em arquivo temp. Para garantir que **nada toca
    # o disco** mesmo em arquivos grandes (CLAUDE.md §3.10), poderíamos
    # subir o threshold via middleware, mas o limite atual de 20 MB ainda
    # vai a disco se Starlette decidir spool. Comentando para revisitar em
    # S16 (hardening); o spool temp é apagado ao fim do request.
    file_bytes = await file.read()

    if not file_bytes:
        raise ValidationAppError(
            "Arquivo enviado está vazio.",
            user_message="O arquivo enviado está vazio.",
        )

    statement = await parser.parse_statement(
        file_bytes=file_bytes,
        filename=file.filename,
        max_upload_bytes=settings.max_upload_bytes,
    )
    return ParseResponse(data=statement)
