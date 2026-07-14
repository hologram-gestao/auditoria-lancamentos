"""days_diff em reconciliation_file_entries (Sprint 2 / BACK 02.4)

Revision ID: f2a7c9d31b84
Revises: d1e8a4b9f2c5
Create Date: 2026-07-13 12:00:00.000000+00:00

Persiste a divergência de data de cada linha CONCILIADA. Hoje o matcher
calcula `days_diff` só para desempatar e joga fora — um match dentro da
tolerância (default 3 dias) vira `conciliado` puro e a divergência SOME do
entregável (tela e Excel). Basta não jogar fora o que já se sabe.

Mudança:
  - Coluna `days_diff INTEGER NULL` (assinado; 0 = data exata) em
    `reconciliation_file_entries`. Gravada pelo `apply_matches`:
    `transaction_date(arquivo) - transaction_date(omie)`.

Compat:
  - Linhas não conciliadas (sem_omie/ignorado) ficam com `NULL` (não há
    divergência a registrar).
  - Sessões pré-migration ficam com `NULL` (divergência não registrada) —
    a UI degrada mostrando só a data do arquivo, como hoje.
  - Nullable + sem backfill → downgrade é o simples drop da coluna;
    round-trip upgrade->downgrade->upgrade validado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a7c9d31b84"
down_revision: str | None = "d1e8a4b9f2c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_file_entries",
        sa.Column("days_diff", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_file_entries", "days_diff")
