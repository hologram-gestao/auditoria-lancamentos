"""Acesso ao DB do módulo de organizações (86e36ecnp).

Repositório fino: unicidade sem caixa, 404 e a regra de suspensão ficam no
service. Sem filtro de alcance: quem chega aqui é a plataforma (guard
`ManagePlatformDep` na rota), que enxerga todas as organizações.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import ScalarSelect, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Client, Organization, User, UserScope


class OrganizationRow(NamedTuple):
    organization: Organization
    clients_count: int
    users_count: int


def _counts() -> tuple[ScalarSelect[int], ScalarSelect[int]]:
    clients_sq = (
        select(func.count(Client.id))
        .where(Client.organization_id == Organization.id)
        .correlate(Organization)
        .scalar_subquery()
    )
    # Só STAFF conta como "usuários da organização": o usuário de cliente
    # pertence ao tenant dele, não ao escritório.
    users_sq = (
        select(func.count(User.id))
        .where(
            User.organization_id == Organization.id,
            User.scope == UserScope.SYSTEM.value,
        )
        .correlate(Organization)
        .scalar_subquery()
    )
    return clients_sq, users_sq


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_paginated(
        self, *, page: int, page_size: int, search: str | None = None
    ) -> tuple[Sequence[OrganizationRow], int]:
        """Lista paginada com contagens, ordenada por nome sem caixa."""
        clients_sq, users_sq = _counts()
        base = select(Organization, clients_sq, users_sq)
        count_base = select(func.count(Organization.id)).select_from(Organization)

        if search:
            term = f"%{search.strip().lower()}%"
            base = base.where(func.lower(Organization.name).like(term))
            count_base = count_base.where(func.lower(Organization.name).like(term))

        base = (
            base.order_by(func.lower(Organization.name), Organization.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        total = (await self._session.execute(count_base)).scalar_one()
        rows = (await self._session.execute(base)).all()
        return [
            OrganizationRow(
                organization=row[0], clients_count=int(row[1] or 0), users_count=int(row[2] or 0)
            )
            for row in rows
        ], int(total)

    async def get_row(self, organization_id: UUID) -> OrganizationRow | None:
        clients_sq, users_sq = _counts()
        row = (
            await self._session.execute(
                select(Organization, clients_sq, users_sq).where(Organization.id == organization_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return OrganizationRow(
            organization=row[0], clients_count=int(row[1] or 0), users_count=int(row[2] or 0)
        )

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        return await self._session.get(Organization, organization_id)

    async def get_by_name_ci(self, name: str) -> Organization | None:
        """Busca sem caixa: "Prospecta" e "prospecta" são a MESMA organização."""
        result = await self._session.execute(
            select(Organization).where(func.lower(Organization.name) == name.strip().lower())
        )
        return result.scalar_one_or_none()

    async def list_platform_admins(self) -> Sequence[User]:
        """Quem tem `scope='platform'` — o oposto exato do `_staff_select` de
        `/users`, que filtra `scope='system'` e por isso nunca devolve estas
        linhas (é o anti-IDOR da 86e36ecar: o admin de uma organização não pode
        alcançar a conta da plataforma).

        Sem filtro de alcance porque quem chega aqui já passou por
        `ManagePlatformDep`. Ordem alfabética por nome, com o e-mail (UNIQUE) de
        desempate determinístico.
        """
        result = await self._session.execute(
            select(User)
            .where(User.scope == UserScope.PLATFORM.value)
            .order_by(User.name.asc(), User.email.asc())
        )
        return result.scalars().all()

    # ------------------------------ WRITE -----------------------------

    async def add(self, organization: Organization) -> None:
        """Insere/atualiza com flush + refresh (carrega `created_at`/`updated_at`)."""
        self._session.add(organization)
        await self._session.flush()
        await self._session.refresh(organization)
