"""s11_client_titles — a CARTEIRA de títulos em aberto do cliente (BACK 11.1).

Cria a base de dados que a Sprint 11 existe para ter: títulos a pagar e a
receber **em aberto**, de **todas** as contas correntes e **sem recorte de
competência**, com linha própria que não morre junto com uma sessão de
conciliação. Hoje o único registro de título é `reconciliation_omie_entries`,
preso a `session_id` com `ondelete=CASCADE` e limitado a uma conta e um mês —
por isso a estrutura atual não serve, e por isso nada dela é tocado aqui.

O que muda:
    - tabela `client_titles` — uma linha por `(cliente, identificador do título
      na origem)`, com `UNIQUE (client_id, external_id)` (é ela que torna o
      ciclo de sincronização idempotente), índice `(client_id, due_date)` para o
      aging e a ordenação, FK para `clients` com `ondelete=CASCADE` e dois
      CHECKs de vocabulário fechado (`title_type`, `status`);
    - `clients.titles_synced_at` e `clients.titles_sync_failed_at`, nuláveis — o
      estado da sincronização, no mesmo precedente de
      `chart_of_accounts_synced_at` (S10).

**Nenhuma coluna de nome.** Nem do devedor, nem do fornecedor, nem da categoria,
nem `observacao` (texto livre de terceiro, que é por onde nome de pessoa entra
no banco sem ninguém decidir isso). §4.5 do primer: nome é resolvido em runtime
pelo cache que já existe. Por consequência **nada aqui é cifrado** — não há PII
na tabela.

**`BigInteger` nos códigos numéricos da origem é obrigatório, não zelo.** A
captura real (`tests/fixtures/omie/listar_contas_pagar.response.json`) traz
`codigo_cliente_fornecedor = 2624256082` e `id_conta_corrente = 2617722760`,
ambos acima do teto de `INTEGER` (2.147.483.647): um `Integer` estouraria na
primeira sincronização real.

Sem backfill: nenhuma linha nasce nesta migration, e as duas colunas novas de
`clients` nascem NULL (= "nunca sincronizou"), que é o estado correto de todo
cliente existente — a carteira parte de 0% por definição (baseline do Outcome).
Tudo é ADITIVO, então a API ANTIGA continua servindo sobre este schema durante a
janela entre o job de migração e o `deploy-api`.

Downgrade REAL e sem pré-check: derrubar a tabela e as duas colunas devolve
exatamente a forma anterior. Não há guarda a fazer — a carteira é **derivada**
da origem (some daqui, ressincroniza de lá), ao contrário da credencial de
`a7f2c1d93e84`, que não se reconstrói. O que se perde é o contexto que a Sprint
15 ainda vai pendurar nestas linhas; enquanto ela não existe, o custo do
rollback é uma ressincronização.

NB: nada de `app.*` importado no topo — importar código de app constrói o
Settings inteiro no import do módulo de migration (ver `d5c81a4e9b27`). As
constantes abaixo são SNAPSHOTS copiados do modelo, e
`tests/unit/test_client_titles_schema.py` compara as duas fontes (o autogenerate
do Alembic não enxerga CHECK constraint — nem índice composto declarado à mão).

Revision ID: c2d9e7f41ab5
Revises: f1b7a52c8e60
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c2d9e7f41ab5"
down_revision = "f1b7a52c8e60"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_EXTERNAL_ID_MAX = 60
_CODE_MAX = 50
_DOCUMENT_MAX = 60

_UQ_CLIENT_TITLE = "uq_client_titles_client_id_external_id"
_IX_CLIENT_TITLE_DUE = "ix_client_titles_client_id_due_date"

# ⚠️ LABEL, não o nome final: a NAMING_CONVENTION do `Base` prefixa
# `ck_client_titles_` (ver `f1b7a52c8e60`).
_CK_TYPE_LABEL = "title_type"
_CK_TYPE = "title_type IN ('a_pagar', 'a_receber')"
_CK_STATUS_LABEL = "status"
_CK_STATUS = "status IN ('em_aberto', 'liquidado', 'ausente_na_origem')"
_DEFAULT_STATUS = "em_aberto"

_FK_CLIENT_TITLE_CLIENT = "fk_client_titles_client_id_clients"

#: As duas colunas de estado da sincronização em `clients`.
_CLIENT_SYNC_COLUMNS = (
    "titles_synced_at",
    "titles_sync_failed_at",
)


def upgrade() -> None:
    op.create_table(
        "client_titles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        # Identificador do título NA ORIGEM, como texto: identificador de
        # terceiro não é número nosso (mesma decisão de `ProviderAccount`).
        sa.Column("external_id", sa.String(length=_EXTERNAL_ID_MAX), nullable=False),
        sa.Column("title_type", sa.String(length=20), nullable=False),
        # Base do aging: DATE, não TIMESTAMP — vencimento é dia, e um fuso a
        # mais moveria títulos de balde.
        sa.Column("due_date", sa.Date(), nullable=False),
        # Dinheiro é Numeric(14,2) em todo o sistema (§3.4).
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=_DEFAULT_STATUS,
            nullable=False,
        ),
        # Só CÓDIGO — nome de categoria e razão social são runtime (§4.5).
        sa.Column("category_code", sa.String(length=_CODE_MAX), nullable=True),
        # BigInteger: a captura real traz 2624256082, acima do teto de INTEGER.
        sa.Column("supplier_code", sa.BigInteger(), nullable=True),
        sa.Column("omie_conta_id", sa.BigInteger(), nullable=True),
        sa.Column("document_number", sa.String(length=_DOCUMENT_MAX), nullable=True),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=_FK_CLIENT_TITLE_CLIENT,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_titles"),
        # Identidade da linha dentro do tenant — e o alvo do `ON CONFLICT` do
        # ciclo de sincronização. Única por CLIENTE, não por (cliente, tipo):
        # se a origem um dia repetir o identificador entre os dois cadastros,
        # queremos o erro barulhento, não duas linhas.
        sa.UniqueConstraint("client_id", "external_id", name=_UQ_CLIENT_TITLE),
        # O vocabulário de tipo e de situação é fechado — e mora no BANCO.
        # ⚠️ LABEL, não o nome final: a NAMING_CONVENTION prefixa `ck_client_titles_`.
        sa.CheckConstraint(_CK_TYPE, name=_CK_TYPE_LABEL),
        sa.CheckConstraint(_CK_STATUS, name=_CK_STATUS_LABEL),
    )

    # O índice do aging (11.4) e da ordenação por vencimento (11.5). Declarado à
    # mão: o autogenerate não gera índice composto que o modelo declara em
    # `__table_args__`. Serve também toda query por `client_id` sozinho, pelo
    # prefixo — por isso não há um segundo índice só de `client_id`.
    op.create_index(_IX_CLIENT_TITLE_DUE, "client_titles", ["client_id", "due_date"])

    # Estado da sincronização, no precedente de `chart_of_accounts_synced_at`:
    # NULL = nunca sincronizou. A de falha NUNCA sobrescreve a de sucesso — é o
    # que sustenta "falha preserva a última carteira íntegra" (R1).
    for column in _CLIENT_SYNC_COLUMNS:
        op.add_column(
            "clients",
            sa.Column(column, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for column in reversed(_CLIENT_SYNC_COLUMNS):
        op.drop_column("clients", column)

    op.drop_index(_IX_CLIENT_TITLE_DUE, table_name="client_titles")
    op.drop_table("client_titles")
