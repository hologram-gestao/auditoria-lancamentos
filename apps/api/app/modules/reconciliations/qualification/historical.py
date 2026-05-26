"""Camada 2 — padrão histórico (S19 BACK 12.1).

Para cada par atual, olha as **últimas 3 sessões em `reviewing|done`** do
mesmo cliente (excluindo a atual + soft-deleted) e descobre a moda de
`(supplier, category)` para o mesmo `supplier` do par atual. Se categoria
atual ≠ moda histórica AND moda tem ≥ 2 ocorrências → flag.

Limitação inerente (CLAUDE.md §4.5):
    `supplier` e `category` só vivem em cache L2 (TTL externo). Em sessões
    pré-S19, ou quando o worker e o web não compartilham Redis, o cache
    pode estar vazio para o histórico — degradamos silenciosamente (sem
    flag). Isso é o comportamento documentado no PLANO §S19.

Spec do prompt:
    > Se >50% dos histórico foi miss, abortar a análise pra esse par e
    > retornar `null` (não flagar).
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    FileEntrySituation,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
)
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.reconciliations.qualification.schemas import (
    HistoricalResult,
    QualificationPair,
)

log = get_logger(__name__)

# Janela histórica. Decisão fixada na spec (PLANO §S19 Camada 2).
_HISTORICAL_SESSION_LIMIT = 3
# Moda precisa de pelo menos 2 ocorrências. Evita flagar com base em
# uma única conciliação histórica — ruído alto.
_MIN_OCCURRENCES_FOR_MODE = 2
# Se mais da metade do histórico não tem supplier/category disponível
# (cache miss), abortamos esse par silenciosamente.
_MAX_CACHE_MISS_RATIO = 0.5


async def find_pattern_breaks(
    db: AsyncSession,
    *,
    client_id: UUID,
    current_session_id: UUID,
    current_pairs: list[QualificationPair],
    cache: OmieLancamentoCache,
) -> list[HistoricalResult]:
    """Detecta quebras de padrão histórico para cada par atual.

    Args:
        db: sessão SQLAlchemy ATIVA. NÃO commitamos aqui — só leitura.
        client_id: cliente em análise.
        current_session_id: a própria sessão sendo qualificada (excluída
            da janela histórica).
        current_pairs: pares conciliados com `supplier`/`category` já
            populados (do cache ou da chamada atual ao Omie).
        cache: cache L1+L2 de lançamentos Omie. Usado pra hidratar
            supplier/category dos lançamentos das sessões históricas.

    Returns:
        Lista de `HistoricalResult` — só pares onde houve flag. Pares com
        dado insuficiente NÃO aparecem na lista (degradação silenciosa).
    """
    if not current_pairs:
        return []

    # 1. Pega as últimas 3 sessões válidas, excluindo a atual.
    hist_session_ids = await _list_historical_session_ids(
        db,
        client_id=client_id,
        current_session_id=current_session_id,
        limit=_HISTORICAL_SESSION_LIMIT,
    )
    if not hist_session_ids:
        log.info("qualification_historical_no_history", client_id=str(client_id))
        return []

    # 2. Coleta os pares conciliados dessas sessões. Devolve
    #    list[(omie_lancamento_id, session_id)].
    hist_pairs = await _list_historical_conciliated_pairs(db, session_ids=hist_session_ids)
    if not hist_pairs:
        return []

    # 3. Hidrata supplier/category de cada `omie_lancamento_id` histórico
    #    via cache. Em prod, com Redis compartilhado, hits são esperados;
    #    em dev/test isolado, pode ser quase todo miss.
    hist_omie_ids = list({omie_id for omie_id, _ in hist_pairs})
    cached_hist = await cache.get_many(client_id=client_id, omie_ids=hist_omie_ids)
    miss_ratio = 1 - (len(cached_hist) / len(hist_omie_ids)) if hist_omie_ids else 1.0
    if miss_ratio > _MAX_CACHE_MISS_RATIO:
        log.info(
            "qualification_historical_cache_miss_abort",
            client_id=str(client_id),
            hist_ids=len(hist_omie_ids),
            cached=len(cached_hist),
            miss_ratio=round(miss_ratio, 2),
        )
        return []

    # 4. Indexa por supplier: dict[supplier_normalizado, list[(category, count_in_session)]].
    #    Aqui contamos sessões distintas que classificaram o mesmo
    #    supplier com a mesma categoria — moda no nível de "vezes que
    #    esse fornecedor apareceu com essa categoria".
    by_supplier: dict[str, list[str]] = {}
    for omie_id, _session_id in hist_pairs:
        data = cached_hist.get(omie_id)
        if data is None or data.supplier is None or data.category is None:
            continue
        key = _normalize(data.supplier)
        by_supplier.setdefault(key, []).append(data.category)

    # 5. Pra cada par atual com supplier, compara com a moda histórica.
    results: list[HistoricalResult] = []
    for pair in current_pairs:
        if pair.supplier is None or pair.category is None:
            continue
        key = _normalize(pair.supplier)
        hist_categories = by_supplier.get(key)
        if not hist_categories:
            continue
        counter = Counter(hist_categories)
        most_common_category, occurrences = counter.most_common(1)[0]
        if occurrences < _MIN_OCCURRENCES_FOR_MODE:
            continue
        if _normalize(most_common_category) == _normalize(pair.category):
            continue
        results.append(
            HistoricalResult(
                pair_id=pair.pair_id,
                motivo=(
                    f"Fornecedor '{pair.supplier}' foi classificado como "
                    f"'{most_common_category}' em {occurrences} das últimas "
                    f"{_HISTORICAL_SESSION_LIMIT} conciliações; agora veio "
                    f"como '{pair.category}'."
                ),
            )
        )

    log.info(
        "qualification_historical_done",
        client_id=str(client_id),
        hist_sessions=len(hist_session_ids),
        hist_omie_ids=len(hist_omie_ids),
        cached_hits=len(cached_hist),
        flagged=len(results),
    )
    return results


async def _list_historical_session_ids(
    db: AsyncSession,
    *,
    client_id: UUID,
    current_session_id: UUID,
    limit: int,
) -> list[UUID]:
    """Últimas N sessões em `reviewing|done` do cliente, exclui a atual."""
    stmt = (
        select(ReconciliationSession.id)
        .where(
            ReconciliationSession.client_id == client_id,
            ReconciliationSession.id != current_session_id,
            ReconciliationSession.deleted_at.is_(None),
            ReconciliationSession.status.in_(
                (
                    ReconciliationStatus.REVIEWING.value,
                    ReconciliationStatus.DONE.value,
                )
            ),
            ReconciliationSession.processed_at.is_not(None),
        )
        .order_by(ReconciliationSession.processed_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows)


async def _list_historical_conciliated_pairs(
    db: AsyncSession,
    *,
    session_ids: list[UUID],
) -> list[tuple[int, UUID]]:
    """Pares (omie_lancamento_id, session_id) com `situation='conciliado'`."""
    if not session_ids:
        return []
    stmt = select(
        ReconciliationFileEntry.omie_lancamento_id,
        ReconciliationFileEntry.session_id,
    ).where(
        ReconciliationFileEntry.session_id.in_(session_ids),
        ReconciliationFileEntry.situation == FileEntrySituation.CONCILIADO.value,
        ReconciliationFileEntry.omie_lancamento_id.is_not(None),
    )
    rows = (await db.execute(stmt)).all()
    # mypy: as colunas vêm tipadas opcionais por causa do filtro IS NOT
    # NULL; faz o cast explícito.
    return [(int(omie_id), session_id) for omie_id, session_id in rows if omie_id is not None]


def _normalize(value: str) -> str:
    """Lowercase + strip pra comparar `supplier`/`category` com tolerância
    a espaços e capitalização (preserva acentos — não vale a pena tirar)."""
    return value.strip().lower()
