"""Consultas do resumo do cliente (86e3k1q3j): só CONTAGENS, IDs e códigos.

**Toda query carrega `client_id` no próprio `WHERE`** e passa por
`scoped_by_tenant` (§3.15): o cliente já foi validado pela rota
(`AccessibleClientDep`), e o filtro na query é a defesa em profundidade, para que
um consumidor futuro que esqueça o guard não vaze o tenant alheio.

**Nada de carregar linha para contar.** Cada bloco é um `COUNT`/`SUM` agrupado no
banco; o que vem para a memória é uma linha por status, por tipo de anomalia ou por
categoria, nunca uma por lançamento. Nenhuma coluna cifrada é lida: o resumo não
toca a DEK, e por isso também responde para cliente encerrado (§4.12).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.core.authz import scoped_by_tenant
from app.db.models import (
    AnomalyType,
    ClientMovement,
    FileEntrySituation,
    MovementStatus,
    OmieAccountCache,
    OmiePostingStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationSession,
    SessionAccountType,
)

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser


@dataclass(frozen=True, slots=True)
class AnomalyCount:
    """Anomalias de um tipo, pelo CÓDIGO do tipo (o nome é do catálogo, no front)."""

    code: str
    open: int
    resolved: int


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    """Σ|valor| dos movimentos PRESENTES de uma categoria numa competência.

    Satisfaz o `MovementLike` de `client_mapping.apply` para que a cobertura saia
    da MESMA `apply_mapping` da prévia do de-para: a cobertura é Σ|valor| por
    situação, e a situação depende só de (tipo de origem, categoria), então
    somar no banco por categoria dá exatamente os mesmos totais que somar
    movimento a movimento em Python, sem trazer cada movimento para a memória.
    """

    source_type: str
    category_code: str | None
    amount: Decimal
    movement_date: date

    @property
    def source_movement_id(self) -> str:
        return f"{self.source_type}:{self.category_code or ''}"

    @property
    def source_account_id(self) -> str | None:
        return None

    @property
    def status(self) -> str:
        return MovementStatus.PRESENTE.value


def _active_sessions(client_id: UUID, user: CurrentUser) -> Select[Any]:
    """As sessões ATIVAS (sem soft-delete) do cliente, com o tenant no `WHERE`."""
    stmt = select(ReconciliationSession.id).where(
        ReconciliationSession.client_id == client_id,
        ReconciliationSession.deleted_at.is_(None),
    )
    return scoped_by_tenant(stmt, ReconciliationSession.client_id, user)


class ClientSummaryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def session_status_counts(
        self, client_id: UUID, month: date, user: CurrentUser
    ) -> dict[str, int]:
        """Sessões ativas do mês, por status."""
        stmt = (
            select(ReconciliationSession.status, func.count(ReconciliationSession.id))
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.deleted_at.is_(None),
                ReconciliationSession.reference_month == month,
            )
            .group_by(ReconciliationSession.status)
        )
        stmt = scoped_by_tenant(stmt, ReconciliationSession.client_id, user)
        rows = (await self._session.execute(stmt)).all()
        return {str(status): int(count) for status, count in rows}

    async def accounts_with_session(self, client_id: UUID, month: date, user: CurrentUser) -> int:
        """Contas distintas (`omie_conta_id`) com sessão ativa no mês."""
        stmt = select(func.count(func.distinct(ReconciliationSession.omie_conta_id))).where(
            ReconciliationSession.client_id == client_id,
            ReconciliationSession.deleted_at.is_(None),
            ReconciliationSession.reference_month == month,
        )
        stmt = scoped_by_tenant(stmt, ReconciliationSession.client_id, user)
        return int((await self._session.execute(stmt)).scalar_one())

    async def accounts_total(self, client_id: UUID, user: CurrentUser) -> int:
        """Contas do cliente no cache de contas (o que a gaveta de conciliação oferece)."""
        stmt = select(func.count(OmieAccountCache.id)).where(
            OmieAccountCache.client_id == client_id
        )
        stmt = scoped_by_tenant(stmt, OmieAccountCache.client_id, user)
        return int((await self._session.execute(stmt)).scalar_one())

    async def anomaly_counts(
        self, client_id: UUID, month: date, user: CurrentUser
    ) -> list[AnomalyCount]:
        """Anomalias das sessões ativas do mês, abertas e resolvidas, por código do tipo."""
        sessions = _active_sessions(client_id, user).where(
            ReconciliationSession.reference_month == month
        )
        open_count = func.count(ReconciliationAnomaly.id).filter(
            ReconciliationAnomaly.resolved.is_(False)
        )
        resolved_count = func.count(ReconciliationAnomaly.id).filter(
            ReconciliationAnomaly.resolved.is_(True)
        )
        stmt = (
            select(AnomalyType.code, open_count, resolved_count)
            .join(AnomalyType, AnomalyType.id == ReconciliationAnomaly.anomaly_type_id)
            .where(ReconciliationAnomaly.session_id.in_(sessions))
            .group_by(AnomalyType.code)
            .order_by(AnomalyType.code)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            AnomalyCount(code=str(code), open=int(opened), resolved=int(resolved))
            for code, opened, resolved in rows
        ]

    async def card_purchases_to_post(
        self, client_id: UUID, month: date, user: CurrentUser
    ) -> tuple[int, Decimal]:
        """Compras da fatura de cartão do mês que ainda podem ir para o Omie.

        A regra é a de `omie_posting.service._eligibility_block`, em SQL: linha
        `sem_omie` (ignorada não é), sem lançamento vinculado, valor NEGATIVO (só
        compra; estorno segue bloqueado, §3.16), em sessão de cartão (`credit_card`,
        o `CR` do Omie), e sem intenção já `confirmed` para a linha. Aquela função
        decide linha a linha sobre a entidade carregada; aqui a mesma pergunta vira
        um `COUNT` + `SUM`, sem trazer a descrição (cifrada) de nenhuma linha.
        """
        card_sessions = _active_sessions(client_id, user).where(
            ReconciliationSession.reference_month == month,
            ReconciliationSession.account_type == SessionAccountType.CREDIT_CARD.value,
        )
        confirmed = select(ReconciliationOmiePosting.file_entry_id).where(
            ReconciliationOmiePosting.client_id == client_id,
            ReconciliationOmiePosting.status == OmiePostingStatus.CONFIRMED.value,
        )
        stmt = select(
            func.count(ReconciliationFileEntry.id),
            func.coalesce(func.sum(ReconciliationFileEntry.amount), Decimal("0.00")),
        ).where(
            ReconciliationFileEntry.session_id.in_(card_sessions),
            ReconciliationFileEntry.situation == FileEntrySituation.SEM_OMIE.value,
            ReconciliationFileEntry.omie_lancamento_id.is_(None),
            ReconciliationFileEntry.amount < 0,
            ReconciliationFileEntry.id.not_in(confirmed),
        )
        count, total = (await self._session.execute(stmt)).one()
        return int(count), Decimal(total).quantize(Decimal("0.01"))

    async def latest_session(
        self, client_id: UUID, user: CurrentUser
    ) -> ReconciliationSession | None:
        """A sessão ativa mais recente do cliente, de qualquer mês."""
        stmt = (
            select(ReconciliationSession)
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.deleted_at.is_(None),
            )
            .order_by(ReconciliationSession.created_at.desc(), ReconciliationSession.id.desc())
            .limit(1)
        )
        stmt = scoped_by_tenant(stmt, ReconciliationSession.client_id, user)
        return (await self._session.execute(stmt)).scalars().first()

    async def movement_totals_by_category(
        self, client_id: UUID, competence: date, user: CurrentUser
    ) -> list[CategoryTotal]:
        """Σ|valor| dos movimentos PRESENTES da competência, por (origem, categoria)."""
        stmt = (
            select(
                ClientMovement.source_type,
                ClientMovement.category_code,
                func.sum(func.abs(ClientMovement.amount)),
            )
            .where(
                ClientMovement.client_id == client_id,
                ClientMovement.competence == competence,
                ClientMovement.status == MovementStatus.PRESENTE.value,
            )
            .group_by(ClientMovement.source_type, ClientMovement.category_code)
        )
        stmt = scoped_by_tenant(stmt, ClientMovement.client_id, user)
        rows = (await self._session.execute(stmt)).all()
        return [
            CategoryTotal(
                source_type=str(source_type),
                category_code=category_code,
                amount=Decimal(total),
                movement_date=competence,
            )
            for source_type, category_code, total in rows
        ]
