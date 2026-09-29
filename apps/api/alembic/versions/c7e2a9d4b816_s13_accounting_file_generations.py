"""s13_accounting_file_generations — registro de cada geração do arquivo contábil, só metadados (BACK 13.4 — R3).

O que muda:
    - tabela `accounting_file_generations` — cliente (desnormalizado, CASCADE),
      materialização (RESTRICT), versão do layout por FK COMPOSTA para
      `export_layout_versions(layout_id, version)` (RESTRICT), autor (RESTRICT),
      competência, linhas, total `DECIMAL(14,2)`, SHA-256 (64 hex) e data. CHECKs de
      faixa, do formato do hash e do 1º dia da competência; índice
      `(client_id, materialization_id)`.

SEM coluna de conteúdo: o arquivo traz histórico (dado do cliente final) e é regenerável
por construção; o download regenera e confere o SHA-256. Nenhum campo cifrado. Aditivo,
sem backfill. Downgrade REAL: `DROP TABLE` (perde-se o histórico de gerações — metadados).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_accounting_file_generation_schema.py`
compara as fontes.

Revision ID: c7e2a9d4b816
Revises: b3d9e5f17a20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7e2a9d4b816"
down_revision = "b3d9e5f17a20"
branch_labels = None
depends_on = None

_TABLE = "accounting_file_generations"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_SHA256_LEN = 64
_IX_CLIENT_MATERIALIZATION = "ix_accounting_file_generations_client_id_materialization_id"
_FK_CLIENT = "fk_accounting_file_generations_client_id_clients"
_FK_MATERIALIZATION = "fk_accounting_file_generations_materialization"
_FK_LAYOUT_VERSION = "fk_accounting_file_generations_layout_version"
_FK_AUTHOR = "fk_accounting_file_generations_author_id_users"
#: (rótulo, SQL) — o rótulo vira `ck_accounting_file_generations_<rótulo>` pela naming
#: convention do `target_metadata` (precedente `e6b2c9d47f13`).
_CHECKS: tuple[tuple[str, str], ...] = (
    ("layout_version_positive", "layout_version >= 1"),
    ("line_count_nonnegative", "line_count >= 0"),
    ("total_amount_nonnegative", "total_amount >= 0"),
    ("sha256_hex", "sha256 ~ '^[0-9a-f]{64}$'"),
    ("competence_first_day", "EXTRACT(DAY FROM competence) = 1"),
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("materialization_id", sa.UUID(), nullable=False),
        sa.Column("layout_id", sa.UUID(), nullable=False),
        sa.Column("layout_version", sa.Integer(), nullable=False),
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("sha256", sa.String(length=_SHA256_LEN), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=_FK_CLIENT, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["materialization_id"],
            ["client_mapping_materializations.id"],
            name=_FK_MATERIALIZATION,
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["layout_id", "layout_version"],
            ["export_layout_versions.layout_id", "export_layout_versions.version"],
            name=_FK_LAYOUT_VERSION,
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=_FK_AUTHOR, ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        *(sa.CheckConstraint(sql, name=label) for label, sql in _CHECKS),
    )
    op.create_index(_IX_CLIENT_MATERIALIZATION, _TABLE, ["client_id", "materialization_id"])


def downgrade() -> None:
    op.drop_index(_IX_CLIENT_MATERIALIZATION, table_name=_TABLE)
    op.drop_table(_TABLE)
