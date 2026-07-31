"""Dependencies do FastAPI compartilhadas — auth, RBAC, settings, DB.

Use sempre via `Depends(...)` em rotas. **Proibido** acessar `session` global,
`Settings()` direto ou JWT manualmente fora destas funções.

Hoje (S3):
    - `get_settings` (em `app.core.config`)
    - `DbSessionDep` — sessão SQLAlchemy async com rollback automático
    - `get_current_user` — extrai JWT do cookie + valida `users.active = true` no DB
    - `require_admin` / `require_manager_or_admin` — RBAC por role

Em S6 (clientes):
    - `require_client_access(client_id)` — guard de tenant/carteira

Sprint 5 (R2 + R4): a REGRA de acesso mora em `app.core.authz`
(`resolve_client_access` + `PERMISSION_MATRIX`). Aqui ficam só os **guards**
FastAPI que a consultam e o efeito colateral de negar (403 + trilha). Proibida
segunda implementação da regra fora do `authz`.
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

    Erros possíveis:
        - 401 `UNAUTHORIZED`: cookie ausente, JWT inválido, user inativo/inexistente.
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

    user = await AuthRepository(db).get_by_id(user_id)
    if user is None or not user.active:
        raise UnauthorizedError("Sessão expirou ou usuário inativo.")

    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        # Da LINHA, não do token — ver docstring de `CurrentUser`.
        scope=user.scope,
        client_id=user.client_id,
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin(user: CurrentUserDep) -> CurrentUser:
    """RBAC: garante perfil admin. Caso contrário, 403."""
    if user.role != "admin":
        raise ForbiddenError("Esta operação requer perfil administrador.")
    return user


def require_manager_or_admin(user: CurrentUserDep) -> CurrentUser:
    """RBAC: aceita admin OU manager."""
    if user.role not in {"admin", "manager"}:
        raise ForbiddenError("Acesso negado.")
    return user


AdminDep = Annotated[CurrentUser, Depends(require_admin)]
ManagerOrAdminDep = Annotated[CurrentUser, Depends(require_manager_or_admin)]


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
    - `scope='system'` → regra atual (admin tudo, manager pela carteira).

    Retorna o `Client` carregado para evitar uma 2ª query no service. Erros:
        - 404 NOT_FOUND: cliente inexistente.
        - 403 FORBIDDEN: fora do escopo (a rota de leitura converte para 404).

    **Não existe segunda implementação da regra** — este guard só executa o
    efeito colateral (`deny_client_access`) quando a decisão vem `False`.
    """
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if client is None:
        raise NotFoundError("Cliente não encontrado.")

    if not await resolve_client_access(db, user, client_id):
        await deny_client_access(db, user, client_id)
    return client


AccessibleClientDep = Annotated[Client, Depends(require_client_access)]


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
