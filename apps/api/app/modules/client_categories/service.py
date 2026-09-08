"""Regras do catálogo de categorias de cliente (86e34jd8m).

- Nome único SEM caixa: "Fintech" e "fintech" são a mesma categoria — o
  pre-check devolve 409 legível antes do IntegrityError da UNIQUE.
- DELETE só de categoria órfã. Em uso → 409 com a contagem; o admin realoca
  os clientes antes (a FK em `clients` é RESTRICT, então o banco também
  recusaria — o service só torna a recusa legível).
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import (
    ClientCategoryInUseError,
    ClientCategoryNameAlreadyExistsError,
    NotFoundError,
)
from app.db.models import ClientCategory, ClientCategoryTone
from app.modules.client_categories.repository import CategoryRow, ClientCategoryRepository
from app.modules.client_categories.schemas import ClientCategoryItem


def _to_item(row: CategoryRow) -> ClientCategoryItem:
    return ClientCategoryItem(
        id=row.category.id,
        name=row.category.name,
        tone=row.category.tone,
        clients_count=row.clients_count,
    )


class ClientCategoryService:
    def __init__(self, repository: ClientCategoryRepository) -> None:
        self._repo = repository

    async def list_categories(self) -> list[ClientCategoryItem]:
        return [_to_item(row) for row in await self._repo.list_all()]

    async def get_category(self, category_id: UUID) -> ClientCategory:
        category = await self._repo.get_by_id(category_id)
        if category is None:
            raise NotFoundError("Categoria não encontrada.")
        return category

    async def create_category(self, *, name: str, tone: ClientCategoryTone) -> ClientCategoryItem:
        if await self._repo.get_by_name_ci(name) is not None:
            raise ClientCategoryNameAlreadyExistsError(f"Categoria já existe: {name!r}")
        category = ClientCategory(name=name, tone=tone.value)
        await self._repo.add(category)
        return _to_item(CategoryRow(category=category, clients_count=0))

    async def update_category(
        self,
        category_id: UUID,
        *,
        name: str | None = None,
        tone: ClientCategoryTone | None = None,
    ) -> ClientCategoryItem:
        category = await self.get_category(category_id)
        if name is not None:
            other = await self._repo.get_by_name_ci(name)
            if other is not None and other.id != category.id:
                raise ClientCategoryNameAlreadyExistsError(f"Categoria já existe: {name!r}")
            category.name = name
        if tone is not None:
            category.tone = tone.value
        await self._repo.add(category)
        count = await self._repo.count_clients(category.id)
        return _to_item(CategoryRow(category=category, clients_count=count))

    async def delete_category(self, category_id: UUID) -> None:
        category = await self.get_category(category_id)
        in_use = await self._repo.count_clients(category_id)
        if in_use > 0:
            raise ClientCategoryInUseError(
                f"Categoria {category.name!r} em uso por {in_use} cliente(s).",
                user_message=(
                    f"Esta categoria está em uso por {in_use} cliente"
                    f"{'s' if in_use != 1 else ''} — mova-o{'s' if in_use != 1 else ''} para "
                    "outra categoria antes de excluir."
                ),
            )
        await self._repo.delete(category)
