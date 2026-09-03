"""s11: snapshot do código de fornecedor nas divergências Omie

Revision ID: b7d4e91c2a53
Revises: a3f8c21d9b47
Create Date: 2026-09-03 13:00:00.000000+00:00

Task 86e33bmkb (fecha o último gap da aba Divergências): a coluna Fornecedor
ficava "—" para divergências de TÍTULO mesmo com o snapshot de valor/categoria,
porque `ListarContasPagar/Receber` devolve só o `codigo_cliente_fornecedor`
(§5.5) e nada era persistido para resolver o nome depois.

  - `supplier_code` BIGINT **em claro** — é o `codigo_cliente_omie` do
    cadastro, um ID numérico sem significado isolado (mesma classe do
    `category_code`). O NOME (razão social/fantasia) continua nunca
    persistindo: é resolvido em runtime via `ConsultarCliente` + cache TTL
    (§4.5 intacta).

Nullable e sem backfill, pelo mesmo motivo das colunas irmãs: linha antiga não
tem fonte retroativa confiável (título pago sai dos filtros da API).

Só DDL. Reversível: `downgrade` dropa a coluna — perda do snapshot, não
corrupção.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e91c2a53"
down_revision: str | None = "a3f8c21d9b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_omie_entries",
        sa.Column("supplier_code", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_omie_entries", "supplier_code")
