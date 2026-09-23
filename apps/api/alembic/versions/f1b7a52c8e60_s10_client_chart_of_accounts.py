"""s10_client_chart_of_accounts — plano de contas do cliente (BACK 10.1).

Traz para dentro do produto a classificação que a origem **já tem**: códigos,
hierarquia, situação, flags e — o que motiva a sprint — o vínculo de cada
categoria com a **conta de demonstrativo** (`dadosDRE.codigoDRE`), que hoje nem
é declarado no DTO. Na amostra real, 37 de 50 categorias chegam com esse vínculo
preenchido.

O que muda:
    - tabela `client_chart_of_accounts` — uma linha por `(cliente, código de
      categoria)`, com `UNIQUE (client_id, category_code)` (é ela que torna o
      upsert da 10.2 idempotente), FK para `clients` com `ondelete=CASCADE` e
      CHECK de `status` (`ativa|inativa|ausente_na_origem`);
    - `clients.chart_of_accounts_synced_at` e
      `clients.chart_of_accounts_sync_failed_at`, nuláveis — o estado da
      sincronização, no mesmo precedente de `omie_accounts_synced_at`.

**Nenhuma coluna de nome ou descrição.** Nem da categoria, nem da conta de
demonstrativo (`descricaoDRE`), nem o rótulo da conta contábil
(`tag_conta_contabil`): §4.5 do primer mantém nome de categoria e de conta fora
do disco em claro, e a tela resolve tudo isso em runtime pelo cache de 6h que já
existe. Por consequência **nada aqui é cifrado** — não há PII na tabela.

Sem backfill: nenhuma linha nasce nesta migration, e as duas colunas novas de
`clients` nascem NULL (= "nunca sincronizou"), que é o estado correto de todo
cliente existente. Tudo é ADITIVO, então a API ANTIGA continua servindo sobre
este schema durante a janela entre o job de migração e o `deploy-api`.

Downgrade REAL e sem pré-check: derrubar a tabela e as duas colunas devolve
exatamente a forma anterior. Não há guarda a fazer — o plano de contas é
**derivado** da origem (some daqui, ressincroniza de lá), ao contrário da
credencial de `a7f2c1d93e84`, que não se reconstrói. O que se perde no downgrade
é o carimbo de "já sincronizou", e o efeito é uma ressincronização a mais.

NB: nada de `app.*` importado no topo — importar código de app constrói o
Settings inteiro no import do módulo de migration (ver `d5c81a4e9b27`). As
constantes abaixo são SNAPSHOTS copiados do modelo, e
`tests/unit/test_chart_of_accounts_schema.py` compara as duas fontes (o
autogenerate do Alembic não enxerga CHECK constraint).

Revision ID: f1b7a52c8e60
Revises: a7f2c1d93e84
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f1b7a52c8e60"
down_revision = "a7f2c1d93e84"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_CODE_MAX = 50

_UQ_CHART_OF_ACCOUNTS = "uq_client_chart_of_accounts_client_code"
# ⚠️ LABEL, não o nome final: a NAMING_CONVENTION do `Base` prefixa
# `ck_client_chart_of_accounts_` (ver `a7f2c1d93e84`).
_CK_STATUS_LABEL = "status"
_CK_STATUS = "status IN ('ativa', 'inativa', 'ausente_na_origem')"
_DEFAULT_STATUS = "ativa"

_FK_CHART_OF_ACCOUNTS_CLIENT = "fk_client_chart_of_accounts_client_id_clients"

#: As duas colunas de estado da sincronização em `clients`.
_CLIENT_SYNC_COLUMNS = (
    "chart_of_accounts_synced_at",
    "chart_of_accounts_sync_failed_at",
)


def upgrade() -> None:
    op.create_table(
        "client_chart_of_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("category_code", sa.String(length=_CODE_MAX), nullable=False),
        sa.Column("parent_code", sa.String(length=_CODE_MAX), nullable=True),
        # O insumo do de-para: NULL = "sem destino declarado", nunca inferido.
        sa.Column("dre_code", sa.String(length=_CODE_MAX), nullable=True),
        sa.Column("dre_level", sa.Integer(), nullable=True),
        sa.Column("dre_sign", sa.String(length=1), nullable=True),
        # Só o CÓDIGO da conta contábil — a `tag` é nome de conta (§4.5).
        sa.Column("conta_contabil_code", sa.String(length=_CODE_MAX), nullable=True),
        sa.Column(
            "totalizadora",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "transferencia",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "nao_exibir",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=_DEFAULT_STATUS,
            nullable=False,
        ),
        sa.Column(
            "synced_at",
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
            name=_FK_CHART_OF_ACCOUNTS_CLIENT,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_chart_of_accounts"),
        # Identidade da linha dentro do tenant — e o alvo do `ON CONFLICT` da 10.2.
        sa.UniqueConstraint("client_id", "category_code", name=_UQ_CHART_OF_ACCOUNTS),
        # O vocabulário de situação é fechado — e mora no BANCO. ⚠️ LABEL, não o
        # nome final: a NAMING_CONVENTION prefixa `ck_client_chart_of_accounts_`.
        sa.CheckConstraint(_CK_STATUS, name=_CK_STATUS_LABEL),
    )

    # Estado da sincronização, no precedente de `omie_accounts_synced_at`: NULL
    # = nunca sincronizou. A de falha NUNCA sobrescreve a de sucesso — é o que
    # sustenta "falha preserva a última sincronização bem-sucedida" (R2).
    for column in _CLIENT_SYNC_COLUMNS:
        op.add_column(
            "clients",
            sa.Column(column, sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for column in reversed(_CLIENT_SYNC_COLUMNS):
        op.drop_column("clients", column)

    op.drop_table("client_chart_of_accounts")
