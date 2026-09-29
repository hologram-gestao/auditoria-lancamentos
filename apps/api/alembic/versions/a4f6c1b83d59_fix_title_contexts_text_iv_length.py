"""fix_title_contexts_text_iv_length — `title_contexts.text_iv` de VARCHAR(32) para VARCHAR(24).

O que muda:
    - `title_contexts.text_iv`: `String(32)` → `String(24)`, alinhando a coluna ao
      modelo (`db/models/title_context.py`, que usa `IV_HEX_LENGTH`).

Por quê: o IV é `os.urandom(12)` gravado em hex (`core/crypto.py`), então TODO valor
tem exatamente 24 caracteres, e as outras 16 colunas `_iv` do sistema declaram
`IV_HEX_LENGTH = 24`. A migration da S15 (`b8ee8f368914`) escreveu um literal 32 em vez
de usar a constante. Não há bug de produto — 24 cabe em 32 e nada trunca —, mas o
`alembic check` acusa drift, e drift acusado toda hora é drift que ninguém lê: o
próximo `--autogenerate` proporia este ALTER sozinho, no meio de outra entrega.

A migration `b8ee8f368914` NÃO é reescrita: migration aplicada é histórico. Esta é o
registro da correção.

Encolher uma coluna faz o Postgres varrer a tabela e ABORTAR se algum valor passar de
24 — aqui não passa, por construção do `hex()` de 12 bytes. Downgrade REAL: volta para
32, que é sempre seguro (alargar não valida nada).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). As constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_iv_column_length.py` compara as fontes.

Revision ID: a4f6c1b83d59
Revises: c7e2a9d4b816
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a4f6c1b83d59"
down_revision = "c7e2a9d4b816"
branch_labels = None
depends_on = None

_TABLE = "title_contexts"
_COLUMN = "text_iv"
# Snapshot de `db/models/client.py::IV_HEX_LENGTH` (12 bytes em hex).
_IV_HEX_LENGTH = 24
# O que a `b8ee8f368914` gravou por engano.
_WRONG_LENGTH = 32


def upgrade() -> None:
    op.alter_column(
        _TABLE,
        _COLUMN,
        existing_type=sa.String(length=_WRONG_LENGTH),
        type_=sa.String(length=_IV_HEX_LENGTH),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        _TABLE,
        _COLUMN,
        existing_type=sa.String(length=_IV_HEX_LENGTH),
        type_=sa.String(length=_WRONG_LENGTH),
        existing_nullable=False,
    )
