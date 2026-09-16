"""Autorização: UMA decisão de acesso, consultada por rota E camada de dados.

Sprint 5 (R2 + R4) + camada de organizações (épico 86e36ec0q, task 86e36ecar).
Este módulo é o **único** lugar onde a regra de acesso vive:

    - `resolve_client_access(...)` — o usuário pode tocar no cliente alvo?
    - `tenant_filter_client_id(...)` — que `client_id` a camada de dados tem de
      forçar no `WHERE`? (defense-in-depth do R3 — a MESMA regra, derivada uma
      vez só, aplicada onde o dado é lido)
    - `reach_filter(...)` / `scoped_by_reach(...)` — a MESMA decisão projetada em
      `WHERE` para coleções endereçadas por `client_id` (lista de clientes,
      notificações, sessões): plataforma vê tudo, admin vê a própria organização,
      manager vê a carteira, cliente vê o próprio tenant.
    - `scoped_by_organization(...)` — o mesmo alcance para tabelas que carregam
      a organização diretamente (`users`, `client_categories`, `clients`).
    - `PERMISSION_MATRIX` / `has_permission(...)` — a matriz permissão x papel
      do PRD, declarativa, sem `if role ==` copiado por rota.

**Por que aqui e não em `dependencies.py`:** `dependencies.py` importa deste
módulo (e não o contrário). Manter `CurrentUser` aqui evita o ciclo e deixa a
decisão testável sem subir FastAPI.

**A fonte da verdade é a LINHA, não o token.** `CurrentUser.scope`/`client_id`/
`organization_id` vêm da linha que `get_current_user` já lê a cada request para
checar `active` — sem query nova e sem janela stale. O JWT também carrega os
três (para o front e para diagnóstico), mas quem decide é a linha: mover um
usuário de tenant ou de organização vale já no PRÓXIMO request, com o token
antigo.

**Três escopos, nesta precedência:** `client` (só o próprio tenant), `platform`
(tudo — é o acesso de suporte, decisão D1 revisada em 09/09/2026), `system`
(staff de UMA organização: admin alcança a org inteira, manager a carteira,
sempre dentro da org). Esquecer a organização no ramo do admin é vazamento
entre BPOs concorrentes — é o equivalente exato do vazamento cross-tenant que a
Sprint 5 fechou, uma camada acima.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, and_, false, select
from sqlalchemy.orm import aliased

from app.db.models import Client, ClientAssignment, UserRole, UserScope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute


class CurrentUser(BaseModel):
    """User autenticado e ATIVO no DB. Garantido por `get_current_user`.

    `scope`/`client_id`/`organization_id` vêm da **linha** lida a cada request (a
    mesma que checa `active`) — não do token. Sem query nova e sem valor stale:
    mover um usuário de tenant ou de organização, ou revogar o escopo, vale já
    no PRÓXIMO request (Sprint 5 / R2).
    """

    id: str  # users.id (UUID em string)
    email: str
    name: str
    role: str  # ver `app.db.models.UserRole`
    scope: str  # "platform" | "system" | "client" — ver `app.db.models.UserScope`
    client_id: UUID | None  # tenant do usuário; None fora de scope="client"
    #: Organização do usuário. `None` SÓ para a plataforma; para `system`/`client`
    #: o CHECK do banco exige valor — `None` aqui é linha corrompida, e todo
    #: filtro abaixo a trata como "não alcança nada" (negado por padrão).
    organization_id: UUID | None = None

    @property
    def is_client_scoped(self) -> bool:
        """`True` quando o usuário pertence a um tenant (usuário DO cliente)."""
        return self.scope == UserScope.CLIENT.value

    @property
    def is_platform(self) -> bool:
        """`True` para a plataforma **bem formada**: escopo `platform` SEM
        organização e SEM tenant. Uma linha `platform` que carregue um dos dois
        é corrompida (o CHECK do banco a recusa) e NÃO ganha o alcance total —
        negado por padrão, como na precedência escopo > papel."""
        return (
            self.scope == UserScope.PLATFORM.value
            and self.organization_id is None
            and self.client_id is None
        )

    @property
    def is_staff(self) -> bool:
        """Plataforma ou staff de organização — quem NÃO é usuário de cliente.

        É o predicado das leituras de staff (lista de clientes, catálogos): não
        decide alcance (isso é `resolve_client_access`/`reach_filter`), só
        separa "opera clientes" de "é cliente".
        """
        return self.is_platform or self.scope == UserScope.SYSTEM.value


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
    # --- Camada de organizações (86e36ecar) ---------------------------------
    #: Criar cliente. Formaliza o que `POST /clients` já aceitava (admin e
    #: manager; o manager criador vira o responsável).
    CREATE_CLIENT = "create_client"
    #: Gerir os usuários de STAFF da organização (`/api/v1/users`) — o que era
    #: "admin-only global" passa a ser "admin da própria org, ou plataforma".
    MANAGE_ORG_USERS = "manage_org_users"
    #: Escrever no catálogo de categorias de cliente (por organização, D3).
    MANAGE_CLIENT_CATEGORIES = "manage_client_categories"
    #: Escrever no catálogo de tipos de anomalia (taxonomia do produto, D3).
    MANAGE_ANOMALY_TYPES = "manage_anomaly_types"
    #: Administrar organizações (CRUD de `organizations`): só a plataforma.
    MANAGE_PLATFORM = "manage_platform"
    #: Disparar o alerta sintético (`POST /system/alert-test`). Fica com o admin
    #: da org também: o smoke do deploy loga como o admin de monitoração.
    RUN_ALERT_TEST = "run_alert_test"


_EVERYONE: frozenset[UserRole] = frozenset(UserRole)
_STAFF: frozenset[UserRole] = frozenset({UserRole.PLATFORM_ADMIN, UserRole.ADMIN, UserRole.MANAGER})
_ADMINS: frozenset[UserRole] = frozenset({UserRole.PLATFORM_ADMIN, UserRole.ADMIN})
_PLATFORM_ONLY: frozenset[UserRole] = frozenset({UserRole.PLATFORM_ADMIN})

#: A matriz permissão x papel do PRD (§4) com a camada de organizações,
#: declarativa e ÚNICA. Cada célula ausente é um ❌ com teste de bloqueio
#: correspondente. **A plataforma está em TODA linha** (D1 revisada) — e um
#: teste unitário trava isso: permissão nova sem a célula da plataforma quebra.
#:
#: "(carteira)" e "(própria org)" NÃO são células: são `resolve_client_access`
#: e os filtros de coleção. A célula diz se o papel pode a AÇÃO; o alcance é
#: outra função.
#:
#: | Ação                          | platform_admin | admin (org) | manager (org)  | client_manager | client_operator |
#: | ----------------------------- | -------------- | ----------- | -------------- | -------------- | --------------- |
#: | Criar/rodar conciliação       | ✅             | ✅          | ✅             | ✅             | ✅              |
#: | Revisar / exportar            | ✅             | ✅          | ✅             | ✅             | ✅              |
#: | Sincronizar contas do Omie    | ✅             | ✅          | ✅             | ✅             | ✅              |
#: | Manter o glossário (S6)       | ✅             | ✅          | ✅ (carteira)  | ✅             | ❌              |
#: | Gerir usuários do cliente     | ✅             | ✅          | ❌ (D2: task 3)| ✅             | ❌              |
#: | Criar cliente                 | ✅             | ✅          | ✅ (vira resp.)| ❌             | ❌              |
#: | Editar dados do cliente (§9)  | ✅             | ✅          | ❌             | ❌             | ❌              |
#: | Ver outro tenant              | ✅             | ✅ (org)    | ✅ (carteira)  | ❌             | ❌              |
#: | Gerir usuários da org         | ✅             | ✅ (org)    | ❌             | ❌             | ❌              |
#: | Categorias de cliente (esc.)  | ✅             | ✅ (org)    | ❌             | ❌             | ❌              |
#: | Tipos de anomalia (escrita)   | ✅             | ✅ (*)      | ❌             | ❌             | ❌              |
#: | Gerir organizações            | ✅             | ❌          | ❌             | ❌             | ❌              |
#: | Teste de alerta               | ✅             | ✅          | ❌             | ❌             | ❌              |
#:
#: (*) D3 final é "só plataforma". O admin fica na célula até a tela de tipos
#: de anomalia virar só-plataforma (onda 2, task 86e36ed1d) — tirar antes
#: deixaria a tela atual mostrando botões que o servidor nega (§4.9), e a
#: segunda organização, que é o que a D3 protege, só nasce na onda 3.
PERMISSION_MATRIX: dict[Permission, frozenset[UserRole]] = {
    Permission.RUN_RECONCILIATION: _EVERYONE,
    Permission.REVIEW_EXPORT: _EVERYONE,
    Permission.SYNC_OMIE_ACCOUNTS: _EVERYONE,
    # D2 (task 86e36ecjp) acrescenta o `manager` aqui, dentro da carteira.
    Permission.MANAGE_CLIENT_USERS: frozenset(
        {UserRole.PLATFORM_ADMIN, UserRole.ADMIN, UserRole.CLIENT_MANAGER}
    ),
    # Glossário: gerente do cliente + staff. O `manager` entra aqui
    # (diferente de MANAGE_CLIENT_USERS) porque o PRD da Sprint 6 pede "admin, e
    # manager dentro da carteira" — o "dentro da carteira" é
    # `resolve_client_access`, não esta linha. O `client_operator` só LÊ.
    Permission.MANAGE_GLOSSARY: frozenset(
        {UserRole.PLATFORM_ADMIN, UserRole.ADMIN, UserRole.MANAGER, UserRole.CLIENT_MANAGER}
    ),
    Permission.EDIT_CLIENT: _ADMINS,
    # "Ver outro tenant": só staff. O `manager` continua limitado à carteira e o
    # `admin` à própria organização — isso é `resolve_client_access`, não esta
    # linha da matriz.
    Permission.VIEW_OTHER_TENANT: _STAFF,
    Permission.CREATE_CLIENT: _STAFF,
    Permission.MANAGE_ORG_USERS: _ADMINS,
    Permission.MANAGE_CLIENT_CATEGORIES: _ADMINS,
    Permission.MANAGE_ANOMALY_TYPES: _ADMINS,
    Permission.MANAGE_PLATFORM: _PLATFORM_ONLY,
    Permission.RUN_ALERT_TEST: _ADMINS,
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
    - Plataforma ou staff de org → `None`: sem restrição por tenant nesta
      camada; o alcance deles é a organização/carteira, aplicada por
      `resolve_client_access` / `reach_filter`.

    Derivada da MESMA regra de `resolve_client_access` — não é uma segunda
    implementação; é a mesma decisão projetada em `WHERE`.
    """
    return user.client_id if user.is_client_scoped else None


def organization_client_filter(
    organization_id: UUID,
    client_id_column: InstrumentedAttribute[UUID] | InstrumentedAttribute[UUID | None],
) -> ColumnElement[bool]:
    """`EXISTS` de ORGANIZAÇÃO para coleções endereçadas por `client_id`.

    "O cliente desta linha é da organização X" — é a regra 3 de
    `resolve_client_access` (admin alcança a própria org) projetada em `WHERE`.
    Tabelas por sessão/notificação não carregam a org; a derivação é sempre via
    `clients.organization_id`, aqui.
    """
    owner = aliased(Client)
    return (
        select(owner.id)
        .where(owner.id == client_id_column, owner.organization_id == organization_id)
        .exists()
    )


async def resolve_client_access(
    db: AsyncSession,
    user: CurrentUser,
    target_client_id: UUID,
    *,
    target_organization_id: UUID | None = None,
) -> bool:
    """**A** decisão de acesso a um cliente. Rota e camada de dados consultam esta.

    Regras, em ordem:
        1. `scope='client'` → libera **apenas** o próprio `client_id` (da linha).
           Nunca consulta `client_assignments`: um usuário do cliente não tem
           carteira, tem tenant.
        2. Plataforma bem formada → libera, sem consulta (D1 revisada: é o
           acesso de suporte).
        3. Staff sem organização (linha corrompida) → nega.
        4. O cliente alvo tem de ser **da organização do ator** — vale para
           `admin` e `manager`. Esquecer este passo é vazamento entre BPOs.
           `target_organization_id` evita a query quando o caller já carregou
           o `Client` (`require_client_access`); senão, 1 SELECT por PK.
        5. `admin` → libera (alcança a org inteira).
        6. `manager` → libera se existe `client_assignments(client_id, user_id)`
           (regra da carteira, sem regressão).
        7. Qualquer outro papel/escopo → nega (negado por padrão).

    Retorna `True`/`False` e **não tem efeito colateral**: quem nega é o guard
    (`require_client_access`), que também grava a trilha. Assim a mesma função
    serve à camada de dados, que não deve auditar cada `SELECT`.
    """
    if user.is_client_scoped:
        return user.client_id is not None and user.client_id == target_client_id

    if user.is_platform:
        return True

    if user.scope != UserScope.SYSTEM.value or user.organization_id is None:
        return False

    if target_organization_id is None:
        target_organization_id = await db.scalar(
            select(Client.organization_id).where(Client.id == target_client_id)
        )
    if target_organization_id is None or target_organization_id != user.organization_id:
        return False

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

    Para plataforma e staff de org é **no-op**: o alcance deles é a
    organização/carteira, aplicada por `resolve_client_access` no detalhe por PK
    e por `scoped_by_reach` nas coleções.

    O tenant vem de `tenant_filter_client_id(user)`, ou seja, da LINHA do
    usuário — **nunca** de `client_id` vindo de URL ou payload.
    """
    tenant = tenant_filter_client_id(user)
    if tenant is None:
        return stmt
    return stmt.where(tenant_column == tenant)


def scoped_by_organization(
    stmt: Select[Any],
    organization_column: InstrumentedAttribute[UUID] | InstrumentedAttribute[UUID | None],
    user: CurrentUser,
) -> Select[Any]:
    """Aplica `AND <organization_column> = <org do usuário>` ao `SELECT`.

    Para tabelas que carregam a organização diretamente (`users`,
    `client_categories`, `clients`). As MESMAS respostas de `reach_filter`, para
    a mesma pergunta feita a outra tabela: plataforma bem formada → no-op;
    plataforma corrompida (com org ou tenant) ou staff sem org → `WHERE false`,
    nunca "todas"; demais → a org **da linha** (a do usuário de cliente é
    desnormalizada de propósito, §4.8).
    """
    if user.is_platform:
        return stmt
    if user.scope == UserScope.PLATFORM.value or user.organization_id is None:
        return stmt.where(false())
    return stmt.where(organization_column == user.organization_id)


def portfolio_filter(
    user_id: UUID,
    client_id_column: InstrumentedAttribute[UUID] | InstrumentedAttribute[UUID | None],
) -> ColumnElement[bool]:
    """`EXISTS` de CARTEIRA para coleções que o manager vê pela carteira (§4.13).

    É a regra 6 de `resolve_client_access` projetada em `WHERE` — a lista de
    clientes e as notificações consultam ESTA função (via `reach_filter`), não
    uma cópia local do `EXISTS`. Qualquer linha `client_assignments(client_id,
    user_id)` concede: responsável ou colaborador. Se a regra da carteira mudar
    (revogação com data, concessão por organização), muda aqui e as coleções
    seguem juntas.
    """
    portfolio = aliased(ClientAssignment)
    return (
        select(portfolio.id)
        .where(portfolio.client_id == client_id_column, portfolio.user_id == user_id)
        .exists()
    )


def reach_filter(
    user: CurrentUser,
    client_id_column: InstrumentedAttribute[UUID] | InstrumentedAttribute[UUID | None],
) -> ColumnElement[bool] | None:
    """A decisão de `resolve_client_access` projetada em `WHERE` para uma coleção
    endereçada por `client_id` (clientes, notificações, sessões).

    - usuário de cliente → `client_id = <tenant da linha>`;
    - plataforma → `None` (sem filtro: vê tudo);
    - `admin` → cliente da própria organização;
    - `manager` → cliente da carteira E da própria organização
      (defense-in-depth: a carteira já nasce intra-org);
    - qualquer outra coisa (papel desconhecido, staff sem org) → `false`.

    Devolve a condição (ou `None`) em vez de aplicá-la porque a lista de
    clientes precisa pôr o mesmo predicado no SELECT da página E no `count`.
    """
    if user.is_client_scoped:
        if user.client_id is None:
            return false()
        return client_id_column == user.client_id

    if user.is_platform:
        return None

    if user.scope != UserScope.SYSTEM.value or user.organization_id is None:
        return false()

    same_org = organization_client_filter(user.organization_id, client_id_column)
    if user.role == UserRole.ADMIN.value:
        return same_org
    if user.role == UserRole.MANAGER.value:
        return and_(portfolio_filter(UUID(user.id), client_id_column), same_org)
    return false()


def scoped_by_reach(
    stmt: Select[Any],
    client_id_column: InstrumentedAttribute[UUID] | InstrumentedAttribute[UUID | None],
    user: CurrentUser,
) -> Select[Any]:
    """`stmt` restrito ao que o usuário alcança (ver `reach_filter`)."""
    condition = reach_filter(user, client_id_column)
    if condition is None:
        return stmt
    return stmt.where(condition)
