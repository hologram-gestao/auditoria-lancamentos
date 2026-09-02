"""s11: snapshot de valor e código de categoria nas divergências Omie

Revision ID: a3f8c21d9b47
Revises: d7c2b9f14a86
Create Date: 2026-09-02 20:00:00.000000+00:00

Task 86e33bmkb (follow-up da correção do 8020): linhas de DIVERGÊNCIA vindas de
título (`ListarContasPagar/Receber`, status Atrasado/Previsto) não existem no
`ListarExtrato` — o enriquecimento em runtime nunca as resolve e a UI mostra
"—" em Valor/Categoria para sempre. O job JÁ tem esses dados em mãos no
processamento; passam a ser persistidos aqui:

  - `amount` NUMERIC(14,2) **em claro** — permitido explicitamente pelo
    CLAUDE.md §4.3 ("valores monetários em claro — números sem identificação").
    Valor COM SINAL (pagar/débito negativo), mesma escala de
    `reconciliation_file_entries.amount`.
  - `category_code` VARCHAR(30) **em claro** — apenas o CÓDIGO contábil
    (ex.: "2.04.78"), nunca a descrição. A §4.5 continua respeitada: o NOME
    da categoria segue sendo resolvido do Omie em tempo real (cache TTL de
    `ListarCategorias`); o que persiste é um código sem significado isolado.

Nullable de propósito: linhas criadas antes desta migration ficam NULL e a
tela cai no comportamento anterior (cache/extrato; título antigo permanece
"—"). Sem backfill — título não tem fonte retroativa fora do reprocessamento.

Só DDL: sem import de código de app, sem crypto, sem Settings.

Reversível: `downgrade` dropa as duas colunas — perda do snapshot, não
corrupção; o enriquecimento via extrato continua funcionando como antes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c21d9b47"
down_revision: str | None = "d7c2b9f14a86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATEGORY_CODE_MAX_LENGTH = 30


def upgrade() -> None:
    op.add_column(
        "reconciliation_omie_entries",
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "reconciliation_omie_entries",
        sa.Column(
            "category_code",
            sa.String(length=_CATEGORY_CODE_MAX_LENGTH),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_omie_entries", "category_code")
    op.drop_column("reconciliation_omie_entries", "amount")
