"""Acesso ao DB do catálogo de categorias de cliente (86e34jd8m).

Repositório fino: unicidade sem caixa, 409 de "em uso" e 404 ficam no service.
"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Client, ClientCategory


class CategoryRow(NamedTuple):
    category: ClientCategory
    clients_count: int


class ClientCategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    async def list_all(self) -> list[CategoryRow]:
        """Catálogo inteiro (é pequeno por natureza) com a contagem de clientes.

        Ordenado por nome sem caixa — é como o admin procura na tela.
        """
        count_sq = (
            select(func.count(Client.id))
            .where(Client.category_id == ClientCategory.id)
            .correlate(ClientCategory)
            .scalar_subquery()
        )
        stmt = select(ClientCategory, count_sq.label("clients_count")).order_by(
            func.lower(ClientCategory.name)
        )
        rows = (await self._session.execute(stmt)).all()
        return [CategoryRow(category=row[0], clients_count=int(row[1] or 0)) for row in rows]

    async def get_by_id(self, category_id: UUID) -> ClientCategory | None:
        result = await self._session.execute(
            select(ClientCategory).where(ClientCategory.id == category_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name_ci(self, name: str) -> ClientCategory | None:
        """Busca sem caixa: "Fintech" e "fintech" são a MESMA categoria."""
        result = await self._session.execute(
            select(ClientCategory).where(func.lower(ClientCategory.name) == name.strip().lower())
        )
        return result.scalar_one_or_none()

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
