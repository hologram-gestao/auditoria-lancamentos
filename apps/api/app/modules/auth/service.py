"""Lógica de negócio do módulo de auth.

Princípios (CLAUDE.md §3):
    - Mensagem de erro genérica em login (não revelar se foi email ou senha errados).
    - Usuário inativo retorna o MESMO erro genérico — não vazar que o email existe.
    - bcrypt sempre via `app.core.security` (cost ≥ 12).
    - Tokens (access + refresh) são JWT HS256 com `jti` único.
    - Timing constante no login: usuário inexistente também consome um
      `verify_password` contra hash dummy, equalizando o tempo de resposta
      (P0-003 — bloqueia enumeração de emails por timing diff).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import UnauthorizedError
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import AuthenticatedUser

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.db.models import User


# Mensagem genérica obrigatória — vale para credenciais inválidas E usuário desativado.
# Não revelar qual dos dois (Doc §7.1 + CLAUDE.md §3.9).
GENERIC_LOGIN_ERROR = "E-mail ou senha incorretos."


@lru_cache(maxsize=1)
def _dummy_bcrypt_hash() -> str:
    """Hash bcrypt pré-computado para equalizar o tempo de `login()` (P0-003).

    Gerado uma vez por processo, sob lazy init. O cost segue o padrão do
    projeto (12, conforme `hash_password` default). A senha dummy é
    arbitrária — só importa que `verify_password("anything", hash)` execute
    o bcrypt completo para consumir o mesmo tempo do caminho positivo.
    """
    return hash_password("timing-equalization-not-a-credential", cost=12)


class AuthService:
    """Operações de autenticação."""

    def __init__(self, repository: AuthRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings

    async def login(self, *, email: str, password: str) -> tuple[User, str, str]:
        """Valida credenciais e retorna (user, access_token, refresh_token).

        Erros:
            - `UnauthorizedError` (401, código UNAUTHORIZED) com mensagem genérica
              em todos os casos: email inexistente, senha errada, usuário inativo.

        Timing constante (P0-003): mesmo quando o email não existe no DB,
        consumimos um `verify_password` contra um hash dummy pré-computado.
        Sem isso, atacante mede `t_response` e enumera emails válidos pela
        ausência do bcrypt (~150-200ms cost=12). Combinado ao rate limit
        do `/login` (5/5min/IP), barra enumeração prática.
        """
        user = await self._repo.get_by_email(email)
        if user is None:
            # Consome bcrypt mesmo sem user — equaliza tempo.
            verify_password(password, _dummy_bcrypt_hash())
            raise UnauthorizedError(
                "Login rejeitado: usuário não encontrado.",
                user_message=GENERIC_LOGIN_ERROR,
            )

        if not verify_password(password, user.password_hash):
            raise UnauthorizedError(
                "Login rejeitado: senha inválida.",
                user_message=GENERIC_LOGIN_ERROR,
            )

        if not user.active:
            # MESMA mensagem — não vazar que conta existe mas está desativada.
            raise UnauthorizedError(
                f"Login rejeitado: usuário {user.id} está inativo.",
                user_message=GENERIC_LOGIN_ERROR,
            )

        access = create_access_token(subject=str(user.id), role=user.role, settings=self._settings)
        refresh = create_refresh_token(
            subject=str(user.id), role=user.role, settings=self._settings
        )
        return user, access, refresh

    async def refresh(self, *, refresh_token: str) -> tuple[User, str, str]:
        """Valida refresh token e emite novo par (access, refresh).

        Erros:
            - `UnauthorizedError` se token inválido / expirado / tipo errado / user inativo.
        """
        payload = decode_token(refresh_token, self._settings, expected_type=TOKEN_TYPE_REFRESH)

        try:
            user_id = UUID(payload.sub)
        except ValueError as exc:
            raise UnauthorizedError("Refresh token com sub inválido.") from exc

        user = await self._repo.get_by_id(user_id)
        if user is None or not user.active:
            # User foi deletado/desativado depois do refresh ser emitido — bloqueia.
            raise UnauthorizedError("Sessão expirada. Faça login novamente.")

        new_access = create_access_token(
            subject=str(user.id), role=user.role, settings=self._settings
        )
        new_refresh = create_refresh_token(
            subject=str(user.id), role=user.role, settings=self._settings
        )
        return user, new_access, new_refresh

    @staticmethod
    def to_authenticated_user(user: User) -> AuthenticatedUser:
        """Mapeia o modelo ORM para o schema seguro (sem senha, sem timestamps)."""
        return AuthenticatedUser(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
        )
