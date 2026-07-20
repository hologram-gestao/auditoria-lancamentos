"""Schemas do módulo system (Sprint 3, BACK 03.6)."""

from __future__ import annotations

from pydantic import BaseModel


class SyntheticAlertResult(BaseModel):
    """Resultado da entrega do alerta sintético, por canal.

    `True` = entregue, `False` = falhou, `None` = canal não configurado.
    """

    delivered: bool
    webhook: bool | None = None
    email: bool | None = None


class SyntheticAlertResponse(BaseModel):
    data: SyntheticAlertResult
