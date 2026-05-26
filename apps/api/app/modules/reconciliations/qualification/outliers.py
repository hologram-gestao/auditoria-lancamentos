"""Camada 3 — outliers de valor (S19 BACK 12.1).

Para cada par atual, calcula `avg +/- 3*sigma` de `|amount|` agregado por
`(client_id, supplier)` nas últimas 6 sessões `reviewing|done`. Se
`|amount_atual| > avg + 3*sigma` E amostra >= 5 → flag `valor_outlier`.

Mesma limitação de cache da Camada 2: `supplier` vive em cache L2 (TTL).
Se a maior parte do histórico está fora do cache, o par não é flagado
(degradação silenciosa). Aceitável pra MVP.

SQL determinístico, sem IA, custo zero (uma query por sessão).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
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
    OutlierResult,
    QualificationPair,
)

log = get_logger(__name__)

# Janela histórica (PLANO §S19 Camada 3).
_HISTORICAL_SESSION_LIMIT = 6
# Amostra mínima para considerar a média estatisticamente útil. Abaixo
# disso, o desvio padrão amostral é volátil demais e gera falsos.
_MIN_SAMPLE_SIZE = 5
# Multiplicador para o limite superior. Distribuição empírica de valores
# em conciliações já vista é razoavelmente log-normal — 3*sigma é conservador.
_SIGMA_MULTIPLIER = Decimal("3")


async def find_value_outliers(
    db: AsyncSession,
    *,
    client_id: UUID,
    current_session_id: UUID,
    current_pairs: list[QualificationPair],
    cache: OmieLancamentoCache,
) -> list[OutlierResult]:
    """Detecta valores fora do padrão histórico por fornecedor.

    Args:
        db: sessão SQLAlchemy ATIVA. Somente leitura.
        client_id: cliente em análise.
        current_session_id: a sessão atual (excluída da janela).
        current_pairs: pares conciliados desta sessão.
        cache: cache L1+L2 — hidrata supplier dos lançamentos históricos.

    Returns:
        Lista de `OutlierResult`. Pares sem amostra suficiente ou sem
        supplier não aparecem (degradação silenciosa).
    """
    if not current_pairs:
        return []

    hist_session_ids = await _list_historical_session_ids(
        db,
        client_id=client_id,
        current_session_id=current_session_id,
        limit=_HISTORICAL_SESSION_LIMIT,
    )
    if not hist_session_ids:
        return []

    # Coleta `(omie_lancamento_id, amount)` dos pares conciliados das
    # sessões históricas. Amount em claro (CLAUDE.md §4.3 — valor isolado
    # não é dado sensível).
    hist_entries = await _list_historical_amounts(db, session_ids=hist_session_ids)
    if not hist_entries:
        return []

    hist_omie_ids = list({omie_id for omie_id, _ in hist_entries})
    cached = await cache.get_many(client_id=client_id, omie_ids=hist_omie_ids)

    # Agrega `|amount|` por supplier (normalizado).
    amounts_by_supplier: dict[str, list[Decimal]] = defaultdict(list)
    for omie_id, amount in hist_entries:
        data = cached.get(omie_id)
        if data is None or data.supplier is None:
            continue
        amounts_by_supplier[_normalize(data.supplier)].append(abs(amount))

    results: list[OutlierResult] = []
    for pair in current_pairs:
        if pair.supplier is None:
            continue
        samples = amounts_by_supplier.get(_normalize(pair.supplier))
        if samples is None or len(samples) < _MIN_SAMPLE_SIZE:
            continue
        avg, stddev = _mean_and_stddev_sample(samples)
        if stddev == 0:
            # Todos os valores históricos idênticos — só flaga se diferir.
            if abs(pair.amount) != avg:
                results.append(
                    OutlierResult(
                        pair_id=pair.pair_id,
                        motivo=(
                            f"Valor R$ {_fmt(pair.amount)} foge do padrão do "
                            f"fornecedor '{pair.supplier}': histórico constante "
                            f"em R$ {_fmt(avg)} (n={len(samples)})."
                        ),
                    )
                )
            continue
        threshold = avg + _SIGMA_MULTIPLIER * stddev
        if abs(pair.amount) > threshold:
            results.append(
                OutlierResult(
                    pair_id=pair.pair_id,
                    motivo=(
                        f"Valor R$ {_fmt(pair.amount)} foge do padrão do "
                        f"fornecedor '{pair.supplier}' (média R$ {_fmt(avg)}, "
                        f"desvio R$ {_fmt(stddev)}, n={len(samples)})."
                    ),
                )
            )

    log.info(
        "qualification_outlier_done",
        client_id=str(client_id),
        hist_sessions=len(hist_session_ids),
        hist_pairs=len(hist_entries),
        suppliers_with_samples=len(amounts_by_supplier),
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
    """Últimas N sessões `reviewing|done` do cliente."""
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


async def _list_historical_amounts(
    db: AsyncSession,
    *,
    session_ids: list[UUID],
) -> list[tuple[int, Decimal]]:
    """Pares (omie_lancamento_id, amount) dos file_entries conciliados."""
    if not session_ids:
        return []
    stmt = select(
        ReconciliationFileEntry.omie_lancamento_id,
        ReconciliationFileEntry.amount,
    ).where(
        ReconciliationFileEntry.session_id.in_(session_ids),
        ReconciliationFileEntry.situation == FileEntrySituation.CONCILIADO.value,
        ReconciliationFileEntry.omie_lancamento_id.is_not(None),
    )
    rows = (await db.execute(stmt)).all()
    return [(int(omie_id), Decimal(amount)) for omie_id, amount in rows if omie_id is not None]


def _mean_and_stddev_sample(values: list[Decimal]) -> tuple[Decimal, Decimal]:
    """Média e desvio padrão AMOSTRAL (N-1) em `Decimal`.

    Usamos amostral (Bessel) porque o histórico é uma amostra do universo
    de transações daquele fornecedor — não a população. Empata com
    `stddev_samp` do Postgres, escolha do PLANO §S19 Camada 3.
    """
    n = len(values)
    if n == 0:
        return Decimal("0"), Decimal("0")
    total = sum(values, Decimal("0"))
    mean = total / Decimal(n)
    if n < 2:
        return mean, Decimal("0")
    variance_numer = sum((v - mean) ** 2 for v in values)
    # `Decimal` não tem sqrt nativo — converte pra float só pra raiz e
    # devolve Decimal arredondado em 2 casas (precisão suficiente pro
    # threshold; valores em centavos).
    variance = variance_numer / Decimal(n - 1)
    stddev_float = float(variance) ** 0.5
    stddev = Decimal(stddev_float).quantize(Decimal("0.01"))
    return mean.quantize(Decimal("0.01")), stddev


def _normalize(value: str) -> str:
    return value.strip().lower()


def _fmt(value: Decimal) -> str:
    """Formata Decimal em pt-BR sem locale: `1.234,56`."""
    quantized = value.quantize(Decimal("0.01"))
    sign = "-" if quantized < 0 else ""
    integer_part, _, decimal_part = f"{abs(quantized):.2f}".partition(".")
    chunks: list[str] = []
    s = integer_part
    while len(s) > 3:
        chunks.append(s[-3:])
        s = s[:-3]
    chunks.append(s)
    formatted_int = ".".join(reversed(chunks))
    return f"{sign}{formatted_int},{decimal_part}"
