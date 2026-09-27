"""s14_client_file_categories — as CATEGORIAS DE ORIGEM derivadas do arquivo (BACK 14.4 — R4).

Cria o registro das categorias que o arquivo do cliente sem ERP traz: uma linha por
grafia distinta, com um código ESTÁVEL gerado na primeira ocorrência (`arq-<hex>`,
nunca derivado do rótulo) e o rótulo — a grafia original da célula — CIFRADO com a
DEK do cliente (novo par de AAD `("client_file_categories", "label_encrypted")`,
14º em `core/crypto_service.py`).

O que muda:
    - tabela `client_file_categories` — `UNIQUE(client_id, code)`, FK para `clients`
      com `ondelete=CASCADE`, CHECK do par ciphertext/IV (molde de `client_connections`).

Sem backfill: nenhuma categoria de arquivo existe antes do primeiro arquivo. Aditivo.
Downgrade REAL: `DROP TABLE`. O que se perde são os rótulos (o código continua nas
linhas da base e nas decisões do de-para, que seguiriam sem nome até o próximo arquivo
recriar o registro com códigos NOVOS — por isso o rollback só é seguro antes do
primeiro arquivo processado em produção).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_client_file_category_schema.py` compara.

Revision ID: c7d3f8a24e61
Revises: b4c8e2d71f95
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7d3f8a24e61"
down_revision = "b4c8e2d71f95"
branch_labels = None
depends_on = None

_TABLE = "client_file_categories"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_CODE_MAX = 50
_IV_HEX_LENGTH = 24

_UQ_CLIENT_CODE = "uq_client_file_categories_client_id_code"
_FK_CLIENT = "fk_client_file_categories_client_id_clients"

# ⚠️ LABEL, não o nome final: a NAMING_CONVENTION prefixa `ck_client_file_categories_`.
_CK_LABEL_PAIR_LABEL = "label_pair"
_CK_LABEL_PAIR = "(label_encrypted IS NULL) = (label_iv IS NULL)"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        # O identificador ESTÁVEL, `arq-<hex>` — nunca derivado do rótulo.
        sa.Column("code", sa.String(length=_CODE_MAX), nullable=False),
        # O rótulo (grafia original da célula), SEMPRE cifrado com a DEK do cliente.
        sa.Column("label_encrypted", sa.Text(), nullable=False),
        sa.Column("label_iv", sa.String(length=_IV_HEX_LENGTH), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=_FK_CLIENT, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("client_id", "code", name=_UQ_CLIENT_CODE),
        sa.CheckConstraint(_CK_LABEL_PAIR, name=_CK_LABEL_PAIR_LABEL),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
