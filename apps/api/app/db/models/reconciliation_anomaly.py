"""Modelo ReconciliationAnomaly — anomalias detectadas durante uma sessão.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §reconciliation_anomalies.

Origem (`detected_by`):
    - `manual`: analista registrou via UI (Doc §14.5).
    - `ai`: criadas automaticamente em S10 (`missing_in_omie`, `missing_in_file`).

Resolução (Doc §17.3):
    pendente → resolvida (one-way, com nota obrigatória ≥ 10 chars).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.anomaly_type import AnomalyType
    from app.db.models.reconciliation_file_entry import ReconciliationFileEntry
    from app.db.models.reconciliation_omie_entry import ReconciliationOmieEntry
    from app.db.models.reconciliation_session import ReconciliationSession


class AnomalyDetectedBy(StrEnum):
    """Origem da anomalia."""

    MANUAL = "manual"
    AI = "ai"


class ReconciliationAnomaly(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_anomalies"

    # Compostos antecipam o gargalo de `list_anomalies_paginated`
    # (review/repository.py) em sessões com muitas anomalias: o filtro
    # principal é por `session_id` e o cruzamento usa `(resolved)` ou
    # JOIN em `anomaly_type_id`. Os índices single-column existentes em
    # `session_id` e `resolved` permanecem — servem queries de contagem
    # global e dashboards futuros.
    __table_args__ = (
        Index(
            "ix_recon_anomalies_session_resolved",
            "session_id",
            "resolved",
        ),
        Index(
            "ix_recon_anomalies_session_type",
            "session_id",
            "anomaly_type_id",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    anomaly_type_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("anomaly_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    file_entry_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_file_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    omie_entry_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_omie_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    detected_by: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AnomalyDetectedBy.MANUAL.value
    )

    # Contexto livre do analista (manual) ou descrição estrutural (AI)
    context_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    resolution_note_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_note_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    session: Mapped[ReconciliationSession] = relationship(
        "ReconciliationSession", back_populates="anomalies", lazy="raise"
    )
    anomaly_type: Mapped[AnomalyType] = relationship("AnomalyType", lazy="raise")
    file_entry: Mapped[ReconciliationFileEntry | None] = relationship(
        "ReconciliationFileEntry", lazy="raise"
    )
    omie_entry: Mapped[ReconciliationOmieEntry | None] = relationship(
        "ReconciliationOmieEntry", lazy="raise"
    )

    def __repr__(self) -> str:
        return (
            f"<Anomaly id={self.id} session={self.session_id} "
            f"type={self.anomaly_type_id} resolved={self.resolved}>"
        )
