"""DTOs internos do export (service → workbook).

NÃO expostos via API — a rota devolve binário XLSX. Estes dataclasses
ficam aqui para que o `service.py` (que toca DB/cache/crypto) entregue
um payload já normalizado, e o `workbook.py` (que só fala openpyxl)
fique 100% determinístico e testável sem fixtures pesadas.

Convenção:
    - Valores monetários sempre `Decimal` (CLAUDE.md §3.4).
    - Texto descriptografado já entra aqui em claro — workbook só formata.
    - Campos opcionais que faltam vêm como `None`; o renderer decide o
      placeholder ("—" para "indisponível", "" para "vazio").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

QualificationStatus = Literal["ok", "suspeita", "incoerente", "padrao_quebrado", "outlier"]


@dataclass(frozen=True)
class SummarySheetData:
    """Aba 1 — Resumo."""

    client_name: str
    bank_name: str
    account_name: str
    period_pt_br: str  # "Abril/2026"
    balance_start: Decimal | None
    balance_end_file: Decimal | None
    balance_end_omie: Decimal | None
    balance_difference: Decimal | None
    total_file_entries: int
    conciliated_count: int
    sem_omie_count: int
    ignored_count: int
    omie_sem_arquivo_count: int
    anomaly_total: int
    anomaly_critical: int
    anomaly_moderate: int
    anomaly_info: int
    anomaly_resolved: int
    anomaly_critical_unresolved: int
    generated_at_brt: datetime
    generated_by_email: str
    # Qualificação (S19): contadores agregados. `qualif_coerentes` =
    # file_entries conciliados SEM anomalia de qualificação na sessão.
    # Defaults zero pra compatibilidade com fixtures pré-S19.
    qualif_coerentes: int = 0
    qualif_suspeitas: int = 0
    qualif_incoerentes: int = 0
    qualif_padrao_quebrado: int = 0
    qualif_valor_outlier: int = 0


@dataclass(frozen=True)
class FileEntryRow:
    """Aba 2 — Movimentação x Lançamento (uma linha por file_entry)."""

    transaction_date: date
    description: str
    amount: Decimal
    balance: Decimal | None
    supplier: str | None  # do cache L2; None quando não houver vínculo / não cacheado
    category: str | None
    situation: str  # conciliado | conciliado_data_divergente | sem_omie | ignorado
    user_note: str | None
    # Status de qualificação (S19). `None` quando a sessão é pré-S19 ou
    # quando a flag QUALIFICATION_ENABLED estava desligada — renderer
    # mostra `—` nesse caso pra distinguir de "ok" explícito.
    qualification_status: QualificationStatus | None = None
    # FASE 1 (BACK 1.9): data do lançamento Omie casado. Renderizada na coluna
    # "Data Omie" (só no export de cartão) para linhas `conciliado_data_
    # divergente`. `None` quando não há match ou o cache não tinha o lançamento.
    omie_date: date | None = None


@dataclass(frozen=True)
class OmieDivergenceRow:
    """Aba 3 — Divergências Omie (uma linha por reconciliation_omie_entry)."""

    transaction_date: date
    supplier: str | None
    category: str | None
    amount: Decimal | None
    omie_status: str  # Atrasado | Previsto | Conciliado
    user_note: str | None


@dataclass(frozen=True)
class SemOmieRow:
    """Aba 4 — Sem Omie (subset de file_entries com situation=sem_omie)."""

    transaction_date: date
    description: str
    amount: Decimal
    user_note: str | None


@dataclass(frozen=True)
class AnomalyRow:
    """Aba 5 — Anomalias."""

    severity: str  # critical | moderate | info
    type_name: str
    related_line: str  # "DD/MM/YYYY · R$ 1.234,56" ou "—"
    detected_by: str  # IA | Manual
    resolved: bool
    resolution_note: str | None


@dataclass(frozen=True)
class ExportPayload:
    """Tudo que o workbook precisa renderizar — encapsulado pelo service."""

    filename: str  # sem extensão (".xlsx" é adicionado em routes.py)
    # FASE 1 (BACK 1.9): cartão → título de fatura na Aba 1 + coluna "Data Omie"
    # na Aba 2. CC mantém o layout sem a coluna extra.
    is_card: bool
    summary: SummarySheetData
    file_entries: list[FileEntryRow] = field(default_factory=list)
    omie_divergences: list[OmieDivergenceRow] = field(default_factory=list)
    sem_omie: list[SemOmieRow] = field(default_factory=list)
    anomalies: list[AnomalyRow] = field(default_factory=list)
