"""Modelo UserClientFavorite — cliente favoritado POR usuário (86e34jd5a).

Favorito é preferência de quem opera, não atributo do cliente: cada gestor tem
os próprios clientes do dia a dia, e um favorito global viraria disputa entre
gerentes. Por isso a chave é `(user_id, client_id)`, e a lista de clientes
(`GET /clients`) ordena "favoritos de QUEM PEDE primeiro" via outer join com o
`user_id` da linha do usuário autenticado — nunca de parâmetro de URL (§3.15).

Cascade nos dois lados: usuário ou cliente removido leva o favorito junto — não
há sentido em favorito órfão, e a exclusão de cliente (86e34jd1d) não pode
travar nesta tabela.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.client import Client
    from app.db.models.user import User

#: Nome da UNIQUE — o `ON CONFLICT DO NOTHING` do PUT idempotente mira nela.
UQ_USER_CLIENT_FAVORITE = "uq_user_client_favorites_user_client"


class UserClientFavorite(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_client_favorites"
    __table_args__ = (UniqueConstraint("user_id", "client_id", name=UQ_USER_CLIENT_FAVORITE),)

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships só de leitura (lazy="raise": ninguém carrega por acidente).
    user: Mapped[User] = relationship("User", lazy="raise")
    client: Mapped[Client] = relationship("Client", lazy="raise")

    def __repr__(self) -> str:
        return f"<UserClientFavorite user={self.user_id} client={self.client_id}>"
