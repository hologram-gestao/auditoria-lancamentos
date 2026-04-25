"""Modelo Client — clientes BPO da Hologram, com credenciais Omie criptografadas.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §clients.

DIVERGÊNCIA INTENCIONAL DO SCHEMA DA DOC:
    A doc lista `encryption_iv` e `encryption_tag` (1 par para todos os campos).
    Nosso `app.core.crypto`:
      - Gera IV NOVO para cada operação (regra: nunca reutilizar IV no AES-GCM).
      - Embute a tag GCM dentro do ciphertext (lib `cryptography` faz isso).
    Por isso temos:
      - omie_app_key_encrypted + omie_app_key_iv  (cada um com IV próprio)
      - omie_app_secret_encrypted + omie_app_secret_iv
    Tag não precisa de coluna: já está nos últimos 16 bytes do ciphertext.
    Esta divergência foi documentada e mantém a INTENÇÃO da doc (AES-256-GCM
    com IVs únicos), seguindo a regra inviolável CLAUDE.md §4.

Credenciais NUNCA são logadas, retornadas em response, nem armazenadas em
claro. Sempre descriptografar em memória, usar e descartar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client_assignment import ClientAssignment
    from app.db.models.omie_account_cache import OmieAccountCache
    from app.db.models.reconciliation_session import ReconciliationSession
    from app.db.models.user import User


# Tamanho fixo do IV em hex (12 bytes = 24 chars hex)
IV_HEX_LENGTH = 24


class Client(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    # AES-256-GCM: ciphertext (com tag embutida) + IV próprio por campo
    omie_app_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    omie_app_key_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)
    omie_app_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    omie_app_secret_iv: Mapped[str] = mapped_column(String(IV_HEX_LENGTH), nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Relationships
    creator: Mapped[User] = relationship("User", foreign_keys=[created_by], lazy="raise")
    assignments: Mapped[list[ClientAssignment]] = relationship(
        "ClientAssignment",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    omie_accounts: Mapped[list[OmieAccountCache]] = relationship(
        "OmieAccountCache",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    reconciliations: Mapped[list[ReconciliationSession]] = relationship(
        "ReconciliationSession",
        back_populates="client",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<Client id={self.id} name={self.name!r}>"
