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
from app.db.models import UserRole, UserScope
from app.modules.auth.repository import AuthContext, AuthRepository
from app.modules.auth.schemas import AuthenticatedUser

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.db.models import User


# Mensagem genérica obrigatória — vale para credenciais inválidas, usuário
# desativado E organização suspensa. Não revelar qual (Doc §7.1 + CLAUDE.md §3.9).
GENERIC_LOGIN_ERROR = "E-mail ou senha incorretos."
# O `message` do `AppError` TAMBÉM vai no corpo da resposta (`to_error_response`),
# então o motivo real fica só em `metadata` (logs/Sentry) — quatro rejeições,
# um corpo só, indistinguíveis para quem enumera e-mails.
LOGIN_REJECTED = "Login rejeitado."


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

    async def login(self, *, email: str, password: str) -> tuple[AuthContext, str, str]:
        """Valida credenciais e retorna (contexto do usuário, access_token, refresh_token).

        Erros:
            - `UnauthorizedError` (401, código UNAUTHORIZED) com mensagem genérica
              em todos os casos: email inexistente, senha errada, usuário inativo,
              organização suspensa (camada de organizações).

        Timing constante (P0-003): mesmo quando o email não existe no DB,
        consumimos um `verify_password` contra um hash dummy pré-computado.
        Sem isso, atacante mede `t_response` e enumera emails válidos pela
        ausência do bcrypt (~150-200ms cost=12). Combinado ao rate limit
        do `/login` (5/5min/IP), barra enumeração prática.
        """
        ctx = await self._repo.get_auth_context_by_email(email)
        if ctx is None:
            # Consome bcrypt mesmo sem user — equaliza tempo.
            verify_password(password, _dummy_bcrypt_hash())
            raise UnauthorizedError(
                LOGIN_REJECTED,
                user_message=GENERIC_LOGIN_ERROR,
                metadata={"reason": "user_not_found"},
            )
        user = ctx.user

        if not verify_password(password, user.password_hash):
            raise UnauthorizedError(
                LOGIN_REJECTED,
                user_message=GENERIC_LOGIN_ERROR,
                metadata={"reason": "invalid_password", "user_id": str(user.id)},
            )

        if not user.active:
            # MESMO corpo — não vazar que a conta existe mas está desativada.
            raise UnauthorizedError(
                LOGIN_REJECTED,
                user_message=GENERIC_LOGIN_ERROR,
                metadata={"reason": "user_inactive", "user_id": str(user.id)},
            )

        if ctx.organization_active is False:
            # Organização suspensa pela plataforma: MESMO corpo.
            raise UnauthorizedError(
                LOGIN_REJECTED,
                user_message=GENERIC_LOGIN_ERROR,
                metadata={"reason": "organization_inactive", "user_id": str(user.id)},
            )

        return ctx, *self._issue_tokens(user)

    async def refresh(self, *, refresh_token: str) -> tuple[AuthContext, str, str]:
        """Valida refresh token e emite novo par (access, refresh).

        Erros:
            - `UnauthorizedError` se token inválido / expirado / tipo errado / user
              inativo / organização suspensa.
        """
        payload = decode_token(refresh_token, self._settings, expected_type=TOKEN_TYPE_REFRESH)

        try:
            user_id = UUID(payload.sub)
        except ValueError as exc:
            raise UnauthorizedError("Refresh token com sub inválido.") from exc

        ctx = await self._repo.get_auth_context_by_id(user_id)
        if ctx is None or not ctx.user.active or ctx.organization_active is False:
            # User foi deletado/desativado (ou a organização suspensa) depois do
            # refresh ser emitido — bloqueia.
            raise UnauthorizedError("Sessão expirada. Faça login novamente.")

        # Reemite a partir da LINHA atual: se o admin mudou o tenant/escopo/org
        # desde o login, o par novo já sai com o valor corrente (Sprint 5 / R2).
        return ctx, *self._issue_tokens(ctx.user)

    def _issue_tokens(self, user: User) -> tuple[str, str]:
        """Par (access, refresh) com `scope`/`client_id`/`organization_id` (S5 / R2).

        Ponto ÚNICO de emissão: login e refresh passam por aqui, senão um dos
        dois esqueceria os claims novos.
        """
        subject = str(user.id)
        client_id = str(user.client_id) if user.client_id else None
        organization_id = str(user.organization_id) if user.organization_id else None
        access = create_access_token(
            subject=subject,
            role=user.role,
            settings=self._settings,
            scope=user.scope,
            client_id=client_id,
            organization_id=organization_id,
        )
        refresh = create_refresh_token(
            subject=subject,
            role=user.role,
            settings=self._settings,
            scope=user.scope,
            client_id=client_id,
            organization_id=organization_id,
        )
        return access, refresh

    @staticmethod
    def to_authenticated_user(ctx: AuthContext) -> AuthenticatedUser:
        """Mapeia o contexto ORM para o schema seguro (sem senha, sem timestamps)."""
        user = ctx.user
        return AuthenticatedUser(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=UserRole(user.role),
            scope=UserScope(user.scope),
            client_id=user.client_id,
            organization_id=user.organization_id,
            organization_name=ctx.organization_name,
        )
