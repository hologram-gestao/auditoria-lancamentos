"""Acesso ao DB do catálogo de categorias de cliente (86e34jd8m + 86e36ecqz).

Repositório fino: unicidade sem caixa, 409 de "em uso" e 404 ficam no service.
O catálogo é POR ORGANIZAÇÃO: toda leitura passa por `scoped_by_organization`
(a org da LINHA do observador; plataforma: todas) e o alvo por PK carrega o
`AND organization_id` no próprio SELECT — categoria de outra organização vira
404, nunca o dado.
"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import CurrentUser, scoped_by_organization
from app.db.models import Client, ClientCategory, Organization


class CategoryRow(NamedTuple):
    category: ClientCategory
    clients_count: int
    organization_name: str


def _category_select(viewer: CurrentUser) -> Select[tuple[ClientCategory, str]]:
    """SELECT base: categoria + nome da org, restrito ao alcance do observador."""
    stmt = select(ClientCategory, Organization.name).join(
        Organization, Organization.id == ClientCategory.organization_id
    )
    return scoped_by_organization(stmt, ClientCategory.organization_id, viewer)


class ClientCategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_all(
        self, *, viewer: CurrentUser, organization_id: UUID | None = None
    ) -> list[CategoryRow]:
        """Catálogo ao alcance do observador (é pequeno por natureza) com a
        contagem de clientes. `organization_id` é o filtro opcional da
        plataforma, já decidido por `resolve_organization_filter` no service.

        Ordenado por organização e nome sem caixa — é como se procura na tela.
        """
        count_sq = (
            select(func.count(Client.id))
            .where(Client.category_id == ClientCategory.id)
            .correlate(ClientCategory)
            .scalar_subquery()
        )
        stmt = _category_select(viewer).add_columns(count_sq.label("clients_count"))
        if organization_id is not None:
            stmt = stmt.where(ClientCategory.organization_id == organization_id)
        stmt = stmt.order_by(func.lower(Organization.name), func.lower(ClientCategory.name))
        rows = (await self._session.execute(stmt)).all()
        return [
            CategoryRow(category=row[0], organization_name=row[1], clients_count=int(row[2] or 0))
            for row in rows
        ]

    async def get_by_id(self, category_id: UUID, *, viewer: CurrentUser) -> CategoryRow | None:
        """Alvo por PK **dentro do alcance** — anti-IDOR: categoria de outra
        organização não retorna linha (404)."""
        row = (
            await self._session.execute(
                _category_select(viewer).where(ClientCategory.id == category_id)
            )
        ).first()
        if row is None:
            return None
        return CategoryRow(category=row[0], organization_name=row[1], clients_count=0)

    async def get_by_name_ci(self, name: str, *, organization_id: UUID) -> ClientCategory | None:
        """Busca sem caixa DENTRO da organização: "Fintech" e "fintech" são a
        MESMA categoria; a mesma palavra em outra organização é outra categoria
        (UNIQUE `(organization_id, name)`)."""
        result = await self._session.execute(
            select(ClientCategory).where(
                ClientCategory.organization_id == organization_id,
                func.lower(ClientCategory.name) == name.strip().lower(),
            )
        )
        return result.scalar_one_or_none()

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Leitor da organização para `resolve_organization_for_creation`."""
        return await self._session.get(Organization, organization_id)

    async def count_clients(self, category_id: UUID) -> int:
        stmt = select(func.count(Client.id)).where(Client.category_id == category_id)
        return int((await self._session.execute(stmt)).scalar_one())

    # ------------------------------ WRITE -----------------------------

    async def add(self, category: ClientCategory) -> None:
        """Insere/atualiza com flush + refresh (carrega `created_at`/`updated_at`)."""
        self._session.add(category)
        await self._session.flush()
        await self._session.refresh(category)

    async def delete(self, category: ClientCategory) -> None:
        await self._session.delete(category)
        await self._session.flush()
