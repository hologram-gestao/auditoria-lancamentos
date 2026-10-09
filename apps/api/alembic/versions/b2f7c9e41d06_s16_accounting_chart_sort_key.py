"""s16_accounting_chart_sort_key — a lista do plano contábil na ordem da CLASSIFICAÇÃO (86e3n70p9).

O que muda:
    - `client_accounting_accounts.sort_key` (VARCHAR(160) `COLLATE "C"`, nullable): a
      chave de ordenação DERIVADA. Com classificação, é a PRÓPRIA classificação como
      texto, sem preenchimento (a ordem do Domínio); sem classificação, o código
      reduzido com os segmentos só de dígitos preenchidos com zeros até 6 (`10` →
      `000010`) e os demais em minúsculas;
    - o índice `ix_client_accounting_accounts_client_id_sort_key` em
      `(client_id, sort_key)`: toda página da listagem é `WHERE client_id … ORDER BY
      sort_key`.

Por quê: a lista saía na ordem do código reduzido como TEXTO (`1, 10, 101, 11`);
para o contador a ordem é a da classificação, com a sintética em cima e as analíticas
dela abaixo. A classificação NÃO é preenchida: sob o mesmo pai o Domínio tem famílias
`00001…` e `001…` distintas, e o preenchimento as intercalava (87 contas fora do lugar
no plano real do Gabriel; decisão do Pedro, 09/10/2026).

**`COLLATE "C"` é parte da regra**: com a collation do sistema (`en_US.utf8` da
glibc), o Postgres ignora a pontuação e ordena `11 < 1.10 < 1.1.10 < 1.1.2`. Em `C`
a comparação é por byte, a mesma do `sorted()` do Python e do Domínio. O índice herda
a collation da coluna e serve o `ORDER BY`.

Backfill, em SQL puro e IDEMPOTENTE (`WHERE sort_key IS NULL`): a MESMA regra de
`app/modules/client_accounting_chart/sort_key.py::chart_sort_key`, repetida aqui
porque a migration não importa `app.*`. `tests/integration/test_migrations.py`
prova que a expressão SQL e a função Python produzem o mesmo valor para a mesma
amostra. A classificação vazia conta como ausente (`NULLIF(classification, '')`),
como o `if classification` do Python; no código, dígito é só `0-9`
(`~ '^[0-9]+$'`) e o `lpad` nunca encurta um segmento mais largo que 6
(`greatest(length, 6)`).

Editada NO LUGAR em 09/10/2026 (regra de texto e collation), antes de qualquer
aplicação fora de banco descartável: a revisão só existia no `develop`, e o deploy
roda a partir da `main`.

Downgrade REAL: derruba o índice e a coluna. A lista volta à ordem por código até a
coluna voltar; o código novo que grava `sort_key` falharia sem ela, então o downgrade
só faz sentido com a revisão anterior da API.

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Largura, collation e
nome do índice são SNAPSHOTS do modelo; `tests/unit/test_accounting_chart_sort_key.py`
compara as duas fontes.

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
_SORT_KEY_COLLATION = "C"
_SORT_SEGMENT_WIDTH = 6
_IX_CLIENT_SORT_KEY = "ix_client_accounting_accounts_client_id_sort_key"


def code_sort_key_sql(code: str) -> str:
    """O código reduzido com os segmentos numéricos preenchidos (a fonte da conta SEM
    classificação), em SQL. Exposta como função para o teste de paridade avaliar a
    MESMA expressão do backfill."""
    return (
        "(SELECT string_agg("
        "  CASE WHEN seg ~ '^[0-9]+$'"
        f"       THEN lpad(seg, greatest(length(seg), {_SORT_SEGMENT_WIDTH}), '0')"
        "       ELSE lower(seg) END,"
        "  '.' ORDER BY ord)"
        f" FROM unnest(string_to_array({code}, '.')) WITH ORDINALITY AS t(seg, ord))"
    )


#: A regra inteira: a classificação como texto quando existe; senão, o código preenchido.
_SORT_KEY_EXPR = f"COALESCE(NULLIF(classification, ''), {code_sort_key_sql('code')})"

_BACKFILL = f"UPDATE {_TABLE} SET {_COLUMN} = {_SORT_KEY_EXPR} WHERE {_COLUMN} IS NULL"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.String(length=_SORT_KEY_MAX, collation=_SORT_KEY_COLLATION),
            nullable=True,
        ),
    )
    op.create_index(_IX_CLIENT_SORT_KEY, _TABLE, ["client_id", _COLUMN])
    op.execute(sa.text(_BACKFILL))


def downgrade() -> None:
    op.drop_index(_IX_CLIENT_SORT_KEY, table_name=_TABLE)
    op.drop_column(_TABLE, _COLUMN)
