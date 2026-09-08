"""s6: favoritos de cliente por usuário

Revision ID: f2a7c4d9e1b3
Revises: b7d4e91c2a53
Create Date: 2026-09-08 14:00:00.000000+00:00

Task 86e34jd5a (épico 86e34jchv — lista de clientes): tabela de associação
`user_client_favorites`, um favorito por `(user_id, client_id)`. Favorito é
preferência de QUEM opera (por usuário), não atributo do cliente — a lista de
clientes ordena os favoritos de quem pede primeiro.

Cascade nos dois lados: apagar usuário ou cliente leva o favorito junto, para a
exclusão de cliente (86e34jd1d) nunca travar aqui.

Só DDL, sem backfill (não há estado anterior a migrar). Reversível: `downgrade`
dropa a tabela — perde-se só a preferência, nunca dado de cliente.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a7c4d9e1b3"
down_revision: str | None = "b7d4e91c2a53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_client_favorites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_id", name="uq_user_client_favorites_user_client"),
    )
    op.create_index(
        "ix_user_client_favorites_user_id", "user_client_favorites", ["user_id"]
    )
    op.create_index(
        "ix_user_client_favorites_client_id", "user_client_favorites", ["client_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_client_favorites_client_id", table_name="user_client_favorites")
    op.drop_index("ix_user_client_favorites_user_id", table_name="user_client_favorites")
    op.drop_table("user_client_favorites")
