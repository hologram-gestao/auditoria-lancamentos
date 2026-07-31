"""Lógica de negócio do CRUD de usuários (admin-only).

Regras (Doc §8 + CLAUDE.md):
    - E-mail único por usuário (case-insensitive).
    - Admin não pode desativar nem rebaixar a si mesmo (Doc §8.2-8.5).
    - Senha hasheada com bcrypt cost ≥12 (`app.core.security.hash_password`).
    - Email é normalizado para lower-case ao persistir.
    - Update é PATCH (parcial): só campos enviados são alterados.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import (
    CannotDeactivateSelfError,
    EmailAlreadyExistsError,
    ForbiddenError,
    NotFoundError,
)
from app.core.security import hash_password
from app.db.models import ClientUserRole, User, UserRole, UserScope
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import PaginationMeta


class UserService:
    """CRUD + regras de negócio para `users`."""

    def __init__(self, repository: UserRepository) -> None:
        self._repo = repository

    # ------------------------------ READ ------------------------------

    async def list_users(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> tuple[list[User], PaginationMeta]:
        """Retorna (usuários da página, metadados de paginação)."""
        rows, total = await self._repo.list_paginated(page=page, page_size=page_size, search=search)
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return list(rows), PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def get_user(self, user_id: UUID) -> User:
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("Usuário não encontrado.")
        return user

    # ------------------------------ CREATE ----------------------------

    async def create_user(
        self,
        *,
        name: str,
        email: str,
        password: str,
        role: UserRole,
    ) -> User:
        """Cria usuário ativo com senha hasheada. Email único — 409 se duplicado."""
        normalized_email = email.lower()
        existing = await self._repo.get_by_email(normalized_email)
        if existing is not None:
            raise EmailAlreadyExistsError(
                f"E-mail já existe: {normalized_email}",
            )

        user = User(
            name=name,
            email=normalized_email,
            password_hash=hash_password(password),
            role=role.value,
            active=True,
        )
        await self._repo.add(user)
        return user

    # ------------------------------ UPDATE ----------------------------

    async def update_user(
        self,
        user_id: UUID,
        *,
        current_user_id: UUID,
        name: str | None = None,
        email: str | None = None,
        role: UserRole | None = None,
    ) -> User:
        """Atualiza campos parcialmente. Bloqueios:
        - Admin NÃO pode rebaixar a si mesmo para manager (Doc §8.4).
        - E-mail só pode mudar se não conflitar com outro usuário.
        """
        user = await self.get_user(user_id)

        if email is not None:
            normalized_email = email.lower()
            if normalized_email != user.email:
                conflict = await self._repo.get_by_email(normalized_email)
                if conflict is not None:
                    raise EmailAlreadyExistsError(
                        f"E-mail já existe: {normalized_email}",
                    )
                user.email = normalized_email

        if name is not None:
            user.name = name

        if role is not None:
            # Admin não pode rebaixar a si mesmo (Doc §8.4)
            if (
                user.id == current_user_id
                and user.role == UserRole.ADMIN.value
                and role != UserRole.ADMIN
            ):
                raise ForbiddenError(
                    "Admin não pode rebaixar o próprio perfil.",
                    user_message="Você não pode rebaixar seu próprio perfil de administrador.",
                )
            user.role = role.value

        # SQLAlchemy detecta mudanças automaticamente — flush para persistir
        # antes de retornar (atualiza updated_at via onupdate).
        await self._repo.add(user)
        return user

    # ------------------------------ ACTIVATE / DEACTIVATE -------------

    async def set_user_active(
        self,
        user_id: UUID,
        *,
        active: bool,
        current_user_id: UUID,
    ) -> User:
        """Soft activation/deactivation. Bloqueios:
        - Admin NÃO pode desativar a si mesmo (Doc §8.2 + §8.5).
        """
        if not active and user_id == current_user_id:
            raise CannotDeactivateSelfError(
                f"User {user_id} tentou desativar a si mesmo.",
            )

        user = await self.get_user(user_id)
        user.active = active
        await self._repo.add(user)
        return user

    # ------------------------------------------------------------------
    # Usuários DO CLIENTE (tenant) — Sprint 5 / R5
    # ------------------------------------------------------------------
    #
    # Todo método recebe `client_id` (o tenant, já validado pela rota via
    # `require_client_access`) e resolve o alvo por `get_by_id_in_tenant` — o
    # `AND client_id` mora no SELECT. É a defesa anti-IDOR: `user_id` forjado de
    # outro tenant, ou de um admin do sistema (`client_id IS NULL`), não retorna
    # linha e vira 404 antes de qualquer escrita.

    async def list_client_users(
        self,
        *,
        client_id: UUID,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> tuple[list[User], PaginationMeta]:
        """Usuários do tenant, paginados. Nunca inclui usuário de outro tenant."""
        rows, total = await self._repo.list_paginated(
            page=page, page_size=page_size, search=search, client_id=client_id
        )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return list(rows), PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def get_client_user(self, *, client_id: UUID, user_id: UUID) -> User:
        user = await self._repo.get_by_id_in_tenant(user_id, client_id=client_id)
        if user is None:
            raise NotFoundError("Usuário não encontrado.")
        return user

    async def create_client_user(
        self,
        *,
        client_id: UUID,
        name: str,
        email: str,
        password: str,
        role: ClientUserRole,
    ) -> User:
        """Cria usuário DO cliente com senha inicial definida por quem cria.

        `client_id` e `scope` são fixados pelo SERVIDOR a partir do tenant da
        rota — nunca vêm do body. `role` já chega restrito a
        `client_manager`/`client_operator` pelo schema (422 do Pydantic), então
        não há caminho para forjar `admin`/`manager`/`scope='system'`.

        E-mail é único GLOBALMENTE (constraint da tabela): colisão com usuário
        de outro tenant, ou da equipe Hologram, devolve 409 — e a mensagem ao
        usuário não diz de quem é o e-mail (não vira oráculo de enumeração
        cross-tenant).
        """
        normalized_email = email.lower()
        if await self._repo.get_by_email(normalized_email) is not None:
            raise EmailAlreadyExistsError(
                f"E-mail já existe: {normalized_email}",
                user_message="Este e-mail já está em uso.",
            )

        user = User(
            name=name,
            email=normalized_email,
            password_hash=hash_password(password),
            role=role.value,
            active=True,
            scope=UserScope.CLIENT.value,
            client_id=client_id,
        )
        await self._repo.add(user)
        return user

    async def update_client_user(
        self,
        *,
        client_id: UUID,
        user_id: UUID,
        name: str | None = None,
        email: str | None = None,
        role: ClientUserRole | None = None,
    ) -> User:
        """PATCH parcial de um usuário do tenant. Papel segue restrito ao enum."""
        user = await self.get_client_user(client_id=client_id, user_id=user_id)

        if email is not None:
            normalized_email = email.lower()
            if normalized_email != user.email:
                if await self._repo.get_by_email(normalized_email) is not None:
                    raise EmailAlreadyExistsError(
                        f"E-mail já existe: {normalized_email}",
                        user_message="Este e-mail já está em uso.",
                    )
                user.email = normalized_email

        if name is not None:
            user.name = name
        if role is not None:
            user.role = role.value

        await self._repo.add(user)
        return user

    async def set_client_user_active(
        self,
        *,
        client_id: UUID,
        user_id: UUID,
        active: bool,
        current_user_id: UUID,
    ) -> User:
        """Ativa/desativa. Desativar revoga no PRÓXIMO request (checagem de `active`).

        Ninguém se desativa: o gerente do cliente ficaria de fora do próprio
        tenant sem ter quem o reative (não há autoatendimento no MVP).
        """
        if not active and user_id == current_user_id:
            raise CannotDeactivateSelfError(
                f"User {user_id} tentou desativar a si mesmo.",
            )
        user = await self.get_client_user(client_id=client_id, user_id=user_id)
        user.active = active
        await self._repo.add(user)
        return user
