"""client_accounting_chart_imports — o registro de cada importação do plano contábil.

O que muda (86e3ydjze, épico 86e3ydjy9):
    - tabela `client_accounting_chart_imports`: uma linha por importação do plano
      contábil que entrou, com quem importou, quando, o layout, as contagens e as
      linhas que ficaram DE FORA por confirmação na prévia (`excluded_lines`, JSONB com
      só `{linha, motivo}`, número e vocabulário fechado, nunca célula);
    - dois CHECKs: contagens não negativas e `excluded_count` igual ao tamanho de
      `excluded_lines` (que tem de ser um array); o índice `(client_id, imported_at)`
      da última importação, que a tela lê para o selo "plano não está 100%".

Sem backfill: as importações anteriores não foram registradas, e o cliente sem
registro não tem selo (é o estado de antes). Downgrade derruba a tabela: o registro é
só o aviso da tela, nenhuma escrita externa depende dele.

NB: nada de `app.*` importado no topo. Rótulos e predicados são SNAPSHOTS de
`db/models/client_accounting_chart_import.py`; `tests/unit/test_chart_import_schema.py`
compara as duas fontes.

Revision ID: e3a1c7d5b9f2
Revises: c8d1f4e6a2b9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e3a1c7d5b9f2"
down_revision = "c8d1f4e6a2b9"
branch_labels = None
depends_on = None

_TABLE = "client_accounting_chart_imports"
_LAYOUT_LENGTH = 20

# Snapshots de `db/models/client_accounting_chart_import.py`.
_COUNTS_CK_LABEL = "counts_non_negative"
_COUNTS_CK = (
    "accounts >= 0 AND new_accounts >= 0 AND updated_accounts >= 0 "
    "AND absent_accounts >= 0 AND excluded_count >= 0"
)
_EXCLUDED_CK_LABEL = "excluded_lines_count"
_EXCLUDED_CK = (
    "jsonb_typeof(excluded_lines) = 'array' "
    "AND excluded_count = jsonb_array_length(excluded_lines)"
)
_LATEST_INDEX = "ix_client_accounting_chart_imports_client_latest"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("imported_by", sa.UUID(), nullable=False),
        sa.Column("layout", sa.String(length=_LAYOUT_LENGTH), nullable=False),
        sa.Column("accounts", sa.Integer(), nullable=False),
        sa.Column("new_accounts", sa.Integer(), nullable=False),
        sa.Column("updated_accounts", sa.Integer(), nullable=False),
        sa.Column("absent_accounts", sa.Integer(), nullable=False),
        sa.Column("excluded_count", sa.Integer(), nullable=False),
        sa.Column(
            "excluded_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(_COUNTS_CK, name=op.f(f"ck_{_TABLE}_{_COUNTS_CK_LABEL}")),
        sa.CheckConstraint(_EXCLUDED_CK, name=op.f(f"ck_{_TABLE}_{_EXCLUDED_CK_LABEL}")),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f(f"fk_{_TABLE}_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["imported_by"],
            ["users.id"],
            name=op.f(f"fk_{_TABLE}_imported_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_TABLE}")),
    )
    op.create_index(_LATEST_INDEX, _TABLE, ["client_id", "imported_at"], unique=False)


def downgrade() -> None:
    op.drop_index(_LATEST_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
