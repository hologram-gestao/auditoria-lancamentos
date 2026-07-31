"""Modelo User — usuários do sistema (Hologram) e usuários DE CLIENTE (tenant).

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §users.

CLAUDE.md §3 — RBAC:
    - admin: acesso total a todos os clientes.
    - manager: acesso apenas via `client_assignments`.

Sprint 5 (R1) — tenancy: a tabela foi ESTENDIDA (decisão fechada no PRD: estender,
não duplicar) com `scope` + `client_id`, e o enum de papel ganhou os papéis de
cliente. Não há segunda tabela nem segundo mecanismo de sessão:
    - `scope='system'` → equipe Hologram. `client_id` **nulo**; o escopo continua
      vindo de `client_assignments` (admin vê tudo, manager vê a carteira).
    - `scope='client'` → usuário DO cliente. `client_id` **obrigatório** e é o
      tenant do usuário; papéis `client_manager` / `client_operator`.

A integridade dessa correspondência é garantida por CHECK **no banco**
(`ck_users_scope_client_id`), não só na aplicação — o critério da sprint exige
que um INSERT/UPDATE inconsistente seja rejeitado pelo Postgres.

Senhas: hash bcrypt (cost ≥ 12) gerado por `app.core.security.hash_password`.
NUNCA armazenar senha em claro nem retornar `password_hash` em response.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    """Perfis de usuário — fonte ÚNICA (proibida string mágica em service/rota).

    Papéis de SISTEMA (equipe Hologram, `scope='system'`):
        - ADMIN: acesso total.
        - MANAGER: acesso pela carteira (`client_assignments`).

    Papéis de CLIENTE (`scope='client'`, Sprint 5 / R1+R4):
        - CLIENT_MANAGER: opera o próprio tenant + gere os usuários dele.
        - CLIENT_OPERATOR: opera o próprio tenant, sem gerir usuários.
    """

    ADMIN = "admin"
    MANAGER = "manager"
    CLIENT_MANAGER = "client_manager"
    CLIENT_OPERATOR = "client_operator"


class UserScope(StrEnum):
    """Escopo de tenancy do usuário — fonte ÚNICA (Sprint 5 / R1)."""

    SYSTEM = "system"
    CLIENT = "client"


class SystemUserRole(StrEnum):
    """Whitelist de papel aceita na API de usuários do SISTEMA (admin-only).

    Subconjunto de `UserRole` — os valores são referenciados, nunca redigitados.
    Existe como enum próprio (e não `Literal[...]`) para virar um componente
    NOMEADO no OpenAPI, consumível pelo front sem type inline.
    """

    ADMIN = UserRole.ADMIN.value
    MANAGER = UserRole.MANAGER.value


class ClientUserRole(StrEnum):
    """Whitelist de papel aceita na API de usuários DO CLIENTE (tenant).

    Impede escalação: um gerente de cliente que forje `role='admin'` recebe 422
    do próprio Pydantic, antes de qualquer regra de service.
    """

    CLIENT_MANAGER = UserRole.CLIENT_MANAGER.value
    CLIENT_OPERATOR = UserRole.CLIENT_OPERATOR.value


#: Papéis da equipe Hologram (`scope='system'`). Derivado — sem lista paralela.
SYSTEM_ROLES: frozenset[UserRole] = frozenset(UserRole(r.value) for r in SystemUserRole)
#: Papéis que só existem dentro de um tenant (`scope='client'`).
CLIENT_ROLES: frozenset[UserRole] = frozenset(UserRole(r.value) for r in ClientUserRole)

#: Label da CHECK constraint. A `NAMING_CONVENTION` do `Base` (app/db/base.py)
#: expande `ck` para `ck_%(table_name)s_%(constraint_name)s` — passar o nome já
#: prefixado geraria `ck_users_ck_users_...`.
SCOPE_CLIENT_ID_CK_LABEL = "scope_client_id"
#: Nome FINAL da constraint no banco (o que a migration cria e os testes checam).
SCOPE_CLIENT_ID_CONSTRAINT = f"ck_users_{SCOPE_CLIENT_ID_CK_LABEL}"
#: Predicado da CHECK constraint. Fonte única: modelo (create_all nos testes) e
#: migration importam/copiam daqui para não divergirem.
SCOPE_CLIENT_ID_CHECK = (
    "(scope = 'client' AND client_id IS NOT NULL) OR (scope = 'system' AND client_id IS NULL)"
)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserRole.MANAGER.value,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Tenancy (Sprint 5 / R1) -------------------------------------------
    # `server_default`: usuários criados por caminhos que não passam pelo ORM
    # (e as linhas pré-existentes, no backfill da migration) nascem 'system'.
    scope: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserScope.SYSTEM.value,
        server_default=UserScope.SYSTEM.value,
    )
    # `use_alter=True` + nome explícito: `users.client_id → clients.id` fecha um
    # CICLO de FK com `clients.created_by → users.id`. Sem isso o
    # `Base.metadata.create_all` (usado pelos testes) não consegue ordenar as
    # tabelas; com isso, a constraint é aplicada por ALTER TABLE depois do
    # CREATE, e o drop_all a remove antes.
    client_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "clients.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_users_client_id_clients",
        ),
        nullable=True,
        default=None,
        index=True,
    )

    __table_args__ = (CheckConstraint(SCOPE_CLIENT_ID_CHECK, name=SCOPE_CLIENT_ID_CK_LABEL),)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role} scope={self.scope}>"
