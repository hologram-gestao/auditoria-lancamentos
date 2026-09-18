"""Acesso ao DB para o módulo de gestão de usuários (admin-only).

Repository fica fino — só queries. Regras (email único, admin não desativa
a si próprio, etc.) ficam no service. Padrão CLAUDE.md §7.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser, scoped_by_organization
from app.db.models import Organization, User, UserScope


class StaffRow(NamedTuple):
    """Staff + o nome da organização, lidos no MESMO SELECT (86e36ecqz).

    O relationship `User.organization` não existe de propósito: o nome entra
    por join explícito nas leituras de staff, e a response o recebe pronto —
    nada de lazy-load na serialização.
    """

    user: User
    organization_name: str | None


def _staff_select(viewer: CurrentUser) -> Select[tuple[User, str | None]]:
    """SELECT base de staff: `scope='system'`, da organização do observador
    (`scoped_by_organization`; plataforma: todas), com o nome da org."""
    stmt = (
        select(User, Organization.name)
        .outerjoin(Organization, Organization.id == User.organization_id)
        .where(User.scope == UserScope.SYSTEM.value)
    )
    return scoped_by_organization(stmt, User.organization_id, viewer)


class UserRepository:
    """Operações de leitura/escrita sobre `users`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_staff_paginated(
        self,
        *,
        viewer: CurrentUser,
        page: int,
        page_size: int,
        search: str | None = None,
        organization_id: UUID | None = None,
        role: str | None = None,
    ) -> tuple[list[StaffRow], int]:
        """Listagem de STAFF, paginada, com busca opcional em `name`/`email`.

        Só linhas `scope='system'` (nem plataforma, nem usuário de cliente) e só
        da organização do observador (`scoped_by_organization`; a plataforma vê
        todas). `organization_id` é o filtro OPCIONAL da plataforma (já decidido
        por `resolve_organization_filter` no service — aqui é só `WHERE`);
        `role` é o filtro do seletor de gerentes do front.

        Returns:
            Tupla `(rows, total_count)`. Total é a contagem ANTES da paginação,
            necessário para `totalPages` no response.
        """
        base = _staff_select(viewer)
        count_base = scoped_by_organization(
            select(func.count()).select_from(User).where(User.scope == UserScope.SYSTEM.value),
            User.organization_id,
            viewer,
        )

        if organization_id is not None:
            base = base.where(User.organization_id == organization_id)
            count_base = count_base.where(User.organization_id == organization_id)
        if role is not None:
            base = base.where(User.role == role)
            count_base = count_base.where(User.role == role)
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
        rows = (await self._session.execute(base)).all()
        return [StaffRow(user=row[0], organization_name=row[1]) for row in rows], int(total)

    async def list_paginated(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None = None,
        client_id: UUID,
    ) -> tuple[Sequence[User], int]:
        """Usuários DO TENANT, paginados, com busca opcional (Sprint 5 / R5).

        `client_id` é obrigatório: a listagem de usuários do cliente nunca
        mostra usuário de outro tenant nem staff (que tem `client_id IS NULL`).
        A listagem de staff é `list_staff_paginated`; a "de todo mundo" não
        existe.

        Returns:
            Tupla `(rows, total_count)`. Total é a contagem ANTES da paginação.
        """
        base = select(User).where(User.client_id == client_id)
        count_base = select(func.count()).select_from(User).where(User.client_id == client_id)

        if search:
            term = f"%{search.strip().lower()}%"
            cond = or_(func.lower(User.name).like(term), func.lower(User.email).like(term))
            base = base.where(cond)
            count_base = count_base.where(cond)

        base = base.order_by(User.created_at.desc(), User.id.desc())
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        total = (await self._session.execute(count_base)).scalar_one()
        rows = (await self._session.execute(base)).scalars().all()
        return rows, int(total)

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_staff_by_id(self, user_id: UUID, *, viewer: CurrentUser) -> StaffRow | None:
        """Usuário de STAFF alvo, **da organização do observador** — anti-IDOR.

        O `scope='system'` e a organização moram no SELECT: um `user_id` de
        plataforma, de usuário de cliente ou de staff de OUTRA organização não
        retorna linha (404), em vez de ser desativado/rebaixado por um admin de
        organização. A plataforma alcança o staff de qualquer organização.
        """
        row = (await self._session.execute(_staff_select(viewer).where(User.id == user_id))).first()
        if row is None:
            return None
        return StaffRow(user=row[0], organization_name=row[1])

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Leitor da organização para `resolve_organization_for_creation`."""
        return await self._session.get(Organization, organization_id)

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
