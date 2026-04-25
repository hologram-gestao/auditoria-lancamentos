"""Modelo ReconciliationSession — uma sessão de conciliação (1 upload + matching).

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §reconciliation_sessions.

Idempotência: UNIQUE(client_id, omie_conta_id, reference_month, file_hash) —
um arquivo só pode ser processado uma vez para a mesma conta/mês. Duplicata
retorna HTTP 409 DUPLICATE_FILE (Doc §11.3 V3).

Estados (Doc §17.1):
    processing → reviewing → done
              ↓
            error
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client
    from app.db.models.reconciliation_anomaly import ReconciliationAnomaly
    from app.db.models.reconciliation_file_entry import ReconciliationFileEntry
    from app.db.models.reconciliation_omie_entry import ReconciliationOmieEntry
    from app.db.models.user import User


class ReconciliationStatus(StrEnum):
    """Estados possíveis de uma sessão (Doc §17.1)."""

    PROCESSING = "processing"
    REVIEWING = "reviewing"
    DONE = "done"
    ERROR = "error"


class ReconciliationSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_sessions"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "omie_conta_id",
            "reference_month",
            "file_hash",
            name="uq_recon_sessions_idempotency",
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    omie_conta_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_month: Mapped[date] = mapped_column(SQLDate, nullable=False, index=True)
    date_tolerance_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hex

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReconciliationStatus.PROCESSING.value,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Saldos calculados ao final do processamento
    balance_start: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    balance_end_file: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    balance_end_omie: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    balance_difference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    # Contadores agregados (default 0; populados ao fim do processamento)
    total_file_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conciliated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sem_omie_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    omie_sem_arquivo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    anomaly_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    client: Mapped[Client] = relationship("Client", back_populates="reconciliations", lazy="raise")
    user: Mapped[User] = relationship("User", foreign_keys=[created_by], lazy="raise")
    file_entries: Mapped[list[ReconciliationFileEntry]] = relationship(
        "ReconciliationFileEntry",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    omie_entries: Mapped[list[ReconciliationOmieEntry]] = relationship(
        "ReconciliationOmieEntry",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    anomalies: Mapped[list[ReconciliationAnomaly]] = relationship(
        "ReconciliationAnomaly",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return (
            f"<ReconciliationSession id={self.id} client={self.client_id} "
            f"month={self.reference_month} status={self.status}>"
        )
