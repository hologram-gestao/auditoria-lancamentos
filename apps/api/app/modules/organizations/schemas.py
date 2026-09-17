"""Schemas Pydantic do módulo de organizações (camada de organizações, 86e36ecnp).

Princípios:
    - `name` é normalizado com `strip()`/colapso de espaços no input; a unicidade
      sem caixa é regra do service (409 legível antes do IntegrityError).
    - `clients_count`/`users_count` só existem na leitura — é o que a plataforma
      precisa ver antes de suspender uma organização.
    - Nenhum dado de cliente final aparece aqui: nome do BPO é dado de negócio da
      plataforma (§4.5 do primer cobre o cliente final, não o escritório).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.db.models import MAX_ORGANIZATION_NAME_CHARS
from app.modules.users.schemas import PaginationMeta


class OrganizationItem(BaseModel):
    """Uma organização — listagem, detalhe e respostas de mutação."""

    id: UUID
    name: str
    active: bool
    clients_count: int = Field(0, ge=0, description="Clientes que pertencem à organização.")
    users_count: int = Field(0, ge=0, description="Staff (admin/manager) da organização.")
    created_at: datetime
    updated_at: datetime


class OrganizationListResponse(BaseModel):
    """Body de GET /api/v1/organizations — lista paginada."""

    data: list[OrganizationItem]
    pagination: PaginationMeta


def _clean_name(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Informe o nome da organização.")
    return cleaned


class OrganizationCreate(BaseModel):
    """Body de POST /api/v1/organizations — só plataforma."""

    name: str = Field(..., min_length=1, max_length=MAX_ORGANIZATION_NAME_CHARS)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        return _clean_name(v)


class OrganizationUpdate(BaseModel):
    """Body de PATCH /api/v1/organizations/{id} — parcial, só plataforma.

    `active=false` SUSPENDE a organização: os usuários dela recebem 401 no
    request seguinte (`get_current_user` lê a organização junto com a linha).
    """

    name: str | None = Field(None, min_length=1, max_length=MAX_ORGANIZATION_NAME_CHARS)
    active: bool | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)
