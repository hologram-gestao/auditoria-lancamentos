"""Serviço do mapeamento de entrada (Sprint 14, BACK 14.1 — R1).

Regra de negócio pura: sem HTTP. Quem decide tenant e permissão é a rota
(`AccessibleClientDep`/`OpenClientDep` + `ManageInputMappingDep`); aqui mora o
pouco que é caro de duplicar — a tradução do payload para colunas, o upsert e o
check de cliente encerrado, que existe no serviço além da rota pelo mesmo motivo
da base de movimentos (ADR-072-BE): o serviço é quem escreve, e o critério pede a
prova sem rota.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import ClientClosedError
from app.modules.client_input_mappings.repository import ClientInputMappingRepository
from app.modules.client_input_mappings.schemas import InputMappingFields, InputMappingResponse

if TYPE_CHECKING:
    from app.core.authz import CurrentUser
    from app.db.models import Client
    from app.db.models.client_input_mapping import ClientInputMapping


class ClientInputMappingService:
    def __init__(self, repository: ClientInputMappingRepository) -> None:
        self._repo = repository

    async def get_row(self, client: Client) -> ClientInputMapping | None:
        """A linha ORM do mapeamento — para quem precisa LER o layout (a ingestão, 14.3)."""
        return await self._repo.get_for_client(client.id)

    async def get(self, client: Client) -> InputMappingResponse | None:
        row = await self.get_row(client)
        return None if row is None else InputMappingResponse.from_row(row)

    async def replace(
        self, client: Client, *, actor: CurrentUser, fields: InputMappingFields
    ) -> tuple[InputMappingResponse, bool]:
        """Cria ou substitui o mapeamento do cliente. Devolve `(resposta, criou?)`."""
        if client.closed_at is not None:
            raise ClientClosedError(
                f"Cliente {client.id} está encerrado desde {client.closed_at.isoformat()}."
            )
        # `mode="json"`: os enums saem como o VALOR (a string do CHECK), não o membro.
        values = fields.model_dump(mode="json", by_alias=False)
        row, created = await self._repo.upsert(client.id, author_id=UUID(actor.id), values=values)
        return InputMappingResponse.from_row(row), created
