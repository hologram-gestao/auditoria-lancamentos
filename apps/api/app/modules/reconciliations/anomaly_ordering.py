"""Ordem canônica das anomalias — ÚNICA definição, usada pela tela e pelo export.

Sugestão 2 da Bruna (04/08/2026): a lista de anomalias precisa vir em ordem
cronológica pela data do lançamento, porque a conferência se faz na ordem em
que o dinheiro se moveu. Antes desta regra existiam TRÊS ordens diferentes
para a mesma sessão — a tela por severidade, o export carregando por
`created_at` e a planilha reordenando por severidade na montagem.

Por que este módulo existe em vez de uma `order_by` repetida nos dois lugares:
tela e relatório divergirem já rendeu um bug reportado pelo cliente (a coluna
de contexto que sai só na tela). Ordem é regra de domínio, não detalhe de cada
consulta — se mudar, muda nos dois de uma vez ou não muda.

A anomalia NÃO tem data própria: ela vem da linha relacionada, e há três casos.
Ligada a uma `file_entry` → a data do extrato. Ligada a um `omie_entry` → a data
do lançamento Omie. Ligada a nenhum dos dois (anomalias estruturais/agregadas,
que aparecem como "—" na coluna "Linha relacionada") → **sem data**, e vão para
o FIM da lista. Nunca para o começo: a primeira coisa que o analista vê não
pode ser justamente a que não tem contexto.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import asc, case, func

from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
)

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement
    from sqlalchemy.sql.selectable import Select

# Ordem custom de severidade (critical → moderate → info). CASE..WHEN é mais
# direto que adicionar uma coluna `severity_rank`. Vive aqui e não no
# repositório da revisão porque o export também precisa da MESMA expressão.
SEVERITY_ORDER_CASE = case(
    {
        AnomalySeverity.CRITICAL.value: 1,
        AnomalySeverity.MODERATE.value: 2,
        AnomalySeverity.INFO.value: 3,
    },
    value=AnomalyType.severity,
    else_=99,
)

# Data efetiva da anomalia: a da linha do arquivo, ou a do lançamento Omie, ou
# NULL quando não há vínculo. Depende dos LEFT JOINs que `join_anomaly_dates`
# acrescenta — usar uma sem a outra devolve NULL para tudo.
ANOMALY_EFFECTIVE_DATE = func.coalesce(
    ReconciliationFileEntry.transaction_date,
    ReconciliationOmieEntry.transaction_date,
)


def join_anomaly_dates(stmt: Select[Any]) -> Select[Any]:
    """Acrescenta os LEFT JOINs de onde sai `ANOMALY_EFFECTIVE_DATE`.

    LEFT, não INNER: anomalia sem linha relacionada é caso legítimo e não pode
    sumir da lista. Ambos os vínculos são many-to-one por FK, então o join não
    multiplica linhas.
    """
    return stmt.outerjoin(
        ReconciliationFileEntry,
        ReconciliationAnomaly.file_entry_id == ReconciliationFileEntry.id,
    ).outerjoin(
        ReconciliationOmieEntry,
        ReconciliationAnomaly.omie_entry_id == ReconciliationOmieEntry.id,
    )


def anomaly_order_by() -> tuple[ColumnElement[Any], ...]:
    """Cláusula `ORDER BY` canônica: data asc, sem-data no fim, desempate estável.

    O desempate importa mais do que parece: a lista é paginada, e duas anomalias
    da mesma data sem critério estável podem aparecer nas páginas 1 e 2 ao mesmo
    tempo, ou em nenhuma. Severidade primeiro (dentro do mesmo dia, o analista
    quer ver a crítica antes) e `id` por último, que é o que garante a
    estabilidade.
    """
    return (
        asc(ANOMALY_EFFECTIVE_DATE).nulls_last(),
        SEVERITY_ORDER_CASE,
        asc(ReconciliationAnomaly.id),
    )
