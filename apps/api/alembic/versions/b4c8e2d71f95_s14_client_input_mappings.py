"""s14_client_input_mappings — o MAPEAMENTO DE ENTRADA do arquivo do cliente (BACK 14.1 — R1).

Cria a tabela que faz a planilha e o extrato virarem origem de primeira classe: por
cliente, UM mapeamento declarando qual coluna é data, descrição, valor, categoria,
conta e documento, o formato do arquivo (CSV ou XLSX), o delimitador e a codificação
do CSV, o formato de data, o separador decimal e a convenção de sinal — tudo
DECLARADO por uma pessoa, nada inferido.

O que muda:
    - tabela `client_input_mappings` — `UNIQUE(client_id)` (um por cliente; alterar é
      upsert, configuração e não vigência), FK para `clients` com `ondelete=CASCADE`,
      FKs de autoria para `users` com `ondelete=RESTRICT` (precedente ADR-074-BE:
      a exclusão definitiva apaga o mapeamento antes dos usuários do tenant),
      CHECKs de vocabulário para todo enum e TRÊS CHECKs de coerência (CSV exige
      delimitador+codificação; `classificacao_livre` exige a coluna; convenção de
      sinal × campos exigidos).

**Nenhuma coluna cifrada.** Nome de coluna do arquivo é estrutura, não PII; o
conteúdo das células nunca chega aqui.

Sem backfill: nenhum cliente tem mapeamento até alguém declará-lo. Aditivo — a API
antiga continua servindo sobre este schema na janela entre o job de migração e o
`deploy-api`. Downgrade REAL: `DROP TABLE` (o que se perde é configuração que a
pessoa redeclara; os movimentos gravados a partir dela ficam em `client_movements`).

NB: nada de `app.*` importado no topo — importar código de app constrói o Settings
inteiro no import do módulo de migration (ver `d5c81a4e9b27`). As constantes abaixo
são SNAPSHOTS copiados do modelo, e `tests/unit/test_client_input_mapping_schema.py`
compara as duas fontes (o autogenerate do Alembic não enxerga CHECK constraint).

Revision ID: b4c8e2d71f95
Revises: a7c2e9f31b58
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b4c8e2d71f95"
down_revision = "a7c2e9f31b58"
branch_labels = None
depends_on = None

_TABLE = "client_input_mappings"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_COLUMN_NAME_MAX = 100
_NATURE_LITERAL_MAX = 30
_DEFAULT_CATEGORY_MODE = "coluna_categoria"

_UQ_CLIENT = "uq_client_input_mappings_client_id"
_FK_CLIENT = "fk_client_input_mappings_client_id_clients"
_FK_CREATED_BY = "fk_client_input_mappings_created_by_users"
_FK_UPDATED_BY = "fk_client_input_mappings_updated_by_users"

# ⚠️ LABELS, não os nomes finais: a NAMING_CONVENTION do `Base` prefixa
# `ck_client_input_mappings_`.
_CK_FILE_FORMAT_LABEL = "file_format"
_CK_FILE_FORMAT = "file_format IN ('csv', 'xlsx')"
_CK_CATEGORY_MODE_LABEL = "category_mode"
_CK_CATEGORY_MODE = "category_mode IN ('coluna_categoria', 'classificacao_livre')"
_CK_DATE_FORMAT_LABEL = "date_format"
_CK_DATE_FORMAT = "date_format IN ('dd/mm/yyyy', 'dd-mm-yyyy', 'yyyy-mm-dd', 'dd/mm/yy')"
_CK_DECIMAL_SEPARATOR_LABEL = "decimal_separator"
_CK_DECIMAL_SEPARATOR = "decimal_separator IN (',', '.')"
_CK_SIGN_CONVENTION_LABEL = "sign_convention"
_CK_SIGN_CONVENTION = (
    "sign_convention IN ('valor_com_sinal', 'coluna_natureza', 'colunas_separadas')"
)
_CK_CSV_DELIMITER_LABEL = "csv_delimiter"
_CK_CSV_DELIMITER = "csv_delimiter IS NULL OR csv_delimiter IN (';', ',', '|')"
_CK_ENCODING_LABEL = "encoding"
_CK_ENCODING = "encoding IS NULL OR encoding IN ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252')"
_CK_CSV_COHERENT_LABEL = "csv_coherent"
_CK_CSV_COHERENT = (
    "(file_format = 'csv' AND csv_delimiter IS NOT NULL AND encoding IS NOT NULL) "
    "OR (file_format = 'xlsx' AND csv_delimiter IS NULL AND encoding IS NULL)"
)
_CK_CATEGORY_COHERENT_LABEL = "category_coherent"
_CK_CATEGORY_COHERENT = "NOT (category_mode = 'classificacao_livre' AND category_column IS NULL)"
_CK_SIGN_COHERENT_LABEL = "sign_coherent"
_CK_SIGN_COHERENT = (
    "(sign_convention = 'valor_com_sinal' AND amount_column IS NOT NULL "
    "AND nature_column IS NULL AND debit_value IS NULL AND credit_value IS NULL "
    "AND debit_column IS NULL AND credit_column IS NULL) "
    "OR (sign_convention = 'coluna_natureza' AND amount_column IS NOT NULL "
    "AND nature_column IS NOT NULL AND debit_value IS NOT NULL AND credit_value IS NOT NULL "
    "AND debit_column IS NULL AND credit_column IS NULL) "
    "OR (sign_convention = 'colunas_separadas' AND amount_column IS NULL "
    "AND nature_column IS NULL AND debit_value IS NULL AND credit_value IS NULL "
    "AND debit_column IS NOT NULL AND credit_column IS NOT NULL)"
)


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


def _column_name(name: str, *, nullable: bool) -> sa.Column[object]:
    return sa.Column(name, sa.String(length=_COLUMN_NAME_MAX), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        # --- formato do arquivo
        sa.Column("file_format", sa.String(length=10), nullable=False),
        sa.Column("csv_delimiter", sa.String(length=1), nullable=True),
        sa.Column("encoding", sa.String(length=20), nullable=True),
        # --- colunas do arquivo (nomes do cabeçalho: estrutura, em claro)
        _column_name("date_column", nullable=False),
        _column_name("description_column", nullable=False),
        _column_name("amount_column", nullable=True),
        _column_name("category_column", nullable=True),
        sa.Column(
            "category_mode",
            sa.String(length=30),
            server_default=_DEFAULT_CATEGORY_MODE,
            nullable=False,
        ),
        _column_name("account_column", nullable=True),
        _column_name("document_column", nullable=True),
        # --- convenções de leitura
        sa.Column("date_format", sa.String(length=20), nullable=False),
        sa.Column("decimal_separator", sa.String(length=1), nullable=False),
        sa.Column("sign_convention", sa.String(length=30), nullable=False),
        _column_name("nature_column", nullable=True),
        sa.Column("debit_value", sa.String(length=_NATURE_LITERAL_MAX), nullable=True),
        sa.Column("credit_value", sa.String(length=_NATURE_LITERAL_MAX), nullable=True),
        _column_name("debit_column", nullable=True),
        _column_name("credit_column", nullable=True),
        # --- autoria
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("updated_by", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=_FK_CLIENT, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=_FK_CREATED_BY, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=_FK_UPDATED_BY, ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("client_id", name=_UQ_CLIENT),
        # ⚠️ LABELS: a NAMING_CONVENTION prefixa `ck_client_input_mappings_`.
        sa.CheckConstraint(_CK_FILE_FORMAT, name=_CK_FILE_FORMAT_LABEL),
        sa.CheckConstraint(_CK_CATEGORY_MODE, name=_CK_CATEGORY_MODE_LABEL),
        sa.CheckConstraint(_CK_DATE_FORMAT, name=_CK_DATE_FORMAT_LABEL),
        sa.CheckConstraint(_CK_DECIMAL_SEPARATOR, name=_CK_DECIMAL_SEPARATOR_LABEL),
        sa.CheckConstraint(_CK_SIGN_CONVENTION, name=_CK_SIGN_CONVENTION_LABEL),
        sa.CheckConstraint(_CK_CSV_DELIMITER, name=_CK_CSV_DELIMITER_LABEL),
        sa.CheckConstraint(_CK_ENCODING, name=_CK_ENCODING_LABEL),
        sa.CheckConstraint(_CK_CSV_COHERENT, name=_CK_CSV_COHERENT_LABEL),
        sa.CheckConstraint(_CK_CATEGORY_COHERENT, name=_CK_CATEGORY_COHERENT_LABEL),
        sa.CheckConstraint(_CK_SIGN_COHERENT, name=_CK_SIGN_COHERENT_LABEL),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
