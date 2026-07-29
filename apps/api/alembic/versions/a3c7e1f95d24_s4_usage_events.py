"""sprint4: tabela usage_events (sink de instrumentação de outcome)

Revision ID: a3c7e1f95d24
Revises: f9b3d5e12a44
Create Date: 2026-07-25 18:00:00.000000+00:00

Sprint 4 (BACK 04.1):

  Cria o SINK dos eventos declarados no `## Outcome & verificação` do PRD. O repo
  não tem infra de analytics e a métrica da sprint precisa de agregação em D+30.

  Tabela mínima: event (text) · session_id (uuid, FK LÓGICA — sem constraint, log
  append-only como `access_audit`) · props (jsonb) · created_at (timestamptz).
  SEM PII: só IDs/enums (a whitelist de chaves por evento é validada no servidor).

  Índices:
    - ix_usage_events_event_created  → leitura do D+30 (por evento no período).
    - uq_usage_events_event_session  → UNIQUE PARCIAL (event, session_id) onde
      session_id IS NOT NULL: idempotência do emissor (INSERT ... ON CONFLICT DO
      NOTHING). Um evento por sessão é o grão da métrica.

  Reversível: downgrade dropa índices + tabela (testado em BEGIN; … ROLLBACK;).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c7e1f95d24"
down_revision: str | None = "f9b3d5e12a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column(
            "props",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_events_event_created",
        "usage_events",
        ["event", "created_at"],
    )
    op.create_index(
        "uq_usage_events_event_session",
        "usage_events",
        ["event", "session_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_usage_events_event_session", table_name="usage_events")
    op.drop_index("ix_usage_events_event_created", table_name="usage_events")
    op.drop_table("usage_events")
