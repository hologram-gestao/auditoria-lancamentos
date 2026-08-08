"""Acesso ao DB da Tela de Revisão (S11).

Centraliza queries para:
    - file_entries: list + get-by-id (FROM same session).
    - omie_entries: list + get-by-id.
    - anomalies: list (com filters), insert, update, recompute counter.
    - sessões: helper para validar pertencimento (`session_id` ↔ `client_id`).

Cada método assume que o RBAC já foi validado pelo caller (rota) e atua
apenas sobre persistência. Não loga nem retorna dados descriptografados.

Decisões:
    - Filtro `search` roda em SQL contra `description_search_hmac` (blind
      index S16). Sessões pré-migration (`description_search_hmac IS NULL`)
      são naturalmente excluídas — `LIKE` contra NULL é NULL.
    - `_recompute_file_entry_counters` e `_recompute_anomaly_count` centralizam
      a lógica de COUNT pra evitar divergência entre 9.3 e 9.8/9.9.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import asc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
)
from app.modules.reconciliations.anomaly_ordering import (
    SEVERITY_ORDER_CASE,
    anomaly_order_by,
    join_anomaly_dates,
)
from app.modules.reconciliations.totals import refresh_session_counters

# `SEVERITY_ORDER_CASE` vem de `anomaly_ordering` (definição ÚNICA, de onde o
# export também importa) e segue disponível a partir deste módulo, que é de
# onde `list_anomaly_type_counts` e os testes já o usavam.


class ReviewRepository:
    """Operações sobre Tela de Revisão."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """A `AsyncSession` desta request.

        Exposta para o service compor OUTRO repository sobre a MESMA transação
        (o sink de `usage_events`, Sprint 6) — sem isso o evento cairia numa
        conexão distinta e deixaria de ser atômico com a marcação.
        """
        return self._session

    async def flush(self) -> None:
        """Helper público — service não acessa `_session` direto."""
        await self._session.flush()

    # ------------------------------------------------------------------
    # Sessão helpers
    # ------------------------------------------------------------------

    async def get_session(self, session_id: UUID) -> ReconciliationSession | None:
        stmt = select(ReconciliationSession).where(ReconciliationSession.id == session_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_client(self, client_id: UUID) -> Client | None:
        """Carrega o `Client` (precisa de `dek_wrapped` para o envelope cripto)."""
        stmt = select(Client).where(Client.id == client_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # File entries (9.1, 9.3)
    # ------------------------------------------------------------------

    async def list_file_entries_all(
        self,
        *,
        session_id: UUID,
        situation: str | None,
        type_filter: str | None,
        search_hmacs: list[str] | None = None,
    ) -> list[ReconciliationFileEntry]:
        """Carrega TODAS as linhas da sessão aplicando filtros SQL-safe.

        Filtros aplicados no SQL:
            - `situation` ∈ {conciliado, sem_omie, ignorado}.
            - `type_filter` ∈ {credit, debit} → amount > 0 ou amount < 0.
            - `search_hmacs`: blind index (S16). Cada HMAC vira um
              `LIKE '% <hmac> %'` ANDado. Linhas com `description_search_hmac
              IS NULL` (sessões pré-migration) saem da contagem porque LIKE
              contra NULL é NULL.

        Resultados ordenados por `transaction_date asc, id asc` para
        paginação estável. Service ainda pagina em Python — manter custo
        de decrypt limitado à página final (não há ganho em mover a paginação
        pro SQL agora que o filtro pesado roda lá).
        """
        stmt = select(ReconciliationFileEntry).where(
            ReconciliationFileEntry.session_id == session_id,
        )
        if situation in {
            FileEntrySituation.CONCILIADO.value,
            # FASE 1: linha que casou por valor com data divergente ≤3 dias.
            # Sem ela na lista de aceitos, o filtro era silenciosamente
            # ignorado e a aba devolvia TUDO — pior que devolver nada.
            FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value,
            FileEntrySituation.SEM_OMIE.value,
            FileEntrySituation.IGNORADO.value,
        }:
            stmt = stmt.where(ReconciliationFileEntry.situation == situation)
        if type_filter == "credit":
            stmt = stmt.where(ReconciliationFileEntry.amount > 0)
        elif type_filter == "debit":
            stmt = stmt.where(ReconciliationFileEntry.amount < 0)
        if search_hmacs:
            for hmac_token in search_hmacs:
                # `% <hmac> %` casa apenas tokens completos — leading/trailing
                # spaces foram gravados pelo `compute_search_hmac` justamente
                # para isso. Bindparam protege contra SQL injection (não
                # interpolamos diretamente).
                stmt = stmt.where(
                    ReconciliationFileEntry.description_search_hmac.like(f"% {hmac_token} %")
                )
        stmt = stmt.order_by(
            asc(ReconciliationFileEntry.transaction_date),
            asc(ReconciliationFileEntry.id),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(rows)

    async def get_file_entry(
        self,
        *,
        session_id: UUID,
        entry_id: UUID,
    ) -> ReconciliationFileEntry | None:
        """Garante que a linha pertence à sessão (FK + WHERE explícito)."""
        stmt = select(ReconciliationFileEntry).where(
            ReconciliationFileEntry.id == entry_id,
            ReconciliationFileEntry.session_id == session_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def file_entry_omie_id_taken_by_another(
        self,
        *,
        session_id: UUID,
        omie_lancamento_id: int,
        exclude_entry_id: UUID,
    ) -> bool:
        """Verifica se outro `file_entry` da sessão já reservou esse Omie ID.

        Usado na operação "Trocar Omie": antes de atribuir um novo
        `omie_lancamento_id`, garantir unicidade dentro da sessão. Idempotente
        — rodar 2x com mesmo ID na MESMA linha (`exclude_entry_id`) retorna
        False.
        """
        stmt = (
            select(ReconciliationFileEntry.id)
            .where(
                ReconciliationFileEntry.session_id == session_id,
                ReconciliationFileEntry.omie_lancamento_id == omie_lancamento_id,
                ReconciliationFileEntry.id != exclude_entry_id,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def list_session_omie_ids_in_use(
        self,
        *,
        session_id: UUID,
    ) -> set[int]:
        """IDs Omie já vinculados em alguma linha da sessão.

        Usado em BACK 9.4 (subtrai do conjunto de disponíveis).
        """
        stmt = select(ReconciliationFileEntry.omie_lancamento_id).where(
            ReconciliationFileEntry.session_id == session_id,
            ReconciliationFileEntry.omie_lancamento_id.is_not(None),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {oid for oid in rows if oid is not None}

    async def recompute_file_entry_counters(self, session_id: UUID) -> tuple[int, int]:
        """Re-materializa os totalizadores da sessão. Delega à **fonte única**.

        Sprint 4 (BACK 04.3): a regra de contagem saiu daqui e foi para
        `app.modules.reconciliations.totals`, compartilhada com o detalhe e com
        a lista. Duas correções vieram junto:

        - `conciliado_data_divergente` agora CONTA como conciliado (antes só
          `conciliado` contava — bastava o analista tocar numa linha para o
          contador da lista cair sozinho, sem nada ter mudado de fato);
        - as cinco colunas são atualizadas juntas, não só duas — atualizar um
          subconjunto foi por onde a divergência entrou.

        Returns:
            Tupla (conciliated_count, sem_omie_count) após o UPDATE — assinatura
            preservada para os callers existentes.
        """
        counters = await refresh_session_counters(self._session, session_id)
        return counters.conciliated_count, counters.sem_omie_count

    # ------------------------------------------------------------------
    # Omie entries (9.5, 9.6)
    # ------------------------------------------------------------------

    async def list_omie_entries_paginated(
        self,
        *,
        session_id: UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[ReconciliationOmieEntry], int]:
        """Lista `omie_entries` da sessão com paginação SQL.

        Como não há filtros texto-baseados aqui, paginação é SQL-pura (LIMIT/
        OFFSET sobre `transaction_date asc, id asc`). Retorna `(rows, total)`.
        """
        total = int(
            (
                await self._session.execute(
                    select(func.count(ReconciliationOmieEntry.id)).where(
                        ReconciliationOmieEntry.session_id == session_id,
                    )
                )
            ).scalar_one()
        )
        rows = (
            (
                await self._session.execute(
                    select(ReconciliationOmieEntry)
                    .where(ReconciliationOmieEntry.session_id == session_id)
                    .order_by(
                        asc(ReconciliationOmieEntry.transaction_date),
                        asc(ReconciliationOmieEntry.id),
                    )
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total

    async def get_omie_entry(
        self,
        *,
        session_id: UUID,
        entry_id: UUID,
    ) -> ReconciliationOmieEntry | None:
        stmt = select(ReconciliationOmieEntry).where(
            ReconciliationOmieEntry.id == entry_id,
            ReconciliationOmieEntry.session_id == session_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Anomalies (9.7, 9.8, 9.9)
    # ------------------------------------------------------------------

    async def list_anomalies_paginated(
        self,
        *,
        session_id: UUID,
        resolved_filter: bool | None,
        severity_filter: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[ReconciliationAnomaly, AnomalyType]], int]:
        """Lista anomalias com JOIN no AnomalyType + filtros + paginação.

        Ordenação cronológica pela data do lançamento relacionado — a MESMA do
        export, definida uma vez em `anomaly_ordering`. Ordenar no SQL e não no
        cliente é obrigatório aqui: a lista é paginada, e ordenar só a página
        já carregada faria a tela contar uma história e a paginação outra.
        """
        base_stmt = join_anomaly_dates(
            select(ReconciliationAnomaly, AnomalyType)
            .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
            .where(ReconciliationAnomaly.session_id == session_id)
        )
        if resolved_filter is not None:
            base_stmt = base_stmt.where(ReconciliationAnomaly.resolved == resolved_filter)
        if severity_filter in {
            AnomalySeverity.CRITICAL.value,
            AnomalySeverity.MODERATE.value,
            AnomalySeverity.INFO.value,
        }:
            base_stmt = base_stmt.where(AnomalyType.severity == severity_filter)

        count_stmt = (
            select(func.count(ReconciliationAnomaly.id))
            .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
            .where(ReconciliationAnomaly.session_id == session_id)
        )
        if resolved_filter is not None:
            count_stmt = count_stmt.where(ReconciliationAnomaly.resolved == resolved_filter)
        if severity_filter in {
            AnomalySeverity.CRITICAL.value,
            AnomalySeverity.MODERATE.value,
            AnomalySeverity.INFO.value,
        }:
            count_stmt = count_stmt.where(AnomalyType.severity == severity_filter)
        total = int((await self._session.execute(count_stmt)).scalar_one())

        rows = (
            await self._session.execute(
                base_stmt.order_by(*anomaly_order_by())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [(anomaly, atype) for anomaly, atype in rows], total

    async def get_anomaly(
        self,
        *,
        session_id: UUID,
        anomaly_id: UUID,
    ) -> tuple[ReconciliationAnomaly, AnomalyType] | None:
        stmt = (
            select(ReconciliationAnomaly, AnomalyType)
            .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
            .where(
                ReconciliationAnomaly.id == anomaly_id,
                ReconciliationAnomaly.session_id == session_id,
            )
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], row[1]

    async def get_active_anomaly_type(
        self,
        anomaly_type_id: UUID,
    ) -> AnomalyType | None:
        """Retorna o tipo se existir E estiver `active=true`. Senão None."""
        stmt = select(AnomalyType).where(
            AnomalyType.id == anomaly_type_id,
            AnomalyType.active.is_(True),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_anomaly(self, anomaly: ReconciliationAnomaly) -> None:
        self._session.add(anomaly)
        await self._session.flush()

    async def recompute_anomaly_count(self, session_id: UUID) -> int:
        """Recalcula `anomaly_count` na sessão (TOTAL — resolvidas + pendentes).

        Também delega à fonte única (BACK 04.3): criar/resolver uma anomalia
        re-materializa TODOS os totalizadores, não só o de anomalias.
        """
        counters = await refresh_session_counters(self._session, session_id)
        return counters.anomaly_count

    # Helpers compartilhados com o serviço de anomaly types (BACK 9.10).

    async def list_active_anomaly_types(self) -> list[AnomalyType]:
        """Lista tipos `active=true` ordenados por severity custom + name."""
        stmt = (
            select(AnomalyType)
            .where(AnomalyType.active.is_(True))
            .order_by(SEVERITY_ORDER_CASE, asc(AnomalyType.name))
        )
        return list((await self._session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Helpers para validação 9.8 (related entry ownership)
    # ------------------------------------------------------------------

    async def file_entry_belongs_to_session(
        self,
        *,
        session_id: UUID,
        entry_id: UUID,
    ) -> bool:
        stmt = (
            select(ReconciliationFileEntry.id)
            .where(
                ReconciliationFileEntry.id == entry_id,
                ReconciliationFileEntry.session_id == session_id,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def omie_entry_belongs_to_session(
        self,
        *,
        session_id: UUID,
        entry_id: UUID,
    ) -> bool:
        stmt = (
            select(ReconciliationOmieEntry.id)
            .where(
                ReconciliationOmieEntry.id == entry_id,
                ReconciliationOmieEntry.session_id == session_id,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Helpers para enrich nas anomalies (related file_entry / omie_entry)
    # ------------------------------------------------------------------

    async def get_file_entries_by_ids(
        self,
        ids: list[UUID],
    ) -> dict[UUID, ReconciliationFileEntry]:
        if not ids:
            return {}
        stmt = select(ReconciliationFileEntry).where(ReconciliationFileEntry.id.in_(ids))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.id: row for row in rows}

    async def get_omie_entries_by_ids(
        self,
        ids: list[UUID],
    ) -> dict[UUID, ReconciliationOmieEntry]:
        if not ids:
            return {}
        stmt = select(ReconciliationOmieEntry).where(ReconciliationOmieEntry.id.in_(ids))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.id: row for row in rows}

    # Helpers utilitários

    @staticmethod
    def expand_period(
        period_start: date,
        period_end: date,
        tolerance_days: int,
    ) -> tuple[date, date]:
        """Aplica tolerância ao período (CLAUDE.md §5.3) — reuso do worker."""
        from datetime import timedelta

        return (
            period_start - timedelta(days=tolerance_days),
            period_end + timedelta(days=tolerance_days),
        )
