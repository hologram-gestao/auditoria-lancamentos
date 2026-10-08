"""card_invoice_due_date_session — vencimento da fatura e modo do cartão na sessão.

O que muda (86e3n70p0):
    - `reconciliation_sessions.invoice_due_date` (DATE, nullable): vencimento da
      fatura de cartão, extraído pelo parser e confirmado pelo usuário na prévia;
    - `reconciliation_sessions.card_posting_date_mode` (VARCHAR(20), nullable):
      SNAPSHOT do modo de lançamento do cartão usado na sessão, com o CHECK
      `ck_reconciliation_sessions_card_posting_date_mode` (NULL ou um dos dois
      valores de `CardPostingDateMode`);
    - o CHECK `ck_reconciliation_sessions_card_due_date_coherent`: sessão no modo
      `invoice_due_date` tem o vencimento (sem ele não há lote para cruzar).

Por quê: no modo `invoice_due_date` a janela do Omie é centrada no vencimento da
fatura, e o cruzamento não olha a data da compra. O modo é copiado do cliente na
criação (ou trocado só nesta conciliação, na gaveta) para que mudar a
configuração do cliente depois não reescreva uma conciliação antiga.

Sem backfill: sessão antiga fica com as duas colunas NULL, e NULL lê como o
processo de sempre (`purchase_date`). Nada muda para ela.

Downgrade REAL: derruba os dois CHECKs e as duas colunas. Sessões criadas no modo
`invoice_due_date` perdem o modo e o vencimento; um reprocessamento delas depois
do downgrade voltaria a cruzar pela data da compra (o comportamento de antes).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). O predicado e o
rótulo são SNAPSHOTS de `db/models/client.py`;
`tests/unit/test_card_posting_date_mode_schema.py` compara as duas fontes.

Revision ID: d881eabdceb7
Revises: 8745bf30d8c8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d881eabdceb7"
down_revision = "8745bf30d8c8"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_sessions"
_LENGTH = 20

# Snapshots de `db/models/client.py` (`card_posting_date_mode_check(nullable=True)`).
_CK_LABEL = "card_posting_date_mode"
_CK = (
    "card_posting_date_mode IS NULL OR "
    "card_posting_date_mode IN ('purchase_date', 'invoice_due_date')"
)
# Snapshot de `CARD_DUE_DATE_COHERENT_CHECK` (`db/models/reconciliation_session.py`).
_COHERENT_CK_LABEL = "card_due_date_coherent"
_COHERENT_CK = (
    "card_posting_date_mode IS DISTINCT FROM 'invoice_due_date' OR invoice_due_date IS NOT NULL"
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("invoice_due_date", sa.Date(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("card_posting_date_mode", sa.String(length=_LENGTH), nullable=True),
    )
    # Rótulo, não o nome final: a NAMING_CONVENTION prefixa `ck_reconciliation_sessions_`.
    op.create_check_constraint(_CK_LABEL, _TABLE, sa.text(_CK))
    op.create_check_constraint(_COHERENT_CK_LABEL, _TABLE, sa.text(_COHERENT_CK))


def downgrade() -> None:
    op.drop_constraint(_COHERENT_CK_LABEL, _TABLE, type_="check")
    op.drop_constraint(_CK_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, "card_posting_date_mode")
    op.drop_column(_TABLE, "invoice_due_date")
