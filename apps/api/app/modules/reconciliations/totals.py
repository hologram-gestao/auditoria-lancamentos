"""**Fonte única** dos totalizadores de uma conciliação (Sprint 4, BACK 04.3).

Por que este módulo existe: o mesmo número (quantos lançamentos estão
conciliados) aparecia em três telas — lista, detalhe e abas de revisão — e cada
uma o obtinha do seu jeito. É o cenário exato do learning "valor derivado
calculado em 2 lugares diverge", e ele **já estava divergindo**: o
`recompute_file_entry_counters` da revisão contava como conciliado apenas
`situation='conciliado'`, deixando de fora `conciliado_data_divergente` (FASE 1)
— então bastava o analista tocar em UMA linha para o contador da lista cair
sozinho, sem nada ter mudado de fato.

A regra passa a viver aqui, num lugar só:

    - **conciliado** = `situation ∈ CONCILIATED_SITUATIONS` (exato OU com data
      divergente — ambos casaram com o Omie por valor);
    - **sem Omie** = `situation = 'sem_omie'`;
    - **Omie sem arquivo** = linhas de `reconciliation_omie_entries`;
    - **anomalias** = linhas de `reconciliation_anomalies` (resolvidas +
      pendentes, igual ao que a aba mostra).

`compute_session_counters` DERIVA das linhas (sempre fresco, é o que o detalhe
usa e o que tem de bater com as abas). `refresh_session_counters` deriva **e**
materializa nas colunas da sessão — que é o que a LISTA lê, para não pagar três
COUNTs por item paginado (guardrail do PRD: a lista não pode ficar mais lenta).
Como as colunas só são escritas por esta função, lista e detalhe não divergem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, update

from app.db.models import (
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: O que conta como "conciliado". `conciliado_data_divergente` ENTRA: o valor
#: casou com o Omie e a data divergiu ≤ 3 dias (CLAUDE.md §5.2) — a linha está
#: conciliada e sinalizada, não pendente. Deixá-la de fora é o bug que este
#: módulo corrige.
CONCILIATED_SITUATIONS: frozenset[str] = frozenset(
    {
        FileEntrySituation.CONCILIADO.value,
        FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value,
    }
)


@dataclass(frozen=True, slots=True)
class SessionCounters:
    """Totalizadores de uma conciliação. Todos derivados das linhas."""

    total_file_entries: int
    conciliated_count: int
    sem_omie_count: int
    omie_sem_arquivo_count: int
    anomaly_count: int


async def compute_session_counters(db: AsyncSession, session_id: UUID) -> SessionCounters:
    """Deriva os totalizadores das linhas persistidas da sessão.

    Três queries **sequenciais** na mesma conexão (nunca `asyncio.gather` sobre
    a mesma `AsyncSession` — `InterfaceError: another operation in progress`).
    """
    situation_rows = (
        await db.execute(
            select(
                ReconciliationFileEntry.situation,
                func.count(ReconciliationFileEntry.id),
            )
            .where(ReconciliationFileEntry.session_id == session_id)
            .group_by(ReconciliationFileEntry.situation)
        )
    ).all()

    total = 0
    conciliated = 0
    sem_omie = 0
    for situation_value, count_value in situation_rows:
        count_int = int(count_value)
        total += count_int
        if situation_value in CONCILIATED_SITUATIONS:
            conciliated += count_int
        elif situation_value == FileEntrySituation.SEM_OMIE.value:
            sem_omie += count_int
        # `ignorado` entra só no total — não é conciliado nem pendente.

    omie_sem_arquivo = int(
        (
            await db.execute(
                select(func.count(ReconciliationOmieEntry.id)).where(
                    ReconciliationOmieEntry.session_id == session_id
                )
            )
        ).scalar_one()
    )
    anomalies = int(
        (
            await db.execute(
                select(func.count(ReconciliationAnomaly.id)).where(
                    ReconciliationAnomaly.session_id == session_id
                )
            )
        ).scalar_one()
    )

    return SessionCounters(
        total_file_entries=total,
        conciliated_count=conciliated,
        sem_omie_count=sem_omie,
        omie_sem_arquivo_count=omie_sem_arquivo,
        anomaly_count=anomalies,
    )


async def refresh_session_counters(db: AsyncSession, session_id: UUID) -> SessionCounters:
    """Deriva e **materializa** os totalizadores nas colunas da sessão.

    Chamada por toda ação de revisão que muda o que é contado. Escreve as cinco
    colunas de uma vez — atualizar só um subconjunto foi como a divergência
    entrou antes (o `anomaly_count` seguia um caminho, os contadores de linha
    outro, e o `total_file_entries` nenhum).
    """
    counters = await compute_session_counters(db, session_id)
    await db.execute(
        update(ReconciliationSession)
        .where(ReconciliationSession.id == session_id)
        .values(
            total_file_entries=counters.total_file_entries,
            conciliated_count=counters.conciliated_count,
            sem_omie_count=counters.sem_omie_count,
            omie_sem_arquivo_count=counters.omie_sem_arquivo_count,
            anomaly_count=counters.anomaly_count,
        )
    )
    return counters
