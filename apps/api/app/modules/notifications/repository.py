"""Acesso ao DB das notificações in-app (Sprint 4, BACK 04.4).

SQL puro. O RBAC (só as minhas, e só de clientes da minha carteira) é aplicado
AQUI, dentro do `WHERE` de toda leitura — e não como um filtro em Python depois
— porque é assim que ele não some quando alguém adiciona um endpoint novo.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClientAssignment, Notification


class NotificationRepository:
    """Operações de leitura/escrita sobre `notifications`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _visibility_filter(
        *, user_id: UUID, is_admin: bool, tenant_client_id: UUID | None = None
    ) -> list[ColumnElement[bool]]:
        """Condições de visibilidade — o RBAC da leitura, em SQL.

        Três camadas:

        1. `user_id = eu` — notificação é pessoal; ninguém lê a do outro.
        2. **tenant** (S5/R3) — usuário de cliente só vê notificação do próprio
           `client_id`. Redundante com (1) hoje, e de propósito: se um dia uma
           notificação for criada para outro usuário do mesmo tenant, ou (1) for
           afrouxado, o filtro de tenant continua de pé.
        3. cliente na carteira — se a carteira for reatribuída, as
           notificações antigas daquele cliente **param de aparecer** para o
           manager anterior. Sem isso, a linha antiga continuaria vazando
           conta+mês de um cliente que já não é dele. Admin não tem essa
           restrição (acessa qualquer cliente, CLAUDE.md §3.11).
        """
        conditions: list[ColumnElement[bool]] = [Notification.user_id == user_id]
        if tenant_client_id is not None:
            conditions.append(Notification.client_id == tenant_client_id)
        elif not is_admin:
            conditions.append(
                select(ClientAssignment.id)
                .where(
                    ClientAssignment.client_id == Notification.client_id,
                    ClientAssignment.user_id == user_id,
                )
                .exists()
            )
        return conditions

    async def count_unread(
        self, *, user_id: UUID, is_admin: bool, tenant_client_id: UUID | None = None
    ) -> int:
        """Contagem de não lidas. Barata: cai no índice PARCIAL
        `ix_notifications_user_unread`, que só indexa `read_at IS NULL` — não
        cresce com o histórico já lido, e é chamada a cada 15 s por usuário."""
        total: int | None = await self._session.scalar(
            select(func.count(Notification.id)).where(
                *self._visibility_filter(
                    user_id=user_id, is_admin=is_admin, tenant_client_id=tenant_client_id
                ),
                Notification.read_at.is_(None),
            )
        )
        return total or 0

    async def list_paginated(
        self,
        *,
        user_id: UUID,
        is_admin: bool,
        tenant_client_id: UUID | None = None,
        page: int,
        page_size: int,
        unread_only: bool = False,
    ) -> tuple[Sequence[Notification], int]:
        """Lista paginada do sino, mais recentes primeiro.

        `total` é contado com os MESMOS filtros da página (senão o rodapé mente
        quando `unread_only` está ligado).
        """
        conditions = self._visibility_filter(
            user_id=user_id, is_admin=is_admin, tenant_client_id=tenant_client_id
        )
        if unread_only:
            conditions.append(Notification.read_at.is_(None))

        total: int | None = await self._session.scalar(
            select(func.count(Notification.id)).where(*conditions)
        )
        rows = (
            await self._session.execute(
                select(Notification)
                .where(*conditions)
                # `id desc` desempata quando duas caem no mesmo instante
                # (acontece em teste e no fim de dois processamentos paralelos).
                .order_by(Notification.created_at.desc(), Notification.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows), total or 0

    async def get_for_user(
        self,
        *,
        notification_id: UUID,
        user_id: UUID,
        is_admin: bool,
        tenant_client_id: UUID | None = None,
    ) -> Notification | None:
        """Uma notificação visível para este usuário, ou `None`.

        `None` cobre "não existe" E "não é sua" — o caller devolve 404 nos dois
        casos, sem distinguir (anti-enumeração).
        """
        row: Notification | None = await self._session.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                *self._visibility_filter(
                    user_id=user_id, is_admin=is_admin, tenant_client_id=tenant_client_id
                ),
            )
        )
        return row

    async def mark_read(self, notification_id: UUID) -> datetime:
        """Marca como lida. **Idempotente**: o `WHERE read_at IS NULL` faz a 2ª
        chamada casar 0 linhas, preservando o timestamp da primeira leitura."""
        now = datetime.now(UTC)
        await self._session.execute(
            update(Notification)
            .where(Notification.id == notification_id, Notification.read_at.is_(None))
            .values(read_at=now)
        )
        return now

    async def add(self, notification: Notification) -> None:
        """Insere a notificação. Commit é do caller (padrão do projeto)."""
        self._session.add(notification)
        await self._session.flush()
