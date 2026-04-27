"""Modelo ClientAssignment — vínculo cliente <-> gerente responsável.

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §client_assignments.

Constraint UNIQUE em `client_id` garante que cada cliente pertence a UM gerente
por vez. Reatribuição atualiza `user_id`, `assigned_by` e `assigned_at`
(timestamp da última atribuição — backlog BACK 3.5).

CLAUDE.md §3 (RBAC): manager só vê clientes via esta tabela. Toda rota
que retorna dados de cliente deve filtrar por `client_assignments.user_id`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client
    from app.db.models.user import User


class ClientAssignment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_assignments"

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 1 cliente -> 1 gerente
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assigned_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    client: Mapped[Client] = relationship("Client", back_populates="assignments", lazy="raise")
    user: Mapped[User] = relationship("User", foreign_keys=[user_id], lazy="raise")
    assigner: Mapped[User] = relationship("User", foreign_keys=[assigned_by], lazy="raise")

    def __repr__(self) -> str:
        return f"<ClientAssignment client={self.client_id} user={self.user_id}>"
