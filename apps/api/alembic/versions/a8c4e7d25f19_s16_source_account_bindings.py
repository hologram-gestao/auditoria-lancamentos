"""s16_source_account_bindings — a conta contábil do BANCO de cada conta de origem (BACK 16.3 — R3).

Cria a associação conta de origem → conta do plano contábil do cliente (16.1), com o
SLOT DA CONTA PADRÃO (`source_account_id` nulo) para as linhas sem conta de origem, e
acrescenta ao item da materialização o snapshot do código da conta do banco e da
presença de histórico — o que a partida (débito/crédito pelo sinal) e o predicado de
"partida completa" leem.

O que muda:
    - tabela `client_source_account_bindings` — `UNIQUE(client_id, source_type,
      source_account_id)`, índice único PARCIAL `(client_id, source_type) WHERE
      source_account_id IS NULL` (o slot padrão), FK para o cliente (CASCADE), para a
      conta do plano (NO ACTION) e autoria RESTRICT;
    - `client_mapping_materialization_items`: `bank_account_code` e `history_present`
      (nuláveis — as materializações anteriores e os outros destinos seguem sem eles).

Sem backfill. Aditivo. Downgrade REAL: `DROP TABLE` + `DROP COLUMN` — perde-se a
associação (configuração, refeita pela tela) e o snapshot do banco das materializações
feitas depois do upgrade (que só existem se o deploy rodou).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_client_source_account_binding_schema.py`
compara as duas fontes (inclusive o predicado do índice parcial).

Revision ID: a8c4e7d25f19
Revises: f5b8d2e61c37
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a8c4e7d25f19"
down_revision = "f5b8d2e61c37"
branch_labels = None
depends_on = None

_TABLE = "client_source_account_bindings"
_ITEMS = "client_mapping_materialization_items"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_SOURCE_TYPE_MAX = 30
_SOURCE_ACCOUNT_MAX = 60
_ACCOUNT_CODE_MAX = 20

_UQ_SOURCE = "uq_client_source_account_bindings_source"
_UQ_DEFAULT = "uq_client_source_account_bindings_default"
_UQ_DEFAULT_PREDICATE = "source_account_id IS NULL"
_FK_CLIENT = "fk_client_source_account_bindings_client_id_clients"
_FK_ACCOUNT = "fk_client_source_account_bindings_accounting_account"
_FK_CREATED_BY = "fk_client_source_account_bindings_created_by_users"
_FK_UPDATED_BY = "fk_client_source_account_bindings_updated_by_users"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=_SOURCE_TYPE_MAX), nullable=False),
        # NULO = o slot da CONTA PADRÃO do tipo de origem.
        sa.Column("source_account_id", sa.String(length=_SOURCE_ACCOUNT_MAX), nullable=True),
        sa.Column("accounting_account_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=_FK_CLIENT, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["accounting_account_id"], ["client_accounting_accounts.id"], name=_FK_ACCOUNT
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=_FK_CREATED_BY, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=_FK_UPDATED_BY, ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("client_id", "source_type", "source_account_id", name=_UQ_SOURCE),
    )
    # À mão: o autogenerate não enxerga o predicado de índice parcial.
    op.create_index(
        _UQ_DEFAULT,
        _TABLE,
        ["client_id", "source_type"],
        unique=True,
        postgresql_where=sa.text(_UQ_DEFAULT_PREDICATE),
    )

    op.add_column(
        _ITEMS, sa.Column("bank_account_code", sa.String(length=_ACCOUNT_CODE_MAX), nullable=True)
    )
    op.add_column(_ITEMS, sa.Column("history_present", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column(_ITEMS, "history_present")
    op.drop_column(_ITEMS, "bank_account_code")
    op.drop_index(_UQ_DEFAULT, table_name=_TABLE)
    op.drop_table(_TABLE)
