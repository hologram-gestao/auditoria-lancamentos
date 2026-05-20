"""soft-delete em reconciliation_sessions

Revision ID: d1e8a4b9f2c5
Revises: b6f1c4d29e57
Create Date: 2026-05-20 20:00:00.000000+00:00

Adiciona soft-delete em `reconciliation_sessions` pra permitir descartar
sessões em erro sem perder histórico, e liberar a UNIQUE de idempotência
(client_id, omie_conta_id, reference_month, file_hash) pra criar uma
sessão nova com o mesmo arquivo no mesmo mês.

Mudanças:
  1. Coluna `deleted_at TIMESTAMPTZ NULL`. Sessões NÃO descartadas têm
     `NULL`; descartadas têm o timestamp da operação. NUNCA usar DELETE
     físico — só virar o flag.
  2. Converter a `UniqueConstraint(uq_recon_sessions_idempotency)` em
     **índice UNIQUE parcial** com `WHERE deleted_at IS NULL`. Postgres
     suporta isso nativamente; SQLAlchemy não modela UniqueConstraint
     parcial, então abandonamos a constraint e usamos só o índice.

Compat:
  - Sessões pré-migration ficam com `deleted_at=NULL` (não descartadas).
  - Repositories e endpoints precisam filtrar `WHERE deleted_at IS NULL`
    em todas as queries de leitura/listagem (ver `repository.py` +
    `clients/repository.py:list_reconciliations_paginated`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e8a4b9f2c5"
down_revision: str | None = "b6f1c4d29e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Coluna deleted_at — NULL = sessão ativa.
    op.add_column(
        "reconciliation_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recon_sessions_deleted_at",
        "reconciliation_sessions",
        ["deleted_at"],
    )

    # 2) Converter UNIQUE constraint em UNIQUE INDEX parcial.
    #    A constraint atual bloqueia recriar sessão com o mesmo arquivo
    #    mesmo após soft-delete. Trocamos por índice parcial que só vale
    #    para sessões NÃO descartadas (deleted_at IS NULL).
    op.drop_constraint(
        "uq_recon_sessions_idempotency",
        "reconciliation_sessions",
        type_="unique",
    )
    op.create_index(
        "uq_recon_sessions_idempotency",
        "reconciliation_sessions",
        ["client_id", "omie_conta_id", "reference_month", "file_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    # Reverter: dropa o índice parcial, recria a UniqueConstraint original
    # e remove a coluna. Cuidado: se houver sessões com `deleted_at IS NOT
    # NULL` que duplicariam alguma sessão ativa pela tupla idempotente, o
    # downgrade vai falhar — operação manual de cleanup necessária antes.
    op.drop_index("uq_recon_sessions_idempotency", table_name="reconciliation_sessions")
    op.create_unique_constraint(
        "uq_recon_sessions_idempotency",
        "reconciliation_sessions",
        ["client_id", "omie_conta_id", "reference_month", "file_hash"],
    )
    op.drop_index("ix_recon_sessions_deleted_at", table_name="reconciliation_sessions")
    op.drop_column("reconciliation_sessions", "deleted_at")
