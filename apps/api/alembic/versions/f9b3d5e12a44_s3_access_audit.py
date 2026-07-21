"""sprint3: tabela access_audit (auditoria de acesso — denied/view/export)

Revision ID: f9b3d5e12a44
Revises: e7a1c9d4b820
Create Date: 2026-07-19 19:30:00.000000+00:00

Sprint 3 (Req. 3) — BACK 03.5:

  Faz nascer a auditoria de acesso que NUNCA existiu (as 9 tabelas da migration
  raiz não incluíam auditoria). Registra a lista fechada { denied, view, export }.
  SÓ IDs — nenhuma PII. Sem FK (log append-only, independente do ciclo de vida
  das linhas referenciadas). Índices enxutos p/ não virar gargalo de escrita.

  Reversível: downgrade dropa a tabela (e seus índices).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9b3d5e12a44"
down_revision: str | None = "e7a1c9d4b820"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_audit",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("rota", sa.String(length=255), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_access_audit_client_timestamp", "access_audit", ["client_id", "timestamp"]
    )
    op.create_index(
        "ix_access_audit_action_timestamp", "access_audit", ["action", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_access_audit_action_timestamp", table_name="access_audit")
    op.drop_index("ix_access_audit_client_timestamp", table_name="access_audit")
    op.drop_table("access_audit")
