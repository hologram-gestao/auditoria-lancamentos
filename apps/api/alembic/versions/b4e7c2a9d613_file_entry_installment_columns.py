"""file_entry_installment_columns — a parcela `N/M` da compra no cartão, estruturada.

O que muda (86e3qhhtb), em `reconciliation_file_entries`:
    - `installment_number` e `installment_total` (SMALLINT, nuláveis): a parcela
      `N` de `M` que a extração leu (regra 9 do prompt);
    - CHECK `ck_reconciliation_file_entries_installment_pair`: as duas vivem e
      morrem juntas, e o par é coerente (`1 <= N <= M`, `2 <= M <= 99`).

Por quê: provisionar as parcelas FUTURAS no Contas a Pagar, e a fatura seguinte
encontrar a parcela já provisionada, dependem do número e do total da parcela.
Até aqui eles só existiam no texto da descrição, que é cifrado.

Sem backfill, de propósito: a linha antiga tem a descrição CIFRADA, e o par é lido
dela em memória na hora de ler (`reconciliations/installments.py`), nunca num
backfill cifrado em massa. Linha existente nasce com as duas nulas e já cumpre o
CHECK.

Downgrade: derruba o CHECK e as colunas. O par se perde, mas continua escrito na
descrição, e a leitura volta a ser a da descrição; a tabela das parcelas futuras
(migration seguinte) aborta o próprio downgrade antes deste, se houver parcela.

NB: nada de `app.*` importado no topo. Rótulo e predicado são SNAPSHOTS de
`db/models/reconciliation_file_entry.py`; `tests/unit/test_installment_schema.py`
compara as duas fontes.

Revision ID: b4e7c2a9d613
Revises: 5d8e2b7c4a91
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b4e7c2a9d613"
down_revision = "5d8e2b7c4a91"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_file_entries"

# Snapshots de `db/models/reconciliation_file_entry.py`.
_PAIR_CK_LABEL = "installment_pair"
_PAIR_CK = (
    "(installment_number IS NULL AND installment_total IS NULL) "
    "OR (installment_number IS NOT NULL AND installment_total IS NOT NULL "
    "AND installment_number >= 1 AND installment_total >= 2 "
    "AND installment_total <= 99 AND installment_number <= installment_total)"
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("installment_number", sa.SmallInteger(), nullable=True))
    op.add_column(_TABLE, sa.Column("installment_total", sa.SmallInteger(), nullable=True))
    # Rótulo, não nome final: a NAMING_CONVENTION prefixa `ck_<tabela>_`.
    op.create_check_constraint(_PAIR_CK_LABEL, _TABLE, sa.text(_PAIR_CK))


def downgrade() -> None:
    op.drop_constraint(_PAIR_CK_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, "installment_total")
    op.drop_column(_TABLE, "installment_number")
