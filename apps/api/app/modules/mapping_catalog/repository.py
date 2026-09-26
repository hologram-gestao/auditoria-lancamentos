"""Camada de dados do catálogo de destinos e alvos (Sprint 12, BACK 12.3 — R1).

SQL puro, uma função por query. **O catálogo é POR ORGANIZAÇÃO:** toda leitura que
parte de um OBSERVADOR passa por `scoped_by_organization` (a org da LINHA dele;
plataforma: todas) e o alvo por PK carrega o `AND organization_id` no próprio
`SELECT` — destino ou alvo de outra organização vira 404, nunca o dado.

As leituras SEM observador (`*_for_organization`) servem às tasks de de-para
(12.4 a 12.6), onde a organização vem do CLIENTE já validado pela rota — nunca do
payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.authz import CurrentUser, scoped_by_organization
from app.db.models import ClientMappingDecision, Organization
from app.db.models.mapping_catalog import (
    DEFAULT_DESTINATION_TYPES,
    UQ_MAPPING_DESTINATION_ORG_TYPE,
    MappingDestination,
    MappingTarget,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class DestinationRow(NamedTuple):
    destination: MappingDestination
    targets_count: int


def _destination_select(viewer: CurrentUser) -> Select[tuple[MappingDestination]]:
    """SELECT base de destinos, restrito ao alcance do observador."""
    stmt = select(MappingDestination)
    return scoped_by_organization(stmt, MappingDestination.organization_id, viewer)


class MappingCatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ (observador) -----------------

    async def list_destinations(
        self, *, viewer: CurrentUser, organization_id: UUID | None = None
    ) -> list[DestinationRow]:
        """Destinos ao alcance do observador, com a contagem de alvos.

        O catálogo é pequeno por natureza (cinco destinos por organização) — sem
        paginação. `organization_id` é o filtro opcional da plataforma, já decidido
        por `resolve_organization_filter` no serviço.
        """
        count_sq = (
            select(func.count(MappingTarget.id))
            .where(MappingTarget.destination_id == MappingDestination.id)
            .correlate(MappingDestination)
            .scalar_subquery()
        )
        stmt = _destination_select(viewer).add_columns(count_sq.label("targets_count"))
        if organization_id is not None:
            stmt = stmt.where(MappingDestination.organization_id == organization_id)
        stmt = stmt.order_by(
            MappingDestination.organization_id, MappingDestination.destination_type
        )
        rows = (await self._session.execute(stmt)).all()
        return [DestinationRow(destination=row[0], targets_count=int(row[1] or 0)) for row in rows]

    async def get_destination(
        self, destination_id: UUID, *, viewer: CurrentUser
    ) -> MappingDestination | None:
        """Destino por PK **dentro do alcance** — de outra organização não volta."""
        stmt = _destination_select(viewer).where(MappingDestination.id == destination_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ------------------------------ READ (organização já decidida) ----

    async def get_destination_for_organization(
        self, organization_id: UUID, destination_id: UUID
    ) -> MappingDestination | None:
        """Destino por PK restrito à organização do CLIENTE (12.4 a 12.6)."""
        stmt = select(MappingDestination).where(
            MappingDestination.id == destination_id,
            MappingDestination.organization_id == organization_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_destination_by_type(
        self, organization_id: UUID, destination_type: str
    ) -> MappingDestination | None:
        stmt = select(MappingDestination).where(
            MappingDestination.organization_id == organization_id,
            MappingDestination.destination_type == destination_type,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Leitor da organização — `resolve_organization_for_creation` e o 409 de
        organização suspensa."""
        return await self._session.get(Organization, organization_id)

    async def list_targets(
        self,
        destination_id: UUID,
        *,
        active: bool | None = None,
        code_prefix: str | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[MappingTarget], int]:
        """Página dos alvos de UM destino (já validado no alcance) + total.

        Ordem por código: é como o plano de demonstração se lê, e é total (o
        código é único no destino), então a paginação não repete nem pula linha.
        """
        stmt = select(MappingTarget).where(MappingTarget.destination_id == destination_id)
        if active is not None:
            stmt = stmt.where(MappingTarget.active.is_(active))
        if code_prefix:
            stmt = stmt.where(MappingTarget.code.startswith(code_prefix, autoescape=True))
        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())
        page = stmt.order_by(MappingTarget.code).limit(limit).offset(offset)
        rows = list((await self._session.execute(page)).scalars().all())
        return rows, total

    async def get_target(self, destination_id: UUID, target_id: UUID) -> MappingTarget | None:
        """Alvo por PK DENTRO do destino — o destino já passou pelo alcance."""
        stmt = select(MappingTarget).where(
            MappingTarget.id == target_id, MappingTarget.destination_id == destination_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_targets_by_codes(
        self, destination_id: UUID, codes: Iterable[str]
    ) -> dict[str, MappingTarget]:
        """Os alvos do destino com estes códigos, por código. UMA query para o lote."""
        wanted = list(set(codes))
        if not wanted:
            return {}
        stmt = select(MappingTarget).where(
            MappingTarget.destination_id == destination_id, MappingTarget.code.in_(wanted)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.code: row for row in rows}

    async def get_targets_by_ids(
        self, destination_id: UUID, target_ids: Iterable[UUID]
    ) -> dict[UUID, MappingTarget]:
        wanted = list(set(target_ids))
        if not wanted:
            return {}
        stmt = select(MappingTarget).where(
            MappingTarget.destination_id == destination_id, MappingTarget.id.in_(wanted)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.id: row for row in rows}

    async def count_decisions_for_target(self, target_id: UUID) -> int:
        """Quantas decisões (de qualquer cliente, qualquer vigência) apontam o alvo.

        É a contagem que decide o 409 de "apagar alvo em uso" — a FK RESTRICT
        recusaria também, o serviço só torna a recusa legível.
        """
        stmt = select(func.count(ClientMappingDecision.id)).where(
            ClientMappingDecision.target_id == target_id
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # ------------------------------ WRITE -----------------------------

    async def add(self, obj: MappingDestination | MappingTarget) -> None:
        """Insere/atualiza com flush + refresh (carrega `created_at`/`updated_at`)."""
        self._session.add(obj)
        await self._session.flush()
        await self._session.refresh(obj)

    async def add_targets(self, targets: Sequence[MappingTarget]) -> None:
        """Lote de alvos numa ida só. A UNIQUE `(destino, código)` é a rede final."""
        self._session.add_all(list(targets))
        await self._session.flush()
        for target in targets:
            await self._session.refresh(target)

    async def delete_target(self, target: MappingTarget) -> None:
        await self._session.delete(target)
        await self._session.flush()

    async def seed_default_destinations(self, organization_id: UUID) -> None:
        """Os cinco destinos do PRD na organização — IDEMPOTENTE.

        `ON CONFLICT DO NOTHING` sobre `UNIQUE(organization_id, destination_type)`:
        rodar duas vezes (ou sobre uma organização que já tenha um deles) não
        duplica nem sobrescreve o nome que alguém tenha editado. É a mesma lista
        que a migration `e6b2c9d47f13` semeou nas organizações que já existiam.
        """
        stmt = (
            pg_insert(MappingDestination)
            .values(
                [
                    {
                        "organization_id": organization_id,
                        "destination_type": destination_type,
                        "name": name,
                    }
                    for destination_type, name in DEFAULT_DESTINATION_TYPES
                ]
            )
            .on_conflict_do_nothing(constraint=UQ_MAPPING_DESTINATION_ORG_TYPE)
        )
        await self._session.execute(stmt)
