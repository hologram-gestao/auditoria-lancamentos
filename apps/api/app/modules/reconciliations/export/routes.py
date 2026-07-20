"""Endpoint de exportação Excel (S14 BACK 10.1).

Rota:
    POST /api/v1/reconciliations/{session_id}/export

RBAC idêntico ao módulo de revisão: admin OU manager-da-carteira; manager
fora → 404 (probing-safe, CLAUDE.md §3.11).

Erros:
    404 — sessão inexistente OU soft-deletada OU manager fora da carteira.
    409 — status `processing` ou `error` (não exportável).
    200 — `StreamingResponse` com binário XLSX + Content-Disposition.

Headers:
    Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    Content-Disposition: attachment; filename="Conciliacao_{...}.xlsx"

Performance:
    Geração síncrona (em memória). Para sessões com até ~10k linhas o
    XLSX sai em < 2 s no MVP — sem necessidade de fila Celery (PLANO §S14).
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.audit import AccessAction, record_access
from app.core.config import Settings, get_settings
from app.core.crypto_service import load_client_cipher
from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    ManagerOrAdminDep,
    require_client_access,
)
from app.core.exceptions import ClientNotAccessibleError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import Client, ReconciliationStatus
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.clients.omie_factory import build_omie_client
from app.modules.reconciliations.export.service import ExportService, load_session_for_export
from app.modules.reconciliations.export.workbook import build_workbook

router = APIRouter(
    prefix="/api/v1/reconciliations/{session_id}",
    tags=["reconciliations:export"],
)

logger = get_logger(__name__)

_SESSION_NOT_FOUND_MSG = "Sessão de conciliação não encontrada."
_EXPORT_BLOCKED_USER_MSG = (
    "Esta conciliação ainda não pode ser exportada. "
    "Conclua a revisão (ou aguarde o processamento) e tente novamente."
)

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Status exportáveis (PLANO S14): `reviewing` (revisão em curso) e `done`
# (revisão concluída). `processing`/`error` recusam com 409.
_EXPORTABLE_STATUSES = frozenset(
    {ReconciliationStatus.REVIEWING.value, ReconciliationStatus.DONE.value}
)


def _get_export_service(
    db: DbSessionDep,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ExportService:
    """Provider: reusa o cache L2 singleton do app + chave de criptografia."""
    cache: OmieLancamentoCache = request.app.state.omie_lancamento_cache
    return ExportService(
        db,
        cache=cache,
        settings=settings,
    )


ExportServiceDep = Annotated[ExportService, Depends(_get_export_service)]


@router.post(
    "/export",
    summary=(
        "Gera o relatório Excel (5 abas) da sessão e devolve binário com "
        "Content-Disposition: attachment. RBAC consistente com /review: "
        "manager fora da carteira recebe 404. Recusa com 409 sessões em "
        "processing/error (não há dado conciliado pra exportar)."
    ),
)
async def export_reconciliation(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    settings: Annotated[Settings, Depends(get_settings)],
    service: ExportServiceDep,
    current_user: CurrentUserDep,
    session_id: UUID,
) -> StreamingResponse:
    sess = await load_session_for_export(db=db, session_id=session_id)

    # RBAC: manager fora da carteira → 404 (CLAUDE.md §3.11). Reusa
    # `require_client_access` e converte para NotFoundError, idêntico ao
    # módulo de revisão.
    try:
        await require_client_access(sess.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc
    except NotFoundError as exc:
        # Cliente foi removido — sessão órfã. 404 também.
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    if sess.status not in _EXPORTABLE_STATUSES:
        raise ConflictError(
            f"Sessão {session_id} em status={sess.status} não é exportável.",
            user_message=_EXPORT_BLOCKED_USER_MSG,
        )

    # Carrega o cliente — precisamos do `name` (para filename + sheet 1) e
    # das credenciais Omie criptografadas (para rehidratar cache L2 quando
    # necessário). Toda RBAC já passou; aqui é só fetch.
    client_row = (
        await db.execute(select(Client).where(Client.id == sess.client_id))
    ).scalar_one_or_none()
    if client_row is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    # BACK 03.5 — auditoria `export`: a leitura que MAIS decifra dado sensível.
    # commit=False: caminho de sucesso; o commit de fim de request persiste.
    await record_access(
        db,
        user_id=UUID(current_user.id),
        client_id=sess.client_id,
        session_id=session_id,
        action=AccessAction.EXPORT,
    )

    cipher = await load_client_cipher(client_row, settings=settings)
    omie_client = build_omie_client(client_row, settings, cipher)
    try:
        payload = await service.build_payload(
            session=sess,
            client=client_row,
            omie_client=omie_client,
            current_user_email=current_user.email,
        )
    finally:
        await omie_client.aclose()

    workbook_bytes = build_workbook(payload)

    filename = f"{payload.filename}.xlsx"

    logger.info(
        "export_generated",
        session_id=str(session_id),
        client_id=str(client_row.id),
        bytes=len(workbook_bytes.getvalue()),
        user_id=current_user.id,
    )

    # `filename*=UTF-8''...` é o cabeçalho RFC 5987 — preserva caracteres
    # acentuados em browsers modernos. Fornecemos também `filename=...`
    # com versão ASCII pra compatibilidade. (`build_filename` já remove
    # acentos, então as duas formas são equivalentes hoje; o `filename*`
    # entra como guard pra evoluções futuras do sanitizer.)
    content_disposition = (
        f"attachment; filename=\"{payload.filename}.xlsx\"; filename*=UTF-8''{quote(filename)}"
    )

    return StreamingResponse(
        workbook_bytes,
        media_type=_XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": content_disposition},
    )
