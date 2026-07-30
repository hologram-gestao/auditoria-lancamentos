"""Regra de negócio das notificações in-app (Sprint 4, BACK 04.4).

Duas responsabilidades:

1. **Criar** o aviso quando a conciliação aterrissa (`reviewing` → Processada,
   `error` → Erro). Chamado pelo fim do processamento, e **fail-soft**: uma
   falha ao notificar não pode desfazer um processamento que deu certo.
2. **Ler/marcar como lida**, com o RBAC embutido nas queries do repository.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authz import tenant_filter_client_id
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import Notification, NotificationType, ReconciliationStatus
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import (
    MarkReadPayload,
    NotificationItem,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.core.authz import CurrentUser

logger = get_logger(__name__)

#: `reviewing` e `error` são os únicos estados que interrompem quem trabalha.
#: `done` (revisada pelo próprio usuário) e `processing` não geram aviso —
#: notificar o usuário sobre uma ação dele mesmo é ruído, e ruído faz o sino
#: ser ignorado (risco S-2 da sprint).
_STATUS_TO_TYPE: dict[str, NotificationType] = {
    ReconciliationStatus.REVIEWING.value: NotificationType.PROCESSADA,
    ReconciliationStatus.ERROR.value: NotificationType.ERRO,
}


class NotificationService:
    """Criação e leitura de notificações in-app."""

    def __init__(self, repository: NotificationRepository) -> None:
        self._repo = repository

    # ------------------------------------------------------------------
    # Criação (chamada pelo fim do processamento)
    # ------------------------------------------------------------------

    async def notify_session_settled(
        self,
        *,
        session_id: UUID,
        client_id: UUID,
        user_id: UUID,
        status: str,
        omie_conta_id: int,
        reference_month: date,
        error_code: str | None,
    ) -> Notification | None:
        """Cria o aviso do desfecho, ou `None` se o estado não gera aviso.

        Sem dedup por (sessão, tipo) de propósito: um `/reprocess` que falha de
        novo **deve** avisar de novo — a pessoa precisa saber que a segunda
        tentativa também não deu certo. (Diferente do `conciliacao_concluida`
        em `usage_events`, que é métrica e conta sessões, não execuções.)

        O `error_code` viaja para que a tela mostre "(cód. X)" — nunca a
        mensagem interna (S2/R9).
        """
        tipo = _STATUS_TO_TYPE.get(status)
        if tipo is None:
            return None

        notification = Notification(
            user_id=user_id,
            session_id=session_id,
            client_id=client_id,
            tipo=tipo.value,
            omie_conta_id=omie_conta_id,
            reference_month=reference_month,
            error_code=error_code if tipo is NotificationType.ERRO else None,
        )
        await self._repo.add(notification)
        logger.info(
            "notification_created",
            session_id=str(session_id),
            user_id=str(user_id),
            tipo=tipo.value,
            error_code=notification.error_code,
        )
        return notification

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    async def unread_count(self, user: CurrentUser) -> int:
        """Contagem de não lidas do usuário autenticado."""
        return await self._repo.count_unread(
            user_id=UUID(user.id),
            is_admin=_is_admin(user),
            tenant_client_id=tenant_filter_client_id(user),
        )

    async def list_notifications(
        self,
        user: CurrentUser,
        *,
        page: int,
        page_size: int,
        unread_only: bool,
    ) -> tuple[list[NotificationItem], PaginationMeta]:
        """Lista paginada do sino."""
        rows, total = await self._repo.list_paginated(
            user_id=UUID(user.id),
            is_admin=_is_admin(user),
            # S5/R3: usuário de cliente filtra pelo próprio tenant, no SELECT.
            tenant_client_id=tenant_filter_client_id(user),
            page=page,
            page_size=page_size,
            unread_only=unread_only,
        )
        items = [NotificationItem.model_validate(row, from_attributes=True) for row in rows]
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return items, PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def mark_read(self, user: CurrentUser, notification_id: UUID) -> MarkReadPayload:
        """Marca como lida. Idempotente; 404 se não existe OU não é do usuário.

        Os dois casos devolvem a MESMA resposta de propósito: distinguir
        "não existe" de "não é sua" permitiria enumerar notificações alheias.
        """
        notification = await self._repo.get_for_user(
            notification_id=notification_id,
            user_id=UUID(user.id),
            is_admin=_is_admin(user),
            tenant_client_id=tenant_filter_client_id(user),
        )
        if notification is None:
            raise NotFoundError("Notificação não encontrada.")

        if notification.read_at is not None:
            return MarkReadPayload(
                id=notification.id, read_at=notification.read_at, already_read=True
            )

        read_at = await self._repo.mark_read(notification_id)
        return MarkReadPayload(id=notification.id, read_at=read_at, already_read=False)


def _is_admin(user: CurrentUser) -> bool:
    return user.role == "admin"
