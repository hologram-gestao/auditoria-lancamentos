"""Schemas do módulo omie_data (BACK 9.2)."""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal

from pydantic import BaseModel


class OmieLancamentoItem(BaseModel):
    """Item de GET /api/v1/omie/lancamentos.

    Inclui só campos relevantes pra UI da revisão: identificador,
    descrição, valor (com sinal), fornecedor/categoria, status.
    """

    omie_id: int
    transaction_date: _date
    description: str
    supplier: str | None
    category: str | None
    amount: Decimal
    status: str


class OmieLancamentoListResponse(BaseModel):
    data: list[OmieLancamentoItem]
