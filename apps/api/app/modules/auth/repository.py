"""Acesso ao DB para o módulo de auth.

Mantemos repository separado de service (CLAUDE.md §7) — repository só lida
com SQLAlchemy/Postgres; service só lida com regras de negócio.

Camada de organizações (86e36ecar): login, refresh e `get_current_user` leem o
usuário JUNTO com a organização dele (`AuthContext`) na mesma query — é o que
faz "organização suspensa" derrubar os usuários dela no request seguinte sem
uma segunda ida ao banco, e é de onde o front recebe o nome da organização.
"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, User


class AuthContext(NamedTuple):
    """Usuário + o que a autenticação precisa saber da organização dele.

    `organization_active`/`organization_name` são `None` para a plataforma
    (que não tem organização). `organization_active=False` é organização
    suspensa: o service/dependency tratam como usuário inativo.
    """

    user: User
    organization_name: str | None
    organization_active: bool | None


class AuthRepository:
    """Operações de leitura sobre `users` necessárias para auth."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _context_stmt() -> Select[tuple[User, str, bool]]:
        """Usuário + nome e `active` da organização.

        O `outerjoin` devolve NULOS para a plataforma (sem organização); o tipo
        do SQLAlchemy não sabe disso — `AuthContext` é quem declara `| None`.
        """
        return select(User, Organization.name, Organization.active).outerjoin(
            Organization, User.organization_id == Organization.id
        )

    async def get_auth_context_by_email(self, email: str) -> AuthContext | None:
        """Usuário (ativo OU inativo — a checagem fica no service) + organização."""
        # Email no DB é normalizado para lower-case (ver service.login)
        row = (
            await self._session.execute(self._context_stmt().where(User.email == email.lower()))
        ).one_or_none()
        return None if row is None else AuthContext(row[0], row[1], row[2])

    async def get_auth_context_by_id(self, user_id: UUID) -> AuthContext | None:
        """Usado pela dependency a cada request: `active` do usuário E da organização."""
        row = (
            await self._session.execute(self._context_stmt().where(User.id == user_id))
        ).one_or_none()
        return None if row is None else AuthContext(row[0], row[1], row[2])

    async def get_by_email(self, email: str) -> User | None:
        """Retorna usuário ativo OU inativo (a checagem de `active` fica no service)."""
        ctx = await self.get_auth_context_by_email(email)
        return None if ctx is None else ctx.user

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Usuário pela PK, sem a organização."""
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()
