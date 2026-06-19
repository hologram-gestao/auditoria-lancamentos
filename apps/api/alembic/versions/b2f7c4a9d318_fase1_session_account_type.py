"""fase1: account_type em reconciliation_sessions (conta corrente vs cartão)

Revision ID: b2f7c4a9d318
Revises: f4d1a7c93e20
Create Date: 2026-06-19 13:30:00.000000+00:00

FASE 1 (conciliação de fatura de cartão) — BACK 1.3:

  Adiciona `reconciliation_sessions.account_type` (VARCHAR(20)) — tipo
  normalizado da conta conciliada: 'checking' (conta corrente) ou
  'credit_card' (cartão). Derivado do `tipo` Omie da conta selecionada no
  service (CR → credit_card; resto → checking).

  Não-destrutivo: a coluna é NOT NULL com server_default 'checking', então
  as linhas existentes (todas conta corrente até aqui) recebem 'checking'
  sem precisar de backfill manual.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2f7c4a9d318"
down_revision: str | None = "f4d1a7c93e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_sessions",
        sa.Column(
            "account_type",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'checking'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_sessions", "account_type")
