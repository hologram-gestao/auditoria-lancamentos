"""Camada de dados dos layouts de exportação (Sprint 13, BACK 13.2 — R1).

SQL puro, uma função por query. **O layout é POR ORGANIZAÇÃO:** toda leitura que parte
de um OBSERVADOR passa por `scoped_by_organization` (a org da LINHA dele; plataforma:
todas) e o layout por PK carrega o `AND organization_id` no próprio `SELECT` — layout de
outra organização vira 404, nunca o dado.

A leitura SEM observador (`get_layout_for_organization`) serve à geração do arquivo
(13.4), em que a organização vem do CLIENTE já validado pela rota — nunca do payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.authz import CurrentUser, scoped_by_organization
from app.db.models import (
    FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION,
    UQ_EXPORT_LAYOUT_ORG_NAME,
    UQ_EXPORT_LAYOUT_VERSION,
    AccountingFileGeneration,
    ExportLayout,
    ExportLayoutVersion,
    Organization,
    User,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class LayoutRow(NamedTuple):
    layout: ExportLayout
    latest_version: int


class VersionRow(NamedTuple):
    version: ExportLayoutVersion
    author: User


def _layout_select(viewer: CurrentUser) -> Select[tuple[ExportLayout]]:
    """SELECT base de layouts, restrito ao alcance do observador."""
    return scoped_by_organization(select(ExportLayout), ExportLayout.organization_id, viewer)


def _violated_constraint(exc: IntegrityError) -> str | None:
    """Nome da constraint violada, pelo diagnóstico do driver (psycopg)."""
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return name if isinstance(name, str) else None


class ExportLayoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ (observador) -----------------

    async def list_layouts(
        self, *, viewer: CurrentUser, organization_id: UUID | None = None
    ) -> list[LayoutRow]:
        """Layouts ao alcance do observador, com a última versão. Sem paginação: são
        poucos por organização (um por sistema contábil de destino, na prática)."""
        latest = (
            select(func.max(ExportLayoutVersion.version))
            .where(ExportLayoutVersion.layout_id == ExportLayout.id)
            .correlate(ExportLayout)
            .scalar_subquery()
        )
        stmt = _layout_select(viewer).add_columns(latest.label("latest_version"))
        if organization_id is not None:
            stmt = stmt.where(ExportLayout.organization_id == organization_id)
        stmt = stmt.order_by(ExportLayout.organization_id, ExportLayout.name, ExportLayout.id)
        rows = (await self._session.execute(stmt)).all()
        return [LayoutRow(layout=row[0], latest_version=int(row[1] or 0)) for row in rows]

    async def get_layout(self, layout_id: UUID, *, viewer: CurrentUser) -> ExportLayout | None:
        """Layout por PK **dentro do alcance** — de outra organização não volta."""
        stmt = _layout_select(viewer).where(ExportLayout.id == layout_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def lock_layout(self, layout_id: UUID, *, viewer: CurrentUser) -> ExportLayout | None:
        """O mesmo `SELECT` de `get_layout`, com `FOR UPDATE`: serializa duas versões
        novas do MESMO layout (a UNIQUE é a rede final)."""
        stmt = _layout_select(viewer).where(ExportLayout.id == layout_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_versions(self, layout_id: UUID) -> list[VersionRow]:
        """As versões de UM layout (já validado no alcance), da mais nova para a mais
        antiga, com o autor."""
        stmt = (
            select(ExportLayoutVersion, User)
            .join(User, User.id == ExportLayoutVersion.author_id)
            .where(ExportLayoutVersion.layout_id == layout_id)
            .order_by(ExportLayoutVersion.version.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [VersionRow(version=row[0], author=row[1]) for row in rows]

    async def latest_version(self, layout_id: UUID) -> int:
        stmt = select(func.max(ExportLayoutVersion.version)).where(
            ExportLayoutVersion.layout_id == layout_id
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def count_generations(self, layout_id: UUID) -> int:
        """Arquivos contábeis gerados por QUALQUER versão do layout (86e3nuuub)."""
        stmt = select(func.count(AccountingFileGeneration.id)).where(
            AccountingFileGeneration.layout_id == layout_id
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # ------------------------------ READ (organização já decidida) ----

    async def get_layout_for_organization(
        self, organization_id: UUID, layout_id: UUID
    ) -> ExportLayout | None:
        """Layout por PK restrito à organização do CLIENTE (13.4). Outra org → `None`."""
        stmt = select(ExportLayout).where(
            ExportLayout.id == layout_id, ExportLayout.organization_id == organization_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_version(self, layout_id: UUID, version: int) -> ExportLayoutVersion | None:
        """UMA versão de um layout já validado no alcance."""
        stmt = select(ExportLayoutVersion).where(
            ExportLayoutVersion.layout_id == layout_id, ExportLayoutVersion.version == version
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Leitor da organização — `resolve_organization_for_creation` e o 409 de
        organização suspensa."""
        return await self._session.get(Organization, organization_id)

    async def get_user(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    # ------------------------------ WRITE -----------------------------

    async def insert_layout(self, layout: ExportLayout, version: ExportLayoutVersion) -> bool:
        """Layout + versão 1, tudo ou nada, num SAVEPOINT.

        `UNIQUE(organization_id, name)` é a rede contra o duplo clique no "criar a
        partir do modelo" (checar antes não protege nada sob concorrência). Devolve
        `False` se ESSA UNIQUE barrou — nada foi gravado e a transação de quem chamou
        segue viva; o serviço responde 409. Outra violação re-levanta (é defeito).
        """
        try:
            async with self._session.begin_nested():
                self._session.add(layout)
                await self._session.flush()
                version.layout_id = layout.id
                self._session.add(version)
                await self._session.flush()
        except IntegrityError as exc:
            if _violated_constraint(exc) != UQ_EXPORT_LAYOUT_ORG_NAME:
                raise
            return False
        await self._session.refresh(layout)
        await self._session.refresh(version)
        return True

    async def insert_version(self, version: ExportLayoutVersion) -> bool:
        """Versão N+1 num SAVEPOINT. O `FOR UPDATE` do layout já serializa; a
        `UNIQUE(layout_id, version)` é a rede final (`False` → 409)."""
        try:
            async with self._session.begin_nested():
                self._session.add(version)
                await self._session.flush()
        except IntegrityError as exc:
            if _violated_constraint(exc) != UQ_EXPORT_LAYOUT_VERSION:
                raise
            return False
        await self._session.refresh(version)
        return True

    async def delete_layout(self, layout: ExportLayout) -> bool:
        """Apaga as versões e depois o layout, tudo ou nada, num SAVEPOINT (86e3nuuub).

        As duas FKs são RESTRICT: `export_layout_versions.layout_id` exige as versões
        fora antes do layout, e a FK composta da geração para a versão é a rede contra
        a corrida (arquivo gerado entre a contagem do serviço e este DELETE). Devolve
        `False` se ESSA FK barrou: nada foi apagado e a transação de quem chamou segue
        viva; o serviço responde 409. Outra violação re-levanta (é defeito).
        """
        try:
            async with self._session.begin_nested():
                await self._session.execute(
                    delete(ExportLayoutVersion).where(ExportLayoutVersion.layout_id == layout.id)
                )
                await self._session.delete(layout)
                await self._session.flush()
        except IntegrityError as exc:
            if _violated_constraint(exc) != FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION:
                raise
            return False
        return True

    async def commit(self) -> None:
        """Barreira: a rota só responde DEPOIS do commit (ADR-091-BE)."""
        await self._session.commit()
