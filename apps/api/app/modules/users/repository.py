"""Acesso ao DB para o módulo de gestão de usuários (admin-only).

Repository fica fino — só queries. Regras (email único, admin não desativa
a si próprio, etc.) ficam no service. Padrão CLAUDE.md §7.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class UserRepository:
    """Operações de leitura/escrita sobre `users`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        client_id: UUID | None = None,
    ) -> tuple[Sequence[User], int]:
        """Lista paginada com busca opcional em `name` ou `email` (ILIKE).

        Args:
            client_id: quando informado, restringe ao TENANT (Sprint 5 / R5) —
                a listagem de usuários do cliente nunca mostra usuário de outro
                tenant nem da equipe Hologram (que tem `client_id IS NULL`).

        Returns:
            Tupla `(rows, total_count)`. Total é a contagem ANTES da paginação,
            necessário para `totalPages` no response.
        """
        base = select(User)
        count_base = select(func.count()).select_from(User)

        if client_id is not None:
            base = base.where(User.client_id == client_id)
            count_base = count_base.where(User.client_id == client_id)

        if search:
            term = f"%{search.strip().lower()}%"
            cond = or_(func.lower(User.name).like(term), func.lower(User.email).like(term))
            base = base.where(cond)
            count_base = count_base.where(cond)

        # Ordem estável: created_at desc, id desc (desempate determinístico)
        base = base.order_by(User.created_at.desc(), User.id.desc())
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        total = (await self._session.execute(count_base)).scalar_one()
        rows = (await self._session.execute(base)).scalars().all()
        return rows, int(total)

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_id_in_tenant(self, user_id: UUID, *, client_id: UUID) -> User | None:
        """Usuário-alvo **dentro do tenant** — a defesa anti-IDOR (Sprint 5 / R5).

        O `AND client_id = :tenant` mora no SELECT, não numa comparação depois
        de carregar: um `user_id` forjado de OUTRO tenant (ou de um admin do
        sistema, que tem `client_id IS NULL`) simplesmente não retorna linha.
        Sem isso, o gerente de um cliente editaria/desativaria usuário alheio.
        """
        result = await self._session.execute(
            select(User).where(User.id == user_id, User.client_id == client_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    # ------------------------------ WRITE -----------------------------

    async def add(self, user: User) -> None:
        """Insere/atualiza e flush + refresh.

        Refresh é necessário para carregar atributos populados server-side
        (`created_at`/`updated_at` via `func.now()`) — sem ele, a serialização
        Pydantic estoura `MissingGreenlet` ao acessá-los.
        """
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
