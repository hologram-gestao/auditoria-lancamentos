"""Modelo ClientAssignment — quem tem ACESSO ao cliente, e quem RESPONDE por ele.

Carteira compartilhada (épico 86e390kku, 14/09/2026): um cliente pode ter
**N gerentes com acesso**, mas exatamente **UM responsável** (`is_primary`).
Antes a tabela era 1:1 (`UNIQUE(client_id)`) e "reatribuir" sobrescrevia o
`user_id` — o gerente anterior perdia o acesso em silêncio (o caso da Bruna
no cliente Hologram). Agora:

    - cada linha = uma pessoa com acesso ao cliente (`assigned_by`/`assigned_at`
      dizem quem concedeu o acesso e quando);
    - `is_primary = true` marca o responsável — o nome que aparece na coluna
      "Gerente responsável" da lista e a quem se cobra;
    - trocar o responsável NÃO remove ninguém; remover acesso é ação própria.

As duas garantias são do BANCO, não da aplicação:
    - `uq_client_assignments_client_user` — a mesma pessoa não entra duas vezes
      no mesmo cliente. Sem ela, `resolve_client_access` (que usa
      `scalar_one_or_none` sobre o par) estouraria com `MultipleResultsFound`;
    - `uq_client_assignments_primary` — índice único PARCIAL (`WHERE is_primary`):
      no máximo um responsável por cliente. O predicado é copiado na migration
      (`6bb85e6b7d72`) e um teste unitário prova que as duas fontes batem.

CLAUDE.md §3.11 / §3.15: manager só vê cliente via esta tabela — QUALQUER
linha (responsável ou colaborador) concede acesso; a decisão continua sendo
`resolve_client_access`, que filtra por `(client_id, user_id)`. A listagem
(`clients/repository.py`) mostra o responsável pelo join restrito a
`is_primary` e filtra a carteira por `EXISTS` sobre todas as linhas.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint, false, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client
    from app.db.models.user import User

#: Nome da UNIQUE `(client_id, user_id)` — o `ON CONFLICT DO NOTHING` de
#: "adicionar gerente" mira nela (idempotente sob concorrência).
UQ_CLIENT_ASSIGNMENT_CLIENT_USER = "uq_client_assignments_client_user"

#: Nome do índice único PARCIAL do responsável.
UQ_CLIENT_ASSIGNMENT_PRIMARY = "uq_client_assignments_primary"


def primary_assignment_index_predicate() -> str:
    """Predicado SQL do índice parcial — a MESMA string vai na migration.

    Função (e não constante solta) para o teste unitário comparar as duas fontes
    do mesmo jeito que `deduped_session_index_predicate()` faz para o dedup de
    `usage_events`: mudar aqui sem mudar lá é drift que o autogenerate do Alembic
    NÃO enxerga (ele não compara `postgresql_where`).
    """
    return "is_primary"


class ClientAssignment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "client_assignments"
    __table_args__ = (
        UniqueConstraint("client_id", "user_id", name=UQ_CLIENT_ASSIGNMENT_CLIENT_USER),
        Index(
            UQ_CLIENT_ASSIGNMENT_PRIMARY,
            "client_id",
            unique=True,
            postgresql_where=text(primary_assignment_index_predicate()),
        ),
    )

    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    #: Responsável pelo cliente (um só, garantido pelo índice parcial). Default
    #: FALSE nos dois lados (ORM e servidor) de propósito: quem cria o vínculo do
    #: responsável marca explicitamente — um default `True` faria qualquer
    #: inserção distraída tentar virar segundo responsável e estourar no índice.
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
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
        return (
            f"<ClientAssignment client={self.client_id} user={self.user_id} "
            f"primary={self.is_primary}>"
        )
