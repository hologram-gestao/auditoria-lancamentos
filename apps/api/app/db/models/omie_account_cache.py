"""Modelo OmieAccountCache — cache L1 de contas correntes Omie por cliente.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §omie_accounts_cache.

Estratégia de cache (Doc §5.2):
    - TTL 24h via coluna `synced_at`.
    - Invalidação manual via endpoint POST /clients/:id/sync-accounts.
    - UNIQUE(client_id, omie_conta_id) — uma linha por conta Omie por cliente.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client


class OmieAccountType(StrEnum):
    """Tipo da conta no Omie (mesmo enum usado pelo `tipo` da API).

    Não-exaustivo — só os tipos com lógica especial no MVP. Doc oficial
    declara 13 valores; ver `app.integrations.omie.schemas.OmieAccountType`
    pra detalhes. Coluna `account_type` é `String(10)` e aceita qualquer
    código devolvido pela Omie.

    **Atenção** (corrigido em 20/05/2026, auditoria M-1): `CA` ≠ cartão!
      - `CA` = Conta Aplicação (investimento)
      - `CR` = Cartão de Crédito
    """

    CHECKING = "CC"  # Conta Corrente
    CREDIT_CARD = "CR"  # Cartão de Crédito
    INVESTMENT = "CA"  # Conta Aplicação


class OmieAccountCache(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "omie_accounts_cache"
    __table_args__ = (
        UniqueConstraint("client_id", "omie_conta_id", name="uq_omie_accounts_client_conta"),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    omie_conta_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # nCodCC do Omie
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_type: Mapped[str] = mapped_column(String(10), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    client: Mapped[Client] = relationship("Client", back_populates="omie_accounts", lazy="raise")

    def __repr__(self) -> str:
        return f"<OmieAccountCache client={self.client_id} conta={self.omie_conta_id}>"
