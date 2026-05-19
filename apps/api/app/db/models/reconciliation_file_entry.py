"""Modelo ReconciliationFileEntry — linhas extraídas do arquivo do banco/cartão.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §reconciliation_file_entries.

Campos criptografados (CLAUDE.md §4):
    - description_encrypted (+ description_iv)
    - user_note_encrypted (+ user_note_iv) — opcional, populado em S12 (revisão)

Valores monetários em CLARO (DECIMAL(14,2)) — sem identificação por si só.
Datas em CLARO — necessárias para SQL filtering/sorting.

Estados (Doc §17.2):
    sem_omie → conciliado
    sem_omie → ignorado
    conciliado ↔ ignorado (restaurar)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.reconciliation_session import ReconciliationSession


class FileEntrySituation(StrEnum):
    """Situação de uma linha após cruzamento + revisão."""

    SEM_OMIE = "sem_omie"
    CONCILIADO = "conciliado"
    IGNORADO = "ignorado"


class FileEntryUserAction(StrEnum):
    """Ação manual registrada pelo analista (Doc §14.2)."""

    CONFIRM = "confirm"
    FLAG = "flag"
    IGNORE = "ignore"


class ReconciliationFileEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_file_entries"

    # Índice único parcial: impede 2 linhas da MESMA sessão de vincularem
    # o mesmo `omie_lancamento_id`. Reforça CLAUDE.md §5.4 no banco — a
    # checagem aplicativa (`file_entry_omie_id_taken_by_another`) trata o
    # caso comum em 1 round trip, este índice é a guarda contra race
    # entre 2 requests concorrentes de "Trocar Omie".
    __table_args__ = (
        Index(
            "ix_recon_file_entry_session_omie_unique",
            "session_id",
            "omie_lancamento_id",
            unique=True,
            postgresql_where=text("omie_lancamento_id IS NOT NULL"),
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transaction_date: Mapped[date] = mapped_column(SQLDate, nullable=False)

    # Descrição: AES-256-GCM (pode conter nomes, CPF, razão social)
    description_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    description_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)

    # Blind index do filtro `search` (S16). Tokens normalizados (lowercase
    # sem acentos, >= 3 chars) aplicados em HMAC-SHA256 truncado em 16 chars
    # hex, concatenados com espaços e cercados por leading/trailing space:
    # " hash1 hash2 hash3 ". Permite SQL `LIKE '% hash %'` antes do decrypt
    # — descriptografia só ocorre na página final. Ver `app/core/search_index.py`.
    # Nullable porque sessões pré-S16 não têm o índice; nesses casos o
    # filtro `search` retorna vazio (decisão registrada).
    description_search_hmac: Mapped[str | None] = mapped_column(Text, nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, index=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    situation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FileEntrySituation.SEM_OMIE.value,
        index=True,
    )
    omie_lancamento_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user_action: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Nota livre do analista — AES-256-GCM (opcional, mas com IV próprio quando set)
    user_note_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_note_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    session: Mapped[ReconciliationSession] = relationship(
        "ReconciliationSession", back_populates="file_entries", lazy="raise"
    )

    def __repr__(self) -> str:
        return (
            f"<FileEntry id={self.id} date={self.transaction_date} "
            f"amount={self.amount} situation={self.situation}>"
        )
