"""client_payables_config — configuração do lançamento como Contas a Pagar.

O que muda (86e3n70q0, bloco B):
    - `clients.omie_default_supplier_code` (BIGINT, nulável): o
      `codigo_cliente_omie` do fornecedor padrão do título quando a linha não
      traz CNPJ. CHECK `ck_clients_omie_default_supplier_code_positive`.
    - `clients.omie_department_required` (BOOLEAN, NOT NULL, server_default
      `false`): o título só sai com rateio por departamento.

Por quê: decisões do bloco 0 (11/10/2026). A PCTAX usa departamento em TODO
gasto, então o rateio é obrigatório para ela, e "fornecedor sem CNPJ" cai num
fornecedor padrão configurado por cliente (senão a linha bloqueia). Os dois são
CÓDIGO e FLAG: nenhum nome persiste (§4.5).

Sem backfill: o `server_default` deixa todo cliente existente com rateio
opcional e sem fornecedor padrão, que é exatamente o comportamento de antes (o
lançamento como provisão nem existia).

Downgrade REAL: derruba o CHECK e as duas colunas. O cliente perde a
configuração; nenhum lançamento já feito no Omie depende dela.

NB: nada de `app.*` importado no topo. Rótulo e predicado do CHECK são SNAPSHOTS
de `db/models/client.py`; `tests/unit/test_client_payables_config_schema.py`
compara as duas fontes.

Revision ID: c4e8a1f7b3d2
Revises: b2f7c9e41d06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e8a1f7b3d2"
down_revision = "b2f7c9e41d06"
branch_labels = None
depends_on = None

_TABLE = "clients"

# Snapshots de `db/models/client.py`.
_CK_LABEL = "omie_default_supplier_code_positive"
_CK = "omie_default_supplier_code IS NULL OR omie_default_supplier_code > 0"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("omie_default_supplier_code", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "omie_department_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Rótulo, não o nome final: a NAMING_CONVENTION prefixa `ck_clients_`.
    op.create_check_constraint(_CK_LABEL, _TABLE, sa.text(_CK))


def downgrade() -> None:
    op.drop_constraint(_CK_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, "omie_department_required")
    op.drop_column(_TABLE, "omie_default_supplier_code")
