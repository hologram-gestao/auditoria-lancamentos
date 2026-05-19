"""s16: índices compostos em reconciliation_anomalies

Revision ID: 7e3b9d2a5c4f
Revises: 4a2f9e8b1c3d
Create Date: 2026-05-19 10:00:00.000000+00:00

Code review da S11 apontou que `list_anomalies_paginated`
(review/repository.py) filtra por `(session_id, resolved)` e cruza com
`AnomalyType` via `anomaly_type_id`, mas só existe índice single-column
em `session_id`. Em sessões com 1000+ anomalias vira seq scan.

Os 2 índices compostos antecipam o gargalo. Hoje (tabelas pequenas) o
planner pode preferir seq scan — não é regressão, é o comportamento
esperado em volume baixo. O ganho aparece quando o volume crescer.

Os índices single-column existentes em `session_id` e `resolved`
permanecem: servem queries de contagem global / dashboards e foram
preservados pra evitar regressão em paths não cobertos por este review.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7e3b9d2a5c4f"
down_revision: str | None = "4a2f9e8b1c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_recon_anomalies_session_resolved",
        "reconciliation_anomalies",
        ["session_id", "resolved"],
    )
    op.create_index(
        "ix_recon_anomalies_session_type",
        "reconciliation_anomalies",
        ["session_id", "anomaly_type_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recon_anomalies_session_type",
        table_name="reconciliation_anomalies",
    )
    op.drop_index(
        "ix_recon_anomalies_session_resolved",
        table_name="reconciliation_anomalies",
    )
