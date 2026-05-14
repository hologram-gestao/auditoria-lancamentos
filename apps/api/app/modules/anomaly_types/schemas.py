"""Schemas do módulo anomaly_types."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class AnomalyTypeItem(BaseModel):
    """Item de GET /api/v1/anomaly-types."""

    id: UUID
    code: str
    name: str
    description: str
    severity: str


class AnomalyTypeListResponse(BaseModel):
    data: list[AnomalyTypeItem]
