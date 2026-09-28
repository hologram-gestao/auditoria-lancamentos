"""Camada de dados da ingestão por arquivo (Sprint 14, BACK 14.3).

SQL puro, uma função por query. **Toda leitura carrega `client_id` no próprio
`WHERE`** (§3.15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import ClientFileImport, User

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


class ClientFileImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_client(self, client_id: UUID) -> None:
        """Serializa a ingestão de UM cliente dentro da transação.

        `pg_advisory_xact_lock` (liberado no fim da transação), no molde de
        `lock_client_destination` (S12). Um argumento só — keyspace diferente do
        lock de dois argumentos do de-para, então os dois não se cruzam. Serve
        também ao registry de categorias (14.4), que confia no chamador para não
        criar a mesma grafia duas vezes em paralelo.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:client))").bindparams(
                client=str(client_id)
            )
        )

    async def exists(self, client_id: UUID, *, competence: date, file_hash: str) -> bool:
        """Leitura AMIGÁVEL do reenvio — a garantia é a UNIQUE em `add`."""
        stmt = select(ClientFileImport.id).where(
            ClientFileImport.client_id == client_id,
            ClientFileImport.competence == competence,
            ClientFileImport.file_hash == file_hash,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def add(self, record: ClientFileImport) -> bool:
        """Insere num SAVEPOINT; `False` se a UNIQUE do reenvio barrou.

        O SAVEPOINT desfaz só a tentativa (a sessão continua utilizável para o
        409 tipado), e a constraint é lida pelo NOME — outra violação re-levanta.
        """
        try:
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_client_file_imports_client_id_competence_file_hash":
                return False
            raise
        return True

    async def list_for_client(
        self, client_id: UUID, *, competence: date | None = None
    ) -> list[tuple[ClientFileImport, User]]:
        """Os arquivos processados do cliente (uma competência ou todas), com o autor."""
        stmt = (
            select(ClientFileImport, User)
            .join(User, User.id == ClientFileImport.created_by)
            .where(ClientFileImport.client_id == client_id)
        )
        if competence is not None:
            stmt = stmt.where(ClientFileImport.competence == competence)
        stmt = stmt.order_by(ClientFileImport.processed_at.desc(), ClientFileImport.id)
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]
