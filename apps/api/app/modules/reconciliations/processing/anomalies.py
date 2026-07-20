"""Criação de anomalias estruturais (BACK 8.5).

Disparado imediatamente após o cruzamento, na MESMA transação:
    - Cada `file_entry` com `situation='sem_omie'` → anomaly_type
      `missing_in_omie`, `detected_by='ai'`, `file_entry_id=...`.
    - Cada `omie_entry` persistido com `omie_status='Atrasado'` → anomaly_type
      `missing_in_file`, `detected_by='ai'`, `omie_entry_id=...`.

`AnomalyType` NUNCA é criado em runtime — vem do seed (CLAUDE.md §11).
Lookup por `code` falha alto se o seed não rodou; isso é melhor que criar
type silenciosamente e mascarar problema de deploy.

`Previsto` NÃO gera anomaly: o título tá no Omie, mas como não venceu, é
esperado não aparecer no extrato. Sem alerta. Doc §13 §17.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto_service import AAD_ANOMALY_CONTEXT, field_locator
from app.db.models import (
    AnomalyDetectedBy,
    AnomalyType,
    OmieEntryStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
)

if TYPE_CHECKING:
    from app.core.crypto import ClientCipher

# Códigos canônicos do seed (CLAUDE.md §11). String literal aqui para evitar
# import circular com seed_dev e porque os codes são contrato persistido no
# DB (mudar implica migration).
ANOMALY_CODE_MISSING_IN_OMIE = "missing_in_omie"
ANOMALY_CODE_MISSING_IN_FILE = "missing_in_file"
# FASE 1: linha conciliada por valor mas com data divergente (1-3 dias).
ANOMALY_CODE_WRONG_DATE = "wrong_date"


@dataclass(frozen=True, slots=True)
class DivergentMatch:
    """Uma linha conciliada por valor mas com data divergente (1-3 dias).

    Alimenta a anomalia `wrong_date`: o `context_encrypted` guarda a data do
    arquivo e a data do Omie lado a lado para a Tela de Revisão (FASE 1 /
    BACK 1.7). As datas são em claro (CLAUDE.md §4.4 permite), mas o contexto
    da anomalia é criptografado por consistência com o resto da tabela.
    """

    file_entry_id: UUID
    file_date: date
    omie_date: date


class _AnomalyTypeMissingError(Exception):
    """`AnomalyType` esperado não está no DB — seed não rodou.

    Mantida privada porque é erro de operação (deploy ruim), não uma falha
    de domínio. O job converte em `error_message` PT-BR genérico para o
    front, mas a stack original cai no Sentry.
    """


async def _load_anomaly_type_ids(
    session: AsyncSession, *, include_wrong_date: bool = False
) -> dict[str, UUID]:
    """Carrega os IDs dos `AnomalyType` ATIVOS necessários por code.

    Faz UMA query (`code IN (...)`) e devolve dict — evita N+1.

    `include_wrong_date`: o tipo `wrong_date` só é exigido quando há linhas
    divergentes a marcar (FASE 1). Mantê-lo fora do conjunto obrigatório por
    padrão evita que conciliações sem divergência (e os testes legados do job)
    falhem se o seed do `wrong_date` não tiver rodado.

    Distingue dois cenários de ausência:
        - Tipo NÃO existe no DB (qualquer state) → seed não rodou → falha alta
          (`_AnomalyTypeMissingError`). Sentry + error_message PT-BR no front.
        - Tipo existe mas `active=False` → admin desativou via S15 BACK 11.1
          → silently omitido do dict. `create_structural_anomalies` pula a
          criação dessas anomalias para conciliações NOVAS. Anomalias antigas
          permanecem (FK em `reconciliation_anomalies` é `ondelete=RESTRICT`).
    """
    needed = {ANOMALY_CODE_MISSING_IN_OMIE, ANOMALY_CODE_MISSING_IN_FILE}
    if include_wrong_date:
        needed.add(ANOMALY_CODE_WRONG_DATE)
    rows = await session.execute(
        select(AnomalyType.id, AnomalyType.code, AnomalyType.active).where(
            AnomalyType.code.in_(needed)
        )
    )
    by_code: dict[str, UUID] = {}
    existing_codes: set[str] = set()
    for type_id, code, active in rows.all():
        existing_codes.add(code)
        if active:
            by_code[code] = type_id
    missing = needed - existing_codes
    if missing:
        raise _AnomalyTypeMissingError(
            f"AnomalyType ausente(s) no DB: {sorted(missing)}. Rodou `pnpm db:seed`?"
        )
    return by_code


def _build_wrong_date_anomalies(
    *,
    session_id: UUID,
    wrong_date_type_id: UUID,
    divergent: list[DivergentMatch],
    cipher: ClientCipher | None,
) -> list[ReconciliationAnomaly]:
    """Monta as anomalias `wrong_date`, cifrando o contexto data-arquivo/data-Omie.

    `cipher` (DEK do cliente) é obrigatório aqui (CLAUDE.md §4 —
    `context_encrypted`). Levanta `ValueError` se ausente em vez de criar a
    anomalia sem contexto. O `id` da anomalia é gerado ANTES de cifrar para
    compor o AAD (client_id‖tabela‖coluna‖pk).
    """
    if cipher is None:
        raise ValueError("cipher é obrigatório quando há divergências (wrong_date).")
    result: list[ReconciliationAnomaly] = []
    for dm in divergent:
        anomaly_id = uuid4()
        context = f"Data arquivo: {dm.file_date:%d/%m/%Y} · Data Omie: {dm.omie_date:%d/%m/%Y}"
        ct, iv = cipher.encrypt(context, field_locator(AAD_ANOMALY_CONTEXT, anomaly_id))
        result.append(
            ReconciliationAnomaly(
                id=anomaly_id,
                session_id=session_id,
                anomaly_type_id=wrong_date_type_id,
                file_entry_id=dm.file_entry_id,
                detected_by=AnomalyDetectedBy.AI.value,
                context_encrypted=ct,
                context_iv=iv,
            )
        )
    return result


async def create_structural_anomalies(
    session: AsyncSession,
    *,
    session_id: UUID,
    unmatched_file_entries: list[ReconciliationFileEntry],
    persisted_omie_entries: list[ReconciliationOmieEntry],
    divergent_matches: list[DivergentMatch] | None = None,
    cipher: ClientCipher | None = None,
) -> int:
    """Cria as anomalias estruturais e devolve o total.

    Espera que os file_entries e omie_entries já tenham sido `flush()`-ados
    no DB (precisam de `id` válido). O caller no `job.py` faz isso na ordem
    correta dentro da mesma transação:
        1. flush file_entries (já criados em /reconciliations).
        2. flush omie_entries (recém-inseridos pelo matcher).
        3. chamar este método.
        4. atualizar contadores na sessão.
        5. commit único.

    Args:
        session: AsyncSession da MESMA transação que escreveu file/omie entries.
        session_id: FK para `reconciliation_sessions`.
        unmatched_file_entries: list[FileEntry] com `situation='sem_omie'` —
            uma anomaly `missing_in_omie` por linha.
        persisted_omie_entries: list[OmieEntry] já inseridos. Apenas os com
            `omie_status='Atrasado'` viram `missing_in_file`. `Previsto` é
            ignorado (Doc §13 — esperado não estar no extrato).
        divergent_matches: linhas conciliadas com data divergente (FASE 1) —
            cada uma vira uma anomaly `wrong_date` com `context_encrypted`
            guardando "Data arquivo: X · Data Omie: Y". O tipo `wrong_date` só
            é exigido no DB quando esta lista é não-vazia.
        cipher: `ClientCipher` do cliente (DEK) para cifrar o contexto das
            anomalias `wrong_date` no envelope corrente + AAD. Obrigatório
            quando há `divergent_matches`.

    Returns:
        Total de anomalias criadas (missing_in_omie + Atrasado + wrong_date).
    """
    divergent = divergent_matches or []
    type_ids = await _load_anomaly_type_ids(session, include_wrong_date=bool(divergent))
    # `.get()` em vez de `[...]` — se o admin desativou o tipo (S15 BACK 11.1),
    # ele simplesmente não vem no dict e os blocos abaixo são pulados em silêncio.
    missing_in_omie_id = type_ids.get(ANOMALY_CODE_MISSING_IN_OMIE)
    missing_in_file_id = type_ids.get(ANOMALY_CODE_MISSING_IN_FILE)
    wrong_date_id = type_ids.get(ANOMALY_CODE_WRONG_DATE)

    anomalies: list[ReconciliationAnomaly] = []

    if missing_in_omie_id is not None:
        for entry in unmatched_file_entries:
            anomalies.append(
                ReconciliationAnomaly(
                    session_id=session_id,
                    anomaly_type_id=missing_in_omie_id,
                    file_entry_id=entry.id,
                    detected_by=AnomalyDetectedBy.AI.value,
                )
            )

    if missing_in_file_id is not None:
        for omie_entry in persisted_omie_entries:
            if omie_entry.omie_status != OmieEntryStatus.ATRASADO.value:
                continue
            anomalies.append(
                ReconciliationAnomaly(
                    session_id=session_id,
                    anomaly_type_id=missing_in_file_id,
                    omie_entry_id=omie_entry.id,
                    detected_by=AnomalyDetectedBy.AI.value,
                )
            )

    # FASE 1: cada linha conciliada com data divergente (1-3 dias) ganha uma
    # anomalia `wrong_date` com o contexto "Data arquivo / Data Omie"
    # criptografado (BACK 1.7). A `situation='conciliado_data_divergente'` já
    # foi gravada em `apply_matches`.
    if wrong_date_id is not None and divergent:
        anomalies.extend(
            _build_wrong_date_anomalies(
                session_id=session_id,
                wrong_date_type_id=wrong_date_id,
                divergent=divergent,
                cipher=cipher,
            )
        )

    if anomalies:
        session.add_all(anomalies)
        await session.flush()
    return len(anomalies)
