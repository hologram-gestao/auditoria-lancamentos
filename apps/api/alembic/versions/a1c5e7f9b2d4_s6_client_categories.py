"""s6: catálogo de categorias de cliente + clients.category_id

Revision ID: a1c5e7f9b2d4
Revises: f2a7c4d9e1b3
Create Date: 2026-09-08 15:00:00.000000+00:00

Task 86e34jd8m (épico 86e34jchv — lista de clientes): catálogo fixo
`client_categories` (nome único + tom semântico) e a coluna nullable
`clients.category_id` — uma categoria por cliente.

FK RESTRICT: apagar categoria com clientes vinculados é 409 no catálogo, com a
contagem — nunca um "sem categoria" silencioso em lote.

Só DDL, sem backfill (todo cliente existente fica sem categoria). Reversível:
`downgrade` remove a coluna e a tabela — perde-se a classificação, nunca dado
de cliente.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5e7f9b2d4"
down_revision: str | None = "f2a7c4d9e1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column(
            "tone", sa.String(length=20), server_default=sa.text("'neutral'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_client_categories_name"),
    )
    op.add_column("clients", sa.Column("category_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_clients_category_id_client_categories",
        "clients",
        "client_categories",
        ["category_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_clients_category_id", "clients", ["category_id"])


def downgrade() -> None:
    op.drop_index("ix_clients_category_id", table_name="clients")
    op.drop_constraint("fk_clients_category_id_client_categories", "clients", type_="foreignkey")
    op.drop_column("clients", "category_id")
    op.drop_table("client_categories")
