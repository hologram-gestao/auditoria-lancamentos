"""Modelo ClientMovementSync — o ESTADO da base de movimentos por competência (Sprint 12, BACK 12.1).

**Por que uma tabela e não duas colunas em `clients`.** O plano de contas (S10) e
a carteira (S11) carimbam em `clients` porque a unidade deles é o cliente. Aqui a
unidade é `(cliente, competência)`: junho pode ter sincronizado ontem e julho nunca,
e é essa diferença que a prévia do de-para (BACK 12.6) precisa mostrar — "nunca
sincronizada" recusa com 409, "sincronizada sem movimento" é outro 409.

**Os dois relógios, no precedente da S10/S11.** `synced_at` é o último sucesso
ÍNTEGRO; `sync_failed_at` é a última falha. A falha **nunca** toca o carimbo do
sucesso: a base anterior continua valendo e a tela diz "falhou agora, e estes
movimentos são de tal dia". Um sucesso limpa a falha ("falhou" é sobre a ÚLTIMA
tentativa).

**Linha ausente = nunca tentou.** Uma linha só com `sync_failed_at` = tentou e
nunca conseguiu. Nos dois casos "nunca sincronizada" é `synced_at IS NULL` — um
estado, uma pergunta.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date as SQLDate
from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

#: A UNIQUE que faz dos carimbos um upsert (`ON CONFLICT`) — uma linha de estado
#: por (cliente, competência), sem leitura anterior.
UQ_CLIENT_MOVEMENT_SYNC = "uq_client_movement_syncs_client_id_competence"


class ClientMovementSync(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_movement_syncs"

    __table_args__ = (UniqueConstraint("client_id", "competence", name=UQ_CLIENT_MOVEMENT_SYNC),)

    #: CASCADE pelo mesmo motivo de `client_movements`: exclusão definitiva leva
    #: tudo; encerramento é `close_client_purge`. A UNIQUE já indexa `client_id`
    #: pelo prefixo.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: A competência (dia 1 do mês), como em `client_movements.competence`.
    competence: Mapped[date] = mapped_column(SQLDate, nullable=False)

    #: Último sucesso ÍNTEGRO. `None` = nunca houve um.
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    #: Última falha. Nunca sobrescreve `synced_at`.
    sync_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    def __repr__(self) -> str:
        return (
            f"<ClientMovementSync client={self.client_id} competence={self.competence} "
            f"synced_at={self.synced_at} failed_at={self.sync_failed_at}>"
        )
