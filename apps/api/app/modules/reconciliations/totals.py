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
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select, update

from app.core.crypto_service import AAD_FILE_ENTRY_DESCRIPTION, field_locator
from app.core.logging import get_logger
from app.db.models import (
    AnomalyType,
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.crypto import ClientCipher

log = get_logger(__name__)

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
    #: Recorte de `conciliated_count`: só as `conciliado_data_divergente`
    #: (86e2u513b). Existe para o card "Conciliados" EXPLICAR a soma — o
    #: filtro "Conciliadas (data exata)" mostra menos que o card, e a
    #: diferença é exatamente este número. Derivado, nunca materializado.
    conciliated_divergent_count: int
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
    conciliated_divergent = 0
    sem_omie = 0
    for situation_value, count_value in situation_rows:
        count_int = int(count_value)
        total += count_int
        if situation_value in CONCILIATED_SITUATIONS:
            conciliated += count_int
            if situation_value == FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value:
                conciliated_divergent += count_int
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
        conciliated_divergent_count=conciliated_divergent,
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


# ----------------------------------------------------------------------
# Somas e breakdown da aba Resumo (86e2u513f)
# ----------------------------------------------------------------------
#
# Antes, o front somava crédito/débito/encargos e o breakdown de anomalias no
# NAVEGADOR, sobre as 50 primeiras linhas — acima disso o número exibido era
# menor que o real, e a maioria dos extratos passa de 50. Pior: a soma era em
# `Number()` (ponto flutuante), que a §3.4 proíbe para dinheiro. A conta agora
# mora aqui, ao lado dos demais totalizadores, pela mesma razão que este módulo
# existe: valor derivado calculado em dois lugares diverge.


@dataclass(frozen=True, slots=True)
class SessionAmountTotals:
    """Somas de valores da sessão inteira (todas as situações, como a tela)."""

    #: Soma das entradas positivas (créditos; no cartão, estornos).
    credits_total: Decimal
    #: Soma das negativas em valor ABSOLUTO (débitos; no cartão, compras).
    debits_total: Decimal


@dataclass(frozen=True, slots=True)
class SessionAnomalyBreakdown:
    """Contagem por severidade + resolvidas, da sessão INTEIRA."""

    critical: int
    moderate: int
    info: int
    resolved: int


#: Encargos de fatura de cartão, identificados pela DESCRIÇÃO (FRONT 1.8).
#: A regra morava no front (`isChargeDescription`) e veio junto com a soma:
#: match case-insensitive por substring.
CARD_CHARGE_KEYWORDS: tuple[str, ...] = ("iof", "juros", "multa")


def is_charge_description(description: str) -> bool:
    """True quando a descrição indica encargo (IOF/juros/multa)."""
    lowered = description.lower()
    return any(keyword in lowered for keyword in CARD_CHARGE_KEYWORDS)


async def compute_session_amounts(db: AsyncSession, session_id: UUID) -> SessionAmountTotals:
    """Soma créditos e débitos de TODAS as linhas, em Decimal, no banco.

    Uma query só (FILTER do Postgres). Inclui todas as situações — inclusive
    `ignorado` — porque o total do extrato é o total do extrato: é a mesma
    janela que o front somava, só que inteira e sem float.
    """
    amount = ReconciliationFileEntry.amount
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(amount).filter(amount > 0), 0),
                func.coalesce(func.sum(-amount).filter(amount < 0), 0),
            ).where(ReconciliationFileEntry.session_id == session_id)
        )
    ).one()
    return SessionAmountTotals(
        credits_total=Decimal(row[0]),
        debits_total=Decimal(row[1]),
    )


async def compute_anomaly_breakdown(db: AsyncSession, session_id: UUID) -> SessionAnomalyBreakdown:
    """Breakdown por severidade + resolvidas, da sessão inteira, em uma query."""
    rows = (
        await db.execute(
            select(
                AnomalyType.severity,
                ReconciliationAnomaly.resolved,
                func.count(ReconciliationAnomaly.id),
            )
            .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
            .where(ReconciliationAnomaly.session_id == session_id)
            .group_by(AnomalyType.severity, ReconciliationAnomaly.resolved)
        )
    ).all()
    critical = moderate = info = resolved = 0
    for severity, is_resolved, count_value in rows:
        count_int = int(count_value)
        if severity == "critical":
            critical += count_int
        elif severity == "moderate":
            moderate += count_int
        elif severity == "info":
            info += count_int
        if is_resolved:
            resolved += count_int
    return SessionAnomalyBreakdown(
        critical=critical, moderate=moderate, info=info, resolved=resolved
    )


async def compute_card_charges_total(
    db: AsyncSession, session_id: UUID, cipher: ClientCipher
) -> Decimal:
    """Soma os DÉBITOS cuja descrição indica encargo (cartão).

    A descrição é CIFRADA no banco (§4.1) — SQL não filtra por conteúdo, então
    esta função descriptografa os débitos da sessão em memória e aplica
    `is_charge_description`. Custo aceitável porque só roda para sessão de
    CARTÃO (fatura mensal, volume bounded) e só no detalhe.

    Linha indecifrável é PULADA e contada — mesma política da tela de revisão
    (`[indecifrável]` + métrica, §4.1): a soma parcial nunca é silenciosa, o
    warning `summary_decrypt_failed` carrega a contagem.
    """
    rows = (
        await db.execute(
            select(
                ReconciliationFileEntry.id,
                ReconciliationFileEntry.amount,
                ReconciliationFileEntry.description_encrypted,
                ReconciliationFileEntry.description_iv,
            ).where(
                ReconciliationFileEntry.session_id == session_id,
                ReconciliationFileEntry.amount < 0,
            )
        )
    ).all()
    total = Decimal("0")
    failures = 0
    for entry_id, amount, ct, iv in rows:
        if not ct or not iv:
            continue
        try:
            description = cipher.decrypt(
                ct, iv, field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry_id)
            )
        except Exception:
            failures += 1
            continue
        if is_charge_description(description):
            total += abs(amount)
    if failures:
        log.warning("summary_decrypt_failed", field="description", count=failures)
    return total
