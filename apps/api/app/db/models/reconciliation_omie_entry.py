"""Modelo ReconciliationOmieEntry — lançamentos Omie SEM correspondente no arquivo.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §reconciliation_omie_entries.

O que persiste aqui (task 86e33bmkb):
    - IDs, data, status e ações do analista;
    - `amount` em claro (§4.3 — número sem identificação, COM sinal) e
      `category_code` em claro (apenas o CÓDIGO contábil, ex. "2.04.78") —
      snapshot do processamento, para que linhas vindas de TÍTULO
      (Atrasado/Previsto, que o `ListarExtrato` não devolve) tenham
      Valor/Categoria na tela sem depender de re-busca impossível.
NOMES (fornecedor, descrição de categoria) seguem a regra inviolável
CLAUDE.md §4.5: nunca em claro no DB — resolvidos do Omie em runtime via
cache TTL (extrato para linhas realizadas; `ListarCategorias` para o
código → descrição).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Text
from sqlalchemy import Date as SQLDate
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client import IV_HEX_LENGTH

if TYPE_CHECKING:
    from app.db.models.reconciliation_session import ReconciliationSession


class OmieEntryStatus(StrEnum):
    """Status do lançamento conforme retorno do Omie."""

    ATRASADO = "Atrasado"
    PREVISTO = "Previsto"


class OmieEntryUserAction(StrEnum):
    """Ação manual do analista (Doc §14.4)."""

    FLAG = "flag"
    IGNORE = "ignore"
    RESOLVED = "resolved"


class ReconciliationOmieEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reconciliation_omie_entries"

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("reconciliation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    omie_lancamento_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_date: Mapped[date] = mapped_column(SQLDate, nullable=False)
    omie_status: Mapped[str] = mapped_column(String(30), nullable=False)

    # Snapshot do processamento (migration a3f8c21d9b47). Nullable: linhas
    # anteriores à migration ficam NULL e a tela usa só o cache/extrato.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    category_code: Mapped[str | None] = mapped_column(String(30), nullable=True)

    user_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_note_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_note_iv: Mapped[str | None] = mapped_column(String(IV_HEX_LENGTH), nullable=True)

    session: Mapped[ReconciliationSession] = relationship(
        "ReconciliationSession", back_populates="omie_entries", lazy="raise"
    )

    def __repr__(self) -> str:
        return (
            f"<OmieEntry session={self.session_id} omie_id={self.omie_lancamento_id} "
            f"status={self.omie_status}>"
        )
