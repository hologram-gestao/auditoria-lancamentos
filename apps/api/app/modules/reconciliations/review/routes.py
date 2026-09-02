"""Rotas HTTP da Tela de Revisão (S11).

Cobertura:
    - BACK 9.1: GET  /api/v1/reconciliations/{session_id}/file-entries
    - BACK 9.3: PATCH /api/v1/reconciliations/{session_id}/file-entries/{entry_id}
    - BACK 9.4: GET  /api/v1/reconciliations/{session_id}/available-omie-entries
    - BACK 9.5: GET  /api/v1/reconciliations/{session_id}/omie-entries
    - BACK 9.6: PATCH /api/v1/reconciliations/{session_id}/omie-entries/{entry_id}
    - BACK 9.7: GET  /api/v1/reconciliations/{session_id}/anomalies
    - BACK 9.8: POST  /api/v1/reconciliations/{session_id}/anomalies
    - BACK 9.9: PATCH /api/v1/reconciliations/{session_id}/anomalies/{anomaly_id}

Padrão RBAC: admin OU manager-da-carteira da sessão; manager fora → 404
para não vazar existência (CLAUDE.md §3.11). Mesma estratégia já adotada em
/check-duplicate, /parse, POST /reconciliations e /status.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser
from app.core.config import Settings, get_settings
from app.core.crypto_service import load_client_cipher
from app.core.dependencies import (
    DbSessionDep,
    ReviewExportDep,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import Client, ReconciliationSession, ReconciliationStatus
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.integrations.omie.client import OmieClient
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.clients.omie_factory import build_omie_client
from app.modules.reconciliations.review.repository import ReviewRepository
from app.modules.reconciliations.review.schemas import (
    AnomalyListResponse,
    AvailableOmieEntriesResponse,
    CreateAnomalyRequest,
    CreateAnomalyResponse,
    FileEntryListResponse,
    OmieEntryListResponse,
    ResolveAnomalyRequest,
    ResolveAnomalyResponse,
    UpdateFileEntryRequest,
    UpdateFileEntryResponse,
    UpdateOmieEntryRequest,
    UpdateOmieEntryResponse,
)
from app.modules.reconciliations.review.service import ReviewService
from app.modules.reconciliations.tenant_scope import require_session_access

router = APIRouter(
    prefix="/api/v1/reconciliations/{session_id}",
    tags=["reconciliations:review"],
)

logger = get_logger(__name__)

_SESSION_NOT_FOUND_MSG = "Sessão de conciliação não encontrada."


def _get_review_service(
    db: DbSessionDep,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReviewService:
    """Provider: injeta os caches singleton do app + Settings."""
    cache: OmieLancamentoCache = request.app.state.omie_lancamento_cache
    categorias_cache: OmieCategoriasCache = request.app.state.omie_categorias_cache
    return ReviewService(
        ReviewRepository(db),
        cache=cache,
        settings=settings,
        categorias_cache=categorias_cache,
    )


ReviewServiceDep = Annotated[ReviewService, Depends(_get_review_service)]


_REVIEW_BLOCKED_USER_MSG = (
    "Esta conciliação terminou em erro e ainda não foi reprocessada. "
    "Reprocesse a sessão antes de abrir a tela de revisão."
)


async def _load_session_for_rbac(
    *,
    session_id: UUID,
    user: CurrentUser,
    db: AsyncSession,
) -> ReconciliationSession:
    """Carrega a sessão e valida RBAC + status reviewável (CLAUDE.md §3.11).

    Status:
      - `processing`/`reviewing`/`done` → segue, endpoints de revisão respondem
        normalmente (processing terá contadores zerados, vide UX que ainda
        mostra o polling).
      - `error` → ConflictError (409). O front intercepta esse status já em
        `useSessionDetail` (rota /reconciliations/{id}) e mostra a página de
        erro com botão "Tentar novamente". Esta camada serve como segunda
        linha de defesa: se o front esquecer o check, a API ainda recusa
        em vez de servir uma tela de revisão vazia em cima de dados inválidos.
    """
    # `require_session_access`: o SELECT da sessão já leva
    # `AND client_id = <tenant do usuário>` (S5/R3) e a carteira do usuário
    # `system` é validada em seguida — tudo com 404 uniforme (anti-enumeração).
    # O `CurrentUser` REAL é repassado (não um remontado a partir de id+role):
    # `scope`/`client_id` fazem parte da decisão e da trilha de auditoria.
    try:
        sess = await require_session_access(db, user, session_id)
    except NotFoundError as exc:
        # Cobre sessão inexistente, de outro tenant, e cliente removido (órfã).
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    if sess.status == ReconciliationStatus.ERROR.value:
        raise ConflictError(
            f"Sessão {session_id} em status=error não é reviewável.",
            user_message=_REVIEW_BLOCKED_USER_MSG,
        )
    return sess


# ----------------------------------------------------------------------
# BACK 9.1 — GET /file-entries
# ----------------------------------------------------------------------


@router.get(
    "/file-entries",
    summary=(
        "Lista movimentações da sessão com filtros (situation, type, search, "
        "onlySuspect) "
        "e paginação. Descriptografa `description` e `user_note` no servidor "
        "antes de retornar. Filtro `search` aplica-se PÓS-decrypt em memória. "
        "`situation` aceita `conciliado_data_divergente` (FASE 1) — sem ele a "
        "aba não conseguia mostrar as linhas que casaram com data divergente, "
        "e a soma dos filtros não fechava com o contador do detalhe."
    ),
)
async def list_file_entries(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    session_id: UUID,
    situation: Annotated[
        Literal["all", "conciliado", "conciliado_data_divergente", "sem_omie", "ignorado"],
        Query(),
    ] = "all",
    type_filter: Annotated[Literal["all", "credit", "debit"], Query(alias="type")] = "all",
    search: Annotated[str | None, Query(max_length=200)] = None,
    # 86e2n4pf1 — filtro server-side de qualificação suspeita. O alias é
    # OBRIGATÓRIO (§7): sem ele o `onlySuspect` do front seria descartado em
    # silêncio e o filtro viraria enfeite — o mesmo furo do pageSize de 14/08.
    only_suspect: Annotated[bool, Query(alias="onlySuspect")] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> FileEntryListResponse:
    await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    rows, pagination = await service.list_file_entries(
        session_id=session_id,
        situation=None if situation == "all" else situation,
        type_filter=None if type_filter == "all" else type_filter,
        search=search,
        only_suspect=only_suspect,
        page=page,
        page_size=page_size,
    )
    return FileEntryListResponse(data=rows, pagination=pagination)


# ----------------------------------------------------------------------
# BACK 9.3 — PATCH /file-entries/{entry_id}
# ----------------------------------------------------------------------


@router.patch(
    "/file-entries/{entry_id}",
    summary=(
        "Atualiza ação/situação/nota/vínculo Omie de uma movimentação. "
        "Para 'Trocar', envia `omie_lancamento_id` novo (ou null para "
        "limpar). Recalcula contadores na sessão. RBAC consistente."
    ),
)
async def update_file_entry(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    session_id: UUID,
    entry_id: UUID,
    body: UpdateFileEntryRequest,
) -> UpdateFileEntryResponse:
    await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    # Quem distingue "chave presente com null" (limpar) de "chave omitida"
    # (não tocar) é o service, via `field_provided` sobre `model_fields_set`.
    # A rota não repassa flag: caminho único, impossível de esquecer.
    updated = await service.update_file_entry(
        session_id=session_id,
        entry_id=entry_id,
        body=body,
    )
    return UpdateFileEntryResponse(data=updated)


# ----------------------------------------------------------------------
# BACK 9.4 — GET /available-omie-entries
# ----------------------------------------------------------------------


@router.get(
    "/available-omie-entries",
    summary=(
        "Lista lançamentos do Omie no período da sessão expandido pela "
        "tolerância, subtraindo os já vinculados em outras linhas. Popula "
        "o cache L2 — chamadas a /omie/lancamentos reaproveitam."
    ),
)
async def list_available_omie_entries(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
    session_id: UUID,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> AvailableOmieEntriesResponse:
    sess = await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    client = (
        await db.execute(select(Client).where(Client.id == sess.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    cipher = await load_client_cipher(client, settings=settings)
    omie_client = build_omie_client(client, settings, cipher)
    try:
        data = await service.list_available_omie_entries(
            session=sess,
            omie_client=omie_client,
            search=search,
        )
    finally:
        await omie_client.aclose()
    return AvailableOmieEntriesResponse(data=data)


# ----------------------------------------------------------------------
# BACK 9.5 — GET /omie-entries
# ----------------------------------------------------------------------


@router.get(
    "/omie-entries",
    summary=(
        "Lista divergências Omie (lançamentos persistidos sem correspondente "
        "no arquivo) enriquecidas com supplier/category/amount via cache L1 — "
        "repopulado do extrato quando algum id está sem dado (86e2z895j)."
    ),
)
async def list_omie_entries(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    settings: Annotated[Settings, Depends(get_settings)],
    session_id: UUID,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> OmieEntryListResponse:
    sess = await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    client = (
        await db.execute(select(Client).where(Client.id == sess.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    # Fail-soft de ponta a ponta: a aba renderiza com "—" antes desta mudança,
    # e credencial indecifrável/ausente não pode transformá-la num 500 — o
    # service só usa o client no MISS, e sem ele degrada para o lookup puro.
    omie_client: OmieClient | None = None
    try:
        cipher = await load_client_cipher(client, settings=settings)
        omie_client = build_omie_client(client, settings, cipher)
    except Exception as exc:
        logger.warning(
            "omie_entries_client_build_failed",
            session_id=str(session_id),
            error=type(exc).__name__,
        )

    try:
        rows, pagination = await service.list_omie_entries(
            session=sess,
            page=page,
            page_size=page_size,
            omie_client=omie_client,
        )
    finally:
        if omie_client is not None:
            await omie_client.aclose()
    return OmieEntryListResponse(data=rows, pagination=pagination)


# ----------------------------------------------------------------------
# BACK 9.6 — PATCH /omie-entries/{entry_id}
# ----------------------------------------------------------------------


@router.patch(
    "/omie-entries/{entry_id}",
    summary=(
        "Atualiza ação/nota em uma divergência Omie. NÃO recalcula "
        "contadores da sessão (omie_sem_arquivo_count é estático)."
    ),
)
async def update_omie_entry(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    session_id: UUID,
    entry_id: UUID,
    body: UpdateOmieEntryRequest,
) -> UpdateOmieEntryResponse:
    sess = await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    updated = await service.update_omie_entry(
        session=sess,
        entry_id=entry_id,
        body=body,
    )
    return UpdateOmieEntryResponse(data=updated)


# ----------------------------------------------------------------------
# BACK 9.7 — GET /anomalies
# ----------------------------------------------------------------------


@router.get(
    "/anomalies",
    summary=(
        "Lista anomalias da sessão com filtros (resolved, severity) e "
        "paginação. Ordenado por severity (critical→moderate→info) e "
        "created_at desc dentro do grupo. Contexto/nota descriptografados."
    ),
)
async def list_anomalies(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    session_id: UUID,
    resolved: Annotated[Literal["all", "true", "false"], Query()] = "all",
    severity: Annotated[Literal["all", "critical", "moderate", "info"], Query()] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> AnomalyListResponse:
    await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    resolved_filter: bool | None
    if resolved == "true":
        resolved_filter = True
    elif resolved == "false":
        resolved_filter = False
    else:
        resolved_filter = None
    rows, pagination = await service.list_anomalies(
        session_id=session_id,
        resolved_filter=resolved_filter,
        severity_filter=None if severity == "all" else severity,
        page=page,
        page_size=page_size,
    )
    return AnomalyListResponse(data=rows, pagination=pagination)


# ----------------------------------------------------------------------
# BACK 9.8 — POST /anomalies
# ----------------------------------------------------------------------


@router.post(
    "/anomalies",
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Cria anomalia manual. Exige `anomaly_type_id` ativo. Aceita ZERO "
        "ou UM entre file_entry_id/omie_entry_id (nunca os dois). Atualiza "
        "`anomaly_count` na sessão."
    ),
)
async def create_anomaly(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    session_id: UUID,
    body: CreateAnomalyRequest,
) -> CreateAnomalyResponse:
    await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    anomaly = await service.create_anomaly(session_id=session_id, body=body)
    return CreateAnomalyResponse(data=anomaly)


# ----------------------------------------------------------------------
# BACK 9.9 — PATCH /anomalies/{anomaly_id}
# ----------------------------------------------------------------------


@router.patch(
    "/anomalies/{anomaly_id}",
    summary=(
        "Atualiza uma anomalia da sessão em DOIS eixos independentes, ambos "
        "opcionais (envie ao menos um; omitir um campo significa não mexer "
        "nele). `resolved` marca/desfaz a resolução — `resolved=true` exige "
        "`resolution_note` com ≥ 10 caracteres (Doc §17.3) e recalcula o "
        "`anomaly_count` da sessão. `review_verdict` "
        "(`procedente`|`improcedente`) registra o julgamento do revisor sobre "
        "o flag e SÓ é aceito em suspeita/incoerência levantada pela análise "
        "de classificação — outros tipos de anomalia devolvem 400 "
        "VALIDATION_ERROR. Mudar o veredito emite o evento de outcome uma vez "
        "por mudança real; reenviar o mesmo valor não duplica. Anomalia de "
        "outra sessão ou de outro tenant devolve 404."
    ),
)
async def resolve_anomaly(
    user: ReviewExportDep,
    db: DbSessionDep,
    service: ReviewServiceDep,
    session_id: UUID,
    anomaly_id: UUID,
    body: ResolveAnomalyRequest,
) -> ResolveAnomalyResponse:
    await _load_session_for_rbac(session_id=session_id, user=user, db=db)
    anomaly = await service.resolve_anomaly(
        session_id=session_id,
        anomaly_id=anomaly_id,
        body=body,
    )
    return ResolveAnomalyResponse(data=anomaly)
