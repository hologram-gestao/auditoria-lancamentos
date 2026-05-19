"""s11 fix: unique constraint omie_lancamento_id + period_start/end persistidos

Revision ID: 4a2f9e8b1c3d
Revises: c8f3407fb8d3
Create Date: 2026-05-18 12:00:00.000000+00:00

Cobre 2 itens do code review da S11:

1. CLAUDE.md §5.4 — "um OmieEntry só matcha uma Movement".
   Cria índice ÚNICO PARCIAL em (session_id, omie_lancamento_id) WHERE
   omie_lancamento_id IS NOT NULL, garantindo a invariante no banco mesmo
   em corrida (duas requests simultâneas de "Trocar Omie" passando pela
   checagem aplicativa). O service captura `IntegrityError` e devolve a
   mesma `ValidationAppError` do path otimista, mantendo a UX idêntica.

2. period_start/period_end agora persistidos em `reconciliation_sessions`.
   Antes, o review service usava `[reference_month, last_day_of_month]`
   como aproximação — quebrava em extratos com período fora do mês
   (ex: 15/04→14/05), faturas de cartão e lançamentos nos primeiros
   dias do mês seguinte. Os valores já vêm no payload de criação
   (`statement.period_start/period_end`) — só não eram salvos.

   Colunas NULL-tolerantes porque sessões pré-migration não têm o valor
   real e seguem com a aproximação por reference_month (ver fallback
   em ReviewService.list_available_omie_entries).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a2f9e8b1c3d"
down_revision: str | None = "c8f3407fb8d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Índice único parcial — impede 2 file_entries da mesma sessão com o
    #    mesmo omie_lancamento_id. Parcial pra permitir múltiplas linhas
    #    "sem vínculo" (NULL).
    op.create_index(
        "ix_recon_file_entry_session_omie_unique",
        "reconciliation_file_entries",
        ["session_id", "omie_lancamento_id"],
        unique=True,
        postgresql_where=sa.text("omie_lancamento_id IS NOT NULL"),
    )

    # 2) Período real do statement — colunas nullable, sem backfill.
    op.add_column(
        "reconciliation_sessions",
        sa.Column("period_start", sa.Date(), nullable=True),
    )
    op.add_column(
        "reconciliation_sessions",
        sa.Column("period_end", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_sessions", "period_end")
    op.drop_column("reconciliation_sessions", "period_start")
    op.drop_index(
        "ix_recon_file_entry_session_omie_unique",
        table_name="reconciliation_file_entries",
    )
