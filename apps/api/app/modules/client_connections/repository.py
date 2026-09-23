"""Camada de dados das conexões de origem (Sprint 9, BACK 09.2 + 09.3).

SQL puro, uma função por query. **Toda query filtra por `client_id`** — nenhuma
busca conexão só pela PK: a linha pertence a um tenant e o tenant vem do
`Client` já validado pela rota (§3.15). Conexão de outro cliente não carrega, e
o miss vira 404 em vez do dado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CursorResult, delete, select, update

from app.db.models.client_connection import ClientConnection, ConnectionStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ClientConnectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_client(self, client_id: UUID) -> list[ClientConnection]:
        """As conexões do cliente, em ordem determinística (tipo, rótulo).

        Determinística de propósito: `select_capable_connection` desempata pela
        PRIMEIRA da sequência, e uma ordem instável faria a mesma conta ser
        escolhida hoje e outra amanhã, sem ninguém mudar nada.
        """
        stmt = (
            select(ClientConnection)
            .where(ClientConnection.client_id == client_id)
            .order_by(ClientConnection.provider_type, ClientConnection.label)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_in_client(
        self, connection_id: UUID, *, client_id: UUID
    ) -> ClientConnection | None:
        """Detalhe por PK **dentro do tenant** — nunca só pela PK.

        `AND client_id = <tenant>` no próprio `SELECT` (padrão anti-IDOR da
        §3.15): conexão de outro cliente não carrega, e o service converte o
        `None` em 404 — nunca no dado.
        """
        stmt = select(ClientConnection).where(
            ClientConnection.id == connection_id,
            ClientConnection.client_id == client_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_by_label(
        self, *, client_id: UUID, provider_type: str, label: str
    ) -> ClientConnection | None:
        """A conexão que ocupa `(cliente, tipo, rótulo)`, se existir.

        Serve ao 409 que aponta a existente. **Não substitui a UNIQUE do banco**:
        entre este `SELECT` e o `INSERT` cabe outra request, e é a constraint
        que decide — o service trata o `IntegrityError` relendo por aqui.
        """
        stmt = select(ClientConnection).where(
            ClientConnection.client_id == client_id,
            ClientConnection.provider_type == provider_type,
            ClientConnection.label == label,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count_of_type(self, *, client_id: UUID, provider_type: str) -> int:
        """Quantas conexões daquele tipo o cliente já tem (para o rótulo padrão)."""
        stmt = select(ClientConnection).where(
            ClientConnection.client_id == client_id,
            ClientConnection.provider_type == provider_type,
        )
        return len((await self._session.execute(stmt)).scalars().all())

    async def add(self, connection: ClientConnection) -> None:
        """Insere e dá flush — a pk precisa existir para compor o AAD (§4.1)."""
        self._session.add(connection)
        await self._session.flush()

    async def delete_in_client(self, connection_id: UUID, *, client_id: UUID) -> bool:
        """Exclusão DEFINITIVA da linha, dentro do tenant.

        Divergência deliberada do soft delete padrão do repositório: o PRD exige
        que reconectar o mesmo `(tipo, rótulo)` volte a funcionar, e uma linha
        com `deleted_at` continuaria ocupando a UNIQUE. A trilha do que
        aconteceu vive em `access_audit`, não na linha.
        """
        stmt = delete(ClientConnection).where(
            ClientConnection.id == connection_id,
            ClientConnection.client_id == client_id,
        )
        result = await self._session.execute(stmt)
        if not isinstance(result, CursorResult):  # pragma: no cover
            return False
        return bool(result.rowcount)

    async def delete_all_for_client(self, client_id: UUID) -> None:
        """Todas as conexões do cliente — usado pelo ENCERRAMENTO (§4.12).

        A DEK morre no encerramento; a credencial cifrada da origem vira
        ciphertext morto, que não serve para nada e não deve ficar.
        """
        await self._session.execute(
            delete(ClientConnection).where(ClientConnection.client_id == client_id)
        )

    async def _update_status(
        self, connection_id: UUID, *, status: str, checked_at: datetime
    ) -> bool:
        """`UPDATE` de estado + carimbo. Lista as DUAS colunas que muda, e só elas.

        ⚠️ **Nunca toca nas colunas cifradas.** Credencial recusada não é
        credencial perdida: apagar o ciphertext aqui transformaria "a senha
        mudou, atualize" em "a conexão sumiu, refaça tudo", e a decifragem do
        que já estava gravado morreria junto.
        """
        stmt = (
            update(ClientConnection)
            .where(ClientConnection.id == connection_id)
            .values(status=status, last_checked_at=checked_at)
        )
        result = await self._session.execute(stmt)
        # UPDATE devolve CursorResult (com rowcount); o narrow é para o mypy —
        # o caminho else não existe em runtime.
        if not isinstance(result, CursorResult):  # pragma: no cover
            return False
        return bool(result.rowcount)

    async def mark_connection_error(
        self, connection_id: UUID, *, at: datetime | None = None
    ) -> bool:
        """Credencial recusada pelo provedor: estado vira `erro`, credencial FICA.

        Convergente — chamar duas vezes deixa o mesmo estado (a segunda só
        reescreve `last_checked_at`). Devolve se alguma linha foi atingida.
        """
        return await self._update_status(
            connection_id,
            status=ConnectionStatus.ERRO.value,
            checked_at=at or datetime.now(UTC),
        )

    async def mark_connection_checked(
        self, connection_id: UUID, *, at: datetime | None = None
    ) -> bool:
        """Verificação bem-sucedida: volta para `ativa` e carimba."""
        return await self._update_status(
            connection_id,
            status=ConnectionStatus.ATIVA.value,
            checked_at=at or datetime.now(UTC),
        )
