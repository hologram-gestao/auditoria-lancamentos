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
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
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


class SessionAccountType(StrEnum):
    """Tipo (normalizado) da conta conciliada na sessão — FASE 1.

    NÃO é o código cru do Omie (`CC`/`CR`/`CA`/…, esse vive em
    `omie_accounts_cache.account_type`). É a classificação que o produto
    usa: conta corrente vs. cartão. Derivado do `tipo` Omie da conta
    selecionada em `create_session_with_entries` — apenas `CR` (Cartão de
    Crédito) → `credit_card`; o resto → `checking`.

    A UI (badge "Cartão", filtros) e o export (coluna "Data Omie") ramificam
    neste campo; a regra de tolerância de data (FASE 1) é a mesma p/ os dois.
    """

    CHECKING = "checking"
    CREDIT_CARD = "credit_card"


class ReconciliationSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_sessions"
    # UNIQUE PARCIAL: a idempotência só vale para sessões ATIVAS
    # (`deleted_at IS NULL`). Soft-delete libera a tupla pra criar uma
    # sessão nova com o mesmo arquivo no mesmo mês — caso do botão
    # "Descartar" na UI de erro. PostgreSQL suporta `WHERE` em índice
    # único nativamente, SQLAlchemy não modela `UniqueConstraint(where=)`,
    # então usamos `Index(unique=True, postgresql_where=...)`.
    __table_args__ = (
        Index(
            "uq_recon_sessions_idempotency",
            "client_id",
            "omie_conta_id",
            "reference_month",
            "file_hash",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
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
    # Tipo normalizado da conta conciliada: 'checking' (conta corrente) ou
    # 'credit_card' (cartão). Derivado do `tipo` Omie da conta selecionada em
    # create_session_with_entries (CR → credit_card; resto → checking). O
    # server_default 'checking' cobre as linhas pré-migration (não-destrutivo).
    account_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SessionAccountType.CHECKING.value,
        server_default=text("'checking'"),
    )
    reference_month: Mapped[date] = mapped_column(SQLDate, nullable=False, index=True)
    # Período REAL extraído do statement (S9). NULL em sessões pré-migration —
    # nesse caso, o review service cai no fallback `[reference_month,
    # last_day_of_month]`. Sem isso, extratos quebrados (15/04→14/05),
    # faturas de cartão e lançamentos nos primeiros dias do mês seguinte
    # ficam fora do período Omie consultado em /available-omie-entries.
    period_start: Mapped[date | None] = mapped_column(SQLDate, nullable=True)
    period_end: Mapped[date | None] = mapped_column(SQLDate, nullable=True)
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

    # Soft-delete: NULL = sessão ativa; timestamp = descartada pela UI.
    # Toda query de leitura/listagem precisa filtrar `WHERE deleted_at IS
    # NULL` — o índice de idempotência também é parcial pra liberar a
    # tupla quando a sessão é descartada (criar uma nova com o mesmo
    # arquivo passa a ser possível).
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

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
