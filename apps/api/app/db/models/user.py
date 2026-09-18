"""Modelo User — plataforma, staff de organização e usuários DE CLIENTE (tenant).

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §users.

CLAUDE.md §3 — RBAC:
    - admin: acesso total aos clientes da PRÓPRIA organização.
    - manager: acesso apenas via `client_assignments` (carteira, intra-org).

Camada de organizações (épico 86e36ec0q, task 86e36ec7p): a linha ganha
`organization_id`. Três escopos, com a consistência garantida por CHECK no banco
(`ck_users_scope_consistency`, que cruza scope x role x organization_id x
client_id):
    - `scope='platform'` → administração geral da ADL (`platform_admin`);
      `organization_id` e `client_id` NULOS. Vê e faz tudo em qualquer
      organização (decisão D1 revisada, 09/09/2026): é o acesso de suporte.
      Nasce só por script (`scripts/promote_platform_admin.py`, task
      86e36ecqz — a confirmar), nunca por endpoint: `platform_admin` não entra
      em nenhuma whitelist de API.
    - `scope='system'` → staff de UMA organização (`admin`/`manager`);
      `organization_id` obrigatório, `client_id` nulo.
    - `scope='client'` → usuário DO cliente; `organization_id` é a org do
      próprio cliente (DESNORMALIZADA de propósito — "listar usuários da minha
      org" vira um WHERE simples, sem join que alguém pode esquecer; o servidor
      a preenche a partir da linha do cliente) e `client_id` obrigatório.

Sprint 5 (R1) — tenancy: a tabela foi ESTENDIDA (decisão fechada no PRD: estender,
não duplicar) com `scope` + `client_id`, e o enum de papel ganhou os papéis de
cliente. Não há segunda tabela nem segundo mecanismo de sessão:
    - `scope='system'` → equipe Hologram. `client_id` **nulo**; o escopo continua
      vindo de `client_assignments` (admin vê tudo, manager vê a carteira).
    - `scope='client'` → usuário DO cliente. `client_id` **obrigatório** e é o
      tenant do usuário; papéis `client_manager` / `client_operator`.

A integridade dessa correspondência é garantida por CHECK **no banco**
(`ck_users_scope_consistency`, desde a camada de organizações), não só na aplicação — o critério da sprint exige
que um INSERT/UPDATE inconsistente seja rejeitado pelo Postgres.

Senhas: hash bcrypt (cost ≥ 12) gerado por `app.core.security.hash_password`.
NUNCA armazenar senha em claro nem retornar `password_hash` em response.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.organization import organization_id_server_default


class UserRole(StrEnum):
    """Perfis de usuário — fonte ÚNICA (proibida string mágica em service/rota).

    Papel de PLATAFORMA (`scope='platform'`, camada de organizações):
        - PLATFORM_ADMIN: vê e faz tudo em qualquer organização (suporte).

    Papéis de ORGANIZAÇÃO (`scope='system'` = staff de UM BPO):
        - ADMIN: acesso total aos clientes da própria organização.
        - MANAGER: acesso pela carteira (`client_assignments`), intra-org.

    Papéis de CLIENTE (`scope='client'`, Sprint 5 / R1+R4):
        - CLIENT_MANAGER: opera o próprio tenant + gere os usuários dele.
        - CLIENT_OPERATOR: opera o próprio tenant, sem gerir usuários.
    """

    PLATFORM_ADMIN = "platform_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    CLIENT_MANAGER = "client_manager"
    CLIENT_OPERATOR = "client_operator"


class UserScope(StrEnum):
    """Escopo de tenancy do usuário — fonte ÚNICA (Sprint 5 / R1 + organizações).

    A ordem dos ramos em `app.core.authz.resolve_client_access` segue esta
    hierarquia: cliente (só o próprio tenant), plataforma (tudo), organização
    (a própria org, pela carteira no caso do manager).
    """

    PLATFORM = "platform"
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


#: Papéis do staff de uma organização (`scope='system'`). Derivado — sem lista paralela.
SYSTEM_ROLES: frozenset[UserRole] = frozenset(UserRole(r.value) for r in SystemUserRole)
#: Papéis que só existem dentro de um tenant (`scope='client'`).
CLIENT_ROLES: frozenset[UserRole] = frozenset(UserRole(r.value) for r in ClientUserRole)
#: Papel da plataforma (`scope='platform'`). Sem whitelist de API de propósito:
#: só o script de promoção o atribui.
PLATFORM_ROLES: frozenset[UserRole] = frozenset({UserRole.PLATFORM_ADMIN})

#: Label da CHECK constraint. A `NAMING_CONVENTION` do `Base` (app/db/base.py)
#: expande `ck` para `ck_%(table_name)s_%(constraint_name)s` — passar o nome já
#: prefixado geraria `ck_users_ck_users_...`.
SCOPE_CONSISTENCY_CK_LABEL = "scope_consistency"
#: Nome FINAL da constraint no banco (o que a migration cria e os testes checam).
SCOPE_CONSISTENCY_CONSTRAINT = f"ck_users_{SCOPE_CONSISTENCY_CK_LABEL}"
#: Predicado da CHECK constraint — ternário (platform | system | client) e cruzando
#: também o PAPEL: um `platform_admin` com escopo de organização, ou um `admin`
#: com escopo de cliente, é recusado pelo Postgres, não só pelas whitelists do
#: Pydantic. Fonte única: modelo (create_all nos testes); a migration
#: `3e8f1a6c9d24` COPIA a string e `tests/unit/test_organization_schema.py`
#: compara as duas — e confere que cada literal é o valor de um membro de
#: `UserScope`/`UserRole` (renomear um enum sem tocar aqui é drift).
SCOPE_CONSISTENCY_CHECK = (
    "(scope = 'platform' AND role = 'platform_admin' "
    "AND organization_id IS NULL AND client_id IS NULL) "
    "OR (scope = 'system' AND role IN ('admin', 'manager') "
    "AND organization_id IS NOT NULL AND client_id IS NULL) "
    "OR (scope = 'client' AND role IN ('client_manager', 'client_operator') "
    "AND organization_id IS NOT NULL AND client_id IS NOT NULL)"
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

    # --- Organização (86e36ec7p) -------------------------------------------
    # Nullable porque a plataforma não tem organização; para `system`/`client`
    # o CHECK exige valor. `server_default` = Hologram: linha gravada sem o
    # campo é a forma ANTIGA da tabela (API antiga na janela de deploy, testes
    # que constroem `User(...)` sem org). Sem `default` no ORM de propósito —
    # o service passa a org da LINHA do ator. ⚠️ No INSERT, o SQLAlchemy OMITE
    # um atributo `None` quando a coluna tem `server_default` (o banco preenche
    # Hologram): para gravar um usuário de PLATAFORMA use `organization_id=null()`
    # (`sqlalchemy.null`); errar é LOUD, não silencioso — o CHECK recusa
    # `platform` com org. No UPDATE, `None` vira NULL normalmente (é o caminho
    # do script de promoção). FK RESTRICT: organização com usuários não some.
    organization_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        server_default=text(organization_id_server_default()),
    )

    __table_args__ = (CheckConstraint(SCOPE_CONSISTENCY_CHECK, name=SCOPE_CONSISTENCY_CK_LABEL),)

    def __repr__(self) -> str:
        return (
            f"<User id={self.id} email={self.email} role={self.role} scope={self.scope} "
            f"organization_id={self.organization_id}>"
        )
