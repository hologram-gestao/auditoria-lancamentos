"""Acesso ao DB para o módulo de auth.

Mantemos repository separado de service (CLAUDE.md §7) — repository só lida
com SQLAlchemy/Postgres; service só lida com regras de negócio.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class AuthRepository:
    """Operações de leitura sobre `users` necessárias para auth."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        """Retorna usuário ativo OU inativo (a checagem de `active` fica no service)."""
        # Email no DB é normalizado para lower-case (ver service.login)
        result = await self._session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Usado pelo middleware/dependency para validar `active = true` a cada request."""
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
