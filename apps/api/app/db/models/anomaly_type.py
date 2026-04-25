"""Modelo AnomalyType — catálogo de tipos de anomalia detectáveis.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §anomaly_types.
Seed inicial: 8 tipos pré-cadastrados (ver scripts/seed-dev.py).

Fase 1 (atual): admin apenas ativa/desativa tipos.
Fase 2 (futura): admin cria tipos custom via UI.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AnomalySeverity(StrEnum):
    """Níveis de severidade de uma anomalia."""

    CRITICAL = "critical"
    MODERATE = "moderate"
    INFO = "info"


class AnomalyType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "anomaly_types"

    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<AnomalyType code={self.code} severity={self.severity}>"
