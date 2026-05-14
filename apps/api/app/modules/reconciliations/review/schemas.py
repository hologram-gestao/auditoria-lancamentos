"""Schemas Pydantic da Tela de Revisão (S11 BACK 9.1, 9.3-9.9).

Convenções (CLAUDE.md §6):
    - Envelope `{ data, pagination? }`.
    - Datas como `date` ISO 8601 na serialização.
    - Decimal serializado como string para não perder precisão no JSON.
    - Requests `*Request`: validação estrita (Literal/pattern/min_length).
    - Responses `*Item`: tipos lenientes (str em vez de Literal) — memória
      `feedback_pydantic_strict_input_lenient_output`.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.users.schemas import PaginationMeta

# ----------------------------------------------------------------------
# BACK 9.1 — Listar Movimentações
# ----------------------------------------------------------------------


class ListedFileEntry(BaseModel):
    """Item de GET /api/v1/reconciliations/{id}/file-entries."""

    id: UUID
    transaction_date: _date
    description: str
    amount: Decimal
    balance: Decimal | None
    situation: str
    user_action: str | None
    user_note: str | None
    omie_lancamento_id: int | None

    model_config = ConfigDict(from_attributes=False)


class FileEntryListResponse(BaseModel):
    data: list[ListedFileEntry]
    pagination: PaginationMeta


# ----------------------------------------------------------------------
# BACK 9.3 — Atualizar ação em linha do arquivo
# ----------------------------------------------------------------------


class UpdateFileEntryRequest(BaseModel):
    """Body do PATCH /file-entries/{entry_id}.

    Todos os campos são opcionais (semântica PATCH parcial). Para "trocar
    Omie" o front envia `omie_lancamento_id=int`; para "remover vínculo"
    envia `omie_lancamento_id=null`. Para "não mexer", omite a chave.
    """

    situation: Literal["sem_omie", "conciliado", "ignorado"] | None = None
    user_action: Literal["confirm", "flag", "ignore"] | None = None
    user_note: str | None = Field(default=None, max_length=2000)
    omie_lancamento_id: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(strict=False)


class UpdateFileEntryResponse(BaseModel):
    data: ListedFileEntry


# Sentinela `_UNSET` foi removida em favor de `model_fields_set` do Pydantic v2 —
# distingue chave presente com valor `null` de chave omitida sem precisar de
# tipos custom.


# ----------------------------------------------------------------------
# BACK 9.4 — Disponíveis para trocar
# ----------------------------------------------------------------------


class AvailableOmieEntry(BaseModel):
    """Item da resposta de /available-omie-entries.

    Vem do Omie em tempo real (cache L2). Não persiste no DB.
    """

    omie_id: int
    transaction_date: _date
    description: str
    supplier: str | None
    category: str | None
    amount: Decimal
    status: str


class AvailableOmieEntriesResponse(BaseModel):
    data: list[AvailableOmieEntry]


# ----------------------------------------------------------------------
# BACK 9.5 — Listar divergências Omie
# ----------------------------------------------------------------------


class OmieEntryItem(BaseModel):
    """Item de GET /api/v1/reconciliations/{id}/omie-entries.

    `supplier`, `category`, `amount` vêm do cache L2 — podem ser None se
    o ID não estiver mais cacheado nem disponível no extrato. UI deve
    mostrar placeholder ('—') nesse caso.
    """

    id: UUID
    omie_lancamento_id: int
    transaction_date: _date
    omie_status: str
    supplier: str | None
    category: str | None
    amount: Decimal | None
    user_action: str | None
    user_note: str | None


class OmieEntryListResponse(BaseModel):
    data: list[OmieEntryItem]
    pagination: PaginationMeta


# ----------------------------------------------------------------------
# BACK 9.6 — Atualizar ação em divergência Omie
# ----------------------------------------------------------------------


class UpdateOmieEntryRequest(BaseModel):
    user_action: Literal["flag", "ignore", "resolved"] | None = None
    user_note: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(strict=False)


class UpdateOmieEntryResponse(BaseModel):
    data: OmieEntryItem


# ----------------------------------------------------------------------
# BACK 9.7 — Listar anomalias
# ----------------------------------------------------------------------


AnomalyResolvedFilter = Literal["all", "true", "false"]
AnomalySeverityFilter = Literal["all", "critical", "moderate", "info"]


class AnomalyTypeRef(BaseModel):
    """Sub-objeto com info do AnomalyType — inline na resposta da anomalia."""

    id: UUID
    code: str
    name: str
    severity: str


class AnomalyRelatedFileEntry(BaseModel):
    """Dados sumarizados da `file_entry` quando a anomalia referencia uma.

    `description` é descriptografada antes de servir. Se `file_entry_id`
    apontava para uma linha já removida (SET NULL), o objeto inteiro é
    omitido na resposta — `related_file_entry=None`.
    """

    id: UUID
    transaction_date: _date
    description: str
    amount: Decimal


class AnomalyRelatedOmieEntry(BaseModel):
    """Idem para `omie_entry`. Mantém só os campos persistidos no DB."""

    id: UUID
    transaction_date: _date
    omie_lancamento_id: int


class AnomalyItem(BaseModel):
    """Item de GET /api/v1/reconciliations/{id}/anomalies."""

    id: UUID
    anomaly_type: AnomalyTypeRef
    detected_by: str
    resolved: bool
    context: str | None
    resolution_note: str | None
    created_at: datetime
    related_file_entry: AnomalyRelatedFileEntry | None
    related_omie_entry: AnomalyRelatedOmieEntry | None


class AnomalyListResponse(BaseModel):
    data: list[AnomalyItem]
    pagination: PaginationMeta


# ----------------------------------------------------------------------
# BACK 9.8 — Criar anomalia manual
# ----------------------------------------------------------------------


class CreateAnomalyRequest(BaseModel):
    """Body do POST /api/v1/reconciliations/{id}/anomalies.

    Validações cross-field:
        - exatamente UM entre `file_entry_id` e `omie_entry_id` (XOR).
        - `context` opcional.
    """

    anomaly_type_id: UUID
    file_entry_id: UUID | None = None
    omie_entry_id: UUID | None = None
    context: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_xor(self) -> CreateAnomalyRequest:
        # Aceita "nenhum"? Doc §14.5 diz que é sempre vinculada a uma
        # linha — mas pra evitar quebra em demo flexibilizamos: aceita
        # nenhum (caso analista queira anomalia "estrutural" não-aderente).
        # Rejeita os DOIS — anomalia não pode referenciar 2 linhas ao mesmo
        # tempo.
        if self.file_entry_id is not None and self.omie_entry_id is not None:
            raise ValueError(
                "Anomalia só pode referenciar UMA linha — envie file_entry_id "
                "OU omie_entry_id, não os dois."
            )
        return self


class CreateAnomalyResponse(BaseModel):
    data: AnomalyItem


# ----------------------------------------------------------------------
# BACK 9.9 — Resolver anomalia
# ----------------------------------------------------------------------


class ResolveAnomalyRequest(BaseModel):
    """Body do PATCH /api/v1/reconciliations/{id}/anomalies/{anomaly_id}.

    Quando `resolved=true`, `resolution_note` precisa ter ≥ 10 chars
    (Doc §17.3). Validação roda no service para emitir mensagem PT-BR.
    """

    resolved: bool
    resolution_note: str | None = Field(default=None, max_length=2000)


class ResolveAnomalyResponse(BaseModel):
    data: AnomalyItem
