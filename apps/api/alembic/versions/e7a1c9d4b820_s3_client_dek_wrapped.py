"""sprint3: clients.dek_wrapped (DEK-por-cliente, envelope encryption)

Revision ID: e7a1c9d4b820
Revises: b2f7c4a9d318
Create Date: 2026-07-19 18:00:00.000000+00:00

Sprint 3 (cripto por cliente) — BACK 03.3:

  Adiciona `clients.dek_wrapped` (BYTEA, nullable) — a Data Encryption Key
  deste cliente, embrulhada pela KEK do KMS (envelope encryption). Cada
  cliente passa a ter uma DEK distinta; a DEK em claro só existe em memória.

  NULLABLE nesta migration de propósito: clientes NOVOS já nascem com DEK
  (gerada no create_client), mas os LEGADOS só ganham a DEK no backfill
  (BACK 03.4). O NOT-NULL efetivo é garantido pelo backfill, não por esta
  migration — forçar NOT NULL aqui quebraria o upgrade sobre a base existente.

  Reversível: o downgrade dropa a coluna. Nenhum dado cifrado é tocado aqui
  (o novo formato de envelope convive com as linhas bare legadas na leitura
  multi-chave).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c9d4b820"
down_revision: str | None = "b2f7c4a9d318"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("dek_wrapped", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clients", "dek_wrapped")
