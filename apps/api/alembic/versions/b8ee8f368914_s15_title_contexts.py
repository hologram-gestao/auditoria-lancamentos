"""s15_title_contexts — contexto humano sobre um título da carteira (BACK 15.1).

Cria `title_contexts`: acordo de pagamento, antecipação, nota a cancelar,
cobrança suspensa, perda provável ou outro — o lugar que hoje NÃO existe para
guardar o motivo pelo qual um título vencido não é inadimplência real
(CONTEXT.md, Sprint 15). Verificado em 21/09/2026: a única anotação que existe
hoje é `reconciliation_file_entries.user_note_encrypted`, presa a uma sessão de
conciliação de um mês — morre quando o mês seguinte nasce. O título (Sprint 11,
`client_titles`) atravessa meses, e é nele que o contexto se pendura.

O que muda:
    - tabela `title_contexts` — uma linha por REGISTRO (append-only: sem
      `UPDATE`/`DELETE` no service, e por isso sem `updated_at`), com
      `client_id` (`ondelete=CASCADE`, molde do precedente cifrado — a cifra
      exige o cliente na própria linha para compor o AAD), `title_id`
      (`ondelete=CASCADE` — título só desaparece no encerramento/exclusão do
      cliente, e aí o contexto some junto), `author_id` (`ondelete=RESTRICT` —
      autoria é histórico, mesmo molde de `created_by`), o texto cifrado
      (`text_encrypted`/`text_iv`, 13º par de AAD do sistema) e um CHECK de
      vocabulário fechado (`context_type`, os 6 tipos do PRD);
    - índice `(title_id, created_at)` — sustenta "histórico completo, mais
      recente primeiro" e a verificação de existência do filtro "vencidos sem
      contexto" em `GET /clients/{client_id}/titles`.

**Cifrado, ao contrário de `client_titles`.** O texto é observação de quem
atende — texto livre de terceiro, exatamente o canal por onde nome/CPF/razão
social entra sem ninguém decidir isso (§4.5). O `tipo`, ao contrário, é enum
fechado e fica em claro — não identifica ninguém sozinho.

Sem backfill: nenhuma linha nasce nesta migration — é dado novo, não correção
de dado existente. Tudo é ADITIVO: a API antiga continua servindo sobre este
schema durante a janela entre o job de migração e o `deploy-api`.

Downgrade REAL: derruba o índice e a tabela. Sem pré-check de duplicata —
`context_type` é local ao registro, não há UNIQUE que uma linha antiga possa
violar na volta.

NB: nada de `app.*` importado no topo — importar código de app constrói o
Settings inteiro no import do módulo de migration (ver `d5c81a4e9b27`). As
constantes abaixo são SNAPSHOTS copiados do modelo
(`app/db/models/title_context.py`), e `tests/unit/test_client_titles_schema.py`
(ou um teste de drift próprio) compara as duas fontes — o autogenerate do
Alembic não enxerga CHECK constraint.

Revision ID: b8ee8f368914
Revises: c2d9e7f41ab5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b8ee8f368914"
down_revision = "c2d9e7f41ab5"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
# IV_HEX_LENGTH (`app/db/models/client.py`) — mesmo teto de todo par cifrado.
_IV_HEX_LENGTH = 32

# ⚠️ LABEL, não o nome final: a NAMING_CONVENTION do `Base` prefixa
# `ck_title_contexts_`.
_CK_TYPE_LABEL = "context_type"
_CK_TYPE = (
    "context_type IN ('acordo_de_pagamento', 'pagamento_antecipado', "
    "'nota_a_cancelar', 'cobranca_suspensa', 'perda_provavel', 'outro')"
)

_IX_TITLE_CONTEXT_TITLE_CREATED = "ix_title_contexts_title_id_created_at"

_FK_CLIENT = "fk_title_contexts_client_id_clients"
_FK_TITLE = "fk_title_contexts_title_id_client_titles"
_FK_AUTHOR = "fk_title_contexts_author_id_users"


def upgrade() -> None:
    op.create_table(
        "title_contexts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("title_id", sa.UUID(), nullable=False),
        sa.Column("context_type", sa.String(length=30), nullable=False),
        # Texto livre — AES-256-GCM (13º par de AAD do sistema). SEMPRE
        # presente: é o payload da entrada, não uma nota opcional.
        sa.Column("text_encrypted", sa.Text(), nullable=False),
        sa.Column("text_iv", sa.String(length=_IV_HEX_LENGTH), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=_FK_CLIENT, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["title_id"], ["client_titles.id"], name=_FK_TITLE, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=_FK_AUTHOR, ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_title_contexts"),
        # Vocabulário fechado dos seis tipos — mora no BANCO, não só no
        # servidor. ⚠️ LABEL, não o nome final (ver acima).
        sa.CheckConstraint(_CK_TYPE, name=_CK_TYPE_LABEL),
    )

    # `client_id` sozinho (defense-in-depth, §3.15 — toda tabela endereçada por
    # cliente carrega o índice que sustenta `AND client_id = <tenant>`).
    op.create_index(op.f("ix_title_contexts_client_id"), "title_contexts", ["client_id"])
    # Histórico "mais recente primeiro" + verificação de existência do filtro
    # "vencidos sem contexto". Declarado à mão: o autogenerate não gera índice
    # composto que o modelo declara em `__table_args__`.
    op.create_index(_IX_TITLE_CONTEXT_TITLE_CREATED, "title_contexts", ["title_id", "created_at"])


def downgrade() -> None:
    op.drop_index(_IX_TITLE_CONTEXT_TITLE_CREATED, table_name="title_contexts")
    op.drop_index(op.f("ix_title_contexts_client_id"), table_name="title_contexts")
    op.drop_table("title_contexts")
