"""Regras do módulo de organizações (camada de organizações, 86e36ecnp).

- Nome único SEM caixa: pre-check devolve 409 legível antes do IntegrityError
  da UNIQUE (`uq_organizations_name`).
- Suspender (`active=false`) não apaga nada: os usuários da organização passam
  a receber 401 no request seguinte (`get_current_user`) e a organização deixa
  de receber cliente novo (`POST /clients` pela plataforma). Reativar desfaz.
- Eventos de uso (`organizacao_criada`, `organizacao_desativada`): só IDs e
  contagens, nunca o nome. Nascem SEM dedup, como todo evento novo.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import OrganizationNameAlreadyExistsError, OrganizationNotFoundError
from app.db.models import Organization
from app.modules.organizations.repository import OrganizationRepository, OrganizationRow
from app.modules.organizations.schemas import OrganizationItem, PlatformAdminItem
from app.modules.usage_events.service import UsageEventService
from app.modules.users.schemas import PaginationMeta


def _to_item(row: OrganizationRow) -> OrganizationItem:
    org = row.organization
    return OrganizationItem(
        id=org.id,
        name=org.name,
        active=org.active,
        clients_count=row.clients_count,
        users_count=row.users_count,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


class OrganizationService:
    def __init__(
        self,
        repository: OrganizationRepository,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._repo = repository
        self._usage_events = usage_events

    async def list_organizations(
        self, *, page: int, page_size: int, search: str | None = None
    ) -> tuple[list[OrganizationItem], PaginationMeta]:
        rows, total = await self._repo.list_paginated(page=page, page_size=page_size, search=search)
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return [_to_item(row) for row in rows], PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def list_platform_admins(self) -> list[PlatformAdminItem]:
        """Quem administra a PLATAFORMA — não é uma organização, e por isso não
        aparece em contagem nenhuma da lista acima (`users_count` conta só
        `scope='system'` da própria org).
        """
        return [
            PlatformAdminItem(
                id=user.id,
                name=user.name,
                email=user.email,
                active=user.active,
                created_at=user.created_at,
            )
            for user in await self._repo.list_platform_admins()
        ]

    async def get_organization(self, organization_id: UUID) -> OrganizationItem:
        row = await self._repo.get_row(organization_id)
        if row is None:
            raise OrganizationNotFoundError(f"Organização inexistente: {organization_id}")
        return _to_item(row)

    async def create_organization(self, *, name: str) -> OrganizationItem:
        if await self._repo.get_by_name_ci(name) is not None:
            raise OrganizationNameAlreadyExistsError(f"Organização já existe: {name!r}")
        organization = Organization(name=name, active=True)
        await self._repo.add(organization)
        if self._usage_events is not None:
            await self._usage_events.emit_organizacao_criada(organization_id=organization.id)
        return _to_item(OrganizationRow(organization=organization, clients_count=0, users_count=0))

    async def update_organization(
        self,
        organization_id: UUID,
        *,
        name: str | None = None,
        active: bool | None = None,
    ) -> OrganizationItem:
        organization = await self._repo.get_by_id(organization_id)
        if organization is None:
            raise OrganizationNotFoundError(f"Organização inexistente: {organization_id}")

        if name is not None:
            other = await self._repo.get_by_name_ci(name)
            if other is not None and other.id != organization.id:
                raise OrganizationNameAlreadyExistsError(f"Organização já existe: {name!r}")
            organization.name = name

        suspended_now = active is False and organization.active is True
        if active is not None:
            organization.active = active
        await self._repo.add(organization)

        row = await self._repo.get_row(organization.id)
        assert row is not None
        if suspended_now and self._usage_events is not None:
            await self._usage_events.emit_organizacao_desativada(
                organization_id=organization.id,
                n_usuarios=row.users_count,
                n_clientes=row.clients_count,
            )
        return _to_item(row)
