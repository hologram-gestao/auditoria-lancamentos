"""sprint4: notifications (aviso in-app) + reconciliation_sessions.error_code

Revision ID: c4f1a8b62e93
Revises: b8e2d4a71f36
Create Date: 2026-07-25 20:20:00.000000+00:00

Sprint 4 (BACK 04.4):

  1. Tabela `notifications` — o aviso in-app de fim de conciliação. SEM PII por
     construção: o conteúdo mora em colunas TIPADAS (conta, mês, tipo, código de
     erro), não num texto livre. Sem FK (trilha append-only, como `access_audit`
     e `usage_events`).

     Índices:
       - ix_notifications_user_unread  → PARCIAL (`read_at IS NULL`). O sino faz
         poll de 15 s por usuário logado; indexar só as não lidas mantém a
         contagem barata mesmo com o histórico crescendo.
       - ix_notifications_user_created → listagem paginada do sino.

  2. `reconciliation_sessions.error_code` — CÓDIGO canônico do desfecho de erro.
     A mensagem PT-BR já existia (`error_message`); faltava o código, que é o
     que a tela de erro e a notificação mostram para o usuário reportar (S2/R9).
     Aditiva e nullable: sessões antigas ficam sem código, e a UI cai na
     mensagem.

Reversível: downgrade dropa a coluna e a tabela (com seus índices).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f1a8b62e93"
down_revision: str | None = "b8e2d4a71f36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("omie_conta_id", sa.BigInteger(), nullable=False),
        sa.Column("reference_month", sa.Date(), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
    )

    op.add_column(
        "reconciliation_sessions",
        sa.Column("error_code", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_sessions", "error_code")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_table("notifications")
