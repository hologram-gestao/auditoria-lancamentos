"""Dependencies do FastAPI compartilhadas — auth, RBAC, settings, DB.

Use sempre via `Depends(...)` em rotas. **Proibido** acessar `session` global,
`Settings()` direto ou JWT manualmente fora destas funções.

Hoje (S3):
    - `get_settings` (em `app.core.config`)
    - `DbSessionDep` — sessão SQLAlchemy async com rollback automático
    - `get_current_user` — extrai JWT do cookie + valida `users.active = true` no DB
    - `require_admin` / `require_manager_or_admin` — RBAC por role

Em S6 (clientes):
    - `require_client_access(client_id)` — checa `client_assignments`
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import get_contextvars

from app.core.audit import AccessAction, record_access
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ClientNotAccessibleError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.core.telemetry import emit_acesso_negado
from app.db.models import Client, ClientAssignment, UserRole
from app.db.session import get_db_session
from app.modules.auth.repository import AuthRepository

# Nomes dos cookies HttpOnly — nomes de cookie, não credenciais.
ACCESS_TOKEN_COOKIE = "access_token"  # noqa: S105
REFRESH_TOKEN_COOKIE = "refresh_token"  # noqa: S105

# Sessão DB por request. Use em rotas: `db: DbSessionDep`.
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class CurrentUser(BaseModel):
    """User autenticado e ATIVO no DB. Garantido por `get_current_user`."""

    id: str  # users.id (UUID em string)
    email: str
    name: str
    role: str  # "admin" | "manager"


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


async def require_client_access(
    client_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> Client:
    """RBAC por carteira: admin acessa qualquer cliente; manager apenas se há
    `client_assignments(client_id, user_id)` (CLAUDE.md §3.11 + S6 §3).

    Retorna o `Client` carregado para evitar uma 2ª query no service. Erros:
        - 404 NOT_FOUND: cliente inexistente.
        - 403 FORBIDDEN: manager sem assignment para o cliente.

    Ao negar acesso a um manager fora da carteira, emite o evento
    `acesso_negado` (só IDs, sem PII) **antes** de levantar
    `ClientNotAccessibleError` — a conversão 403→404 anti-enumeração feita nas
    rotas de leitura permanece intacta (Sprint 3, Req. 3).
    """
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if client is None:
        raise NotFoundError("Cliente não encontrado.")

    if user.role == UserRole.ADMIN.value:
        return client

    assignment = (
        await db.execute(
            select(ClientAssignment.id).where(
                ClientAssignment.client_id == client_id,
                ClientAssignment.user_id == UUID(user.id),
            )
        )
    ).scalar_one_or_none()
    if assignment is None:
        # `rota` vem do path já vinculado pelo CorrelationIdMiddleware nos
        # contextvars do structlog — evita propagar `Request` por 11 call sites.
        rota = str(get_contextvars().get("path", ""))
        # Evento (telemetria/alerting) + linha de auditoria (registro LGPD
        # durável), ambos ANTES da conversão 403→404 anti-enumeração das rotas
        # de leitura — auditoria e anti-enumeração convivem (CONTEXT.md).
        emit_acesso_negado(user_id=user.id, client_id_alvo=str(client_id), rota=rota)
        # `commit=True`: a request vai terminar em erro e o `get_db_session`
        # daria ROLLBACK — sem o commit a linha de `denied` se perderia. Aqui a
        # única escrita pendente é a própria auditoria (a dependency só fez SELECT).
        await record_access(
            db,
            user_id=UUID(user.id),
            client_id=client_id,
            action=AccessAction.DENIED,
            rota=rota,
            commit=True,
        )
        raise ClientNotAccessibleError(
            f"Manager {user.id} tentou acessar cliente {client_id} fora da carteira.",
        )
    return client


AccessibleClientDep = Annotated[Client, Depends(require_client_access)]
