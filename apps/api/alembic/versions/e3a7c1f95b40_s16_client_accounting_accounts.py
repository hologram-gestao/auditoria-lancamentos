"""s16_client_accounting_accounts — o PLANO DE CONTAS CONTÁBIL do cliente (BACK 16.1 — R1).

Cria o plano do SISTEMA CONTÁBIL DE DESTINO, por cliente (não confundir com
`client_chart_of_accounts`, o plano da ORIGEM da S10): código reduzido em claro (é o
que vai no arquivo), classificação opcional, nome CIFRADO com a DEK do cliente (novo
par de AAD `("client_accounting_accounts", "name_encrypted")`, 16º em
`core/crypto_service.py`), tipo `analitica|sintetica` e situação ativa/inativa.

O que muda:
    - tabela `client_accounting_accounts` — `UNIQUE(client_id, code)`, FK para
      `clients` com `ondelete=CASCADE`, autoria (`created_by`/`updated_by`) para
      `users` com `RESTRICT`, CHECK do par ciphertext/IV do nome e CHECK do tipo.

Sem backfill: nenhum plano contábil existe antes da primeira importação. Aditivo.
Downgrade REAL: `DROP TABLE`. O que se perde é o plano importado (reimportável da
planilha) — e, a partir da 16.2, as decisões do de-para que apontarem para ele
impedem o downgrade pela FK (o downgrade da 16.2 roda antes, em ordem).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_client_accounting_account_schema.py`
compara as duas fontes.

Revision ID: e3a7c1f95b40
Revises: d9e4a1b57c26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e3a7c1f95b40"
down_revision = "d9e4a1b57c26"
branch_labels = None
depends_on = None

_TABLE = "client_accounting_accounts"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_CODE_MAX = 20
_CLASSIFICATION_MAX = 40
_TYPE_MAX = 20
_IV_HEX_LENGTH = 24

_UQ_CLIENT_CODE = "uq_client_accounting_accounts_client_id_code"
_FK_CLIENT = "fk_client_accounting_accounts_client_id_clients"
_FK_CREATED_BY = "fk_client_accounting_accounts_created_by_users"
_FK_UPDATED_BY = "fk_client_accounting_accounts_updated_by_users"

# ⚠️ LABELS, não os nomes finais: a NAMING_CONVENTION prefixa `ck_client_accounting_accounts_`.
_CK_NAME_PAIR_LABEL = "name_pair"
_CK_NAME_PAIR = "(name_encrypted IS NULL) = (name_iv IS NULL)"
_CK_TYPE_LABEL = "account_type"
_CK_TYPE = "account_type IN ('analitica', 'sintetica')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        # O código reduzido, em CLARO: é o que vai no arquivo contábil.
        sa.Column("code", sa.String(length=_CODE_MAX), nullable=False),
        sa.Column("classification", sa.String(length=_CLASSIFICATION_MAX), nullable=True),
        # O nome da conta, SEMPRE cifrado com a DEK do cliente.
        sa.Column("name_encrypted", sa.Text(), nullable=False),
        sa.Column("name_iv", sa.String(length=_IV_HEX_LENGTH), nullable=False),
        sa.Column("account_type", sa.String(length=_TYPE_MAX), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=_FK_CLIENT, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=_FK_CREATED_BY, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=_FK_UPDATED_BY, ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("client_id", "code", name=_UQ_CLIENT_CODE),
        sa.CheckConstraint(_CK_NAME_PAIR, name=_CK_NAME_PAIR_LABEL),
        sa.CheckConstraint(_CK_TYPE, name=_CK_TYPE_LABEL),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
