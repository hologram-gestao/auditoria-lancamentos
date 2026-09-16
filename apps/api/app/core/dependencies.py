"""Dependencies do FastAPI compartilhadas — auth, RBAC, settings, DB.

Use sempre via `Depends(...)` em rotas. **Proibido** acessar `session` global,
`Settings()` direto ou JWT manualmente fora destas funções.

Hoje:
    - `get_settings` (em `app.core.config`)
    - `DbSessionDep` — sessão SQLAlchemy async com rollback automático
    - `get_current_user` — extrai JWT do cookie + valida `users.active = true`
      e organização ativa no DB
    - guards por PERMISSÃO da matriz (`*Dep` abaixo) e `StaffDep` (plataforma ou
      staff de organização — leituras de staff)
    - `require_client_access(client_id)` — guard de tenant/organização/carteira

Sprint 5 (R2 + R4) + camada de organizações: a REGRA de acesso mora em
`app.core.authz` (`resolve_client_access` + `PERMISSION_MATRIX`). Aqui ficam só
os **guards** FastAPI que a consultam e o efeito colateral de negar (403 +
trilha). Proibida segunda implementação da regra fora do `authz` — os antigos
`require_admin`/`require_manager_or_admin` (comparação de string) saíram de
propósito: `platform_admin` não pode casar com um literal por acidente.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_cross_tenant_denied
from app.core.authz import (
    CurrentUser,
    Permission,
    has_permission,
    resolve_client_access,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ClientClosedError,
    ClientNotAccessibleError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.models import Client
from app.db.session import get_db_session
from app.modules.auth.repository import AuthRepository

# Nomes dos cookies HttpOnly — nomes de cookie, não credenciais.
ACCESS_TOKEN_COOKIE = "access_token"  # noqa: S105
REFRESH_TOKEN_COOKIE = "refresh_token"  # noqa: S105

# Sessão DB por request. Use em rotas: `db: DbSessionDep`.
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    settings: SettingsDep,
    db: DbSessionDep,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
) -> CurrentUser:
    """Extrai o usuário atual do cookie HttpOnly `access_token`.

    Validações em ordem:
        1. Cookie presente.
        2. JWT válido (assinatura, formato, type=access, não expirado).
        3. **`users.active = true` no DB** — query a cada request (CLAUDE.md §3.12).
           Usuário desativado pelo Admin perde acesso instantaneamente, mesmo com
           JWT vivo até a expiração natural.
        4. **Organização ativa** (camada de organizações): a MESMA query traz
           `organizations.active`; suspender a organização derruba os usuários
           dela no request seguinte. A plataforma não tem organização.

    Erros possíveis:
        - 401 `UNAUTHORIZED`: cookie ausente, JWT inválido, user inativo/inexistente,
          organização suspensa.
        - 401 `TOKEN_EXPIRED`: assinatura ok mas `exp` no passado
          (frontend deve tentar `/api/v1/auth/refresh`).
    """
    if not access_token:
        raise UnauthorizedError("Cookie de acesso ausente.")

    payload = decode_token(access_token, settings, expected_type=TOKEN_TYPE_ACCESS)

    try:
        user_id = UUID(payload.sub)
    except ValueError as exc:
        raise UnauthorizedError("Sub do token inválido.") from exc

    ctx = await AuthRepository(db).get_auth_context_by_id(user_id)
    if ctx is None or not ctx.user.active or ctx.organization_active is False:
        # Mensagem única para os três casos: não vazar se a conta existe, está
        # desativada, ou se a organização inteira foi suspensa.
        raise UnauthorizedError("Sessão expirou ou usuário inativo.")

    user = ctx.user
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        # Da LINHA, não do token — ver docstring de `CurrentUser`.
        scope=user.scope,
        client_id=user.client_id,
        organization_id=user.organization_id,
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_staff(user: CurrentUserDep) -> CurrentUser:
    """Plataforma ou staff de organização — recusa usuário de cliente com 403.

    Guard de LEITURA de staff (lista de clientes, catálogos, testar credenciais).
    Não decide alcance: isso é `resolve_client_access`/`reach_filter` na query.
    """
    if not user.is_staff:
        raise ForbiddenError("Acesso negado.")
    return user


StaffDep = Annotated[CurrentUser, Depends(require_staff)]


async def deny_client_access(db: AsyncSession, user: CurrentUser, client_id: UUID) -> None:
    """Efeito colateral de negar um tenant: trilha + eventos, depois 403.

    Separado de `resolve_client_access` (a DECISÃO, pura) de propósito: a camada
    de dados consulta a decisão a cada `SELECT` e não deve auditar nada; o guard
    de rota audita. Um caminho de gravação só (`record_cross_tenant_denied`).
    """
    # A trilha é gravada ANTES da conversão 403→404 anti-enumeração das rotas de
    # leitura — auditoria e anti-enumeração convivem (CONTEXT.md). A `rota` sai
    # dos contextvars do structlog.
    await record_cross_tenant_denied(
        db,
        user_id=UUID(user.id),
        user_scope=user.scope,
        actor_client_id=user.client_id,
        actor_organization_id=user.organization_id,
        target_client_id=client_id,
    )
    # Mensagem sem NENHUM dado do tenant alvo (nada de nome/razão social/CNPJ) —
    # só IDs, que o requisitante já conhece.
    raise ClientNotAccessibleError(
        f"Usuário {user.id} (scope={user.scope}) tentou acessar cliente {client_id} "
        "fora do seu escopo.",
    )


async def require_client_access(
    client_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Client:
    """Guard de tenant. Delega a decisão a `authz.resolve_client_access`.

    - `scope='client'` → só o próprio tenant.
    - plataforma → tudo.
    - `scope='system'` → cliente da própria organização (admin) ou da carteira
      dentro dela (manager).

    Retorna o `Client` carregado para evitar uma 2ª query no service — e passa a
    organização dele à decisão, que assim não repete o SELECT. Erros:
        - 404 NOT_FOUND: cliente inexistente.
        - 403 FORBIDDEN: fora do escopo (a rota de leitura converte para 404).

    **Não existe segunda implementação da regra** — este guard só executa o
    efeito colateral (`deny_client_access`) quando a decisão vem `False`.
    """
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if client is None:
        raise NotFoundError("Cliente não encontrado.")

    if not await resolve_client_access(
        db, user, client_id, target_organization_id=client.organization_id
    ):
        await deny_client_access(db, user, client_id)
    return client


AccessibleClientDep = Annotated[Client, Depends(require_client_access)]


async def require_open_client(client: AccessibleClientDep) -> Client:
    """Guard de ESCRITA: além do acesso ao tenant, o cliente precisa estar ABERTO.

    Cliente ENCERRADO (86e36pm1z — `clients.closed_at` preenchido) é só-leitura:
    a DEK foi destruída e as credenciais Omie removidas, então toda escrita é
    recusada com 409 ANTES de tocar em qualquer coisa — inclusive o
    provisionamento lazy de DEK (`crypto_service`), que re-embrulharia uma DEK
    nova num tenant cujo conteúdo já morreu. Rotas de LEITURA continuam com
    `AccessibleClientDep`: o histórico operacional fica disponível.
    """
    if client.closed_at is not None:
        raise ClientClosedError(
            f"Cliente {client.id} está encerrado desde {client.closed_at.isoformat()}."
        )
    return client


OpenClientDep = Annotated[Client, Depends(require_open_client)]


def require_permission(permission: Permission) -> Callable[[CurrentUser], CurrentUser]:
    """Fábrica de guard por permissão — consulta a MATRIZ, nunca `if role ==`.

    Uso: `RunReconciliationDep = Annotated[CurrentUser, Depends(require_permission(...))]`.
    Negado por padrão: papel fora da célula recebe 403 sem vazar dado.
    """

    def _guard(user: CurrentUserDep) -> CurrentUser:
        if not has_permission(user, permission):
            raise ForbiddenError(
                f"Papel {user.role} não tem a permissão {permission.value}.",
                user_message="Você não tem permissão para esta ação.",
            )
        return user

    return _guard


# Guards prontos por permissão da matriz (§4 do PRD). Rotas importam estes —
# assim a matriz é o único lugar que decide quem pode o quê.
RunReconciliationDep = Annotated[
    CurrentUser, Depends(require_permission(Permission.RUN_RECONCILIATION))
]
ReviewExportDep = Annotated[CurrentUser, Depends(require_permission(Permission.REVIEW_EXPORT))]
SyncOmieAccountsDep = Annotated[
    CurrentUser, Depends(require_permission(Permission.SYNC_OMIE_ACCOUNTS))
]
ManageClientUsersDep = Annotated[
    CurrentUser, Depends(require_permission(Permission.MANAGE_CLIENT_USERS))
]
EditClientDep = Annotated[CurrentUser, Depends(require_permission(Permission.EDIT_CLIENT))]
ManageGlossaryDep = Annotated[CurrentUser, Depends(require_permission(Permission.MANAGE_GLOSSARY))]
# --- Camada de organizações (86e36ecar) -------------------------------------
CreateClientDep = Annotated[CurrentUser, Depends(require_permission(Permission.CREATE_CLIENT))]
ManageOrgUsersDep = Annotated[CurrentUser, Depends(require_permission(Permission.MANAGE_ORG_USERS))]
ManageClientCategoriesDep = Annotated[
    CurrentUser, Depends(require_permission(Permission.MANAGE_CLIENT_CATEGORIES))
]
ManageAnomalyTypesDep = Annotated[
    CurrentUser, Depends(require_permission(Permission.MANAGE_ANOMALY_TYPES))
]
ManagePlatformDep = Annotated[CurrentUser, Depends(require_permission(Permission.MANAGE_PLATFORM))]
RunAlertTestDep = Annotated[CurrentUser, Depends(require_permission(Permission.RUN_ALERT_TEST))]
