"""s16_accounting_chart_sort_key — a lista do plano contábil na ordem da CLASSIFICAÇÃO (86e3n70p9).

O que muda:
    - `client_accounting_accounts.sort_key` (VARCHAR(160), nullable): a chave de
      ordenação DERIVADA da classificação (ou do código reduzido, sem ela), segmento
      a segmento, com os segmentos numéricos preenchidos com zeros até 6 dígitos
      (`1.1.10` → `000001.000001.000010`) e os demais em minúsculas;
    - o índice `ix_client_accounting_accounts_client_id_sort_key` em
      `(client_id, sort_key)`: toda página da listagem é `WHERE client_id … ORDER BY
      sort_key`.

Por quê: a lista saía na ordem do código reduzido como TEXTO (`1, 10, 101, 11`);
para o contador a ordem é a da classificação, com a sintética em cima e as analíticas
dela abaixo — é assim que se acha o lugar de uma conta nova.

Backfill, em SQL puro e IDEMPOTENTE (`WHERE sort_key IS NULL`): a MESMA regra de
`app/modules/client_accounting_chart/sort_key.py::chart_sort_key`, repetida aqui
porque a migration não importa `app.*`. `tests/integration/test_migrations.py`
prova que a expressão SQL e a função Python produzem o mesmo valor para a mesma
amostra. Dígito é só `0-9` (`~ '^[0-9]+$'`), o `lpad` nunca encurta um segmento
mais largo que 6 (`greatest(length, 6)`), e a fonte é `COALESCE(NULLIF(classification,
''), code)` — a classificação vazia conta como ausente, como o `if classification`
do Python.

Downgrade REAL: derruba o índice e a coluna. A lista volta à ordem por código até a
coluna voltar; o código novo que grava `sort_key` falharia sem ela, então o downgrade
só faz sentido com a revisão anterior da API.

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Largura e nome do índice
são SNAPSHOTS do modelo; `tests/unit/test_accounting_chart_sort_key.py` compara as
duas fontes.

Revision ID: b2f7c9e41d06
Revises: d881eabdceb7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2f7c9e41d06"
down_revision = "d881eabdceb7"
branch_labels = None
depends_on = None

_TABLE = "client_accounting_accounts"
_COLUMN = "sort_key"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_SORT_KEY_MAX = 160
_SORT_SEGMENT_WIDTH = 6
_IX_CLIENT_SORT_KEY = "ix_client_accounting_accounts_client_id_sort_key"


def sort_key_sql(source: str) -> str:
    """A regra de `chart_sort_key` em SQL, sobre a expressão `source` (a fonte já
    escolhida: classificação ou código). Exposta como função para o teste de paridade
    avaliar a MESMA expressão do backfill sobre uma amostra."""
    return (
        "(SELECT string_agg("
        "  CASE WHEN seg ~ '^[0-9]+$'"
        f"       THEN lpad(seg, greatest(length(seg), {_SORT_SEGMENT_WIDTH}), '0')"
        "       ELSE lower(seg) END,"
        "  '.' ORDER BY ord)"
        f" FROM unnest(string_to_array({source}, '.')) WITH ORDINALITY AS t(seg, ord))"
    )


_SOURCE = "COALESCE(NULLIF(classification, ''), code)"

_BACKFILL = (
    f"UPDATE {_TABLE} SET {_COLUMN} = {sort_key_sql(_SOURCE)} WHERE {_COLUMN} IS NULL"
)


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column(_COLUMN, sa.String(length=_SORT_KEY_MAX), nullable=True)
    )
    op.create_index(_IX_CLIENT_SORT_KEY, _TABLE, ["client_id", _COLUMN])
    op.execute(sa.text(_BACKFILL))


def downgrade() -> None:
    op.drop_index(_IX_CLIENT_SORT_KEY, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
