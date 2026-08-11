"""Schemas Pydantic da Tela de Revisão (S11 BACK 9.1, 9.3-9.9).

Convenções (CLAUDE.md §7):
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

#: Enum de ENTRADA do veredito do revisor. `Literal` derivado do StrEnum do
#: modelo (fonte única — nunca redigitar a string): valor fora dele vira 422 do
#: Pydantic automaticamente, sem `if/elif` no service.
AnomalyReviewVerdictLiteral = Literal["procedente", "improcedente"]


def field_provided(body: BaseModel, field: str) -> bool:
    """True se a chave veio NO JSON — mesmo que com valor `null`.

    Num PATCH parcial, "omitido" e "nulo explícito" são pedidos DIFERENTES
    ("não mexa" vs "apague"), e o Pydantic colapsa os dois em `None`.
    `model_fields_set` é o que os separa. Este helper existe para que todo
    campo opcional de PATCH responda a `null` da MESMA forma: ter duas
    convenções de "limpar" no mesmo endpoint foi o que fez `user_note`
    ignorar o pedido de apagar e ainda responder 200.
    """
    return field in body.model_fields_set


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

    Todos os campos são opcionais (semântica PATCH parcial). Dois deles têm
    estado de "limpar" alcançável, e nos dois a convenção é a mesma — ver
    `field_provided`:

        chave omitida   → não mexe
        `null`          → limpa
        valor           → grava

    Valem assim `omie_lancamento_id` (remover vínculo) e `user_note` (apagar
    anotação); string vazia em `user_note` também limpa, por compatibilidade
    com o contrato anterior.

    `situation` e `user_action` NÃO têm caminho de limpeza: para eles, `null`
    e chave omitida significam a mesma coisa (não mexe). Em `situation` isso
    não é neutro — dentro de uma troca de vínculo Omie, ausência de
    `situation` é o gatilho da classificação automática.
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
    """Body do PATCH /omie-entries/{entry_id}.

    Mesma convenção do `UpdateFileEntryRequest`: `user_note` omitido não
    mexe, `null` (ou string vazia) limpa, valor grava.
    """

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
    # Sprint 6 (BACK 06.5) — julgamento do REVISOR, eixo diferente de `resolved`.
    # `None` = ainda não julgado. Só existe em flag da Camada 1 da qualificação.
    review_verdict: AnomalyReviewVerdictLiteral | None = None
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

    Validações cross-field (XOR estrito — Doc §14.5):
        - EXATAMENTE UM entre `file_entry_id` e `omie_entry_id`. Nem zero,
          nem os dois. Anomalia órfã (zero) gera linha sem âncora no
          relatório Excel; anomalia com dois cria ambiguidade no JOIN.
        - `context` opcional, ≤ 2000 chars.
    """

    anomaly_type_id: UUID
    file_entry_id: UUID | None = None
    omie_entry_id: UUID | None = None
    context: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_xor(self) -> CreateAnomalyRequest:
        # XOR estrito (P1-004): a flexibilização "aceita zero" foi
        # introduzida durante a demo e nunca foi usada em cliente real.
        # Removida antes do deploy.
        has_file = self.file_entry_id is not None
        has_omie = self.omie_entry_id is not None
        if has_file and has_omie:
            raise ValueError(
                "Anomalia só pode referenciar UMA linha — envie file_entry_id "
                "OU omie_entry_id, não os dois."
            )
        if not has_file and not has_omie:
            raise ValueError(
                "Anomalia precisa estar vinculada a uma linha do arquivo "
                "(file_entry_id) OU a um lançamento Omie (omie_entry_id)."
            )
        return self


class CreateAnomalyResponse(BaseModel):
    data: AnomalyItem


# ----------------------------------------------------------------------
# BACK 9.9 — Resolver anomalia
# ----------------------------------------------------------------------


class ResolveAnomalyRequest(BaseModel):
    """Body do PATCH /api/v1/reconciliations/{id}/anomalies/{anomaly_id}.

    **Dois eixos independentes**, e é por isso que a Sprint 6 ESTENDEU este
    endpoint em vez de criar um paralelo (ADR-014):

    - `resolved` — alguém agiu sobre a anomalia. Quando `true`,
      `resolution_note` precisa ter ≥ 10 chars (Doc §17.3).
    - `review_verdict` — o flag *devia* ter sido levantado? É o dado que
      alimenta a métrica de outcome da Sprint 6, e só vale para flags da
      Camada 1 da qualificação (o servidor recusa os demais tipos).

    Os dois viraram OPCIONAIS: marcar um flag como improcedente sem resolvê-lo
    é o caminho comum, e resolver sem julgar continua valendo. Omitir um campo
    significa "não mexa nele" — o contrato antigo (`{resolved: bool}`) continua
    válido. Corpo vazio é erro: PATCH que não muda nada é bug do cliente.
    """

    resolved: bool | None = None
    resolution_note: str | None = Field(default=None, max_length=2000)
    review_verdict: AnomalyReviewVerdictLiteral | None = None

    @model_validator(mode="after")
    def _validate_note_when_resolved(self) -> ResolveAnomalyRequest:
        if self.resolved is None and self.review_verdict is None:
            raise ValueError(
                "Envie ao menos um entre `resolved` e `review_verdict` — não há o que atualizar."
            )
        if self.resolved:
            stripped = (self.resolution_note or "").strip()
            if len(stripped) < 10:
                raise ValueError(
                    "Para resolver uma anomalia, descreva o que foi feito em "
                    "pelo menos 10 caracteres."
                )
        return self


class ResolveAnomalyResponse(BaseModel):
    data: AnomalyItem
