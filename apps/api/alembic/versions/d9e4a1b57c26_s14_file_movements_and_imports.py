"""s14_file_movements_and_imports — as colunas que só o ARQUIVO tem e o registro dos arquivos (BACK 14.3 — R3).

O R3 NÃO cria uma segunda entidade de lançamento: grava na base de movimentos da
Sprint 12 com `source_type = 'arquivo'`. O que esta migration acrescenta:

    - em `client_movements`, três colunas NULÁVEIS: `description_encrypted` +
      `description_iv` (a descrição da linha do arquivo, cifrada com a DEK do cliente;
      novo par de AAD `("client_movements", "description_encrypted")`, 15º em
      `core/crypto_service.py`; CHECK do par no molde de `client_connections`) e
      `document` (número do documento, em claro — identificador, como `supplier_code`,
      ADR-082-BE). As linhas vindas do Omie seguem com as três nulas;
    - tabela `client_file_imports` — um registro por arquivo processado:
      `UNIQUE(client_id, competence, file_hash)` (é ela que dá o 409 do reenvio, no
      banco), FK `clients` CASCADE, `mapping_id` FK `client_input_mappings` SET NULL,
      `created_by` FK `users` RESTRICT, CHECK de competência no dia 1.

Sem backfill; tudo aditivo — a API antiga continua servindo sobre este schema na
janela entre o job de migração e o `deploy-api`. Downgrade REAL: derruba a tabela,
o CHECK e as três colunas (perde-se descrição e documento das linhas de arquivo; os
movimentos em si ficam).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_client_file_import_schema.py` e
`tests/unit/test_client_movements_schema.py` comparam.

Revision ID: d9e4a1b57c26
Revises: c7d3f8a24e61
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d9e4a1b57c26"
down_revision = "c7d3f8a24e61"
branch_labels = None
depends_on = None

_MOVEMENTS = "client_movements"
_IMPORTS = "client_file_imports"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_IV_HEX_LENGTH = 24
_DOCUMENT_MAX = 60
_FILE_HASH_HEX_LENGTH = 64

# ⚠️ LABELS, não os nomes finais: a NAMING_CONVENTION prefixa `ck_<tabela>_`.
_CK_DESCRIPTION_PAIR_LABEL = "description_pair"
_CK_DESCRIPTION_PAIR = "(description_encrypted IS NULL) = (description_iv IS NULL)"
_CK_DESCRIPTION_PAIR_NAME = f"ck_{_MOVEMENTS}_{_CK_DESCRIPTION_PAIR_LABEL}"
_CK_IMPORT_COMPETENCE_LABEL = "competence_first_day"
_CK_IMPORT_COMPETENCE = "EXTRACT(DAY FROM competence) = 1"

_UQ_IMPORT = "uq_client_file_imports_client_id_competence_file_hash"
_FK_IMPORT_CLIENT = "fk_client_file_imports_client_id_clients"
_FK_IMPORT_MAPPING = "fk_client_file_imports_mapping_id_client_input_mappings"
_FK_IMPORT_CREATED_BY = "fk_client_file_imports_created_by_users"


def upgrade() -> None:
    op.add_column(_MOVEMENTS, sa.Column("description_encrypted", sa.Text(), nullable=True))
    op.add_column(
        _MOVEMENTS,
        sa.Column("description_iv", sa.String(length=_IV_HEX_LENGTH), nullable=True),
    )
    op.add_column(_MOVEMENTS, sa.Column("document", sa.String(length=_DOCUMENT_MAX), nullable=True))
    op.create_check_constraint(_CK_DESCRIPTION_PAIR_LABEL, _MOVEMENTS, _CK_DESCRIPTION_PAIR)

    op.create_table(
        _IMPORTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("file_hash", sa.String(length=_FILE_HASH_HEX_LENGTH), nullable=False),
        sa.Column("mapping_id", sa.UUID(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=_FK_IMPORT_CLIENT, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["client_input_mappings.id"],
            name=_FK_IMPORT_MAPPING,
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=_FK_IMPORT_CREATED_BY, ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_IMPORTS}"),
        sa.UniqueConstraint("client_id", "competence", "file_hash", name=_UQ_IMPORT),
        sa.CheckConstraint(_CK_IMPORT_COMPETENCE, name=_CK_IMPORT_COMPETENCE_LABEL),
    )


def downgrade() -> None:
    op.drop_table(_IMPORTS)
    # O LABEL, não o nome final: a NAMING_CONVENTION prefixa `ck_<tabela>_` também no
    # drop (precedente `d5c81a4e9b27`/`3e8f1a6c9d24`). Com o nome final aqui o `--sql`
    # renderizava `ck_client_movements_ck_client_movements_description_pair`.
    op.drop_constraint(_CK_DESCRIPTION_PAIR_LABEL, _MOVEMENTS, type_="check")
    op.drop_column(_MOVEMENTS, "document")
    op.drop_column(_MOVEMENTS, "description_iv")
    op.drop_column(_MOVEMENTS, "description_encrypted")
