"""fase1: situation conciliado_data_divergente + anomalia wrong_date

Revision ID: f4d1a7c93e20
Revises: d1e8a4b9f2c5
Create Date: 2026-06-18 12:00:00.000000+00:00

FASE 1 (conciliação de fatura de cartão) — BACK 1.2:

  1. Alarga `reconciliation_file_entries.situation` de VARCHAR(20) → VARCHAR(30).
     O novo valor de situação `conciliado_data_divergente` tem 26 chars e não
     cabe em 20. Widening é não-destrutivo (linhas existentes intactas).

  2. Insere (idempotente) o tipo de anomalia `wrong_date` em `anomaly_types`.
     Necessário em produção: o `seed_dev.py` só roda em dev, mas o Cloud Run
     Job de migrate aplica `alembic upgrade head`. `ON CONFLICT (code)` torna
     a inserção idempotente (não falha se o seed já tiver criado o registro).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4d1a7c93e20"
down_revision: str | None = "d1e8a4b9f2c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_WRONG_DATE_DESCRIPTION = (
    "O valor do lançamento bate com o arquivo enviado, mas a data registrada no Omie "
    "é diferente da data no arquivo. Pode indicar erro de lançamento manual ou ajuste "
    "automático de data para dia útil."
)


def upgrade() -> None:
    # 1) Alargar a coluna situation (cabe 'conciliado_data_divergente', 26 chars).
    op.alter_column(
        "reconciliation_file_entries",
        "situation",
        existing_type=sa.String(20),
        type_=sa.String(30),
        existing_nullable=False,
    )

    # 2) Seed idempotente do tipo de anomalia wrong_date. `id` é UUID app-side
    #    no ORM (uuid4); aqui usamos gen_random_uuid() (PG 13+). created_at/
    #    updated_at têm server_default, mas explicitamos por clareza.
    op.execute(
        sa.text(
            """
            INSERT INTO anomaly_types
                (id, code, name, description, severity, active, created_at, updated_at)
            VALUES (
                gen_random_uuid(),
                'wrong_date',
                'Data do lançamento diverge do extrato ou fatura',
                :description,
                'moderate',
                true,
                now(),
                now()
            )
            ON CONFLICT (code) DO NOTHING
            """
        ).bindparams(description=_WRONG_DATE_DESCRIPTION)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM anomaly_types WHERE code = 'wrong_date'"))
    # ⚠️ Reverter a largura falha se existir alguma linha com
    # 'conciliado_data_divergente' (26 chars não cabem em VARCHAR(20)).
    # Limpar/migrar essas linhas antes de fazer o downgrade.
    op.alter_column(
        "reconciliation_file_entries",
        "situation",
        existing_type=sa.String(30),
        type_=sa.String(20),
        existing_nullable=False,
    )
