"""Orquestrador da qualificação (S19 BACK 12.1).

Responsabilidades:
    1. Hidrata supplier/category dos pares conciliados via cache L2.
    2. Decriptografa as descrições dos `file_entries` correspondentes.
    3. Roda Camada 1 (IA), Camada 2 (histórico), Camada 3 (outlier).
    4. Persiste anomalias no mesmo `AsyncSession` recebido — caller
       commit a única transação.
    5. Retorna `QualificationReport` consumido pelo log estruturado.

Falhas:
    - Cada camada é independente: falha de uma NÃO atinge as outras
      (try/except por camada).
    - Caller (`job.py`) envolve a chamada inteira em try/except —
      qualquer erro daqui converte em "qualification_failed" e a sessão
      segue para `status='reviewing'` normalmente.

Identificadores `AnomalyType`:
    Codes do seed (CLAUDE.md §11). Lookup por `code` (active=True). Se um
    tipo foi desativado pelo admin via S15, simplesmente pulamos a
    criação dessas anomalias — mesma política de `processing/anomalies.py`.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.core.logging import get_logger
from app.db.models import (
    AnomalyDetectedBy,
    AnomalyType,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
)
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.omie.lancamento_cache import OmieLancamentoCache, OmieLancamentoData
from app.modules.reconciliations.qualification import historical, outliers, semantic
from app.modules.reconciliations.qualification.schemas import (
    HistoricalResult,
    OutlierResult,
    QualificationPair,
    QualificationReport,
    SemanticResult,
    TokenUsage,
)

log = get_logger(__name__)

# Codes canônicos do catálogo (CLAUDE.md §11). Adicionados ao seed nesta
# mesma sessão (S19).
ANOMALY_CODE_QUALIF_SUSPEITA = "qualificacao_suspeita"
ANOMALY_CODE_QUALIF_INCOERENTE = "qualificacao_incoerente"
ANOMALY_CODE_PADRAO_QUEBRADO = "padrao_quebrado"
ANOMALY_CODE_VALOR_OUTLIER = "valor_outlier"

_ALL_QUALIF_CODES: tuple[str, ...] = (
    ANOMALY_CODE_QUALIF_SUSPEITA,
    ANOMALY_CODE_QUALIF_INCOERENTE,
    ANOMALY_CODE_PADRAO_QUEBRADO,
    ANOMALY_CODE_VALOR_OUTLIER,
)


async def qualify_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    client_id: UUID,
    match_pairs: list[tuple[UUID, int]],
    cache: OmieLancamentoCache,
    anthropic_client: AnthropicClient,
    encryption_key: SecretStr,
    account_type: str = "checking",
) -> QualificationReport:
    """Executa as 3 camadas e persiste anomalias na transação atual.

    Args:
        db: `AsyncSession` ATIVA (caller controla commit/rollback).
        session_id: sessão sendo qualificada.
        client_id: cliente da sessão (para o cache + RBAC histórico).
        match_pairs: pares conciliados resultantes do matcher (S10).
        cache: cache L1+L2 com os lançamentos da sessão atual já
            populados pelo caller (`populate_from_extrato`). Histórico
            faz lookup-only.
        anthropic_client: cliente Anthropic configurado.
        encryption_key: chave AES-256 hex (CLAUDE.md §4) para cifrar o
            `motivo` em `context_encrypted`.

    Returns:
        `QualificationReport` com contadores + token usage. Caller loga
        em event estruturado.
    """
    if not match_pairs:
        return QualificationReport(skipped_reason="no_match_pairs")

    # 1. Carrega tipos de anomalia ativos (lookup por code IN ...). Se
    #    nenhum dos codes existe (seed não rodou), aborta e loga.
    type_ids = await _load_qualif_type_ids(db)
    if not type_ids:
        log.warning(
            "qualification_skipped_seed_missing",
            session_id=str(session_id),
            codes=list(_ALL_QUALIF_CODES),
        )
        return QualificationReport(skipped_reason="seed_missing")

    # 2. Hidrata cache (lookup-only — o caller já populou via
    #    `populate_from_extrato`). Resolve supplier/category dos pares.
    file_entry_ids = [fid for fid, _ in match_pairs]
    omie_ids = [oid for _, oid in match_pairs]
    cached = await cache.get_many(client_id=client_id, omie_ids=omie_ids)

    # 3. Carrega file_entries (com `description_encrypted`/iv + amount)
    #    SOMENTE dos IDs casados — query única.
    file_entries = await _load_file_entries(db, file_entry_ids)
    file_entries_by_id = {fe.id: fe for fe in file_entries}

    # 4. Monta a lista de pares completos.
    hex_key = encryption_key.get_secret_value()
    pairs = _build_pairs(
        match_pairs=match_pairs,
        cached=cached,
        file_entries_by_id=file_entries_by_id,
        hex_key=hex_key,
    )
    if not pairs:
        return QualificationReport(skipped_reason="no_pairs_built")

    # 5. Roda as 3 camadas — cada uma independente.
    semantic_results, tokens, calls = await _run_semantic(
        pairs, anthropic_client=anthropic_client, account_type=account_type
    )
    historical_results = await _run_historical(
        db,
        client_id=client_id,
        current_session_id=session_id,
        pairs=pairs,
        cache=cache,
    )
    outlier_results = await _run_outliers(
        db,
        client_id=client_id,
        current_session_id=session_id,
        pairs=pairs,
        cache=cache,
    )

    # 6. Persiste anomalias.
    pair_by_id = {p.pair_id: p for p in pairs}
    anomalies: list[ReconciliationAnomaly] = []
    suspeitas = incoerentes = padrao = outlier_count = 0

    for sr in semantic_results:
        if sr.status == "ok":
            continue
        pair = pair_by_id.get(sr.pair_id)
        if pair is None:
            continue
        code = (
            ANOMALY_CODE_QUALIF_INCOERENTE
            if sr.status == "incoerente"
            else ANOMALY_CODE_QUALIF_SUSPEITA
        )
        type_id = type_ids.get(code)
        if type_id is None:
            continue
        anomalies.append(
            _build_anomaly(
                session_id=session_id,
                anomaly_type_id=type_id,
                file_entry_id=pair.file_entry_id,
                motivo=sr.motivo,
                hex_key=hex_key,
            )
        )
        if sr.status == "incoerente":
            incoerentes += 1
        else:
            suspeitas += 1

    for hr in historical_results:
        pair = pair_by_id.get(hr.pair_id)
        if pair is None:
            continue
        type_id = type_ids.get(ANOMALY_CODE_PADRAO_QUEBRADO)
        if type_id is None:
            continue
        anomalies.append(
            _build_anomaly(
                session_id=session_id,
                anomaly_type_id=type_id,
                file_entry_id=pair.file_entry_id,
                motivo=hr.motivo,
                hex_key=hex_key,
            )
        )
        padrao += 1

    for or_ in outlier_results:
        pair = pair_by_id.get(or_.pair_id)
        if pair is None:
            continue
        type_id = type_ids.get(ANOMALY_CODE_VALOR_OUTLIER)
        if type_id is None:
            continue
        anomalies.append(
            _build_anomaly(
                session_id=session_id,
                anomaly_type_id=type_id,
                file_entry_id=pair.file_entry_id,
                motivo=or_.motivo,
                hex_key=hex_key,
            )
        )
        outlier_count += 1

    if anomalies:
        db.add_all(anomalies)
        await db.flush()

    coerentes = sum(1 for sr in semantic_results if sr.status == "ok")

    return QualificationReport(
        pairs_analyzed=len(pairs),
        coerentes=coerentes,
        suspeitas=suspeitas,
        incoerentes=incoerentes,
        padrao_quebrado=padrao,
        valor_outlier=outlier_count,
        semantic_anthropic_calls=calls,
        tokens=tokens,
    )


# ----------------------------------------------------------------------
# Camadas (wrappers com try/except — falha de uma não afeta as outras)
# ----------------------------------------------------------------------


async def _run_semantic(
    pairs: list[QualificationPair],
    *,
    anthropic_client: AnthropicClient,
    account_type: str,
) -> tuple[list[SemanticResult], TokenUsage, int]:
    try:
        return await semantic.analyze_pairs(
            pairs, anthropic_client=anthropic_client, account_type=account_type
        )
    except Exception:
        log.exception("qualification_semantic_failed", pairs=len(pairs))
        return [], TokenUsage(), 0


async def _run_historical(
    db: AsyncSession,
    *,
    client_id: UUID,
    current_session_id: UUID,
    pairs: list[QualificationPair],
    cache: OmieLancamentoCache,
) -> list[HistoricalResult]:
    try:
        return await historical.find_pattern_breaks(
            db,
            client_id=client_id,
            current_session_id=current_session_id,
            current_pairs=pairs,
            cache=cache,
        )
    except Exception:
        log.exception("qualification_historical_failed", session_id=str(current_session_id))
        return []


async def _run_outliers(
    db: AsyncSession,
    *,
    client_id: UUID,
    current_session_id: UUID,
    pairs: list[QualificationPair],
    cache: OmieLancamentoCache,
) -> list[OutlierResult]:
    try:
        return await outliers.find_value_outliers(
            db,
            client_id=client_id,
            current_session_id=current_session_id,
            current_pairs=pairs,
            cache=cache,
        )
    except Exception:
        log.exception("qualification_outliers_failed", session_id=str(current_session_id))
        return []


# ----------------------------------------------------------------------
# Helpers de DB / construção de pairs / anomalia
# ----------------------------------------------------------------------


async def _load_qualif_type_ids(db: AsyncSession) -> dict[str, UUID]:
    """Lookup por code dos 4 tipos novos (ativos)."""
    rows = await db.execute(
        select(AnomalyType.id, AnomalyType.code).where(
            AnomalyType.code.in_(_ALL_QUALIF_CODES),
            AnomalyType.active.is_(True),
        )
    )
    return {code: type_id for type_id, code in rows.all()}


async def _load_file_entries(
    db: AsyncSession,
    file_entry_ids: list[UUID],
) -> list[ReconciliationFileEntry]:
    if not file_entry_ids:
        return []
    rows = await db.execute(
        select(ReconciliationFileEntry).where(ReconciliationFileEntry.id.in_(file_entry_ids))
    )
    return list(rows.scalars().all())


def _build_pairs(
    *,
    match_pairs: list[tuple[UUID, int]],
    cached: dict[int, OmieLancamentoData],
    file_entries_by_id: dict[UUID, ReconciliationFileEntry],
    hex_key: str,
) -> list[QualificationPair]:
    """Monta `QualificationPair` por par, decifrando a descrição."""
    pairs: list[QualificationPair] = []
    for file_entry_id, omie_id in match_pairs:
        entry = file_entries_by_id.get(file_entry_id)
        if entry is None:
            log.warning(
                "qualification_pair_missing_file_entry",
                file_entry_id=str(file_entry_id),
            )
            continue
        try:
            description = decrypt(entry.description_encrypted, entry.description_iv, hex_key)
        except Exception:
            log.warning(
                "qualification_pair_decrypt_failed",
                file_entry_id=str(file_entry_id),
            )
            continue
        data = cached.get(omie_id)
        supplier = data.supplier if data is not None else None
        category = data.category if data is not None else None
        pairs.append(
            QualificationPair(
                pair_id=str(file_entry_id),
                file_entry_id=file_entry_id,
                omie_lancamento_id=omie_id,
                description=description,
                supplier=supplier,
                category=category,
                amount=entry.amount,
            )
        )
    return pairs


def _build_anomaly(
    *,
    session_id: UUID,
    anomaly_type_id: UUID,
    file_entry_id: UUID,
    motivo: str,
    hex_key: str,
) -> ReconciliationAnomaly:
    """Cria `ReconciliationAnomaly` com `context_encrypted` populado."""
    ct, iv = encrypt(motivo, hex_key)
    return ReconciliationAnomaly(
        session_id=session_id,
        anomaly_type_id=anomaly_type_id,
        file_entry_id=file_entry_id,
        detected_by=AnomalyDetectedBy.AI.value,
        context_encrypted=ct,
        context_iv=iv,
        resolved=False,
    )
