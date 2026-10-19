"""accounting_chart_nature — natureza Devedora/Credora da conta contábil (86e3qhhrr).

O que muda:
    - `client_accounting_accounts.nature` (VARCHAR(20), nullable): `devedora` ou
      `credora`, a "Natureza da Operação" que a planilha de importação do plano de
      contas da Omie exige em toda conta. DERIVADA da classificação.
    - `client_accounting_accounts.nature_overridden` (BOOLEAN NOT NULL, default
      `false`): a natureza foi escolhida à mão e a reimportação não a recalcula.
    - CHECK `ck_client_accounting_accounts_nature`: vocabulário fechado.
    - CHECK `ck_client_accounting_accounts_nature_override`: a natureza escolhida à mão
      existe.

Backfill idempotente das contas existentes, pela MESMA regra da função
`client_accounting_chart.nature.derive_nature` (o grupo é o primeiro dígito
significativo da classificação: 1 devedora, 2 e 3 credora, 4 em diante devedora; sem
grupo, nula). Só toca linha com `nature IS NULL` e sem marca de escolha à mão: rodar
duas vezes não muda nada. O teste de integração prova que SQL e Python dão o mesmo.

Downgrade REAL: derruba os CHECKs e as duas colunas. A natureza escolhida à mão se
perde (a automática se recalcula ao subir de novo).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Rótulos e predicados dos
CHECKs são SNAPSHOTS de `db/models/client_accounting_account.py`;
`tests/unit/test_client_accounting_account_schema.py` compara as duas fontes.

Revision ID: 5d8e2b7c4a91
Revises: 7c3e9a1d5f28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "5d8e2b7c4a91"
down_revision = "7c3e9a1d5f28"
branch_labels = None
depends_on = None

_TABLE = "client_accounting_accounts"
_COLUMN_NATURE = "nature"
_COLUMN_OVERRIDDEN = "nature_overridden"

# Snapshots de `db/models/client_accounting_account.py`.
_NATURE_MAX = 20
_CK_NATURE_LABEL = "nature"
_CK_NATURE = "nature IS NULL OR nature IN ('devedora', 'credora')"
_CK_NATURE_OVERRIDE_LABEL = "nature_override"
_CK_NATURE_OVERRIDE = "NOT nature_overridden OR nature IS NOT NULL"

# A regra de `derive_nature` em SQL: o grupo é o primeiro dígito significativo.
_GROUP = r"substring(classification from '^\s*0*([1-9])')"
_BACKFILL = f"""
UPDATE client_accounting_accounts
   SET nature = CASE
       WHEN {_GROUP} IN ('2', '3') THEN 'credora'
       WHEN {_GROUP} IS NOT NULL THEN 'devedora'
   END
 WHERE nature IS NULL
   AND NOT nature_overridden
   AND {_GROUP} IS NOT NULL
"""


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN_NATURE, sa.String(_NATURE_MAX), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN_OVERRIDDEN, sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    # Rótulos, não os nomes finais: a NAMING_CONVENTION prefixa `ck_<tabela>_`.
    op.create_check_constraint(_CK_NATURE_LABEL, _TABLE, sa.text(_CK_NATURE))
    op.create_check_constraint(_CK_NATURE_OVERRIDE_LABEL, _TABLE, sa.text(_CK_NATURE_OVERRIDE))
    op.execute(sa.text(_BACKFILL))


def downgrade() -> None:
    op.drop_constraint(_CK_NATURE_OVERRIDE_LABEL, _TABLE, type_="check")
    op.drop_constraint(_CK_NATURE_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN_OVERRIDDEN)
    op.drop_column(_TABLE, _COLUMN_NATURE)
