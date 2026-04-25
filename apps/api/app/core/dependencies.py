"""Dependencies do FastAPI compartilhadas — auth, RBAC, settings, DB.

Use sempre via `Depends(...)` em rotas. **Proibido** acessar `session` global,
`Settings()` direto ou JWT manualmente fora destas funções.

Hoje (S2):
    - `get_settings` (em `app.core.config`)
    - `DbSessionDep` — sessão SQLAlchemy async com rollback automático
    - `get_current_user` — extrai e valida JWT do cookie HttpOnly
    - `require_admin` / `require_manager_or_admin` — RBAC por role

Em S3 (auth real):
    - `get_current_user` passa a validar `users.active = true` no DB
    - `require_client_access(client_id)` — checa `client_assignments`
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_db_session

# Nome do cookie HttpOnly que carrega o access JWT — não é um valor de senha.
ACCESS_TOKEN_COOKIE = "access_token"  # noqa: S105

# Sessão DB por request. Use em rotas: `db: DbSessionDep`.
DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class CurrentUser(BaseModel):
    """User autenticado, extraído do JWT.

    Em S2, ganhará campos vindos do DB (email, name) após validação de `active`.
    """

    id: str  # users.id (UUID)
    role: str  # "admin" | "manager"


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_current_user(
    settings: SettingsDep,
    access_token: Annotated[str | None, Cookie(alias=ACCESS_TOKEN_COOKIE)] = None,
) -> CurrentUser:
    """Extrai o usuário atual do cookie HttpOnly `access_token`.

    Erros possíveis:
        - 401 `UNAUTHORIZED`: cookie ausente, JWT inválido, claims malformados.
        - 401 `TOKEN_EXPIRED`: assinatura válida mas `exp` no passado
          (frontend deve tentar refresh).

    Em S2:
        Acrescenta consulta ao DB para validar `users.active = true` —
        usuário desativado perde acesso na próxima request, mesmo com JWT vivo.
    """
    if not access_token:
        raise UnauthorizedError("Cookie de acesso ausente.")

    payload = decode_token(access_token, settings, expected_type=TOKEN_TYPE_ACCESS)
    return CurrentUser(id=payload.sub, role=payload.role)


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
