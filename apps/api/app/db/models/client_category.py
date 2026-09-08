"""Modelo ClientCategory — catálogo de categorias (nicho/segmento) de cliente.

Task 86e34jd8m (épico 86e34jchv). Catálogo FIXO, editado por admin: texto
livre viraria "Fintech" / "fintech" / "Fin tech" e mataria a análise por nicho
que motivou o pedido (lucratividade por segmento). Uma categoria por cliente
(`clients.category_id`, nullable — cliente sem categoria é o estado inicial de
todos os existentes).

Escopo: hoje o catálogo é global (uma organização só). Quando a camada de
organizações (86e32fp4b) chegar, esta tabela ganha `organization_id` — por
isso nasce como tabela própria, e não como enum no código.

A cor é um TOM semântico (`ClientCategoryTone`), nunca um hex: o front mapeia o
tom para tokens do tema (§7 — cor em componente é sempre token, e o tema da
marca precisa chegar no chip sem variante).

Não é dado identificável: rótulo genérico, sem CNPJ ou razão social — fica em
claro, na mesma classe do `clients.name` (§4.5).
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

#: Tamanho máximo do nome — chip na lista, precisa caber numa linha.
MAX_CATEGORY_NAME_CHARS = 60

#: Nome da UNIQUE de `name` (case-sensitive no banco; o service compara sem caixa).
UQ_CLIENT_CATEGORY_NAME = "uq_client_categories_name"


class ClientCategoryTone(StrEnum):
    """Tom do chip — mapeado para tokens do tema no front, nunca cor fixa."""

    NEUTRAL = "neutral"
    PRIMARY = "primary"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"


class ClientCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_categories"
    __table_args__ = (UniqueConstraint("name", name=UQ_CLIENT_CATEGORY_NAME),)

    name: Mapped[str] = mapped_column(String(MAX_CATEGORY_NAME_CHARS), nullable=False)
    tone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ClientCategoryTone.NEUTRAL.value,
        server_default=text("'neutral'"),
    )

    def __repr__(self) -> str:
        return f"<ClientCategory name={self.name!r} tone={self.tone}>"
