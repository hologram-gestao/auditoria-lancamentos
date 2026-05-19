"""s16: blind index para filtro de search em file_entries

Revision ID: b6f1c4d29e57
Revises: 7e3b9d2a5c4f
Create Date: 2026-05-19 14:00:00.000000+00:00

Adiciona `reconciliation_file_entries.description_search_hmac` (TEXT NULL)
para suportar o filtro `search` da Tela de Revisão em SQL puro, sem
descriptografar todas as linhas em memória antes de paginar.

Formato armazenado: " hmac1 hmac2 hmac3 " (cada hmac = HMAC-SHA256 truncado
em 16 chars hex sobre um token normalizado da description). A consulta usa
`LIKE '% <hmac> %'` para cada token do termo buscado, ANDando as condições.

Nullability: sessões criadas antes desta migration não terão a coluna
populada — para o filtro `search` esses registros são naturalmente
excluídos (LIKE contra NULL é NULL, falsy em WHERE). Não há backfill:
descriptografar e reindexar todas as descrições antigas em prod exigiria
janela cuidadosa, e o ganho é baixo (filtro continua disponível para
sessões novas). Decisão registrada com Pedro.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6f1c4d29e57"
down_revision: str | None = "7e3b9d2a5c4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_file_entries",
        sa.Column("description_search_hmac", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reconciliation_file_entries", "description_search_hmac")
