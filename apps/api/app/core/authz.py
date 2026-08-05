"""Autorização: UMA decisão de acesso, consultada por rota E camada de dados.

Sprint 5 (R2 + R4). Este módulo é o **único** lugar onde a regra de acesso vive:

    - `resolve_client_access(...)` — o usuário pode tocar no tenant alvo?
    - `tenant_filter_client_id(...)` — que `client_id` a camada de dados tem de
      forçar no `WHERE`? (defense-in-depth do R3 — a MESMA regra, derivada uma
      vez só, aplicada onde o dado é lido)
    - `PERMISSION_MATRIX` / `has_permission(...)` — a matriz permissão x papel
      do PRD, declarativa, sem `if role ==` copiado por rota.

**Por que aqui e não em `dependencies.py`:** `dependencies.py` importa deste
módulo (e não o contrário). Manter `CurrentUser` aqui evita o ciclo e deixa a
decisão testável sem subir FastAPI.

**A fonte da verdade é a LINHA, não o token.** `CurrentUser.scope`/`client_id`
vêm da linha que `get_current_user` já lê a cada request para checar `active` —
sem query nova e sem janela stale. O JWT também carrega `scope`/`client_id`
(para o front e para diagnóstico), mas quem decide é a linha: mover um usuário
de tenant vale já no PRÓXIMO request, com o token antigo.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, select

from app.db.models import ClientAssignment, UserRole, UserScope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute


class CurrentUser(BaseModel):
    """User autenticado e ATIVO no DB. Garantido por `get_current_user`.

    `scope`/`client_id` vêm da **linha** lida a cada request (a mesma que checa
    `active`) — não do token. Sem query nova e sem valor stale: mover um usuário
    de tenant, ou revogar o escopo, vale já no PRÓXIMO request (Sprint 5 / R2).
    """

    id: str  # users.id (UUID em string)
    email: str
    name: str
    role: str  # ver `app.db.models.UserRole`
    scope: str  # "system" | "client" — ver `app.db.models.UserScope`
    client_id: UUID | None  # tenant do usuário; None quando scope="system"

    @property
    def is_client_scoped(self) -> bool:
        """`True` quando o usuário pertence a um tenant (usuário DO cliente)."""
        return self.scope == UserScope.CLIENT.value


class Permission(StrEnum):
    """Ações da matriz do PRD (§4). Nomes canônicos — nunca strings mágicas."""

    RUN_RECONCILIATION = "run_reconciliation"
    REVIEW_EXPORT = "review_export"
    SYNC_OMIE_ACCOUNTS = "sync_omie_accounts"
    MANAGE_CLIENT_USERS = "manage_client_users"
    # Sprint 6 (BACK 06.3): manter o GLOSSÁRIO do tenant. Leitura não pede
    # permissão (todo papel com acesso ao tenant lê); escrita pede esta.
    MANAGE_GLOSSARY = "manage_glossary"
    EDIT_CLIENT = "edit_client"
    VIEW_OTHER_TENANT = "view_other_tenant"


#: A matriz permissão x papel do PRD (§4), declarativa e ÚNICA. Cada célula
#: ausente é um ❌ com teste de bloqueio correspondente.
#:
#: | Ação                          | client_manager | client_operator | admin | manager |
#: | ----------------------------- | -------------- | --------------- | ----- | ------- |
#: | Criar/rodar conciliação       | ✅             | ✅              | ✅    | ✅      |
#: | Revisar / exportar            | ✅             | ✅              | ✅    | ✅      |
#: | Sincronizar contas do Omie    | ✅             | ✅              | ✅    | ✅      |
#: | Gerir usuários do cliente     | ✅             | ❌              | ✅    | ❌      |
#: | Manter o glossário (S6)       | ✅             | ❌              | ✅    | ✅ (carteira) |
#: | Editar dados do cliente (§9)  | ❌             | ❌              | ✅    | ❌      |
#: | Ver outro tenant              | ❌             | ❌              | ✅    | ✅ (carteira) |
PERMISSION_MATRIX: dict[Permission, frozenset[UserRole]] = {
    Permission.RUN_RECONCILIATION: frozenset(
        {
            UserRole.ADMIN,
            UserRole.MANAGER,
            UserRole.CLIENT_MANAGER,
            UserRole.CLIENT_OPERATOR,
        }
    ),
    Permission.REVIEW_EXPORT: frozenset(
        {
            UserRole.ADMIN,
            UserRole.MANAGER,
            UserRole.CLIENT_MANAGER,
            UserRole.CLIENT_OPERATOR,
        }
    ),
    Permission.SYNC_OMIE_ACCOUNTS: frozenset(
        {
            UserRole.ADMIN,
            UserRole.MANAGER,
            UserRole.CLIENT_MANAGER,
            UserRole.CLIENT_OPERATOR,
        }
    ),
    Permission.MANAGE_CLIENT_USERS: frozenset({UserRole.ADMIN, UserRole.CLIENT_MANAGER}),
    # Glossário: gerente do cliente + equipe do sistema. O `manager` entra aqui
    # (diferente de MANAGE_CLIENT_USERS) porque o PRD da Sprint 6 pede "admin, e
    # manager dentro da carteira" — o "dentro da carteira" é
    # `resolve_client_access`, não esta linha. O `client_operator` só LÊ.
    Permission.MANAGE_GLOSSARY: frozenset(
        {UserRole.ADMIN, UserRole.MANAGER, UserRole.CLIENT_MANAGER}
    ),
    Permission.EDIT_CLIENT: frozenset({UserRole.ADMIN}),
    # "Ver outro tenant": só a equipe do sistema. O `manager` continua limitado
    # à carteira — isso é `resolve_client_access`, não esta linha da matriz.
    Permission.VIEW_OTHER_TENANT: frozenset({UserRole.ADMIN, UserRole.MANAGER}),
}


def has_permission(user: CurrentUser, permission: Permission) -> bool:
    """Consulta a matriz. Negado por padrão: papel desconhecido não passa."""
    try:
        role = UserRole(user.role)
    except ValueError:
        return False
    return role in PERMISSION_MATRIX[permission]


def tenant_filter_client_id(user: CurrentUser) -> UUID | None:
    """`client_id` que a camada de dados DEVE forçar no `WHERE` (R3).

    - Usuário `client` → o próprio tenant (**da linha**, nunca de URL/payload).
    - Usuário `system` → `None`: sem restrição por tenant nesta camada; o escopo
      dele é a carteira (`client_assignments`), aplicada por
      `resolve_client_access` / pelo filtro de carteira já existente.

    Derivada da MESMA regra de `resolve_client_access` — não é uma segunda
    implementação; é a mesma decisão projetada em `WHERE`.
    """
    return user.client_id if user.is_client_scoped else None


async def resolve_client_access(
    db: AsyncSession,
    user: CurrentUser,
    target_client_id: UUID,
) -> bool:
    """**A** decisão de acesso a um tenant. Rota e camada de dados consultam esta.

    Regras, em ordem:
        1. `scope='client'` → libera **apenas** o próprio `client_id` (da linha).
           Nunca consulta `client_assignments`: um usuário do cliente não tem
           carteira, tem tenant.
        2. `scope='system'` + `admin` → libera tudo.
        3. `scope='system'` + `manager` → libera se existe
           `client_assignments(client_id, user_id)` (regra atual, sem regressão).
        4. Qualquer outro papel/escopo → nega (negado por padrão).

    Retorna `True`/`False` e **não tem efeito colateral**: quem nega é o guard
    (`require_client_access`), que também grava a trilha. Assim a mesma função
    serve à camada de dados, que não deve auditar cada `SELECT`.
    """
    if user.is_client_scoped:
        return user.client_id is not None and user.client_id == target_client_id

    if user.role == UserRole.ADMIN.value:
        return True

    if user.role != UserRole.MANAGER.value:
        return False

    assignment = (
        await db.execute(
            select(ClientAssignment.id).where(
                ClientAssignment.client_id == target_client_id,
                ClientAssignment.user_id == UUID(user.id),
            )
        )
    ).scalar_one_or_none()
    return assignment is not None


def scoped_by_tenant(
    stmt: Select[Any],
    tenant_column: InstrumentedAttribute[UUID] | InstrumentedAttribute[UUID | None],
    user: CurrentUser,
) -> Select[Any]:
    """Aplica `AND <tenant_column> = <tenant do usuário>` ao `SELECT` (R3).

    Defense-in-depth: negar na rota é necessário, não suficiente — um endpoint
    novo que esqueça o guard vazaria. Com o filtro NO PRÓPRIO SELECT, o recurso
    de outro tenant simplesmente não é carregado (vira 404), mesmo sem guard.

    Para usuário `system` é **no-op**: o escopo dele é a carteira, aplicada por
    `resolve_client_access` — não há regressão para admin/manager.

    O tenant vem de `tenant_filter_client_id(user)`, ou seja, da LINHA do
    usuário — **nunca** de `client_id` vindo de URL ou payload.
    """
    tenant = tenant_filter_client_id(user)
    if tenant is None:
        return stmt
    return stmt.where(tenant_column == tenant)
