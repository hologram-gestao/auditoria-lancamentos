"""sprint7: intenção de lançamento no Omie (dedup primária do ADL)

Revision ID: d7c2b9f14a86
Revises: c5a2f81b6d34
Create Date: 2026-08-18 15:00:00.000000+00:00

Sprint 7 (BACK 07.2) — ADR-021-BE.

Cria `reconciliation_omie_postings`: o estado PRÓPRIO do ADL sobre "esta linha
da fatura já foi lançada no Omie?". A proteção contra lançamento duplicado
**não** pode repousar no fornecedor — que a Omie imponha unicidade sobre
`cCodIntLanc` é suposição não-verificada (S-1). Aqui ela é verificável hoje:

  - `uq_recon_omie_postings_file_entry` — UMA intenção por linha da fatura.
    É o que torna `register_intent` idempotente sob concorrência (duplo-clique,
    retry, dois workers), porque a garantia é do BANCO e não de um
    "SELECT antes do INSERT".
  - `uq_recon_omie_postings_client_cod_int` — a chave enviada à Omie não se
    repete dentro do tenant. Colisão do encoding (85 bits — ver
    `omie_posting/keys.py`) vira `IntegrityError`, nunca uma linha
    silenciosamente não lançada.

Sem import de código de app (learning "migration que chama código de app precisa
das MESMAS secrets do serviço"): só DDL, sem backfill, sem crypto, sem Settings.

Reversível: `downgrade` dropa a tabela. Como o estado de lançamento só existe
aqui, o rollback é **perda de feature**, não corrupção de dado pré-existente —
nenhuma tabela anterior é alterada. Atenção operacional: reverter esta migration
sobre um ambiente que JÁ lançou no Omie apaga a memória de "o que já foi
lançado"; o `reconciliation_file_entries.omie_lancamento_id` das linhas
confirmadas permanece e continua sendo o freio contra relançar.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7c2b9f14a86"
down_revision: str | None = "c5a2f81b6d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COD_INT_LANC_MAX_LENGTH = 20


def upgrade() -> None:
    op.create_table(
        "reconciliation_omie_postings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("file_entry_id", sa.UUID(), nullable=False),
        sa.Column("cod_int_lanc", sa.String(length=_COD_INT_LANC_MAX_LENGTH), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("omie_lancamento_id", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["session_id"], ["reconciliation_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["file_entry_id"], ["reconciliation_file_entries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_entry_id", name="uq_recon_omie_postings_file_entry"),
        sa.UniqueConstraint(
            "client_id", "cod_int_lanc", name="uq_recon_omie_postings_client_cod_int"
        ),
    )
    op.create_index(
        "ix_reconciliation_omie_postings_session_id",
        "reconciliation_omie_postings",
        ["session_id"],
    )
    op.create_index(
        "ix_reconciliation_omie_postings_client_id",
        "reconciliation_omie_postings",
        ["client_id"],
    )
    op.create_index(
        "ix_recon_omie_postings_session_status",
        "reconciliation_omie_postings",
        ["session_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recon_omie_postings_session_status", table_name="reconciliation_omie_postings"
    )
    op.drop_index(
        "ix_reconciliation_omie_postings_client_id", table_name="reconciliation_omie_postings"
    )
    op.drop_index(
        "ix_reconciliation_omie_postings_session_id", table_name="reconciliation_omie_postings"
    )
    op.drop_table("reconciliation_omie_postings")
