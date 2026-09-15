"""s6_client_assignments_shared_portfolio — carteira compartilhada (86e390kz8).

Um cliente passa a poder ter N gerentes com acesso e UM responsável. Antes a
tabela era 1:1 pelo índice único `ix_client_assignments_client_id`, e
"reatribuir" sobrescrevia o `user_id` — o gerente anterior perdia o acesso em
silêncio (incidente da Bruna no cliente Hologram, 14/09/2026).

O que muda em `client_assignments`:
    - coluna `is_primary` (boolean, NOT NULL, **DEFAULT true no banco**) — marca
      o responsável. O default TRUE é o backfill: a coluna nasce como "fast
      default" do Postgres (catálogo, sem reescrever linha), e toda linha
      existente — uma por cliente, garantido pelo índice único antigo que ainda
      está de pé — lê `true`. O mesmo default cobre a janela de deploy: a API
      ANTIGA (que roda sobre este schema até o `deploy-api`) insere sem o campo
      e a linha nasce responsável, não órfã. O ORM grava o campo explicitamente
      (default `False` no modelo) — o default do banco só fala por quem não fala;
    - o índice único em `client_id` SAI e não volta como índice comum: a UNIQUE
      `(client_id, user_id)` serve toda busca por `client_id` pelo prefixo;
    - UNIQUE `(client_id, user_id)` — a mesma pessoa não entra duas vezes;
    - índice único PARCIAL `uq_client_assignments_primary` (`client_id` WHERE
      `is_primary`) — um responsável por cliente. O autogenerate NÃO enxerga o
      predicado: escrito à mão, e `_PRIMARY_PREDICATE` é comparado com o modelo
      por teste unitário.

Semântica de `assigned_by`/`assigned_at` em linha pré-migration: o código antigo
sobrescrevia os dois a cada reatribuição, então em linha antiga eles dizem quem
fez a ÚLTIMA reatribuição e quando — não quem concedeu o acesso original. Sem
backfill possível (a informação original não existe); linhas novas seguem a
semântica nova ("acesso concedido por/em").

Downgrade: recriar o UNIQUE(client_id) só cabe se cada cliente tiver UMA linha.
Se algum cliente tiver mais de uma (responsável + colaboradores), a migration
ABORTA com a consulta que mostra quem seria apagado — decidir quem perde acesso
é decisão de dado, não de migration. Um cliente com uma linha só desce limpo,
seja ela responsável ou não.

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
# O critério é o que o índice antigo exige — UMA linha por cliente — e não
# "existe colaborador": um cliente cuja única linha é um colaborador cabe.
_ABORT_IF_SHARED = """
DO $$
DECLARE
    shared_count integer;
BEGIN
    SELECT count(*) INTO shared_count
    FROM (
        SELECT client_id
        FROM client_assignments
        GROUP BY client_id
        HAVING count(*) > 1
    ) d;

    IF shared_count > 0 THEN
        RAISE EXCEPTION
            'Downgrade bloqueado: % cliente(s) com mais de um gerente em '
            'client_assignments. A forma antiga da tabela so cabe UM gerente '
            'por cliente. Remova os acessos extras antes (SELECT client_id, '
            'user_id, is_primary FROM client_assignments WHERE client_id IN '
            '(SELECT client_id FROM client_assignments GROUP BY client_id '
            'HAVING count(*) > 1)) — tirar acesso de alguem e decisao de dado.',
            shared_count;
    END IF;
END $$;
"""


def upgrade() -> None:
    # DEFAULT true = backfill por catálogo: cada linha existente (uma por
    # cliente, pelo índice único que ainda está de pé) passa a ler `true`.
    op.add_column(
        _TABLE,
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.drop_index(_IX_CLIENT, table_name=_TABLE)
    op.create_unique_constraint(_UQ_CLIENT_USER, _TABLE, ["client_id", "user_id"])
    op.create_index(
        _UQ_PRIMARY,
        _TABLE,
        ["client_id"],
        unique=True,
        postgresql_where=sa.text(_PRIMARY_PREDICATE),
    )


def downgrade() -> None:
    op.execute(sa.text(_ABORT_IF_SHARED))
    op.drop_index(_UQ_PRIMARY, table_name=_TABLE)
    op.drop_constraint(_UQ_CLIENT_USER, _TABLE, type_="unique")
    op.create_index(_IX_CLIENT, _TABLE, ["client_id"], unique=True)
    op.drop_column(_TABLE, "is_primary")
