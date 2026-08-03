"""sprint6: veredito do revisor na anomalia + flag de glossário na sessão

Revision ID: c5a2f81b6d34
Revises: b3e6a91d4c78
Create Date: 2026-08-03 15:00:00.000000+00:00

Sprint 6 (BACK 06.5) — ADR-014:

  1. `reconciliation_anomalies.review_verdict` (nullable,
     `procedente` | `improcedente`): o julgamento do REVISOR sobre um flag da
     qualificação. É um eixo DIFERENTE de `resolved` — "resolvida" diz que
     alguém agiu; "improcedente" diz que o flag não devia ter sido levantado.
     É este segundo eixo que alimenta o numerador da métrica de outcome da
     sprint. NULL = ainda não julgado (todo o histórico).

  2. `reconciliation_sessions.qualification_used_glossary` (NOT NULL, default
     `false`): a qualificação daquela sessão rodou com o bloco de glossário no
     prompt? Persistido para a tela de revisão exibir o selo sem consultar o
     sink de métrica, e escrito por `qualify_session` a partir do bloco
     REALMENTE injetado. `false` em sessão antiga e em cliente sem glossário —
     nenhuma regressão.

  Ambas são colunas aditivas e nulas/default: nenhum backfill necessário, e
  nada de crypto ou import de código de app aqui.

  Reversível: `downgrade` dropa as duas colunas. Perde-se o julgamento já
  registrado (o dado só existe nesta coluna) — é perda de feature, não
  corrupção de dado pré-existente.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5a2f81b6d34"
down_revision: str | None = "b3e6a91d4c78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_anomalies",
        sa.Column("review_verdict", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "reconciliation_sessions",
        sa.Column(
            "qualification_used_glossary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_sessions", "qualification_used_glossary")
    op.drop_column("reconciliation_anomalies", "review_verdict")
