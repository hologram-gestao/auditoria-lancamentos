"""Schemas Pydantic do módulo anomaly_types (S11 BACK 9.10 + S15 BACK 11.1).

Princípios:
    - `code` é IMUTÁVEL após criação (chave usada por matcher/seed/integradores).
      O PATCH não aceita `code`; o POST valida snake_case + length + unicidade.
    - `severity` valida contra `AnomalySeverity` enum no INPUT, mas é string no
      OUTPUT (lenient out — não derruba listagem se algum tipo legado tiver
      severity fora do enum por algum motivo).
    - `active` é controlado via PATCH (não há endpoints dedicados como em users)
      porque o estado-único do tipo justifica um PATCH atomico.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.db.models import AnomalySeverity
from app.modules.users.schemas import PaginationMeta

_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class AnomalyTypeItem(BaseModel):
    """Item retornado nas listagens e respostas de mutação."""

    id: UUID
    code: str
    name: str
    description: str
    severity: str
    active: bool = True

    model_config = {"from_attributes": True}


class AnomalyTypeListResponse(BaseModel):
    """Resposta NÃO paginada — preserva o contrato legado consumido pela tela
    de revisão (`/api/v1/anomaly-types` sem `?page`).

    O wrapper do front (`apiGet`) desempacota envelopes single-key `{ data }`
    automaticamente, então o consumidor recebe `AnomalyTypeItem[]` direto.
    """

    data: list[AnomalyTypeItem]


class AnomalyTypeListPaginatedResponse(BaseModel):
    """Resposta paginada — ativada quando o cliente passa `?page=...`.

    Envelope com 2 chaves (`data` + `pagination`) NÃO sofre auto-unwrap no
    cliente — admin UI lê os dois explicitamente.
    """

    data: list[AnomalyTypeItem]
    pagination: PaginationMeta


class AnomalyTypeCreate(BaseModel):
    """Body de POST /api/v1/anomaly-types — admin cria tipo custom (Fase 2)."""

    code: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="snake_case_lower (regex `^[a-z][a-z0-9_]*$`).",
    )
    name: str = Field(..., min_length=1, max_length=150)
    description: str = Field(..., min_length=1)
    severity: AnomalySeverity = Field(..., description="critical | moderate | info")
    active: bool = True

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if not _CODE_PATTERN.match(v):
            raise ValueError(
                "Code deve começar com letra minúscula e conter apenas "
                "[a-z0-9_] (snake_case_lower)."
            )
        return v


class AnomalyTypeUpdate(BaseModel):
    """Body de PATCH /api/v1/anomaly-types/{id} — só admin.

    `code` está deliberadamente AUSENTE do schema: é imutável. Pydantic
    silenciosamente ignora chaves extras no body (model_config padrão), então
    `code` no body do request não é processado nem lança erro 422 — o caller
    sabe que está agindo no ID, não no code.
    """

    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, min_length=1)
    severity: AnomalySeverity | None = None
    active: bool | None = None
