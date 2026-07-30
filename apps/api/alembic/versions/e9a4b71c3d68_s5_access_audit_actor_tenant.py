"""sprint5: access_audit com escopo e tenant do ATOR

Revision ID: e9a4b71c3d68
Revises: d5c81a4e9b27
Create Date: 2026-07-30 19:30:00.000000+00:00

Sprint 5 (R6) — BACK 05.2:

  A `access_audit` (S3) só sabia o tenant ALVO (`client_id`). Com tenants, a
  negação cross-tenant fica cega sem saber DE QUE tenant partiu o acesso. Duas
  colunas novas:
    - `user_scope`      : 'system' | 'client' — escopo do ATOR. NOT NULL,
                          server_default 'system'.
    - `actor_client_id` : tenant do ATOR (UUID nulável — `system` não tem tenant).

  **Sem FK**, coerente com a decisão da S3: a tabela é log append-only e durável,
  independente do ciclo de vida das linhas que referencia.

  Backfill IDEMPOTENTE: as linhas da S3 viram `user_scope='system'`,
  `actor_client_id NULL` — por definição, só existiam usuários da equipe
  Hologram quando foram gravadas. O UPDATE é convergente: reexecutar é no-op.

  **Sem índice novo.** A consulta do D+30 conta ocorrências de negação por
  período — já coberta por `ix_access_audit_action_timestamp`. Índice
  especulativo em `user_scope` (2 valores, baixa cardinalidade) só penalizaria a
  escrita.

  A ação continua sendo `denied` (a lista fechada { denied, view, export } não
  muda): cross-tenant é PROPRIEDADE do ator, derivável de
  `user_scope='client' AND actor_client_id IS DISTINCT FROM client_id`. O porquê
  está no docstring de `app/db/models/access_audit.py`.

  Reversível: o downgrade dropa as duas colunas.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9a4b71c3d68"
down_revision: str | None = "d5c81a4e9b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "access_audit",
        sa.Column("user_scope", sa.String(length=20), nullable=False, server_default="system"),
    )
    op.add_column("access_audit", sa.Column("actor_client_id", sa.UUID(), nullable=True))

    # Backfill convergente (não incremental) — reexecutar é no-op.
    op.execute(
        sa.text(
            "UPDATE access_audit SET user_scope = 'system', actor_client_id = NULL "
            "WHERE user_scope IS NULL OR user_scope NOT IN ('system', 'client')"
        )
    )


def downgrade() -> None:
    op.drop_column("access_audit", "actor_client_id")
    op.drop_column("access_audit", "user_scope")
