"""Modelo ClientSourceAccountBinding — a CONTA CONTÁBIL DO BANCO de cada conta de origem (Sprint 16, BACK 16.3 — R3).

Toda linha do arquivo contábil tem dois lados, e um deles é sempre a conta do banco
no plano contábil do cliente (`649`, Bradesco, na amostra): no recebimento é o
débito, no pagamento é o crédito. A plataforma não tinha esse dado. Esta tabela é a
associação **conta de origem → conta ANALÍTICA e ATIVA do plano contábil do cliente**
(`client_accounting_accounts`, 16.1), por cliente:

    - `(source_type, source_account_id)` — a conta de origem como a base de movimentos a
      grava (`client_movements.source_account_id`: a conta corrente do Omie; a coluna
      de conta do arquivo, quando o mapeamento de entrada declara uma);
    - `source_account_id` NULO é o **slot da CONTA PADRÃO** daquele tipo de origem: cobre
      as linhas SEM conta de origem (o arquivo sem coluna de conta, caso da MSFG).
      Decisão do planejador (ADR-088-BE): a padrão NÃO cobre conta de origem sem
      associação — uma conta do Omie esquecida fica PENDENTE, nunca cai na padrão em
      silêncio.

**Unicidade no BANCO, em duas garantias:** a UNIQUE `(client_id, source_type,
source_account_id)` para as contas explícitas (no Postgres, NULL não colide em UNIQUE)
e o índice único PARCIAL `uq_client_source_account_bindings_default` sobre
`(client_id, source_type) WHERE source_account_id IS NULL` para o slot padrão. O
predicado é COPIADO na migration e o teste compara as duas fontes (precedente
`uq_client_assignments_primary`).

**É configuração MUTÁVEL** (upsert `ON CONFLICT`), não vigência: o que é imutável é o
SNAPSHOT — o código da conta do banco de cada linha é gravado no item da materialização
na hora de materializar, e trocar a associação depois não muda materialização existente.

**Encerramento/exclusão:** sai em `close_client_purge` e na exclusão definitiva ANTES
das contas do plano (FK) e dos usuários do tenant (autoria RESTRICT, ADR-074-BE).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.client_movement import MAX_MOVEMENT_REF_CHARS, MAX_MOVEMENT_SOURCE_TYPE_CHARS

if TYPE_CHECKING:
    from app.db.models.client import Client

#: A UNIQUE das contas de origem EXPLÍCITAS (NULL não colide nela — o slot padrão tem
#: a garantia própria, abaixo).
UQ_SOURCE_ACCOUNT_BINDING = "uq_client_source_account_bindings_source"
#: O índice único PARCIAL do slot padrão: uma conta padrão por (cliente, tipo de origem).
UQ_SOURCE_ACCOUNT_BINDING_DEFAULT = "uq_client_source_account_bindings_default"
#: FK para o plano contábil — nome EXPLÍCITO (a convenção passaria de 63).
FK_SOURCE_ACCOUNT_BINDING_ACCOUNT = "fk_client_source_account_bindings_accounting_account"


def default_binding_index_predicate() -> str:
    """Predicado SQL do índice parcial do slot padrão — a MESMA string vai na migration.

    Função (e não constante solta) para o teste comparar as duas fontes: o
    autogenerate do Alembic NÃO compara `postgresql_where`.
    """
    return "source_account_id IS NULL"


class ClientSourceAccountBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Conta de origem (ou o slot padrão) → conta do banco no plano contábil do cliente."""

    __tablename__ = "client_source_account_bindings"

    __table_args__ = (
        UniqueConstraint(
            "client_id", "source_type", "source_account_id", name=UQ_SOURCE_ACCOUNT_BINDING
        ),
        Index(
            UQ_SOURCE_ACCOUNT_BINDING_DEFAULT,
            "client_id",
            "source_type",
            unique=True,
            postgresql_where=text(default_binding_index_predicate()),
        ),
    )

    #: CASCADE: a exclusão definitiva leva as associações; o encerramento as purga
    #: explicitamente. A UNIQUE começa por `client_id` e serve toda busca por cliente.
    client_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    #: TIPO do provedor (`omie`, `arquivo`) — nunca FK de conexão (como no de-para).
    source_type: Mapped[str] = mapped_column(String(MAX_MOVEMENT_SOURCE_TYPE_CHARS), nullable=False)
    #: A conta de origem como a base a grava. NULO = o slot da CONTA PADRÃO.
    source_account_id: Mapped[str | None] = mapped_column(
        String(MAX_MOVEMENT_REF_CHARS), nullable=True, default=None
    )
    #: A conta do banco no plano contábil do cliente. Sem `ondelete` (NO ACTION): conta
    #: do plano nunca é apagada (só inativada); a purga apaga a associação ANTES.
    accounting_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("client_accounting_accounts.id", name=FK_SOURCE_ACCOUNT_BINDING_ACCOUNT),
        nullable=False,
    )
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    client: Mapped[Client] = relationship("Client", lazy="raise")

    def __repr__(self) -> str:
        return (
            f"<ClientSourceAccountBinding client={self.client_id} {self.source_type}:"
            f"{self.source_account_id or '<padrão>'} → {self.accounting_account_id}>"
        )
