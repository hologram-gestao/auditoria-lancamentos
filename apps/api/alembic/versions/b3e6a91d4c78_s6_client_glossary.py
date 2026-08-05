"""sprint6: glossário por tenant + marcador de versão no cliente

Revision ID: b3e6a91d4c78
Revises: a1d7f36c9b52
Create Date: 2026-08-03 13:00:00.000000+00:00

Sprint 6 (BACK 06.2) — ADR-011:

  1. `client_glossary_entries`: UMA tabela com discriminador `kind`
     (`categoria` | `fornecedor` | `regra`), escopada a `client_id`.
     Campos textuais (`code`, `name`, `description`) são CIFRADOS com a DEK do
     cliente (envelope AES-256-GCM + AAD por linha + IV novo por operação) —
     nome de categoria/fornecedor e enunciado de regra são dado identificável do
     cliente final e o CLAUDE.md §4.5 proíbe persistir em claro. `kind` fica em
     claro: é enum do sistema e é por ele que a leitura ordena/filtra.
     Soft delete (`deleted_at`), padrão do repo — DELETE físico é proibido.

  2. `clients.glossary_version`: contador (NOT NULL, default 0) incrementado na
     MESMA transação de qualquer escrita no glossário, INCLUSIVE a remoção. É o
     marcador que invalida o bloco de prompt cacheado da qualificação (R3).
     `MAX(updated_at)` das entradas não serviria — um delete não mexe no MAX.

  Sem import de código de app aqui (learning "job de migration sem as secrets do
  serviço não sobe"): o backfill é puro SQL (`server_default '0'`), não precisa
  de crypto nem de Settings.

  Reversível: `downgrade` dropa a tabela e a coluna. ⚠️ Como as entradas só
  existem nesta tabela, o downgrade DESCARTA o glossário — por isso a tabela é
  criada, e não alterada: rollback é perda de feature, não corrupção de dado
  pré-existente.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3e6a91d4c78"
down_revision: str | None = "a1d7f36c9b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IV_HEX_LENGTH = 24


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "glossary_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.create_table(
        "client_glossary_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("code_encrypted", sa.Text(), nullable=True),
        sa.Column("code_iv", sa.String(length=_IV_HEX_LENGTH), nullable=True),
        sa.Column("name_encrypted", sa.Text(), nullable=False),
        sa.Column("name_iv", sa.String(length=_IV_HEX_LENGTH), nullable=False),
        sa.Column("description_encrypted", sa.Text(), nullable=True),
        sa.Column("description_iv", sa.String(length=_IV_HEX_LENGTH), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_client_glossary_active",
        "client_glossary_entries",
        ["client_id", "kind", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_client_glossary_active", table_name="client_glossary_entries")
    op.drop_table("client_glossary_entries")
    op.drop_column("clients", "glossary_version")
