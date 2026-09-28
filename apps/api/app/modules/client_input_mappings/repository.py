"""Camada de dados do mapeamento de entrada (Sprint 14, BACK 14.1).

SQL puro, uma função por query. **Toda leitura carrega `client_id` no próprio
`WHERE`** — a linha pertence a um tenant, e o tenant vem do `Client` já validado
pela rota (§3.15).

**Um mapeamento por cliente, e a substituição é `ON CONFLICT (client_id)`** — a
UNIQUE do banco decide, não uma leitura anterior: duas abas salvando ao mesmo
tempo não produzem duas linhas nem um 500 de `IntegrityError` (§7 Backend).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models.client_input_mapping import UQ_CLIENT_INPUT_MAPPING_CLIENT, ClientInputMapping

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ClientInputMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_client(self, client_id: UUID) -> ClientInputMapping | None:
        stmt = select(ClientInputMapping).where(ClientInputMapping.client_id == client_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self, client_id: UUID, *, author_id: UUID, values: dict[str, Any]
    ) -> tuple[ClientInputMapping, bool]:
        """Cria ou SUBSTITUI o mapeamento do cliente. Devolve `(linha, criou?)`.

        `xmax = 0` na linha devolvida pelo `RETURNING` é o jeito do Postgres de
        dizer "esta linha foi inserida, não atualizada" — é o que separa o 201 do
        200 sem uma segunda consulta. `created_by` fica o do INSERT original; só
        `updated_by` acompanha a substituição.
        """
        insert_values = {
            "client_id": client_id,
            "created_by": author_id,
            "updated_by": author_id,
            **values,
        }
        # O `onupdate` do `TimestampMixin` NÃO vale para `ON CONFLICT DO UPDATE`:
        # sem esta coluna explícita, substituir o mapeamento deixaria o
        # "Atualizado em" da tela no valor da criação. `clock_timestamp()` e não
        # `now()`: o `now()` é o início da TRANSAÇÃO, e duas substituições na
        # mesma transação gravariam o mesmo instante.
        update_values = {"updated_by": author_id, "updated_at": func.clock_timestamp(), **values}
        stmt: Any = (
            pg_insert(ClientInputMapping)
            .values(**insert_values)
            .on_conflict_do_update(constraint=UQ_CLIENT_INPUT_MAPPING_CLIENT, set_=update_values)
            .returning(ClientInputMapping.id, literal_column("(xmax = 0)").label("inserted"))
        )
        row = (await self._session.execute(stmt)).one()
        mapping_id = UUID(str(row.id))
        created = bool(row.inserted)
        # Releitura pela chave do tenant, com `populate_existing`: a instância
        # pode já estar no identity map (substituição na mesma sessão) com os
        # valores antigos.
        reload = (
            select(ClientInputMapping)
            .where(ClientInputMapping.id == mapping_id, ClientInputMapping.client_id == client_id)
            .execution_options(populate_existing=True)
        )
        mapping = (await self._session.execute(reload)).scalar_one()
        return mapping, created
