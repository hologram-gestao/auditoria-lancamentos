"""Schemas Pydantic do módulo de usuários (admin-only).

Princípios:
    - NUNCA expor `password_hash` em response.
    - `password` (criação) só vai em request, jamais em response.
    - `role` é validado contra os papéis de SISTEMA (`admin`/`manager`) — ver
      `SystemUserRole` abaixo.
    - Update é parcial (PATCH semantics): só campos enviados são alterados.
    - `active` muda apenas via endpoints dedicados /activate /deactivate
      (mais auditável e evita race com outros campos).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Whitelist de papel para ESTE módulo (usuários do SISTEMA, admin-only).
# A Sprint 5 ampliou `UserRole` com `client_manager`/`client_operator`; sem a
# whitelist, o admin poderia criar um usuário `scope='system'` carregando um
# papel de cliente — estado sem sentido que a CHECK do banco não pega (ela só
# cruza `scope` com `client_id`). Os papéis de cliente são criados
# exclusivamente pela API de usuários DO CLIENTE, com a whitelist simétrica.
from app.db.models import ClientUserRole, SystemUserRole


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
    role: SystemUserRole = Field(..., description="Perfil: admin ou manager.")


class UpdateUserRequest(BaseModel):
    """Body de PATCH /api/v1/users/{id} — campos opcionais (semântica PATCH parcial)."""

    name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None
    role: SystemUserRole | None = None


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


# ----------------------------------------------------------------------
# Usuários DO CLIENTE (tenant) — Sprint 5 / R5
# ----------------------------------------------------------------------

#: Mínimo de senha para usuário de cliente. O `hash_password` só trunca em 72
#: bytes — não impõe mínimo — e senha de usuário externo é superfície nova.
CLIENT_USER_MIN_PASSWORD_LENGTH = 10


class CreateClientUserRequest(BaseModel):
    """Body de POST /api/v1/clients/{client_id}/users.

    Note o que **não** está aqui: `client_id` e `scope`. Os dois são fixados
    pelo servidor a partir do tenant da rota — aceitar qualquer um deles no body
    seria o mesmo vetor de escalação que o `role`. Campo desconhecido é
    rejeitado (`extra="forbid"`), então enviá-los dá 422 em vez de ser ignorado
    em silêncio.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=150, description="Nome completo.")
    email: EmailStr = Field(..., description="E-mail único de login.")
    password: str = Field(
        ...,
        min_length=CLIENT_USER_MIN_PASSWORD_LENGTH,
        max_length=128,
        description=(
            f"Senha inicial definida pelo gerente do cliente. Mínimo de "
            f"{CLIENT_USER_MIN_PASSWORD_LENGTH} caracteres; hash bcrypt (cost ≥12)."
        ),
    )
    role: ClientUserRole = Field(
        ...,
        description="Papel dentro do cliente: client_manager ou client_operator.",
    )


class UpdateClientUserRequest(BaseModel):
    """Body de PATCH /api/v1/clients/{client_id}/users/{user_id} (parcial)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None
    role: ClientUserRole | None = None


class ClientUserResponse(BaseModel):
    """Usuário do cliente. NUNCA inclui `password_hash` nem a senha enviada."""

    id: UUID
    name: str
    email: str
    role: str  # value do StrEnum (client_manager | client_operator)
    scope: str
    client_id: UUID | None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ClientUserListResponse(BaseModel):
    """Body de GET /api/v1/clients/{client_id}/users — lista paginada."""

    data: list[ClientUserResponse]
    pagination: PaginationMeta
