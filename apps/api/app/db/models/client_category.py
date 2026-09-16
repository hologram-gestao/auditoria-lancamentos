"""Modelo ClientCategory — catálogo de categorias (nicho/segmento) de cliente.

Task 86e34jd8m (épico 86e34jchv). Catálogo FIXO, editado por admin: texto
livre viraria "Fintech" / "fintech" / "Fin tech" e mataria a análise por nicho
que motivou o pedido (lucratividade por segmento). Uma categoria por cliente
(`clients.category_id`, nullable — cliente sem categoria é o estado inicial de
todos os existentes).

Escopo: POR ORGANIZAÇÃO (decisão D3 da camada de organizações, 86e36ec7p):
cada BPO categoriza os próprios clientes sem enxergar as categorias dos
outros. A unicidade do nome é `(organization_id, name)`; `server_default` =
Hologram pelo mesmo motivo das colunas irmãs em `clients`/`users` (linha sem o
campo é a forma antiga da tabela). O service continua comparando o nome sem
caixa — dentro da organização.

A cor é um TOM semântico (`ClientCategoryTone`), nunca um hex: o front mapeia o
tom para tokens do tema (§7 — cor em componente é sempre token, e o tema da
marca precisa chegar no chip sem variante).

Não é dado identificável: rótulo genérico, sem CNPJ ou razão social — fica em
claro, na mesma classe do `clients.name` (§4.5).
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.organization import organization_id_server_default

#: Tamanho máximo do nome — chip na lista, precisa caber numa linha.
MAX_CATEGORY_NAME_CHARS = 60

#: Nome da UNIQUE `(organization_id, name)` — case-sensitive no banco; o service
#: compara sem caixa, dentro da organização. Substituiu `uq_client_categories_name`
#: (global) na migration `3e8f1a6c9d24`.
UQ_CLIENT_CATEGORY_ORGANIZATION_NAME = "uq_client_categories_organization_id_name"


class ClientCategoryTone(StrEnum):
    """Tom do chip — mapeado para tokens do tema no front, nunca cor fixa."""

    NEUTRAL = "neutral"
    PRIMARY = "primary"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"


class ClientCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_categories"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name=UQ_CLIENT_CATEGORY_ORGANIZATION_NAME),
    )

    #: Sem índice próprio: a UNIQUE `(organization_id, name)` serve toda busca por
    #: organização pelo prefixo (mesmo racional de `client_assignments`).
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        server_default=text(organization_id_server_default()),
    )
    name: Mapped[str] = mapped_column(String(MAX_CATEGORY_NAME_CHARS), nullable=False)
    tone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ClientCategoryTone.NEUTRAL.value,
        server_default=text("'neutral'"),
    )

    def __repr__(self) -> str:
        return f"<ClientCategory name={self.name!r} tone={self.tone}>"
