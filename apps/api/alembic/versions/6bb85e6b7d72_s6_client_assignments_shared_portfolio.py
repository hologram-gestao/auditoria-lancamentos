"""s6_client_assignments_shared_portfolio — carteira compartilhada (86e390kz8).

Um cliente passa a poder ter N gerentes com acesso e UM responsável. Antes a
tabela era 1:1 pelo índice único `ix_client_assignments_client_id`, e
"reatribuir" sobrescrevia o `user_id` — o gerente anterior perdia o acesso em
silêncio (incidente da Bruna no cliente Hologram, 14/09/2026).

O que muda em `client_assignments`:
    - coluna `is_primary` (boolean, NOT NULL, default false) — marca o responsável;
    - **backfill**: toda linha existente vira responsável. É seguro porque, no
      momento do UPDATE, o índice único antigo ainda garante uma linha por
      cliente — não há como o backfill produzir dois responsáveis. Convergente
      (`WHERE NOT is_primary`): rodar de novo não muda nada;
    - o índice único em `client_id` vira índice comum (mesmo nome, sem unique);
    - UNIQUE `(client_id, user_id)` — a mesma pessoa não entra duas vezes;
    - índice único PARCIAL `uq_client_assignments_primary` (`client_id` WHERE
      `is_primary`) — um responsável por cliente. O autogenerate NÃO enxerga o
      predicado: escrito à mão, e `_PRIMARY_PREDICATE` é comparado com o modelo
      por teste unitário.

Downgrade: recriar o UNIQUE(client_id) só cabe se cada cliente tiver UMA linha.
Se houver colaboradores (linhas não-responsáveis), a migration ABORTA com a
consulta que mostra quem seria apagado — decidir quem perde acesso é decisão
de dado, não de migration. Sem colaboradores, desce limpo.

Revision ID: 6bb85e6b7d72
Revises: c9e4a7b2d5f8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "6bb85e6b7d72"
down_revision = "c9e4a7b2d5f8"
branch_labels = None
depends_on = None

_TABLE = "client_assignments"
_IX_CLIENT = "ix_client_assignments_client_id"
_UQ_CLIENT_USER = "uq_client_assignments_client_user"
_UQ_PRIMARY = "uq_client_assignments_primary"

# Snapshot congelado de `primary_assignment_index_predicate()` (modelo).
_PRIMARY_PREDICATE = "is_primary"

# Guarda do downgrade: sem isto, o `CREATE UNIQUE INDEX (client_id)` estouraria
# com "could not create unique index" e uma mensagem que não diz o que fazer.
_ABORT_IF_COLLABORATORS = """
DO $$
DECLARE
    extra_count integer;
BEGIN
    SELECT count(*) INTO extra_count
    FROM client_assignments
    WHERE NOT is_primary;

    IF extra_count > 0 THEN
        RAISE EXCEPTION
            'Downgrade bloqueado: % linha(s) de client_assignments sao de '
            'colaboradores (is_primary = false). A forma antiga da tabela so '
            'cabe UM gerente por cliente. Remova os acessos extras antes '
            '(SELECT client_id, user_id FROM client_assignments WHERE NOT '
            'is_primary) — tirar acesso de alguem e decisao de dado.',
            extra_count;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Backfill ANTES de soltar o UNIQUE(client_id): com ele ainda de pé, cada
    # cliente tem no máximo uma linha, logo no máximo um responsável.
    op.execute(sa.text("UPDATE client_assignments SET is_primary = true WHERE NOT is_primary"))

    op.drop_index(_IX_CLIENT, table_name=_TABLE)
    op.create_index(_IX_CLIENT, _TABLE, ["client_id"], unique=False)
    op.create_unique_constraint(_UQ_CLIENT_USER, _TABLE, ["client_id", "user_id"])
    op.create_index(
        _UQ_PRIMARY,
        _TABLE,
        ["client_id"],
        unique=True,
        postgresql_where=sa.text(_PRIMARY_PREDICATE),
    )


def downgrade() -> None:
    op.execute(sa.text(_ABORT_IF_COLLABORATORS))
    op.drop_index(_UQ_PRIMARY, table_name=_TABLE)
    op.drop_constraint(_UQ_CLIENT_USER, _TABLE, type_="unique")
    op.drop_index(_IX_CLIENT, table_name=_TABLE)
    op.create_index(_IX_CLIENT, _TABLE, ["client_id"], unique=True)
    op.drop_column(_TABLE, "is_primary")
