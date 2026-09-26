"""Regras do catálogo de destinos e alvos (Sprint 12, BACK 12.3 — R1).

- **Por organização.** O observador lê e escreve só o da própria (a plataforma,
  todos). O destino NASCE na organização decidida pela LINHA do ator
  (`resolve_organization_for_creation`); alvo e destino por PK fora do alcance = 404.
- **Organização suspensa não recebe escrita** (409): nem a plataforma edita o
  catálogo de um BPO suspenso.
- **Alvo é código de catálogo.** O validador `require_target` é o ÚNICO lugar que
  decide se um código aponta um alvo utilizável — as decisões (12.4) e a
  importação (12.5) o consomem. Inexistente OU desativado → 422 NOMEANDO o código.
- **Apagar alvo referenciado → 409**; desativar é o caminho.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.authz import (
    CurrentUser,
    resolve_organization_filter,
    resolve_organization_for_creation,
)
from app.core.exceptions import (
    MappingDestinationTypeAlreadyExistsError,
    MappingTargetCodeAlreadyExistsError,
    MappingTargetInUseError,
    MappingTargetNotFoundError,
    NotFoundError,
    OrganizationInactiveError,
)
from app.db.models.mapping_catalog import MappingDestination, MappingTarget
from app.modules.mapping_catalog.schemas import (
    MappingDestinationItem,
    MappingTargetCreate,
    MappingTargetItem,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from app.modules.mapping_catalog.repository import MappingCatalogRepository


class MappingCatalogService:
    def __init__(self, repository: MappingCatalogRepository) -> None:
        self._repo = repository

    # ------------------------------ DESTINOS --------------------------

    async def list_destinations(
        self, *, viewer: CurrentUser, requested_organization_id: UUID | None = None
    ) -> list[MappingDestinationItem]:
        organization_id = resolve_organization_filter(viewer, requested_organization_id)
        rows = await self._repo.list_destinations(viewer=viewer, organization_id=organization_id)
        return [
            MappingDestinationItem.build(row.destination, targets_count=row.targets_count)
            for row in rows
        ]

    async def get_destination(
        self, destination_id: UUID, *, viewer: CurrentUser
    ) -> MappingDestination:
        """Destino ao alcance do observador — 404 fora dele (anti-IDOR, sem vazar)."""
        destination = await self._repo.get_destination(destination_id, viewer=viewer)
        if destination is None:
            raise NotFoundError("Destino não encontrado.")
        return destination

    async def create_destination(
        self,
        *,
        actor: CurrentUser,
        destination_type: str,
        name: str,
        requested_organization_id: UUID | None = None,
    ) -> MappingDestinationItem:
        organization_id = await resolve_organization_for_creation(
            actor,
            requested_organization_id,
            get_organization=self._repo.get_organization,
            subject="o destino",
        )
        await self._ensure_organization_active(organization_id)
        if await self._repo.get_destination_by_type(organization_id, destination_type):
            raise MappingDestinationTypeAlreadyExistsError(
                f"Destino {destination_type!r} já existe na organização {organization_id}.",
                user_message=f"Esta organização já tem um destino do tipo '{destination_type}'.",
            )
        destination = MappingDestination(
            organization_id=organization_id,
            destination_type=destination_type,
            name=name,
            active=True,
        )
        await self._repo.add(destination)
        return MappingDestinationItem.build(destination, targets_count=0)

    async def update_destination(
        self,
        destination_id: UUID,
        *,
        actor: CurrentUser,
        name: str | None = None,
        active: bool | None = None,
    ) -> MappingDestinationItem:
        destination = await self.get_destination(destination_id, viewer=actor)
        await self._ensure_organization_active(destination.organization_id)
        if name is not None:
            destination.name = name
        if active is not None:
            destination.active = active
        await self._repo.add(destination)
        _, total = await self._repo.list_targets(destination.id, limit=1, offset=0)
        return MappingDestinationItem.build(destination, targets_count=total)

    # ------------------------------ ALVOS -----------------------------

    async def list_targets(
        self,
        destination_id: UUID,
        *,
        viewer: CurrentUser,
        page: int,
        page_size: int,
        active: bool | None = None,
        code_prefix: str | None = None,
    ) -> tuple[list[MappingTargetItem], PaginationMeta]:
        destination = await self.get_destination(destination_id, viewer=viewer)
        rows, total = await self._repo.list_targets(
            destination.id,
            active=active,
            code_prefix=code_prefix,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        return [MappingTargetItem.build(row) for row in rows], PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        )

    async def create_targets(
        self,
        destination_id: UUID,
        *,
        actor: CurrentUser,
        targets: Sequence[MappingTargetCreate],
    ) -> list[MappingTargetItem]:
        """Lote ATÔMICO: um código repetido (no lote ou no destino) recusa tudo.

        A mensagem lista os códigos — eles são do catálogo da própria organização,
        não dado de outro tenant.
        """
        destination = await self.get_destination(destination_id, viewer=actor)
        await self._ensure_organization_active(destination.organization_id)

        codes = [target.code for target in targets]
        repeated_in_batch = sorted({code for code in codes if codes.count(code) > 1})
        existing = await self._repo.get_targets_by_codes(destination.id, codes)
        conflicts = sorted(set(repeated_in_batch) | set(existing))
        if conflicts:
            raise MappingTargetCodeAlreadyExistsError(
                f"Códigos repetidos no destino {destination.id}: {conflicts}",
                user_message=(
                    "Estes códigos já existem neste destino ou se repetem no lote: "
                    + ", ".join(conflicts)
                    + "."
                ),
            )

        rows = [
            MappingTarget(destination_id=destination.id, code=t.code, name=t.name, active=True)
            for t in targets
        ]
        await self._repo.add_targets(rows)
        return [MappingTargetItem.build(row) for row in rows]

    async def update_target(
        self,
        destination_id: UUID,
        target_id: UUID,
        *,
        actor: CurrentUser,
        name: str | None = None,
        active: bool | None = None,
    ) -> MappingTargetItem:
        destination = await self.get_destination(destination_id, viewer=actor)
        await self._ensure_organization_active(destination.organization_id)
        target = await self._get_target(destination, target_id)
        if name is not None:
            target.name = name
        if active is not None:
            target.active = active
        await self._repo.add(target)
        return MappingTargetItem.build(target)

    async def delete_target(
        self, destination_id: UUID, target_id: UUID, *, actor: CurrentUser
    ) -> None:
        destination = await self.get_destination(destination_id, viewer=actor)
        await self._ensure_organization_active(destination.organization_id)
        target = await self._get_target(destination, target_id)
        in_use = await self._repo.count_decisions_for_target(target.id)
        if in_use > 0:
            raise MappingTargetInUseError(
                f"Alvo {target.code!r} referenciado por {in_use} decisão(ões).",
                user_message=(
                    f"O alvo '{target.code}' está em uso por {in_use} "
                    f"decis{'ões' if in_use != 1 else 'ão'} de de-para — desative-o em vez "
                    "de excluir."
                ),
            )
        await self._repo.delete_target(target)

    # ------------------------------ VALIDADOR (12.4 / 12.5) -----------

    async def require_targets(
        self, destination: MappingDestination, codes: Sequence[str]
    ) -> dict[str, MappingTarget]:
        """Os alvos UTILIZÁVEIS do destino para estes códigos — ou 422 nomeando.

        A fonte ÚNICA da regra "alvo é código de catálogo, existente e ativo". Um
        lote inteiro numa query; o primeiro código problemático (ordem alfabética,
        para a mensagem ser estável) nomeia o erro, e `details` leva a lista.
        """
        found = await self._repo.get_targets_by_codes(destination.id, codes)
        missing = sorted({code for code in codes if code not in found or not found[code].active})
        if missing:
            raise MappingTargetNotFoundError(
                f"Alvo(s) inexistente(s) ou inativo(s) no destino {destination.id}: {missing}",
                user_message=(
                    f"O alvo '{missing[0]}' não existe (ou está desativado) no destino "
                    f"'{destination.destination_type}'."
                ),
                details={"targetCodes": ",".join(missing)},
            )
        return found

    async def require_target(self, destination: MappingDestination, code: str) -> MappingTarget:
        return (await self.require_targets(destination, [code]))[code]

    # ------------------------------ internals -------------------------

    async def _get_target(self, destination: MappingDestination, target_id: UUID) -> MappingTarget:
        target = await self._repo.get_target(destination.id, target_id)
        if target is None:
            raise NotFoundError("Alvo não encontrado.")
        return target

    async def _ensure_organization_active(self, organization_id: UUID) -> None:
        organization = await self._repo.get_organization(organization_id)
        if organization is None or not organization.active:
            raise OrganizationInactiveError(
                f"Organização {organization_id} suspensa: catálogo do de-para só-leitura."
            )
