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
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Whitelist de papel para ESTE módulo (usuários do SISTEMA, admin-only).
# A Sprint 5 ampliou `UserRole` com `client_manager`/`client_operator`; sem a
# whitelist, o admin poderia criar um usuário `scope='system'` carregando um
# papel de cliente — estado sem sentido que a CHECK do banco não pega (ela só
# cruza `scope` com `client_id`). Os papéis de cliente são criados
# exclusivamente pela API de usuários DO CLIENTE, com a whitelist simétrica.
from app.db.models import ClientUserRole, SystemUserRole

if TYPE_CHECKING:
    from app.db.models import User


class CreateUserRequest(BaseModel):
    """Body de POST /api/v1/users — cria staff da organização."""

    name: str = Field(..., min_length=1, max_length=150, description="Nome completo.")
    email: EmailStr = Field(..., description="E-mail único de login.")
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Senha inicial em texto plano (bcrypt cost ≥12).",
    )
    role: SystemUserRole = Field(..., description="Perfil: admin ou manager.")
    # Camada de organizações (86e36ecqz): a plataforma ESCOLHE onde o staff
    # nasce (obrigatório para ela). Para o admin de organização, ou é omitido
    # (a org da LINHA do ator) ou é a própria org — outro valor é 403.
    organization_id: UUID | None = Field(
        None,
        description=(
            "Organização do novo usuário. Obrigatória para a plataforma; para o admin "
            "de organização, omitir (usa a própria) ou repetir a própria."
        ),
    )


class UpdateUserRequest(BaseModel):
    """Body de PATCH /api/v1/users/{id} — campos opcionais (semântica PATCH parcial)."""

    name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None
    role: SystemUserRole | None = None


class TransferUserRequest(BaseModel):
    """Body de POST /api/v1/users/{id}/transfer — só plataforma (86e3bvbfx).

    Só a organização de destino. O papel NÃO é campo: admin continua admin e
    gerente continua gerente na organização nova — trocar papel é o PATCH.
    """

    organization_id: UUID = Field(..., description="Organização de destino (existe e ativa).")


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
    scope: str  # sempre "system" aqui: a listagem é só de staff (86e36ecqz)
    active: bool
    created_at: datetime
    updated_at: datetime
    # Camada de organizações (86e36ecqz): a coluna "Organização" da visão da
    # plataforma. Para o admin de organização é sempre a própria.
    organization_id: UUID | None = None
    organization_name: str | None = None

    @classmethod
    def from_staff(cls, user: User, *, organization_name: str | None) -> UserResponse:
        """Monta a response a partir da LINHA + o nome da org lido no mesmo SELECT.

        Explícito (e não `model_validate(user)`): o nome da organização não é
        atributo do `User` (não há relationship `User.organization`; o nome vem
        por join no repositório), e nada aqui pode cair num acesso preguiçoso
        dentro da serialização.
        """
        return cls(
            id=user.id,
            name=user.name,
            email=user.email,
            role=user.role,
            scope=user.scope,
            active=user.active,
            created_at=user.created_at,
            updated_at=user.updated_at,
            organization_id=user.organization_id,
            organization_name=organization_name,
        )


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
