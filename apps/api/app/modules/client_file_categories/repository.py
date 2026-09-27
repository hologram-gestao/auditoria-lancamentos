"""Camada de dados das categorias de origem do arquivo (Sprint 14, BACK 14.4).

SQL puro, uma função por query. **Toda leitura carrega `client_id` no próprio
`WHERE`** (§3.15): o registry de A nunca carrega linha de B — e o AAD garante que,
mesmo que carregasse, o rótulo de A não decifra com a DEK de B.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.db.models.client_file_category import ClientFileCategory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ClientFileCategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_client(self, client_id: UUID) -> list[ClientFileCategory]:
        """Todas as categorias do cliente, em ordem estável (primeira ocorrência)."""
        stmt = (
            select(ClientFileCategory)
            .where(ClientFileCategory.client_id == client_id)
            .order_by(ClientFileCategory.first_seen_at, ClientFileCategory.code)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add(self, category: ClientFileCategory) -> None:
        """Insere e dá flush — a pk precisa existir para compor o AAD (§4.1)."""
        self._session.add(category)
        await self._session.flush()
