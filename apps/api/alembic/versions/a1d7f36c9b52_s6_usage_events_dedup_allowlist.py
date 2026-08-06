"""sprint6: dedup de usage_events vira allow-list por evento

Revision ID: a1d7f36c9b52
Revises: e9a4b71c3d68
Create Date: 2026-08-03 12:00:00.000000+00:00

Sprint 6 (BACK 06.1) — ADR-010:

  O índice `uq_usage_events_event_session` era UNIQUE PARCIAL `(event,
  session_id) WHERE session_id IS NOT NULL`: dedup para TODO evento que tenha
  sessão. Isso estava certo para os 4 eventos da Sprint 4 (grão "1 por sessão"),
  mas os eventos da Sprint 6 `qualificacao_emitida` e `flag_revisado` ocorrem
  **N vezes por sessão** — com o índice antigo, N-1 emissões desapareceriam em
  silêncio (`ON CONFLICT DO NOTHING`) e a razão do outcome sairia errada.

  Esta migration troca o predicado por uma **allow-list explícita** dos eventos
  que aceitam dedup. Evento novo passa a nascer SEM dedup: o pior caso vira
  "linha a mais" (visível na leitura) em vez de "linha que sumiu" (invisível).

  A expressão precisa bater byte-a-byte com
  `app.db.models.usage_event.deduped_session_index_predicate()` e com o
  `index_where` do `ON CONFLICT` do repository — senão o Postgres não infere o
  índice como árbitro e o INSERT morre com `42P10`. A string é repetida aqui de
  propósito (migration é snapshot congelado; importar código de app no topo do
  arquivo é o learning "job de migration sem as secrets do serviço não sobe").
  O teste `test_indice_parcial_bate_com_o_modelo` trava o drift.

  Reversível. O `downgrade` ABORTA com mensagem acionável se já existirem
  linhas que só o predicado novo permite (dois `qualificacao_emitida` da mesma
  sessão, por exemplo): recriar o índice antigo violaria a UNIQUE, e escolher
  qual linha da métrica morre é decisão de dado, não de migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d7f36c9b52"
down_revision: str | None = "e9a4b71c3d68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_usage_events_event_session"

# Snapshot congelado de `deduped_session_index_predicate()` (ordem alfabética).
_NEW_PREDICATE = (
    "session_id IS NOT NULL AND event IN ("
    "'autor_navegou_fora', 'conciliacao_concluida', "
    "'conciliacao_criada', 'notificacao_entregue')"
)
_OLD_PREDICATE = "session_id IS NOT NULL"

# Guarda do downgrade: sem isto, o `CREATE UNIQUE INDEX` estouraria com
# "could not create unique index" e uma mensagem que não diz o que fazer.
_ABORT_IF_DUPLICATES = """
DO $$
DECLARE
    dup_count integer;
BEGIN
    SELECT count(*) INTO dup_count
    FROM (
        SELECT event, session_id
        FROM usage_events
        WHERE session_id IS NOT NULL
        GROUP BY event, session_id
        HAVING count(*) > 1
    ) d;

    IF dup_count > 0 THEN
        RAISE EXCEPTION
            'Downgrade bloqueado: % par(es) (event, session_id) com mais de uma '
            'linha em usage_events. O indice antigo e UNIQUE sobre TODO evento '
            'com sessao e nao cabe esses dados. Consolide/remova as linhas '
            'excedentes antes (SELECT event, session_id, count(*) FROM '
            'usage_events WHERE session_id IS NOT NULL GROUP BY 1,2 HAVING '
            'count(*) > 1) — apagar linha de metrica e decisao de dado.',
            dup_count;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.drop_index(_INDEX, table_name="usage_events")
    op.create_index(
        _INDEX,
        "usage_events",
        ["event", "session_id"],
        unique=True,
        postgresql_where=sa.text(_NEW_PREDICATE),
    )


def downgrade() -> None:
    op.execute(sa.text(_ABORT_IF_DUPLICATES))
    op.drop_index(_INDEX, table_name="usage_events")
    op.create_index(
        _INDEX,
        "usage_events",
        ["event", "session_id"],
        unique=True,
        postgresql_where=sa.text(_OLD_PREDICATE),
    )
