"""Contrato HTTP do catálogo de destinos e alvos (Sprint 12, BACK 12.3).

Forma inválida (tipo fora do formato de slug, código vazio, lote acima do teto,
`pageSize` > 100) é validação de FORMA: 400 `VALIDATION_ERROR` genérico do handler
global, nunca 422 (§4.8). O 422 deste domínio é só o do alvo inexistente numa
decisão (`MappingTargetNotFoundError`), que precisa NOMEAR o alvo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.mapping_catalog import (
    DESTINATION_TYPE_PATTERN,
    MAX_DESTINATION_NAME_CHARS,
    MAX_TARGET_CODE_CHARS,
    MAX_TARGET_NAME_CHARS,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.db.models.mapping_catalog import MappingDestination, MappingTarget

#: Teto de alvos por lote. O exemplo do PRD importa 14; o plano de contas real mais
#: longo visto tem ~130 categorias. 500 cobre um plano de demonstração inteiro numa
#: chamada sem virar vetor de carga.
MAX_TARGETS_PER_BATCH = 500


def _clean_text(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("vazio")
    return cleaned


class MappingDestinationItem(BaseModel):
    """Um destino do catálogo da organização."""

    id: UUID
    type: str = Field(description="Tipo do destino (slug, ex.: `demonstrativo_contabil`).")
    name: str
    active: bool
    organization_id: UUID = Field(alias="organizationId")
    targets_count: int = Field(ge=0, alias="targetsCount", description="Alvos cadastrados.")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def build(
        cls, destination: MappingDestination, *, targets_count: int
    ) -> MappingDestinationItem:
        return cls(
            id=destination.id,
            type=destination.destination_type,
            name=destination.name,
            active=destination.active,
            organization_id=destination.organization_id,
            targets_count=targets_count,
        )


class MappingDestinationListResponse(BaseModel):
    data: list[MappingDestinationItem]


class MappingDestinationEnvelope(BaseModel):
    data: MappingDestinationItem


class MappingDestinationCreate(BaseModel):
    """Cria destino na organização do ator (a plataforma escolhe, obrigatório)."""

    type: str = Field(
        pattern=DESTINATION_TYPE_PATTERN,
        description=(
            "Tipo do destino, em slug (minúsculas, dígitos e `_`). Os cinco do PRD "
            "já nascem em toda organização; um sexto é cadastro, não migração."
        ),
    )
    name: str = Field(min_length=1, max_length=MAX_DESTINATION_NAME_CHARS)
    organization_id: UUID | None = Field(
        default=None,
        alias="organizationId",
        description=(
            "Organização dona. Obrigatória para a plataforma; o admin omite (usa a "
            "própria) ou repete a própria — outra é 403."
        ),
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _clean_text(value)


class MappingDestinationUpdate(BaseModel):
    """Edita nome e/ou situação. O TIPO não muda (é a chave do de-para e da métrica)."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_DESTINATION_NAME_CHARS)
    active: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value)


class MappingTargetItem(BaseModel):
    """Um alvo do destino — código de catálogo + nome."""

    id: UUID
    code: str
    name: str
    active: bool

    @classmethod
    def build(cls, target: MappingTarget) -> MappingTargetItem:
        return cls(id=target.id, code=target.code, name=target.name, active=target.active)


class MappingTargetListResponse(BaseModel):
    data: list[MappingTargetItem]
    pagination: PaginationMeta


class MappingTargetEnvelope(BaseModel):
    data: MappingTargetItem


class MappingTargetCreate(BaseModel):
    code: str = Field(min_length=1, max_length=MAX_TARGET_CODE_CHARS)
    name: str = Field(min_length=1, max_length=MAX_TARGET_NAME_CHARS)

    model_config = ConfigDict(extra="forbid")

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ch.isspace() for ch in cleaned):
            raise ValueError("código sem espaço")
        return cleaned

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _clean_text(value)


class MappingTargetBatchCreate(BaseModel):
    """Lote de alvos (ex.: os 14 do demonstrativo). Atômico: tudo ou nada."""

    targets: list[MappingTargetCreate] = Field(min_length=1, max_length=MAX_TARGETS_PER_BATCH)

    model_config = ConfigDict(extra="forbid")


class MappingTargetBatchResponse(BaseModel):
    data: list[MappingTargetItem]


class MappingTargetUpdate(BaseModel):
    """Edita nome e/ou situação. O CÓDIGO não muda: é por ele que a importação casa
    e que a materialização guarda snapshot."""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_TARGET_NAME_CHARS)
    active: bool | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value)
