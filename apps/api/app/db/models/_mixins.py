"""Mixins compartilhados entre modelos ORM.

Centralizam padrões obrigatórios (CLAUDE.md §3-4):
    - UUID v4 para todas as PKs (nunca IDs sequenciais).
    - TIMESTAMPTZ com timezone-aware via `func.now()` no DB.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPrimaryKeyMixin:
    """PK UUID v4 gerado pela aplicação (não DB-side, evita ida ao banco para checar)."""

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class TimestampMixin:
    """`created_at` e `updated_at` em TIMESTAMPTZ.

    `updated_at` é atualizado pelo SQLAlchemy a cada flush via `onupdate`.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
