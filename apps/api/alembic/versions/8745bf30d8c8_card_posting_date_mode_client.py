"""card_posting_date_mode_client — em que data o cliente lança as compras do cartão.

O que muda (86e3n70p0):
    - `clients.card_posting_date_mode` (VARCHAR(20), NOT NULL, server_default
      `'purchase_date'`), com o CHECK `ck_clients_card_posting_date_mode`.

Por quê: a fatura de cartão da PCTEX (cliente da Prospecta) conciliou 0 de 5
compras. Na Prospecta, as compras do cartão entram no Omie em LOTE na data de
VENCIMENTO da fatura; na Hologram, na data da COMPRA. O cruzamento só conhecia o
segundo processo. O processo é declarado por cliente (decisão do Pedro,
08/10/2026), nunca inferido dos dados.

Sem backfill explícito: o `server_default` preenche toda linha existente com
`purchase_date`, que é exatamente o comportamento de antes. Nenhum cliente muda
de processo na migration.

Downgrade REAL: derruba o CHECK e a coluna. Clientes marcados como
`invoice_due_date` perdem a marcação e voltam ao processo de antes (o cruzamento
pela data da compra); as conciliações já criadas guardam o modo delas na sessão
(migration seguinte).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). O predicado e o
rótulo são SNAPSHOTS de `db/models/client.py`;
`tests/unit/test_card_posting_date_mode_schema.py` compara as duas fontes.

Revision ID: 8745bf30d8c8
Revises: 490bffa3f6e2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8745bf30d8c8"
down_revision = "490bffa3f6e2"
branch_labels = None
depends_on = None

_TABLE = "clients"
_COLUMN = "card_posting_date_mode"
_LENGTH = 20
_DEFAULT = "purchase_date"

# Snapshots de `db/models/client.py` (`card_posting_date_mode_check()` e o rótulo).
_CK_LABEL = "card_posting_date_mode"
_CK = "card_posting_date_mode IN ('purchase_date', 'invoice_due_date')"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=_LENGTH),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT}'"),
        ),
    )
    # Rótulo, não o nome final: a NAMING_CONVENTION prefixa `ck_clients_`.
    op.create_check_constraint(_CK_LABEL, _TABLE, sa.text(_CK))


def downgrade() -> None:
    # Também o rótulo: a convenção vale no drop (precedente `3e8f1a6c9d24`).
    op.drop_constraint(_CK_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
