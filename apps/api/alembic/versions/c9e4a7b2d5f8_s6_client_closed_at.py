"""s6_client_closed_at — encerramento de cliente com retenção (86e36pm1z).

Uma coluna só: `clients.closed_at` (timestamptz, nullable). NULL = aberto;
preenchida = ENCERRADO (terminal). O scrub em si (nome anonimizado, credenciais
vazias, `dek_wrapped` NULL, usuários do tenant anonimizados) é feito pelo
service no ato do encerramento — não há backfill: nenhum cliente existente
nasce encerrado.

Aditiva e reversível: o downgrade só remove a coluna (clientes já encerrados
voltariam a parecer abertos com credenciais vazias — aceitável porque o
downgrade só é usado antes de existir encerramento em produção).

Revision ID: c9e4a7b2d5f8
Revises: a1c5e7f9b2d4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c9e4a7b2d5f8"
down_revision = "a1c5e7f9b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clients", "closed_at")
