"""Endpoints das notificações in-app (Sprint 4, BACK 04.4).

    - GET  /api/v1/notifications/unread-count   (badge do sino, poll de 15 s)
    - GET  /api/v1/notifications                (lista paginada)
    - POST /api/v1/notifications/{id}/read      (marca lida — idempotente)

⚠️ A rota literal `/unread-count` é declarada ANTES de qualquer rota com path
param: o FastAPI casa por ordem de declaração, e um `/{notification_id}` acima
engoliria "unread-count" como UUID inválido.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import (
    MarkReadResponse,
    NotificationListResponse,
    UnreadCountPayload,
    UnreadCountResponse,
)
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


def _get_notification_service(db: DbSessionDep) -> NotificationService:
    """Provider para injeção do service nos endpoints."""
    return NotificationService(NotificationRepository(db))


NotificationServiceDep = Annotated[NotificationService, Depends(_get_notification_service)]


@router.get(
    "/unread-count",
    summary=(
        "Contagem de notificações NÃO LIDAS do usuário autenticado — é o badge "
        "do sino, consultado por polling a cada 15 s (só com a aba em foco). "
        "Barato por construção: cai no índice PARCIAL "
        "`ix_notifications_user_unread`, que indexa apenas `read_at IS NULL` e "
        "não cresce com o histórico já lido. Devolve SÓ a contagem do próprio "
        "usuário; notificação de cliente fora da carteira do manager não entra."
    ),
)
async def get_unread_count(
    user: CurrentUserDep,
    service: NotificationServiceDep,
) -> UnreadCountResponse:
    unread = await service.unread_count(user)
    return UnreadCountResponse(data=UnreadCountPayload(unread=unread))


@router.get(
    "",
    summary=(
        "Lista paginada das notificações do usuário autenticado, mais recentes "
        "primeiro. Cada item traz `session_id` e `tipo` — é o que o front usa "
        "para emitir `notificacao_entregue` em `usage_events` (via + latência) "
        "sem uma 2ª chamada. Nenhum texto pronto vem daqui: a frase é montada "
        "no front a partir de conta/mês/tipo/código, e por isso nenhuma PII do "
        "conteúdo do arquivo pode vazar no aviso. `unreadOnly=true` filtra as "
        "não lidas (o `total` acompanha o filtro)."
    ),
)
async def list_notifications(
    user: CurrentUserDep,
    service: NotificationServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    unread_only: Annotated[bool, Query(alias="unreadOnly")] = False,
) -> NotificationListResponse:
    items, pagination = await service.list_notifications(
        user, page=page, page_size=page_size, unread_only=unread_only
    )
    return NotificationListResponse(data=items, pagination=pagination)


@router.post(
    "/{notification_id}/read",
    summary=(
        "Marca a notificação como lida. **Idempotente**: reenviar responde 200 "
        "com `already_read=true` e preserva o timestamp da 1ª leitura — lida "
        "não reaparece no contador. 404 quando a notificação não existe OU não "
        "é do usuário (mesma resposta nos dois casos, anti-enumeração)."
    ),
)
async def mark_notification_read(
    user: CurrentUserDep,
    service: NotificationServiceDep,
    notification_id: UUID,
) -> MarkReadResponse:
    payload = await service.mark_read(user, notification_id)
    return MarkReadResponse(data=payload)
