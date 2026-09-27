"""users_password_changed_at — carimbo da redefinição de senha (86e3ewukz).

Uma coluna nula em `users`: `password_changed_at TIMESTAMPTZ NULL`. É o
mecanismo de revogação de sessão: `get_current_user` e o refresh recusam token
com `iat` anterior ao carimbo. NULL = nunca redefinida (as linhas existentes
continuam com as sessões válidas). Sem backfill, sem índice: a coluna é lida
junto com a linha que a autenticação já carrega a cada request.

Downgrade REAL: derruba a coluna. Perde-se o carimbo e as sessões abertas antes
da última redefinição voltam a valer até expirarem — por isso o rollback só é
seguro sem redefinição de emergência em curso.

Revision ID: a7c2e9f31b58
Revises: e6b2c9d47f13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7c2e9f31b58"
down_revision = "e6b2c9d47f13"
branch_labels = None
depends_on = None

_TABLE = "users"
_COLUMN = "password_changed_at"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
