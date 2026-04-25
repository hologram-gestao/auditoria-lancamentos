"""Schemas Pydantic do módulo de usuários (admin-only).

Princípios:
    - NUNCA expor `password_hash` em response.
    - `password` (criação) só vai em request, jamais em response.
    - `role` é validado contra `UserRole` enum (admin/manager).
    - Update é parcial (PATCH semantics): só campos enviados são alterados.
    - `active` muda apenas via endpoints dedicados /activate /deactivate
      (mais auditável e evita race com outros campos).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.db.models import UserRole


class CreateUserRequest(BaseModel):
    """Body de POST /api/v1/users — admin cria novo usuário."""

    name: str = Field(..., min_length=1, max_length=150, description="Nome completo.")
    email: EmailStr = Field(..., description="E-mail único de login.")
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Senha inicial em texto plano (bcrypt cost ≥12).",
    )
    role: UserRole = Field(..., description="Perfil: admin ou manager.")


class UpdateUserRequest(BaseModel):
    """Body de PATCH /api/v1/users/{id} — campos opcionais (semântica PATCH parcial)."""

    name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None
    role: UserRole | None = None


class UserResponse(BaseModel):
    """Representação pública de um usuário. NUNCA inclui `password_hash`.

    `email` é `str` (e não `EmailStr`) propositalmente: validação estrita acontece
    apenas no INPUT (CreateUserRequest/UpdateUserRequest). Aqui, qualquer linha
    no banco tem que ser serializável — caso contrário, um único registro com
    e-mail historicamente tolerado mas agora rejeitado pelo email-validator
    (e.g. TLDs reservados como `.local`/`.test`) derruba a listagem inteira.
    """

    id: UUID  # serializado como string em JSON
    name: str
    email: str
    role: str  # value do StrEnum
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PaginationMeta(BaseModel):
    """Metadados de paginação. Compartilhado entre módulos no futuro."""

    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100, alias="pageSize")
    total: int = Field(..., ge=0)
    total_pages: int = Field(..., ge=0, alias="totalPages")

    model_config = {"populate_by_name": True}


class UserListResponse(BaseModel):
    """Body de GET /api/v1/users — lista paginada."""

    data: list[UserResponse]
    pagination: PaginationMeta
