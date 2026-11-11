"""session_processing_stage — etapa do processamento da conciliação (86e3mpmcw).

O que muda:
    - `reconciliation_sessions.processing_stage` (VARCHAR(20), nullable): a etapa em
      que o processamento está ou parou (`carregando`, `omie`, `cruzamento`,
      `gravacao`), com o CHECK `ck_reconciliation_sessions_processing_stage` (NULL ou
      um dos quatro valores de `ProcessingStage`).

Por quê: o alerta do watchdog (`heartbeat_missing`) dizia só quantas sessões
derrubou. Com a etapa, o plantão sabe onde a conciliação parou sem abrir o banco. O
job grava a etapa numa transação curta antes de cada passo.

Sem backfill: sessão antiga fica com NULL, lido como "não registrada".

Downgrade REAL: derruba o CHECK e a coluna. A etapa gravada é diagnóstico e se
perde; nenhuma regra de negócio a lê.

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). O predicado e o rótulo
são SNAPSHOTS de `db/models/reconciliation_session.py`;
`tests/unit/test_processing_stage_schema.py` compara as duas fontes.

Revision ID: a4c8e2f6b1d3
Revises: e3a1c7d5b9f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a4c8e2f6b1d3"
down_revision = "e3a1c7d5b9f2"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_sessions"
_COLUMN = "processing_stage"
_LENGTH = 20

# Snapshot de `processing_stage_check()` (`db/models/reconciliation_session.py`).
_CK_LABEL = "processing_stage"
_CK = (
    "processing_stage IS NULL OR "
    "processing_stage IN ('carregando', 'omie', 'cruzamento', 'gravacao')"
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=_LENGTH), nullable=True))
    # Rótulo, não o nome final: a NAMING_CONVENTION prefixa `ck_reconciliation_sessions_`.
    op.create_check_constraint(_CK_LABEL, _TABLE, sa.text(_CK))


def downgrade() -> None:
    op.drop_constraint(_CK_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
