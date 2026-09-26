"""s12_client_movements — a BASE DE MOVIMENTOS REALIZADOS do cliente (BACK 12.1 — R0).

Cria a entrada que o de-para da Sprint 12 consome e que não existia: os movimentos
realizados de TODAS as contas do cliente, por competência, numa entidade própria e
agnóstica de origem. Hoje `reconciliation_file_entries` não tem categoria,
`reconciliation_omie_entries` guarda só as divergências de uma conciliação e
`client_titles` só os títulos em aberto — nenhuma serve de base.

O que muda:
    - tabela `client_movements` — uma linha por `(cliente, tipo de origem,
      identificador do movimento na origem)`, com `UNIQUE` sobre essa chave (é ela
      que torna o ciclo de sincronização idempotente), índice
      `(client_id, competence)` para a aplicação do de-para e o ciclo, FK para
      `clients` com `ondelete=CASCADE`, CHECK de `status` e CHECK de competência
      (sempre o dia 1 do mês);
    - tabela `client_movement_syncs` — o estado da base por `(cliente,
      competência)`: último sucesso íntegro e última falha.

**Nenhuma coluna de nome nem de texto livre.** A descrição do lançamento fica fora
(§4.5). Por consequência nada aqui é cifrado — não há PII.

**`source_type` não é FK de conexão nem tem CHECK**, no precedente de
`client_connections.provider_type`: o de-para é do cliente e sobrevive a trocar a
conexão, e o tipo `arquivo` da Sprint 14 entra sem mexer nesta tabela.

Sem backfill: nenhuma linha nasce aqui — a base parte vazia e toda competência nasce
"nunca sincronizada", que é o estado correto. Tudo é ADITIVO, então a API ANTIGA
continua servindo sobre este schema durante a janela entre o job de migração e o
`deploy-api`.

Downgrade REAL e sem pré-check: a base é DERIVADA da origem (some daqui,
ressincroniza de lá). O que se perderia é materialização do de-para apontando para
ela — e a materialização (BACK 12.3) guarda SNAPSHOT, sem FK para esta tabela.

NB: nada de `app.*` importado no topo — importar código de app constrói o Settings
inteiro no import do módulo de migration (ver `d5c81a4e9b27`). As constantes abaixo
são SNAPSHOTS copiados do modelo, e `tests/unit/test_client_movements_schema.py`
compara as duas fontes (o autogenerate do Alembic não enxerga CHECK constraint nem
índice composto declarado à mão).

Revision ID: d3a8f5c21e47
Revises: b8ee8f368914
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d3a8f5c21e47"
down_revision = "b8ee8f368914"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_SOURCE_TYPE_MAX = 30
_SOURCE_ID_MAX = 60
_CATEGORY_CODE_MAX = 50
_REF_MAX = 60

_UQ_MOVEMENT = "uq_client_movements_client_id_source_type_source_movement_id"
_IX_MOVEMENT_COMPETENCE = "ix_client_movements_client_id_competence"
_UQ_MOVEMENT_SYNC = "uq_client_movement_syncs_client_id_competence"

# ⚠️ LABEL, não o nome final: a NAMING_CONVENTION do `Base` prefixa
# `ck_client_movements_`.
_CK_STATUS_LABEL = "status"
_CK_STATUS = "status IN ('presente', 'ausente_na_origem')"
_CK_COMPETENCE_LABEL = "competence_first_day"
_CK_COMPETENCE = "EXTRACT(DAY FROM competence) = 1"
_DEFAULT_STATUS = "presente"

_FK_MOVEMENT_CLIENT = "fk_client_movements_client_id_clients"
_FK_MOVEMENT_SYNC_CLIENT = "fk_client_movement_syncs_client_id_clients"


def _timestamps() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "client_movements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        # O TIPO do provedor — nunca FK de conexão (o de-para sobrevive à troca).
        sa.Column("source_type", sa.String(length=_SOURCE_TYPE_MAX), nullable=False),
        # Identificador do movimento NA ORIGEM, como texto.
        sa.Column("source_movement_id", sa.String(length=_SOURCE_ID_MAX), nullable=False),
        # Competência = dia 1 do mês (CHECK abaixo).
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("movement_date", sa.Date(), nullable=False),
        # Dinheiro COM SINAL, Numeric(14,2) em todo o sistema (§3.4).
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        # Só CÓDIGOS — nome e descrição são runtime (§4.5). NULO em categoria é o
        # "sem categoria de origem" do R3.
        sa.Column("category_code", sa.String(length=_CATEGORY_CODE_MAX), nullable=True),
        sa.Column("supplier_code", sa.String(length=_REF_MAX), nullable=True),
        sa.Column("source_account_id", sa.String(length=_REF_MAX), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=_DEFAULT_STATUS,
            nullable=False,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=_FK_MOVEMENT_CLIENT,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_movements"),
        sa.UniqueConstraint("client_id", "source_type", "source_movement_id", name=_UQ_MOVEMENT),
        # ⚠️ LABEL, não o nome final: a NAMING_CONVENTION prefixa `ck_client_movements_`.
        sa.CheckConstraint(_CK_STATUS, name=_CK_STATUS_LABEL),
        sa.CheckConstraint(_CK_COMPETENCE, name=_CK_COMPETENCE_LABEL),
    )

    # Declarado à mão: o autogenerate não gera índice composto de `__table_args__`.
    # Serve também toda query por `client_id` sozinho, pelo prefixo.
    op.create_index(_IX_MOVEMENT_COMPETENCE, "client_movements", ["client_id", "competence"])

    op.create_table(
        "client_movement_syncs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("competence", sa.Date(), nullable=False),
        # NULL = nunca houve sucesso. A falha NUNCA sobrescreve o sucesso.
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_failed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=_FK_MOVEMENT_SYNC_CLIENT,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_movement_syncs"),
        sa.UniqueConstraint("client_id", "competence", name=_UQ_MOVEMENT_SYNC),
    )


def downgrade() -> None:
    op.drop_table("client_movement_syncs")
    op.drop_index(_IX_MOVEMENT_COMPETENCE, table_name="client_movements")
    op.drop_table("client_movements")
