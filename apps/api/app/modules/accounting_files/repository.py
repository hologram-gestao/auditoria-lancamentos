"""Camada de dados do arquivo contábil (Sprint 13, BACK 13.4 — R3).

SQL puro, uma função por query. **Toda query filtra `client_id` no próprio `SELECT`**
(§3.15): a materialização e a geração por PK de outro cliente não voltam (viram 404), e
o `client_id` vem do cliente já validado pela rota (`AccessibleClientDep`/`OpenClientDep`),
nunca do payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import func, select

from app.db.models import (
    ACCOUNTING_DESTINATION_TYPE,
    AccountingFileGeneration,
    ClientMappingMaterialization,
    ExportLayout,
    User,
)

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class GenerationRow(NamedTuple):
    generation: AccountingFileGeneration
    author: User
    materialization_version: int
    layout_name: str


class AccountingFileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ MATERIALIZAÇÃO ---------------------

    async def latest_accounting_materialization(
        self, client_id: UUID, competence: date
    ) -> ClientMappingMaterialization | None:
        """A ÚLTIMA versão materializada no destino `conta_contabil` da competência.

        Pelo tipo DESNORMALIZADO na própria materialização (imutável), com `client_id` no
        WHERE. `None` = a competência não tem materialização nesse destino (a geração é
        409, e NADA é materializado aqui).
        """
        stmt = (
            select(ClientMappingMaterialization)
            .where(
                ClientMappingMaterialization.client_id == client_id,
                ClientMappingMaterialization.destination_type == ACCOUNTING_DESTINATION_TYPE,
                ClientMappingMaterialization.competence == competence,
            )
            .order_by(ClientMappingMaterialization.version.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_materialization(
        self, client_id: UUID, materialization_id: UUID
    ) -> ClientMappingMaterialization | None:
        """Materialização por PK, SÓ do cliente (outro cliente → `None` → 404)."""
        stmt = select(ClientMappingMaterialization).where(
            ClientMappingMaterialization.id == materialization_id,
            ClientMappingMaterialization.client_id == client_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ------------------------------ GERAÇÕES ---------------------------

    async def insert_generation(self, generation: AccountingFileGeneration) -> None:
        """Cada POST é uma geração (idempotência não exigida): sem UNIQUE a disputar."""
        self._session.add(generation)
        await self._session.flush()
        await self._session.refresh(generation)

    async def get_generation(
        self, client_id: UUID, generation_id: UUID
    ) -> AccountingFileGeneration | None:
        """Geração por PK, SÓ do cliente (outro cliente → `None` → 404)."""
        stmt = select(AccountingFileGeneration).where(
            AccountingFileGeneration.id == generation_id,
            AccountingFileGeneration.client_id == client_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_row(self, client_id: UUID, generation_id: UUID) -> GenerationRow | None:
        """UMA geração com autor, versão da materialização e nome do layout."""
        rows = await self._rows(client_id, generation_id=generation_id, limit=1, offset=0)
        return rows[0] if rows else None

    async def list_generations(
        self, client_id: UUID, *, competence: date | None, limit: int, offset: int
    ) -> tuple[list[GenerationRow], int]:
        """Página do histórico de gerações do cliente (mais recentes primeiro) + total."""
        count = select(func.count(AccountingFileGeneration.id)).where(
            AccountingFileGeneration.client_id == client_id
        )
        if competence is not None:
            count = count.where(AccountingFileGeneration.competence == competence)
        total = int((await self._session.execute(count)).scalar_one())
        rows = await self._rows(client_id, competence=competence, limit=limit, offset=offset)
        return rows, total

    async def _rows(
        self,
        client_id: UUID,
        *,
        generation_id: UUID | None = None,
        competence: date | None = None,
        limit: int,
        offset: int,
    ) -> list[GenerationRow]:
        # O JOIN com a materialização e com o layout carrega o `client_id`/a org já
        # garantidos pela geração (FKs); o filtro de tenant é o da própria geração.
        stmt = (
            select(
                AccountingFileGeneration,
                User,
                ClientMappingMaterialization.version,
                ExportLayout.name,
            )
            .join(User, User.id == AccountingFileGeneration.author_id)
            .join(
                ClientMappingMaterialization,
                (ClientMappingMaterialization.id == AccountingFileGeneration.materialization_id)
                & (ClientMappingMaterialization.client_id == AccountingFileGeneration.client_id),
            )
            .join(ExportLayout, ExportLayout.id == AccountingFileGeneration.layout_id)
            .where(AccountingFileGeneration.client_id == client_id)
        )
        if generation_id is not None:
            stmt = stmt.where(AccountingFileGeneration.id == generation_id)
        if competence is not None:
            stmt = stmt.where(AccountingFileGeneration.competence == competence)
        stmt = (
            stmt.order_by(
                AccountingFileGeneration.created_at.desc(), AccountingFileGeneration.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            GenerationRow(
                generation=row[0], author=row[1], materialization_version=row[2], layout_name=row[3]
            )
            for row in rows
        ]

    async def commit(self) -> None:
        """Barreira: a rota só responde DEPOIS do commit (ADR-093-BE)."""
        await self._session.commit()
