"""Schemas Pydantic do catálogo de categorias de cliente (86e34jd8m).

Princípios:
    - `tone` valida contra `ClientCategoryTone` no INPUT e sai como string
      (lenient out — um tom novo no banco não derruba a listagem).
    - `name` é normalizado com `strip()` no input; a unicidade sem caixa é
      regra do service (409 legível antes do IntegrityError).
    - `clients_count` só existe na leitura — é a informação que o admin precisa
      antes de excluir (FK RESTRICT ⇒ 409 quando > 0).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.db.models import MAX_CATEGORY_NAME_CHARS, ClientCategoryTone


class ClientCategoryItem(BaseModel):
    """Item do catálogo — listagem e respostas de mutação."""

    id: UUID
    name: str
    tone: str
    clients_count: int = Field(0, ge=0, description="Clientes vinculados hoje.")
    # 86e36ecqz — a organização dona: a coluna "Organização" da visão da
    # plataforma; para o staff de organização é sempre a própria.
    organization_id: UUID
    organization_name: str


class ClientCategoryListResponse(BaseModel):
    """Envelope single-key: o `apiGet` do front desempacota para `ClientCategoryItem[]`."""

    data: list[ClientCategoryItem]


def _clean_name(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Informe o nome da categoria.")
    return cleaned


class ClientCategoryCreate(BaseModel):
    """Body de POST /api/v1/client-categories — quem gere o catálogo."""

    name: str = Field(..., min_length=1, max_length=MAX_CATEGORY_NAME_CHARS)
    tone: ClientCategoryTone = ClientCategoryTone.NEUTRAL
    # 86e36ecqz — a plataforma ESCOLHE a organização (obrigatório para ela); o
    # admin de organização omite (a da LINHA) ou repete a própria — outro é 403.
    organization_id: UUID | None = Field(
        None,
        description=(
            "Organização dona da categoria. Obrigatória para a plataforma; para o admin "
            "de organização, omitir (usa a própria) ou repetir a própria."
        ),
    )

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        return _clean_name(v)


class ClientCategoryUpdate(BaseModel):
    """Body de PATCH /api/v1/client-categories/{id} — parcial, admin-only."""

    name: str | None = Field(None, min_length=1, max_length=MAX_CATEGORY_NAME_CHARS)
    tone: ClientCategoryTone | None = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)
