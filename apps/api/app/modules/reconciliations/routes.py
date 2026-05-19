"""Endpoints do módulo de conciliações.

S8 (BACK 6.2):
    - GET /api/v1/reconciliations/check-duplicate
S9 (BACK 7.1):
    - POST /api/v1/reconciliations/parse
S10 (BACK 8.1 + 8.6):
    - POST /api/v1/reconciliations
    - GET /api/v1/reconciliations/{session_id}/status
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    ManagerOrAdminDep,
    require_client_access,
)
from app.core.exceptions import (
    AppError,
    ClientNotAccessibleError,
    NotFoundError,
    ValidationAppError,
)
from app.core.rate_limit import limiter, user_id_key_func
from app.integrations.anthropic.client import AnthropicClient
from app.modules.reconciliations.parse_service import ParseService
from app.modules.reconciliations.processing.dispatcher import enqueue_processing
from app.modules.reconciliations.repository import ReconciliationRepository
from app.modules.reconciliations.schemas import (
    CheckDuplicateResponse,
    CreateReconciliationPayload,
    CreateReconciliationRequest,
    CreateReconciliationResponse,
    DuplicateCheckPayload,
    ParseResponse,
    SessionDetailResponse,
    SessionStatusResponse,
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


def _get_parse_service(
    anthropic: AnthropicClientDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ParseService:
    """Provider para o `ParseService` (BACK 7.1)."""
    return ParseService(
        anthropic,
        mock_enabled=settings.MOCK_PARSE,
        mock_delay_seconds=settings.MOCK_PARSE_DELAY_SECONDS,
    )


ParseServiceDep = Annotated[ParseService, Depends(_get_parse_service)]


# Provider do dispatcher de jobs — função em vez de import direto para que
# testes sobrescrevam via `dependency_overrides` e evitem subir Redis real.
async def _enqueue_reconciliation_job(
    session_id: UUID,
    settings: Settings,
) -> str:
    """Wrapper async sobre o dispatcher — separa o ponto de override."""
    return await enqueue_processing(session_id, redis_url=settings.REDIS_URL)


_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_HASH_PATTERN = r"^[a-fA-F0-9]{64}$"

# Mensagens reusadas em vários handlers — manter texto idêntico evita probing
# (manager fora da carteira não distingue de cliente inexistente, CLAUDE.md §3.11).
_CLIENT_NOT_FOUND_MSG = "Cliente não encontrado."
_SESSION_NOT_FOUND_MSG = "Sessão de conciliação não encontrada."


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
    try:
        await require_client_access(client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_CLIENT_NOT_FOUND_MSG) from exc

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
        "manager-da-carteira; cliente inacessível devolve 404. "
        "Rate limit: 10/min/usuário — controla custo Anthropic."
    ),
)
@limiter.limit("10/minute", key_func=user_id_key_func)
async def parse_statement(
    request: Request,
    response: Response,
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    parser: ParseServiceDep,
    client_id: Annotated[UUID, Form(description="UUID do cliente.")],
    file: Annotated[UploadFile, File(description="Extrato/fatura: PDF, CSV ou XLSX.")],
) -> ParseResponse:
    try:
        await require_client_access(client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_CLIENT_NOT_FOUND_MSG) from exc

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


# ----------------------------------------------------------------------
# BACK 8.1 — POST /reconciliations
# ----------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Cria sessão de conciliação a partir do ParsedStatement (S9) e "
        "enfileira o processamento async. RBAC: admin OU "
        "manager-da-carteira; cliente inacessível devolve 404. Idempotência "
        "garantida por UNIQUE (client_id, omie_conta_id, reference_month, "
        "file_hash) — duplicata retorna 409 DUPLICATE_FILE. "
        "Rate limit: 10/min/usuário — uma sessão = 1 job ARQ + várias "
        "chamadas Omie."
    ),
)
@limiter.limit("10/minute", key_func=user_id_key_func)
async def create_reconciliation(
    request: Request,
    response: Response,
    user: CurrentUserDep,
    db: DbSessionDep,
    service: ReconciliationServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
    payload: CreateReconciliationRequest,
) -> CreateReconciliationResponse:
    if user.role not in {"admin", "manager"}:
        raise NotFoundError(_CLIENT_NOT_FOUND_MSG)

    try:
        await require_client_access(payload.client_id, user, db)
    except ClientNotAccessibleError as exc:
        # Mesma decisão dos outros endpoints (CLAUDE.md §3.11): manager fora
        # da carteira recebe 404, não 403.
        raise NotFoundError(_CLIENT_NOT_FOUND_MSG) from exc

    session_id = await service.create_session_with_entries(
        request=payload,
        created_by=UUID(user.id),
        encryption_key=settings.OMIE_ENCRYPTION_KEY,
        search_blind_index_key=settings.SEARCH_BLIND_INDEX_KEY,
    )
    # O commit oficial acontece no `DbSessionDep` ao final do request bem
    # sucedido (`get_db_session`). NÃO commitamos manualmente aqui: na prática,
    # o ARQ leva ~segundos para um worker pollar o job (polling padrão > 500ms),
    # enquanto o commit transacional do request fica em microssegundos depois
    # do enqueue. A janela de race "worker picks up before DB commit" é
    # estatisticamente desprezível para o MVP. Em produção, se virar
    # problema, mover o `enqueue_processing` para um middleware after-commit.

    try:
        await _enqueue_reconciliation_job(session_id, settings)
    except Exception as exc:
        # Enqueue falhou. Levantar AppError → handler global responde 500 e
        # o `DbSessionDep` faz rollback (sessão NÃO é persistida). Usuário
        # tenta de novo, sem inconsistência.
        raise AppError(
            f"Falha ao enfileirar job para session_id={session_id}: {exc}",
            user_message=(
                "Sessão criada, mas a fila de processamento está indisponível. "
                "Tente novamente em instantes."
            ),
        ) from exc

    return CreateReconciliationResponse(
        data=CreateReconciliationPayload(
            session_id=session_id,
            status="processing",
        )
    )


# ----------------------------------------------------------------------
# BACK 8.6 — GET /reconciliations/{id}/status
# ----------------------------------------------------------------------


@router.get(
    "/{session_id}/status",
    summary=(
        "Polling de status da sessão. Front chama a cada 3s enquanto "
        "status='processing'. RBAC: admin OU manager-da-carteira do cliente "
        "dono da sessão; manager fora devolve 404 (consistência com /parse "
        "e /check-duplicate)."
    ),
)
async def get_reconciliation_status(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    service: ReconciliationServiceDep,
    session_id: UUID,
) -> SessionStatusResponse:
    # 1. Carrega a sessão (sem cliente eager) — precisamos do client_id pra
    #    validar RBAC. Se a sessão não existe → 404.
    repo_session = await ReconciliationRepository(db).get_status_view(session_id)
    if repo_session is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    # 2. RBAC por carteira via require_client_access. Manager fora → 404.
    try:
        await require_client_access(repo_session.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    payload = await service.get_session_status(session_id)
    return SessionStatusResponse(data=payload)


# ----------------------------------------------------------------------
# S11 — GET /reconciliations/{id}  (header da Tela de Revisão)
# ----------------------------------------------------------------------


@router.get(
    "/{session_id}",
    summary=(
        "Detalhe da sessão (header da Tela de Revisão). Substitui o scan "
        "O(N) que o front fazia via histórico paginado do cliente para "
        "resolver reference_month + omie_conta_id + total_file_entries. "
        "RBAC idêntico ao /status: manager fora da carteira recebe 404."
    ),
)
async def get_reconciliation_detail(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    service: ReconciliationServiceDep,
    session_id: UUID,
) -> SessionDetailResponse:
    # Mesma estratégia do /status: carrega a sessão pelo client_id, valida
    # RBAC, e só então pede o payload completo pro service. Manter as 2
    # rotas com o MESMO formato de RBAC evita probing (manager fora não
    # distingue 404-existe de 404-fora-da-carteira).
    repo_session = await ReconciliationRepository(db).get_detail_view(session_id)
    if repo_session is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    try:
        await require_client_access(repo_session.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    payload = await service.get_session_detail(session_id)
    return SessionDetailResponse(data=payload)
