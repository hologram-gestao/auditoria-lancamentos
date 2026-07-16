"""Endpoints do módulo de conciliações.

S8 (BACK 6.2):
    - GET /api/v1/reconciliations/check-duplicate
S9 (BACK 7.1):
    - POST /api/v1/reconciliations/parse
S10 (BACK 8.1 + 8.6):
    - POST /api/v1/reconciliations
    - GET /api/v1/reconciliations/{session_id}/status
S11.fix (retry de sessão em erro):
    - POST /api/v1/reconciliations/{session_id}/reprocess
    - POST /api/v1/reconciliations/{session_id}/discard  (soft-delete)
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    ManagerOrAdminDep,
    require_client_access,
)
from app.core.exceptions import (
    ClientNotAccessibleError,
    ConflictError,
    DuplicateFileError,
    NotFoundError,
    ValidationAppError,
)
from app.core.rate_limit import limiter, user_id_key_func
from app.db.models import ReconciliationStatus
from app.integrations.anthropic.client import AnthropicClient
from app.modules.reconciliations.parse_service import ParseService
from app.modules.reconciliations.processing.job import run_reconciliation_processing
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
from app.utils.upload import parse_content_length, read_upload_within_limit

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
        max_output_tokens=settings.ADL_PARSE_MAX_OUTPUT_TOKENS,
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


# Ponto único de agendamento do processamento — função de módulo (não chamada
# direta ao `add_task`) para que os testes a sobrescrevam via monkeypatch e
# não disparem o processamento real (o TestClient do Starlette executa as
# BackgroundTasks de verdade após a resposta).
def _schedule_reconciliation_processing(
    background_tasks: BackgroundTasks,
    session_id: UUID,
) -> None:
    """Agenda `run_reconciliation_processing` como BackgroundTask do FastAPI.

    Roda depois que a resposta HTTP é enviada, no mesmo processo. A sessão já
    foi commitada antes desta chamada (ver `create_reconciliation`), então a
    task — que abre a própria DB session — enxerga a linha recém-criada.
    """
    background_tasks.add_task(run_reconciliation_processing, str(session_id))


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
    service: ReconciliationServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
    parser: ParseServiceDep,
    client_id: Annotated[UUID, Form(description="UUID do cliente.")],
    file: Annotated[UploadFile, File(description="Extrato/fatura: PDF, CSV ou XLSX.")],
) -> ParseResponse:
    try:
        await require_client_access(client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_CLIENT_NOT_FOUND_MSG) from exc

    # BACK 02.8 — valida o tamanho ANTES de carregar o arquivo inteiro: pré-check
    # por Content-Length + leitura em streaming com corte no teto (fonte única em
    # Settings). Antes, o `await file.read()` alocava o arquivo todo e só depois
    # o `parse_service` conferia o tamanho — o limite não protegia a memória.
    file_bytes = await read_upload_within_limit(
        file,
        declared_content_length=parse_content_length(request.headers.get("content-length")),
        max_bytes=settings.max_upload_bytes,
    )

    if not file_bytes:
        raise ValidationAppError(
            "Arquivo enviado está vazio.",
            user_message="O arquivo enviado está vazio.",
        )

    # BACK 02.6 — dedup DENTRO do /parse, ANTES de qualquer chamada à IA (custo).
    # O hash é do CONTEÚDO, recalculado no servidor (nunca confiar no hash que o
    # cliente manda no /check-duplicate). Se já existe sessão ATIVA do cliente
    # com esse conteúdo → 409 sem pagar a Anthropic. Sessão anterior em `error`
    # não conta: reimportar é permitido (não punir o usuário por erro do sistema).
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    duplicate = await service.find_parse_duplicate(client_id=client_id, file_hash=file_hash)
    if duplicate is not None:
        dup_session_id, imported_at = duplicate
        raise DuplicateFileError(
            f"Conteúdo já importado para client_id={client_id} "
            f"(sessão {dup_session_id}, hash {file_hash[:8]}).",
            user_message=(
                f"Este extrato já foi importado em {imported_at.strftime('%d/%m/%Y')}. "
                "Abra a conciliação existente para revisá-la em vez de reenviar."
            ),
            metadata={
                "session_id": str(dup_session_id),
                "imported_at": imported_at.isoformat(),
            },
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
        "Cria sessão de conciliação a partir do ParsedStatement (S9) e agenda "
        "o processamento como BackgroundTask do FastAPI. RBAC: admin OU "
        "manager-da-carteira; cliente inacessível devolve 404. Idempotência "
        "garantida por UNIQUE (client_id, omie_conta_id, reference_month, "
        "file_hash) — duplicata retorna 409 DUPLICATE_FILE. "
        "Rate limit: 10/min/usuário — uma sessão = 1 processamento em "
        "background + várias chamadas Omie."
    ),
)
@limiter.limit("10/minute", key_func=user_id_key_func)
async def create_reconciliation(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
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
    # Commit ANTES de agendar. A BackgroundTask roda no MESMO processo, abrindo
    # sua PRÓPRIA DB session — precisa enxergar a sessão já commitada.
    # EMPÍRICO: a BackgroundTask executa ANTES do commit de teardown do
    # `get_db_session` → sem este commit, a task lê a sessão antes de existir e
    # sai com `reconciliation_session_not_found`, deixando-a presa em
    # `processing`. (O ARQ mascarava isso: o worker pollava o Redis segundos
    # depois, com o commit já feito.) Commit explícito torna a ordem
    # determinística. Em teste a fixture usa SAVEPOINT — não vaza estado (conftest).
    await db.commit()

    # Sem broker: o processamento (Omie + matching + qualificação) roda em
    # background no próprio processo da API. `add_task` é in-memory e não falha.
    _schedule_reconciliation_processing(background_tasks, session_id)

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
# S11.fix — POST /reconciliations/{id}/reprocess (retry de sessão em erro)
# ----------------------------------------------------------------------


@router.post(
    "/{session_id}/reprocess",
    summary=(
        "Reprocessa uma sessão que terminou em `status='error'`. Mantém "
        "as `file_entries` (resultado do parse Anthropic, vale dinheiro), "
        "limpa dados parciais de matching/anomalias, reset da sessão pra "
        "`status='processing'`, e reagenda o processamento em background. "
        "Rate limit: 10/min/usuário (igual ao create) — uma sessão = 1 "
        "processamento em background + várias chamadas Omie. "
        "Conflito (409): se a sessão NÃO está em error (já processando, "
        "em revisão ou concluída), recusamos pra não duplicar processamento."
    ),
)
@limiter.limit("10/minute", key_func=user_id_key_func)
async def reprocess_reconciliation(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    session_id: UUID,
) -> CreateReconciliationResponse:
    """Endpoint de "Tentar novamente" da tela de revisão / lista de conciliações."""
    repo = ReconciliationRepository(db)

    # 1. Carrega a sessão (validação de existência + status atual).
    sess = await repo.get_status_view(session_id)
    if sess is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    # 2. RBAC: manager-da-carteira ou admin. Manager fora → 404 (probing-safe).
    try:
        await require_client_access(sess.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    # 3. Só faz sentido reprocessar quando o estado atual é error.
    if sess.status != ReconciliationStatus.ERROR.value:
        raise ConflictError(
            f"Sessão {session_id} não está em erro (status={sess.status}).",
            user_message=(
                "Esta conciliação não está em estado de erro — só sessões "
                "que terminaram com erro podem ser reprocessadas."
            ),
        )

    # 4. Reset + agendamento. Idem create: commit explícito ANTES de agendar pra
    #    a BackgroundTask (mesmo processo, session própria) enxergar o reset já
    #    persistido — a task roda antes do commit de teardown do `get_db_session`.
    await repo.reset_session_for_reprocess(session_id)
    await db.commit()

    _schedule_reconciliation_processing(background_tasks, session_id)

    return CreateReconciliationResponse(
        data=CreateReconciliationPayload(
            session_id=session_id,
            status="processing",
        )
    )


# ----------------------------------------------------------------------
# S11.fix — POST /reconciliations/{id}/discard  (soft-delete de sessão em erro)
# ----------------------------------------------------------------------


@router.post(
    "/{session_id}/discard",
    status_code=status.HTTP_204_NO_CONTENT,
    summary=(
        "Exclui (soft-delete) uma conciliação em `reviewing`, `done` ou "
        "`error`. Marca `deleted_at=now()`; o histórico fica preservado pra "
        "auditoria, mas a sessão some da UI. Libera o índice UNIQUE de "
        "idempotência (`client_id, omie_conta_id, reference_month, file_hash`) "
        "— usuário pode recriar a mesma conta+mês+arquivo (refazer). "
        "Conflito (409): sessão em `processing` NÃO pode ser excluída — "
        "cancele o processamento antes (`POST /cancel`)."
    ),
)
async def discard_reconciliation(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    session_id: UUID,
) -> Response:
    """Soft-delete da sessão. 204 sem corpo no sucesso."""
    repo = ReconciliationRepository(db)

    sess = await repo.get_status_view(session_id)
    if sess is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    try:
        await require_client_access(sess.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    # Só bloqueia o que está em processamento (o job ainda escreve nela) — para
    # essas, o caminho é cancelar primeiro. reviewing/done/error podem ser
    # excluídas (soft-delete libera a tupla de idempotência pra refazer).
    if sess.status == ReconciliationStatus.PROCESSING.value:
        raise ConflictError(
            f"Sessão {session_id} está em processamento (status={sess.status}).",
            user_message=(
                "Não é possível excluir uma conciliação em processamento. "
                "Cancele o processamento primeiro."
            ),
        )

    await repo.soft_delete_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------
# POST /reconciliations/{id}/cancel  (cancela uma sessão em processamento)
# ----------------------------------------------------------------------


@router.post(
    "/{session_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    summary=(
        "Cancela uma conciliação em `processing`: marca `status='error'` com "
        "uma mensagem de cancelamento. A BackgroundTask em andamento não é "
        "interrompida (não dá pra matar a task), mas o `update_session_after_"
        "matching` final tem guarda `WHERE status='processing'` — então o job "
        "não sobrescreve o cancelamento. Depois de cancelada, a sessão vira "
        "`error`: o usuário pode reprocessar ou excluir. Conflito (409): "
        "sessão que NÃO está em processamento (já em revisão/concluída/erro)."
    ),
)
async def cancel_reconciliation(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    session_id: UUID,
) -> Response:
    """Cancela o processamento marcando a sessão como erro. 204 no sucesso."""
    repo = ReconciliationRepository(db)

    sess = await repo.get_status_view(session_id)
    if sess is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    try:
        await require_client_access(sess.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    if sess.status != ReconciliationStatus.PROCESSING.value:
        raise ConflictError(
            f"Sessão {session_id} não está em processamento (status={sess.status}).",
            user_message=(
                "Só conciliações em processamento podem ser canceladas. "
                "Para as demais, use Excluir."
            ),
        )

    await repo.mark_session_error(
        session_id,
        user_message="Processamento cancelado pelo usuário.",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
