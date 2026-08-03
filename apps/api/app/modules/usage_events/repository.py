"""Acesso ao DB da instrumentação de outcome (Sprint 4, BACK 04.1).

SQL puro sobre `usage_events` + a resolução do `client_id` da sessão (usada pelo
RBAC do endpoint). Não conhece regra de negócio nem política de erro — quem
decide o fail-soft é o service.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser, scoped_by_tenant
from app.db.models import ReconciliationSession, UsageEvent
from app.db.models.usage_event import deduped_session_index_predicate


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
        (`WHERE session_id IS NOT NULL AND event IN (…)`) e o Postgres não
        infere índice parcial a partir das colunas sozinhas — sem repetir o
        predicado EXATO, o INSERT morre com `42P10: there is no unique or
        exclusion constraint matching the ON CONFLICT specification` e o
        fail-soft engole o erro, gravando NADA. Por isso a expressão vem de
        `deduped_session_index_predicate()`, a mesma função que monta o índice
        no modelo.

        Evento FORA da allow-list de dedup (`qualificacao_emitida`,
        `flag_revisado`) simplesmente nunca conflita — a linha não casa com o
        predicado do índice árbitro — e sempre grava. É o que preserva a
        contagem da métrica da Sprint 6 (ADR-010).

        **SAVEPOINT obrigatório:** este INSERT roda dentro da transação de quem
        chamou (a request que cria a conciliação, a transação do job). Sem o
        `begin_nested`, qualquer erro aqui marcaria a session SQLAlchemy como
        abortada e derrubaria o fluxo de negócio junto — exatamente o oposto do
        fail-soft exigido. Com o savepoint, o rollback é só desta linha.
        """
        inserted = await self.insert_many_ignore_duplicate(
            event=event,
            rows=[(session_id, props)],
        )
        return inserted == 1

    async def insert_many_ignore_duplicate(
        self,
        *,
        event: str,
        rows: Sequence[tuple[UUID | None, dict[str, Any]]],
    ) -> int:
        """Insere N linhas do MESMO evento num único statement; devolve quantas gravaram.

        Existe para os eventos multi-ocorrência da Sprint 6: uma qualificação
        de 50 pares emite 50 `qualificacao_emitida`, e 50 round-trips (cada um
        com o seu SAVEPOINT) pagariam latência dentro da transação do job — o
        guardrail do PRD é justamente "a qualificação não pode ficar mais
        lenta". Um `INSERT ... VALUES (…), (…)` resolve em uma ida.

        Mesma política do singular: `ON CONFLICT DO NOTHING` com o predicado
        exato do índice parcial, tudo dentro de UM savepoint.
        """
        if not rows:
            return 0
        stmt = (
            pg_insert(UsageEvent)
            .values([{"event": event, "session_id": sid, "props": p} for sid, p in rows])
            .on_conflict_do_nothing(
                index_elements=["event", "session_id"],
                index_where=text(deduped_session_index_predicate()),
            )
            .returning(UsageEvent.id)
        )
        async with self._session.begin_nested():
            result = await self._session.execute(stmt)
            return len(result.scalars().all())

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
