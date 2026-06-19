"""Orquestração do export Excel (S14 BACK 10.1).

Responsabilidades:
    1. Carregar sessão + entries + omie_entries + anomalies do DB
       (já reusando o `ReviewRepository` quando faz sentido).
    2. Descriptografar em memória todos os campos sensíveis (CLAUDE.md §4).
    3. Buscar `bank_name`/`account_name` do `OmieAccountCache`.
    4. Hidratar `supplier`/`category`/`amount` dos lançamentos Omie via
       cache L2 — popula via `populate_from_extrato` quando o cache
       expira (paridade com S11 `list_available_omie_entries`).
    5. Sanitizar nome do arquivo (sem `\\ / : * ? " < > |` nem acentos).
    6. Montar `ExportPayload` para o `workbook.build_workbook`.

NÃO loga plaintext. NÃO persiste o XLSX. NÃO retorna ciphertext em logs.
"""

from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    OmieAccountCache,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
)
from app.modules.reconciliations.export.schemas import (
    AnomalyRow,
    ExportPayload,
    FileEntryRow,
    OmieDivergenceRow,
    QualificationStatus,
    SemOmieRow,
    SummarySheetData,
)
from app.modules.reconciliations.processing.matcher import DATE_DIVERGENCE_RANGE
from app.modules.reconciliations.qualification.service import (
    ANOMALY_CODE_PADRAO_QUEBRADO,
    ANOMALY_CODE_QUALIF_INCOERENTE,
    ANOMALY_CODE_QUALIF_SUSPEITA,
    ANOMALY_CODE_VALOR_OUTLIER,
)

if TYPE_CHECKING:
    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.lancamento_cache import (
        OmieLancamentoCache,
        OmieLancamentoData,
    )

logger = get_logger(__name__)

# UTC-3 fixo: o Brasil saiu do horário de verão em 2019, então `America/Sao_Paulo`
# é sempre UTC-3 hoje. Evita dependência de `zoneinfo` (e do tzdata em ambientes
# Alpine/Distroless). Se o governo restaurar DST, atualizar aqui.
_BRT = timezone(timedelta(hours=-3))

# Padrão de sanitização: caracteres inválidos para NTFS/Windows + chars
# de controle. Backslash escapado em raw string.
_FILENAME_INVALID_RE = re.compile(r'[\\/:*?"<>|\x00-\x1F]+')
_FILENAME_WHITESPACE_RE = re.compile(r"\s+")

_PT_BR_MONTHS = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}


class ExportService:
    """Coordena leitura+decrypt+cache+montagem do payload do workbook."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        cache: OmieLancamentoCache,
        encryption_key: SecretStr,
    ) -> None:
        self._db = db
        self._cache = cache
        self._hex_key = encryption_key.get_secret_value()

    async def build_payload(
        self,
        *,
        session: ReconciliationSession,
        client: Client,
        omie_client: OmieClient | None,
        current_user_email: str,
    ) -> ExportPayload:
        """Monta o `ExportPayload` (DTO consumido pelo `workbook.build_workbook`).

        Args:
            session: sessão já carregada (RBAC + status já validados pelo
                router).
            client: Client carregado (pra `client.name`).
            omie_client: opcional. Quando passado, e o cache L2 estiver
                vazio/expirado para algum ID, populamos via extrato do
                período. Quando None (modo "best-effort"), apenas o que já
                estiver em cache é usado; campos faltantes aparecem como
                placeholder no Excel.
            current_user_email: email do `current_user` para o rodapé.

        Returns:
            `ExportPayload` pronto pra serializar.
        """
        bank_name, account_name = await self._load_account_info(
            client_id=client.id,
            omie_conta_id=session.omie_conta_id,
        )

        file_entries = await self._load_file_entries(session.id)
        omie_entries = await self._load_omie_entries(session.id)
        anomalies_rows = await self._load_anomalies(session.id)

        all_omie_ids = self._collect_omie_ids(file_entries, omie_entries)
        cached = await self._hydrate_lancamento_cache(
            session=session,
            omie_client=omie_client,
            omie_ids=all_omie_ids,
        )

        ignored_count = sum(
            1 for e in file_entries if e.situation == FileEntrySituation.IGNORADO.value
        )

        # Aggrega o status de qualificação pior por file_entry_id (S19).
        # Map: file_entry_id -> QualificationStatus | None
        qualif_status_by_entry = _compute_qualification_status_by_entry(anomalies_rows)
        qualif_counters = _compute_qualification_counters(
            file_entries=file_entries,
            status_by_entry=qualif_status_by_entry,
        )

        summary = self._build_summary(
            client=client,
            session=session,
            bank_name=bank_name,
            account_name=account_name,
            ignored_count=ignored_count,
            anomalies=anomalies_rows,
            current_user_email=current_user_email,
            qualif_counters=qualif_counters,
        )

        file_entry_rows = self._build_file_entry_rows(file_entries, cached, qualif_status_by_entry)
        omie_div_rows = self._build_omie_divergence_rows(omie_entries, cached)
        sem_omie_rows = self._build_sem_omie_rows(file_entries)
        anomaly_rows = self._build_anomaly_rows(
            anomalies_rows, file_entries=file_entries, omie_entries=omie_entries
        )

        filename = build_filename(
            client_name=client.name,
            account_name=account_name,
            reference_month=session.reference_month,
        )

        logger.info(
            "export_payload_built",
            session_id=str(session.id),
            client_id=str(client.id),
            file_entries=len(file_entry_rows),
            omie_divergences=len(omie_div_rows),
            sem_omie=len(sem_omie_rows),
            anomalies=len(anomaly_rows),
            cached_lancamentos=len(cached),
            missing_from_cache=sum(1 for oid in all_omie_ids if oid not in cached),
        )

        return ExportPayload(
            filename=filename,
            summary=summary,
            file_entries=file_entry_rows,
            omie_divergences=omie_div_rows,
            sem_omie=sem_omie_rows,
            anomalies=anomaly_rows,
        )

    # ------------------------------------------------------------------
    # Loaders (DB)
    # ------------------------------------------------------------------

    async def _load_account_info(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
    ) -> tuple[str, str]:
        """Pega `(bank_name, account_name)` do cache L1 de contas Omie.

        Cache pode estar vazio (cliente novo sem sync). Nesse caso, devolve
        placeholders — o relatório ainda sai, só sem o nome amigável.
        """
        row = (
            await self._db.execute(
                select(OmieAccountCache).where(
                    OmieAccountCache.client_id == client_id,
                    OmieAccountCache.omie_conta_id == omie_conta_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return ("—", f"Conta {omie_conta_id}")
        return (row.bank_name, row.name)

    async def _load_file_entries(self, session_id: UUID) -> list[ReconciliationFileEntry]:
        stmt = (
            select(ReconciliationFileEntry)
            .where(ReconciliationFileEntry.session_id == session_id)
            .order_by(
                ReconciliationFileEntry.transaction_date.asc(),
                ReconciliationFileEntry.id.asc(),
            )
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def _load_omie_entries(self, session_id: UUID) -> list[ReconciliationOmieEntry]:
        stmt = (
            select(ReconciliationOmieEntry)
            .where(ReconciliationOmieEntry.session_id == session_id)
            .order_by(
                ReconciliationOmieEntry.transaction_date.asc(),
                ReconciliationOmieEntry.id.asc(),
            )
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def _load_anomalies(
        self, session_id: UUID
    ) -> list[tuple[ReconciliationAnomaly, AnomalyType]]:
        stmt = (
            select(ReconciliationAnomaly, AnomalyType)
            .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
            .where(ReconciliationAnomaly.session_id == session_id)
            .order_by(ReconciliationAnomaly.created_at.asc())
        )
        rows = (await self._db.execute(stmt)).all()
        return [(anomaly, atype) for anomaly, atype in rows]

    # ------------------------------------------------------------------
    # Cache L2
    # ------------------------------------------------------------------

    def _collect_omie_ids(
        self,
        file_entries: list[ReconciliationFileEntry],
        omie_entries: list[ReconciliationOmieEntry],
    ) -> set[int]:
        ids: set[int] = set()
        for fe in file_entries:
            if fe.omie_lancamento_id is not None:
                ids.add(fe.omie_lancamento_id)
        for oe in omie_entries:
            ids.add(oe.omie_lancamento_id)
        return ids

    async def _hydrate_lancamento_cache(
        self,
        *,
        session: ReconciliationSession,
        omie_client: OmieClient | None,
        omie_ids: set[int],
    ) -> dict[int, OmieLancamentoData]:
        """Tenta resolver todos os IDs via L1+L2; popula extrato se faltar.

        Estratégia:
            1. `get_many` resolve do L1/L2 — barato.
            2. Se restou ID faltando E temos `omie_client`, chamamos
               `populate_from_extrato` no período EXPANDIDO da sessão (mesma
               lógica de `list_available_omie_entries`). O extrato traz TUDO
               do período — atualiza L1+L2 e fazemos novo lookup.
            3. IDs que ainda faltarem ficam fora do dict — renderer mostra
               placeholder "—" no Excel + log de warning.
        """
        if not omie_ids:
            return {}

        cached = await self._cache.get_many(
            client_id=session.client_id,
            omie_ids=list(omie_ids),
        )
        missing = omie_ids - set(cached.keys())
        if not missing or omie_client is None:
            return cached

        # Popula via extrato do período expandido (mesma estratégia do
        # ReviewService.list_available_omie_entries).
        period_start, period_end = _resolve_session_period(session)
        # FASE 1: range fixo (não mais a tolerância por sessão) — sessões novas
        # gravam date_tolerance_days=0, então usar a coluna encolheria a janela.
        expanded_start = period_start - timedelta(days=DATE_DIVERGENCE_RANGE)
        expanded_end = period_end + timedelta(days=DATE_DIVERGENCE_RANGE)

        try:
            populated = await self._cache.populate_from_extrato(
                client_id=session.client_id,
                omie_client=omie_client,
                omie_conta_id=session.omie_conta_id,
                period_start=expanded_start,
                period_end=expanded_end,
            )
        except Exception as exc:
            # Falha na ida ao Omie NÃO derruba o export — degrada para "o
            # que estiver no cache". Log estruturado pra observabilidade.
            logger.warning(
                "export_omie_populate_failed",
                session_id=str(session.id),
                client_id=str(session.client_id),
                error=type(exc).__name__,
            )
            return cached

        # `populated` já cobre o período expandido — merge no `cached`.
        merged: dict[int, OmieLancamentoData] = {**cached}
        for oid in missing:
            if oid in populated:
                merged[oid] = populated[oid]

        still_missing = omie_ids - set(merged.keys())
        if still_missing:
            logger.warning(
                "export_lancamento_cache_miss",
                session_id=str(session.id),
                client_id=str(session.client_id),
                missing_count=len(still_missing),
            )

        return merged

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        *,
        client: Client,
        session: ReconciliationSession,
        bank_name: str,
        account_name: str,
        ignored_count: int,
        anomalies: list[tuple[ReconciliationAnomaly, AnomalyType]],
        current_user_email: str,
        qualif_counters: _QualifCounters,
    ) -> SummarySheetData:
        anomaly_critical = sum(
            1 for _, atype in anomalies if atype.severity == AnomalySeverity.CRITICAL.value
        )
        anomaly_moderate = sum(
            1 for _, atype in anomalies if atype.severity == AnomalySeverity.MODERATE.value
        )
        anomaly_info = sum(
            1 for _, atype in anomalies if atype.severity == AnomalySeverity.INFO.value
        )
        anomaly_resolved = sum(1 for anomaly, _ in anomalies if anomaly.resolved)
        anomaly_critical_unresolved = sum(
            1
            for anomaly, atype in anomalies
            if atype.severity == AnomalySeverity.CRITICAL.value and not anomaly.resolved
        )

        return SummarySheetData(
            client_name=client.name,
            bank_name=bank_name,
            account_name=account_name,
            period_pt_br=_format_reference_month_pt_br(session.reference_month),
            balance_start=session.balance_start,
            balance_end_file=session.balance_end_file,
            balance_end_omie=session.balance_end_omie,
            balance_difference=session.balance_difference,
            total_file_entries=session.total_file_entries,
            conciliated_count=session.conciliated_count,
            sem_omie_count=session.sem_omie_count,
            ignored_count=ignored_count,
            omie_sem_arquivo_count=session.omie_sem_arquivo_count,
            anomaly_total=len(anomalies),
            anomaly_critical=anomaly_critical,
            anomaly_moderate=anomaly_moderate,
            anomaly_info=anomaly_info,
            anomaly_resolved=anomaly_resolved,
            anomaly_critical_unresolved=anomaly_critical_unresolved,
            generated_at_brt=datetime.now(UTC).astimezone(_BRT),
            generated_by_email=current_user_email,
            qualif_coerentes=qualif_counters.coerentes,
            qualif_suspeitas=qualif_counters.suspeitas,
            qualif_incoerentes=qualif_counters.incoerentes,
            qualif_padrao_quebrado=qualif_counters.padrao_quebrado,
            qualif_valor_outlier=qualif_counters.valor_outlier,
        )

    def _build_file_entry_rows(
        self,
        file_entries: list[ReconciliationFileEntry],
        cached: dict[int, OmieLancamentoData],
        qualif_status_by_entry: dict[UUID, QualificationStatus],
    ) -> list[FileEntryRow]:
        rows: list[FileEntryRow] = []
        for entry in file_entries:
            description = self._decrypt_required(
                entry.description_encrypted, entry.description_iv, field="description"
            )
            note = self._decrypt_optional(
                entry.user_note_encrypted, entry.user_note_iv, field="user_note"
            )
            supplier: str | None = None
            category: str | None = None
            if entry.omie_lancamento_id is not None:
                data = cached.get(entry.omie_lancamento_id)
                if data is not None:
                    supplier = data.supplier
                    category = data.category
            # Status de qualificação: explícito "ok" pra conciliados sem
            # anomalia de qualificação (analista lê como "IA validou");
            # `None` pra sem_omie/ignorado (qualificação não rodou nesses).
            qualif: QualificationStatus | None
            if entry.situation == FileEntrySituation.CONCILIADO.value:
                qualif = qualif_status_by_entry.get(entry.id, "ok")
            else:
                qualif = None
            rows.append(
                FileEntryRow(
                    transaction_date=entry.transaction_date,
                    description=description,
                    amount=entry.amount,
                    balance=entry.balance,
                    supplier=supplier,
                    category=category,
                    situation=entry.situation,
                    user_note=note,
                    qualification_status=qualif,
                )
            )
        return rows

    def _build_omie_divergence_rows(
        self,
        omie_entries: list[ReconciliationOmieEntry],
        cached: dict[int, OmieLancamentoData],
    ) -> list[OmieDivergenceRow]:
        rows: list[OmieDivergenceRow] = []
        for entry in omie_entries:
            data = cached.get(entry.omie_lancamento_id)
            note = self._decrypt_optional(
                entry.user_note_encrypted, entry.user_note_iv, field="omie_user_note"
            )
            rows.append(
                OmieDivergenceRow(
                    transaction_date=entry.transaction_date,
                    supplier=data.supplier if data is not None else None,
                    category=data.category if data is not None else None,
                    amount=data.amount if data is not None else None,
                    omie_status=entry.omie_status,
                    user_note=note,
                )
            )
        return rows

    def _build_sem_omie_rows(self, file_entries: list[ReconciliationFileEntry]) -> list[SemOmieRow]:
        rows: list[SemOmieRow] = []
        for entry in file_entries:
            if entry.situation != FileEntrySituation.SEM_OMIE.value:
                continue
            description = self._decrypt_required(
                entry.description_encrypted, entry.description_iv, field="description"
            )
            note = self._decrypt_optional(
                entry.user_note_encrypted, entry.user_note_iv, field="user_note"
            )
            rows.append(
                SemOmieRow(
                    transaction_date=entry.transaction_date,
                    description=description,
                    amount=entry.amount,
                    user_note=note,
                )
            )
        return rows

    def _build_anomaly_rows(
        self,
        anomalies: list[tuple[ReconciliationAnomaly, AnomalyType]],
        *,
        file_entries: list[ReconciliationFileEntry],
        omie_entries: list[ReconciliationOmieEntry],
    ) -> list[AnomalyRow]:
        file_entry_map = {entry.id: entry for entry in file_entries}
        omie_entry_map = {entry.id: entry for entry in omie_entries}

        rows: list[AnomalyRow] = []
        for anomaly, atype in anomalies:
            related = self._related_line_label(
                anomaly,
                file_entry_map=file_entry_map,
                omie_entry_map=omie_entry_map,
            )
            note = self._decrypt_optional(
                anomaly.resolution_note_encrypted,
                anomaly.resolution_note_iv,
                field="resolution_note",
            )
            rows.append(
                AnomalyRow(
                    severity=atype.severity,
                    type_name=atype.name,
                    related_line=related,
                    detected_by=anomaly.detected_by,
                    resolved=anomaly.resolved,
                    resolution_note=note,
                )
            )
        return rows

    def _related_line_label(
        self,
        anomaly: ReconciliationAnomaly,
        *,
        file_entry_map: dict[UUID, ReconciliationFileEntry],
        omie_entry_map: dict[UUID, ReconciliationOmieEntry],
    ) -> str:
        """Texto humano para `Linha relacionada` da aba 5.

        Formato: `DD/MM/YYYY · R$ 1.234,56`. Se a anomaly não tem vínculo
        com nenhuma linha (caso comum em anomalias estruturais agregadas),
        devolvemos "—".
        """
        if anomaly.file_entry_id is not None and (fe := file_entry_map.get(anomaly.file_entry_id)):
            return _format_date_amount(fe.transaction_date, fe.amount)
        if anomaly.omie_entry_id is not None and (oe := omie_entry_map.get(anomaly.omie_entry_id)):
            # omie_entry não tem amount próprio — usamos só a data.
            return _format_date_only(oe.transaction_date)
        return "—"

    # ------------------------------------------------------------------
    # Crypto helpers (mesmo padrão de review/service.py, isolado pra evitar
    # acoplamento entre módulos)
    # ------------------------------------------------------------------

    def _decrypt_required(self, ct: str | None, iv: str | None, *, field: str) -> str:
        if not ct or not iv:
            return ""
        try:
            return decrypt(ct, iv, self._hex_key)
        except Exception:
            logger.warning("export_decrypt_failed", field=field)
            return "[indecifrável]"

    def _decrypt_optional(self, ct: str | None, iv: str | None, *, field: str) -> str | None:
        if ct is None or iv is None:
            return None
        try:
            return decrypt(ct, iv, self._hex_key)
        except Exception:
            logger.warning("export_decrypt_failed", field=field)
            return None


# ----------------------------------------------------------------------
# Helpers de módulo (puros — testáveis sem DB)
# ----------------------------------------------------------------------


def build_filename(
    *,
    client_name: str,
    account_name: str,
    reference_month: date,
) -> str:
    """Monta `Conciliacao_{NomeCliente}_{Conta}_{MM-YYYY}` sanitizado.

    Sanitização:
        - NFKD normalize → remove diacríticos (Padaria São Paulo → Padaria Sao Paulo).
        - Substitui caracteres inválidos para nome de arquivo (NTFS) por `_`.
        - Colapsa espaços em `_`.
        - Trim de underscores nas pontas.
    """
    parts = [
        "Conciliacao",
        _sanitize_filename_part(client_name),
        _sanitize_filename_part(account_name),
        f"{reference_month.month:02d}-{reference_month.year}",
    ]
    return "_".join(parts)


def _sanitize_filename_part(value: str) -> str:
    """NFKD + strip inválidos + colapsa espaços."""
    # NFKD separa o caractere base do diacrítico; depois descartamos os
    # combining marks (categoria "Mn"). Funciona pra acento, cedilha, til,
    # ñ, etc.
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    # Remove caracteres inválidos.
    cleaned = _FILENAME_INVALID_RE.sub("_", ascii_only)
    # Colapsa whitespace.
    cleaned = _FILENAME_WHITESPACE_RE.sub("_", cleaned).strip("_ ")
    return cleaned or "sem_nome"


def _resolve_session_period(session: ReconciliationSession) -> tuple[date, date]:
    """Resolve período da sessão (mesma lógica de S11 review service)."""
    if session.period_start is not None and session.period_end is not None:
        return session.period_start, session.period_end
    last_day = monthrange(session.reference_month.year, session.reference_month.month)[1]
    return session.reference_month, session.reference_month.replace(day=last_day)


def _format_reference_month_pt_br(reference_month: date) -> str:
    """`date(2026, 4, 1)` → `"Abril/2026"`."""
    return f"{_PT_BR_MONTHS[reference_month.month]}/{reference_month.year}"


def _format_date_amount(d: date, amount: Decimal) -> str:
    """`DD/MM/YYYY · R$ 1.234,56` — formato pt-BR sem locale dependency."""
    return f"{d.strftime('%d/%m/%Y')} · {_format_brl(amount)}"


def _format_date_only(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _format_brl(amount: Decimal) -> str:
    """Formata `Decimal` como `R$ 1.234,56`. Sinal preservado."""
    sign = "-" if amount < 0 else ""
    integer_part, _, decimal_part = f"{abs(amount):.2f}".partition(".")
    # Separador de milhar `.`
    chunks: list[str] = []
    s = integer_part
    while len(s) > 3:
        chunks.append(s[-3:])
        s = s[:-3]
    chunks.append(s)
    formatted_int = ".".join(reversed(chunks))
    return f"{sign}R$ {formatted_int},{decimal_part}"


# ----------------------------------------------------------------------
# Funções utilitárias usadas pela rota — RBAC e status já cuidados nela;
# aqui só exposes o builder + checagem de status compatível com export.
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Helpers de qualificação (S19)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _QualifCounters:
    """Contadores agregados pra aba 1 do Excel."""

    coerentes: int
    suspeitas: int
    incoerentes: int
    padrao_quebrado: int
    valor_outlier: int


# Ordem do PIOR pro melhor — usado pra agregar a `pior anomalia` quando
# um mesmo file_entry tem múltiplas. `incoerente` > `outlier` ≈
# `padrao_quebrado` > `suspeita`. Outlier/padrão_quebrado têm severity
# INFO no catálogo; usamos `padrao_quebrado` antes pq mexer no
# fornecedor sugere classificação errada (mais grave do que valor).
_QUALIF_STATUS_RANK: dict[str, int] = {
    "incoerente": 0,
    "padrao_quebrado": 1,
    "outlier": 2,
    "suspeita": 3,
}


def _compute_qualification_status_by_entry(
    anomalies: list[tuple[ReconciliationAnomaly, AnomalyType]],
) -> dict[UUID, QualificationStatus]:
    """Pra cada file_entry com anomalia de qualificação AI, escolhe a pior.

    Anomalias sem `file_entry_id` (ex: estruturais `missing_in_file`) são
    ignoradas — não são de qualificação.
    """
    by_entry: dict[UUID, QualificationStatus] = {}
    for anomaly, atype in anomalies:
        if anomaly.file_entry_id is None:
            continue
        status = _code_to_qualif_status(atype.code)
        if status is None:
            continue
        current = by_entry.get(anomaly.file_entry_id)
        if current is None or _QUALIF_STATUS_RANK[status] < _QUALIF_STATUS_RANK[current]:
            by_entry[anomaly.file_entry_id] = status
    return by_entry


def _code_to_qualif_status(code: str) -> QualificationStatus | None:
    """Mapeia `anomaly_type.code` → `QualificationStatus` da aba 2.

    `None` quando o code não é de qualificação (estrutural ou pré-S19).
    """
    if code == ANOMALY_CODE_QUALIF_INCOERENTE:
        return "incoerente"
    if code == ANOMALY_CODE_QUALIF_SUSPEITA:
        return "suspeita"
    if code == ANOMALY_CODE_PADRAO_QUEBRADO:
        return "padrao_quebrado"
    if code == ANOMALY_CODE_VALOR_OUTLIER:
        return "outlier"
    return None


def _compute_qualification_counters(
    *,
    file_entries: list[ReconciliationFileEntry],
    status_by_entry: dict[UUID, QualificationStatus],
) -> _QualifCounters:
    """Contadores agregados pra Aba 1.

    - `coerentes` = file_entries CONCILIADAS sem flag (qualificação rodou
      e disse "ok" implicitamente). Inclui entries sem flag mesmo quando
      a sessão é pré-S19 — nesse caso todos viram "coerentes" e o
      analista entende pela ausência de outras anomalias.
    - Outros = contagem por código.
    """
    suspeitas = sum(1 for s in status_by_entry.values() if s == "suspeita")
    incoerentes = sum(1 for s in status_by_entry.values() if s == "incoerente")
    padrao = sum(1 for s in status_by_entry.values() if s == "padrao_quebrado")
    outlier = sum(1 for s in status_by_entry.values() if s == "outlier")
    conciliados = sum(1 for e in file_entries if e.situation == FileEntrySituation.CONCILIADO.value)
    # `coerentes` = conciliados que NÃO têm anomalia de qualificação.
    # Um conciliado pode ter mais de uma flag mas só conta 1x aqui.
    flagged_ids: set[UUID] = set(status_by_entry.keys())
    coerentes = sum(
        1
        for e in file_entries
        if e.situation == FileEntrySituation.CONCILIADO.value and e.id not in flagged_ids
    )
    # Sanity check (não asserta — só pra teste de regressão futura).
    _ = conciliados
    return _QualifCounters(
        coerentes=coerentes,
        suspeitas=suspeitas,
        incoerentes=incoerentes,
        padrao_quebrado=padrao,
        valor_outlier=outlier,
    )


# ----------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------


async def load_session_for_export(
    *,
    db: AsyncSession,
    session_id: UUID,
) -> ReconciliationSession:
    """Carrega sessão para export. Garante existência e não-soft-deletada.

    Status processable é validado no router (409 quando processing/error).
    """
    sess = (
        await db.execute(
            select(ReconciliationSession).where(
                ReconciliationSession.id == session_id,
                ReconciliationSession.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if sess is None:
        raise NotFoundError("Sessão de conciliação não encontrada.")
    return sess
