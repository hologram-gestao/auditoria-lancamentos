"""Lógica de negócio do CRUD de tipos de anomalia (S15 BACK 11.1).

Regras (Doc §0 §anomaly_types + CLAUDE.md §11):
    - `code` é IMUTÁVEL após criação — `update_anomaly_type` não recebe code.
    - `code` deve ser único (UNIQUE no DB) — pre-check no service devolve 409
      legível antes do IntegrityError.
    - Tipo desativado (`active=False`) continua existindo, é apenas filtrado:
        - Lista para manager — sempre filtra ativos.
        - Worker de processamento (`processing/anomalies.py`) — só carrega
          ativos, então novas conciliações não geram anomalias de tipos OFF.
        - Anomalias HISTÓRICAS permanecem visíveis na revisão e no export
          (a FK em `reconciliation_anomalies` é `ondelete=RESTRICT`).
    - DELETE só é permitido em tipos órfãos (zero anomalias referenciando).
      Caso contrário, instrui o admin a desativar.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import (
    AnomalyTypeCodeAlreadyExistsError,
    AnomalyTypeInUseError,
    NotFoundError,
)
from app.db.models import AnomalySeverity, AnomalyType
from app.modules.anomaly_types.repository import AnomalyTypeRepository
from app.modules.users.schemas import PaginationMeta


class AnomalyTypeService:
    """CRUD + regras de negócio para `anomaly_types`."""

    def __init__(self, repository: AnomalyTypeRepository) -> None:
        self._repo = repository

    # ------------------------------ READ ------------------------------

    @staticmethod
    def _effective_include_inactive(*, role: str, requested: bool) -> bool:
        """Manager nunca enxerga inativos, mesmo passando `?include_inactive=true`.

        Mantemos `True` apenas se o caller for admin — silently ignorado para
        manager, sem 403, porque o GET é compartilhado com a tela de revisão e
        404/403 espúrios atrapalhariam o fluxo principal.
        """
        return requested and role == "admin"

    async def list_all(self, *, role: str, include_inactive: bool) -> list[AnomalyType]:
        """Lista sem paginação (contrato legado da tela de revisão)."""
        effective = self._effective_include_inactive(role=role, requested=include_inactive)
        return await self._repo.list_all(include_inactive=effective)

    async def list_paginated(
        self,
        *,
        role: str,
        include_inactive: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[AnomalyType], PaginationMeta]:
        effective = self._effective_include_inactive(role=role, requested=include_inactive)
        rows, total = await self._repo.list_paginated(
            page=page, page_size=page_size, include_inactive=effective
        )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return list(rows), PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def get_anomaly_type(self, type_id: UUID) -> AnomalyType:
        anomaly_type = await self._repo.get_by_id(type_id)
        if anomaly_type is None:
            raise NotFoundError("Tipo de anomalia não encontrado.")
        return anomaly_type

    # ------------------------------ CREATE ----------------------------

    async def create_anomaly_type(
        self,
        *,
        code: str,
        name: str,
        description: str,
        severity: AnomalySeverity,
        active: bool,
    ) -> AnomalyType:
        """Cria tipo custom. 409 se `code` já existe."""
        existing = await self._repo.get_by_code(code)
        if existing is not None:
            raise AnomalyTypeCodeAlreadyExistsError(
                f"AnomalyType code já existe: {code}",
            )

        anomaly_type = AnomalyType(
            code=code,
            name=name,
            description=description,
            severity=severity.value,
            active=active,
        )
        await self._repo.add(anomaly_type)
        return anomaly_type

    # ------------------------------ UPDATE ----------------------------

    async def update_anomaly_type(
        self,
        type_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        severity: AnomalySeverity | None = None,
        active: bool | None = None,
    ) -> AnomalyType:
        """PATCH parcial — só campos enviados são alterados. `code` é imutável."""
        anomaly_type = await self.get_anomaly_type(type_id)

        if name is not None:
            anomaly_type.name = name
        if description is not None:
            anomaly_type.description = description
        if severity is not None:
            anomaly_type.severity = severity.value
        if active is not None:
            anomaly_type.active = active

        await self._repo.add(anomaly_type)
        return anomaly_type

    # ------------------------------ DELETE ----------------------------

    async def delete_anomaly_type(self, type_id: UUID) -> None:
        """Exclusão segura: bloqueia (409) se houver anomalias referenciando."""
        anomaly_type = await self.get_anomaly_type(type_id)
        in_use_count = await self._repo.count_anomalies_using_type(type_id)
        if in_use_count > 0:
            raise AnomalyTypeInUseError(
                f"AnomalyType {anomaly_type.code} em uso por {in_use_count} anomalia(s).",
            )
        await self._repo.delete(anomaly_type)
