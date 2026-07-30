"""Acesso ao DB da instrumentação de outcome (Sprint 4, BACK 04.1).

SQL puro sobre `usage_events` + a resolução do `client_id` da sessão (usada pelo
RBAC do endpoint). Não conhece regra de negócio nem política de erro — quem
decide o fail-soft é o service.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser, scoped_by_tenant
from app.db.models import ReconciliationSession, UsageEvent


class UsageEventRepository:
    """Operações de leitura/escrita da tabela `usage_events`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_ignore_duplicate(
        self,
        *,
        event: str,
        session_id: UUID | None,
        props: dict[str, Any],
    ) -> bool:
        """Insere o evento; devolve `False` se já existia para aquela sessão.

        `ON CONFLICT DO NOTHING` sobre a UNIQUE parcial
        `uq_usage_events_event_session` — a idempotência mora no banco, não numa
        checagem prévia em Python (que perderia a corrida entre dois requests).

        **O `index_where` NÃO é decorativo:** o índice é PARCIAL
        (`WHERE session_id IS NOT NULL`) e o Postgres não infere índice parcial
        a partir das colunas sozinhas — sem repetir o predicado, o INSERT morre
        com `42P10: there is no unique or exclusion constraint matching the ON
        CONFLICT specification` e o fail-soft engole o erro, gravando NADA.

        **SAVEPOINT obrigatório:** este INSERT roda dentro da transação de quem
        chamou (a request que cria a conciliação, a transação do job). Sem o
        `begin_nested`, qualquer erro aqui marcaria a session SQLAlchemy como
        abortada e derrubaria o fluxo de negócio junto — exatamente o oposto do
        fail-soft exigido. Com o savepoint, o rollback é só desta linha.
        """
        stmt = (
            pg_insert(UsageEvent)
            .values(event=event, session_id=session_id, props=props)
            .on_conflict_do_nothing(
                index_elements=["event", "session_id"],
                index_where=text("session_id IS NOT NULL"),
            )
            .returning(UsageEvent.id)
        )
        async with self._session.begin_nested():
            result = await self._session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def get_session_client_id(self, session_id: UUID, *, user: CurrentUser) -> UUID | None:
        """`client_id` da sessão ATIVA, ou `None` se não existe/foi descartada.

        Entrada do RBAC do `POST /usage-events`: o `client_id` que sai daqui é o
        que vai para `require_client_access` — o cliente nunca informa a que
        carteira a sessão pertence.

        S5/R3: o `SELECT` já leva `AND client_id = <tenant do usuário>`, então
        sessão de outro tenant devolve `None` (→ 404) sem nem ser lida.
        """
        stmt = select(ReconciliationSession.client_id).where(
            ReconciliationSession.id == session_id,
            ReconciliationSession.deleted_at.is_(None),
        )
        client_id: UUID | None = await self._session.scalar(
            scoped_by_tenant(stmt, ReconciliationSession.client_id, user)
        )
        return client_id
