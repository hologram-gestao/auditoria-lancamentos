"""Regras do catálogo de categorias de cliente (86e34jd8m + 86e36ecqz).

- Catálogo POR ORGANIZAÇÃO: o observador lê e escreve só o da própria (a
  plataforma, todos). A categoria nasce na organização decidida pela LINHA do
  ator (`resolve_organization_for_creation`): o admin na própria, a plataforma
  escolhe (obrigatório). Alvo por PK fora do alcance é 404.
- Nome único SEM caixa dentro da organização: "Fintech" e "fintech" são a
  mesma categoria — o pre-check devolve 409 legível antes do IntegrityError da
  UNIQUE `(organization_id, name)`.
- DELETE só de categoria órfã. Em uso → 409 com a contagem; o admin realoca
  os clientes antes (a FK em `clients` é RESTRICT, então o banco também
  recusaria — o service só torna a recusa legível).
"""

from __future__ import annotations

from uuid import UUID

from app.core.authz import (
    CurrentUser,
    resolve_organization_filter,
    resolve_organization_for_creation,
)
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
        organization_id=row.category.organization_id,
        organization_name=row.organization_name,
    )


class ClientCategoryService:
    def __init__(self, repository: ClientCategoryRepository) -> None:
        self._repo = repository

    async def list_categories(
        self, *, viewer: CurrentUser, requested_organization_id: UUID | None = None
    ) -> list[ClientCategoryItem]:
        organization_id = resolve_organization_filter(viewer, requested_organization_id)
        rows = await self._repo.list_all(viewer=viewer, organization_id=organization_id)
        return [_to_item(row) for row in rows]

    async def get_category(self, category_id: UUID, *, viewer: CurrentUser) -> CategoryRow:
        """Categoria ao alcance do observador — 404 fora dele (anti-IDOR)."""
        row = await self._repo.get_by_id(category_id, viewer=viewer)
        if row is None:
            raise NotFoundError("Categoria não encontrada.")
        return row

    async def create_category(
        self,
        *,
        actor: CurrentUser,
        name: str,
        tone: ClientCategoryTone,
        requested_organization_id: UUID | None = None,
    ) -> ClientCategoryItem:
        organization_id = await resolve_organization_for_creation(
            actor,
            requested_organization_id,
            get_organization=self._repo.get_organization,
            subject="a categoria",
        )
        if await self._repo.get_by_name_ci(name, organization_id=organization_id) is not None:
            raise ClientCategoryNameAlreadyExistsError(f"Categoria já existe: {name!r}")
        category = ClientCategory(name=name, tone=tone.value, organization_id=organization_id)
        await self._repo.add(category)
        if actor.is_platform:
            organization = await self._repo.get_organization(organization_id)
            organization_name = organization.name if organization is not None else ""
        else:
            organization_name = actor.organization_name or ""
        return _to_item(
            CategoryRow(category=category, clients_count=0, organization_name=organization_name)
        )

    async def update_category(
        self,
        category_id: UUID,
        *,
        viewer: CurrentUser,
        name: str | None = None,
        tone: ClientCategoryTone | None = None,
    ) -> ClientCategoryItem:
        row = await self.get_category(category_id, viewer=viewer)
        category = row.category
        if name is not None:
            other = await self._repo.get_by_name_ci(name, organization_id=category.organization_id)
            if other is not None and other.id != category.id:
                raise ClientCategoryNameAlreadyExistsError(f"Categoria já existe: {name!r}")
            category.name = name
        if tone is not None:
            category.tone = tone.value
        await self._repo.add(category)
        count = await self._repo.count_clients(category.id)
        return _to_item(
            CategoryRow(
                category=category, clients_count=count, organization_name=row.organization_name
            )
        )

    async def delete_category(self, category_id: UUID, *, viewer: CurrentUser) -> None:
        category = (await self.get_category(category_id, viewer=viewer)).category
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
