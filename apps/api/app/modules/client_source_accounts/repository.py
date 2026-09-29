"""Camada de dados da associação conta de origem → conta do banco (Sprint 16, BACK 16.3).

SQL puro, uma função por query. **Toda query carrega `client_id` no próprio WHERE**
(§3.15). A associação é CONFIGURAÇÃO mutável: a gravação é UPSERT com `ON CONFLICT`
— duplo clique ou duas abas não produzem duas linhas nem um 500 de `IntegrityError`
(§7 Backend). São DUAS garantias de unicidade no banco (a UNIQUE das contas explícitas
e o índice único PARCIAL do slot padrão), então há dois alvos de `ON CONFLICT`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import (
    UQ_SOURCE_ACCOUNT_BINDING,
    ClientAccountingAccount,
    ClientConnection,
    ClientMovement,
    ClientSourceAccountBinding,
    default_binding_index_predicate,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.client_mapping.partida import BindingKey


class SourceAccountBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_bindings(self, client_id: UUID) -> list[ClientSourceAccountBinding]:
        stmt = (
            select(ClientSourceAccountBinding)
            .where(ClientSourceAccountBinding.client_id == client_id)
            .order_by(
                ClientSourceAccountBinding.source_type,
                ClientSourceAccountBinding.source_account_id.nulls_first(),
            )
            .execution_options(populate_existing=True)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def bank_codes(self, client_id: UUID) -> dict[BindingKey, str]:
        """`(tipo, conta de origem | None) → código da conta do banco`, numa query.

        O JOIN com o plano carrega `client_id` dos DOIS lados: uma associação jamais
        resolve para conta de outro cliente, nem que a linha fosse forjada no banco.
        """
        stmt = (
            select(
                ClientSourceAccountBinding.source_type,
                ClientSourceAccountBinding.source_account_id,
                ClientAccountingAccount.code,
            )
            .join(
                ClientAccountingAccount,
                (ClientAccountingAccount.id == ClientSourceAccountBinding.accounting_account_id)
                & (ClientAccountingAccount.client_id == ClientSourceAccountBinding.client_id),
            )
            .where(ClientSourceAccountBinding.client_id == client_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {(row.source_type, row.source_account_id): row.code for row in rows}

    async def movement_source_keys(self, client_id: UUID) -> set[BindingKey]:
        """As contas de origem VISTAS na base de movimentos (conta nula = sem conta)."""
        stmt = (
            select(ClientMovement.source_type, ClientMovement.source_account_id)
            .where(ClientMovement.client_id == client_id)
            .distinct()
        )
        rows = (await self._session.execute(stmt)).all()
        return {(row.source_type, row.source_account_id) for row in rows}

    async def connection_types(self, client_id: UUID) -> set[str]:
        """Os tipos de provedor das conexões do cliente (em qualquer estado)."""
        stmt = (
            select(ClientConnection.provider_type)
            .where(ClientConnection.client_id == client_id)
            .distinct()
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def upsert(
        self,
        client_id: UUID,
        *,
        source_type: str,
        source_account_id: str | None,
        accounting_account_id: UUID,
        author_id: UUID,
    ) -> tuple[ClientSourceAccountBinding, bool]:
        """Cria ou TROCA a associação. Devolve `(linha, criou?)`.

        Conta explícita: `ON CONFLICT` na UNIQUE. Slot padrão (`source_account_id`
        nulo): `ON CONFLICT` no índice PARCIAL — `index_where` repete o predicado. A
        UNIQUE do banco decide, não uma leitura anterior. `xmax = 0` separa inserção
        de substituição sem segunda consulta (precedente do mapeamento de entrada).
        """
        values: dict[str, Any] = {
            "client_id": client_id,
            "source_type": source_type,
            "source_account_id": source_account_id,
            "accounting_account_id": accounting_account_id,
            "created_by": author_id,
            "updated_by": author_id,
        }
        update_values = {
            "accounting_account_id": accounting_account_id,
            "updated_by": author_id,
            # O `onupdate` do mixin não vale para ON CONFLICT (ADR-083-BE).
            "updated_at": func.clock_timestamp(),
        }
        insert: Any = pg_insert(ClientSourceAccountBinding).values(**values)
        if source_account_id is None:
            stmt = insert.on_conflict_do_update(
                index_elements=["client_id", "source_type"],
                index_where=text(default_binding_index_predicate()),
                set_=update_values,
            )
        else:
            stmt = insert.on_conflict_do_update(
                constraint=UQ_SOURCE_ACCOUNT_BINDING, set_=update_values
            )
        stmt = stmt.returning(
            ClientSourceAccountBinding.id, literal_column("(xmax = 0)").label("inserted")
        )
        row = (await self._session.execute(stmt)).one()
        reload = (
            select(ClientSourceAccountBinding)
            .where(
                ClientSourceAccountBinding.id == UUID(str(row.id)),
                ClientSourceAccountBinding.client_id == client_id,
            )
            .execution_options(populate_existing=True)
        )
        binding = (await self._session.execute(reload)).scalar_one()
        return binding, bool(row.inserted)
