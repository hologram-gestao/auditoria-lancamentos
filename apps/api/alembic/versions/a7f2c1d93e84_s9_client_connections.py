"""s9_client_connections — cliente sem credencial + origens tipadas (BACK 09.1).

Desacopla **cliente** de **credencial do Omie**. Até aqui as 4 colunas de
credencial em `clients` eram `NOT NULL`: um cliente só existia se um par Omie
fosse validado antes de salvar, o que impede cadastrar a maior parte da carteira
de um escritório contábil parceiro (decisão do Lucas, 21/09/2026).

O que muda:
    - as 4 colunas de credencial de `clients` viram NULÁVEIS. Nada mais: os
      clientes existentes seguem com a credencial onde sempre esteve, e a
      conversão para a tabela nova é a 09.5 (risco de outra natureza — mover
      ciphertext em SQL quebraria a decifra de todo mundo, porque o AAD amarra
      `client_id ‖ tabela ‖ coluna ‖ pk`);
    - tabela `client_connections` — 0..N origens por cliente, com tipo, rótulo,
      estado (CHECK `ativa|inativa|erro`), `last_checked_at`, `accounts_synced_at`
      e credenciais cifradas num par só; `UNIQUE (client_id, provider_type,
      label)`; FK para `clients` com `ondelete=CASCADE`;
    - `omie_accounts_cache.connection_id` NULÁVEL, FK `CASCADE` — o cache de
      contas passa a ser POR CONEXÃO na 09.6; nulável cobre a janela até lá.

Sem backfill: nenhuma linha nasce nesta migration. Tudo é ADITIVO (coluna nova
nulável, tabela nova, restrição afrouxada), então a API ANTIGA continua servindo
sobre este schema durante a janela entre o job de migração e o `deploy-api`.

Downgrade REAL, com pré-check:
    - ABORTA se existir cliente **ABERTO** (`closed_at IS NULL`) com qualquer
      das 4 colunas nula ou vazia — devolver a coluna para `NOT NULL` exigiria
      inventar uma credencial, e isso é decisão de dado, não de schema;
    - cliente **ENCERRADO** fica FORA do predicado: ele já grava `''` hoje
      (`modules/clients/service.py:570-573`, crypto-shredding da §4.12) e
      incluí-lo tornaria o rollback impossível em qualquer base real. Se o
      encerrado estiver com NULL (forma nova), o downgrade o converte para `''`
      — exatamente o que o encerramento escreve —, o que mantém o `SET NOT NULL`
      possível sem apagar nada de ninguém.

NB: nada de `app.*` importado no topo — importar código de app constrói o
Settings inteiro no import do módulo de migration (ver `d5c81a4e9b27`). As
constantes abaixo são SNAPSHOTS copiados do modelo, e
`tests/unit/test_client_connection_schema.py` compara as duas fontes (o
autogenerate do Alembic não enxerga CHECK constraint).

Revision ID: a7f2c1d93e84
Revises: 3e8f1a6c9d24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7f2c1d93e84"
down_revision = "3e8f1a6c9d24"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_IV_HEX_LENGTH = 24
_LABEL_MAX = 100

_UQ_CONNECTION = "uq_client_connections_client_provider_label"
# ⚠️ LABEL, não o nome final: a NAMING_CONVENTION do `Base` prefixa
# `ck_client_connections_` (ver `d5c81a4e9b27`).
_CK_STATUS_LABEL = "status"
_CK_STATUS = "status IN ('ativa', 'inativa', 'erro')"
_CK_CREDENTIALS_PAIR_LABEL = "credentials_pair"
_CK_CREDENTIALS_PAIR = "(credentials_encrypted IS NULL) = (credentials_iv IS NULL)"
_DEFAULT_STATUS = "ativa"

_FK_CACHE_CONNECTION = "fk_omie_accounts_cache_connection_id_client_connections"
_IX_CACHE_CONNECTION = "ix_omie_accounts_cache_connection_id"

_CREDENTIAL_COLUMNS = (
    ("omie_app_key_encrypted", sa.Text()),
    ("omie_app_key_iv", sa.String(length=_IV_HEX_LENGTH)),
    ("omie_app_secret_encrypted", sa.Text()),
    ("omie_app_secret_iv", sa.String(length=_IV_HEX_LENGTH)),
)

#: Cliente ABERTO cuja credencial não cabe na forma antiga da tabela.
_OPEN_WITHOUT_CREDENTIAL = (
    "closed_at IS NULL AND ("
    "omie_app_key_encrypted IS NULL OR omie_app_key_encrypted = '' "
    "OR omie_app_key_iv IS NULL OR omie_app_key_iv = '' "
    "OR omie_app_secret_encrypted IS NULL OR omie_app_secret_encrypted = '' "
    "OR omie_app_secret_iv IS NULL OR omie_app_secret_iv = '')"
)

#: O MESMO predicado dentro da MENSAGEM do `RAISE`: aspas simples dobradas,
#: senão o literal plpgsql termina no primeiro `''` e a consulta de diagnóstico
#: sai truncada (mesmo cuidado de `3e8f1a6c9d24`). Aqui o predicado já contém
#: `''` (string vazia em SQL), então na mensagem ele vira `''''`.
_OPEN_WITHOUT_CREDENTIAL_IN_MESSAGE = _OPEN_WITHOUT_CREDENTIAL.replace("'", "''")

# Guarda do downgrade. Inventar credencial para um cliente que opera sem origem
# seria gravar lixo cifrável; apagar o cliente seria pior. Então ABORTA dizendo
# quantos são e qual consulta rodar.
# S608: só constantes deste módulo entram no f-string — nenhum dado externo.
_ABORT_DOWNGRADE_IF_OPEN_WITHOUT_CREDENTIAL = f"""
DO $$
DECLARE
    bad_count integer;
BEGIN
    SELECT count(*) INTO bad_count FROM clients WHERE {_OPEN_WITHOUT_CREDENTIAL};

    IF bad_count > 0 THEN
        RAISE EXCEPTION
            'Downgrade bloqueado: % cliente(s) ABERTO(s) sem credencial Omie. A forma '
            'antiga da tabela exige as 4 colunas preenchidas, e inventar credencial e '
            'decisao de dado, nao de schema. Veja quais sao (SELECT id, name FROM clients '
            'WHERE {_OPEN_WITHOUT_CREDENTIAL_IN_MESSAGE}) e conecte uma origem Omie ou encerre o '
            'cliente antes de descer.',
            bad_count;
    END IF;
END $$;
"""  # noqa: S608

# Cliente ENCERRADO com NULL (forma nova) recebe o MESMO `''` que o encerramento
# grava hoje — convergente, roda duas vezes sem mudar nada.
_NORMALIZE_CLOSED_CLIENTS = """
UPDATE clients SET
    omie_app_key_encrypted = coalesce(omie_app_key_encrypted, ''),
    omie_app_key_iv = coalesce(omie_app_key_iv, ''),
    omie_app_secret_encrypted = coalesce(omie_app_secret_encrypted, ''),
    omie_app_secret_iv = coalesce(omie_app_secret_iv, '')
WHERE closed_at IS NOT NULL
  AND (omie_app_key_encrypted IS NULL OR omie_app_key_iv IS NULL
       OR omie_app_secret_encrypted IS NULL OR omie_app_secret_iv IS NULL)
"""


def upgrade() -> None:
    # 1. O cliente deixa de SER um par de credenciais com nome.
    for name, type_ in _CREDENTIAL_COLUMNS:
        op.alter_column(
            "clients",
            name,
            existing_type=type_,
            existing_nullable=False,
            nullable=True,
        )

    # 2. As origens, tipadas e com estado próprio.
    op.create_table(
        "client_connections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("provider_type", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=_LABEL_MAX), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=_DEFAULT_STATUS,
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accounts_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("credentials_iv", sa.String(length=_IV_HEX_LENGTH), nullable=True),
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
            name="fk_client_connections_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_connections"),
        # N conexões do mesmo tipo; o rótulo distingue.
        sa.UniqueConstraint("client_id", "provider_type", "label", name=_UQ_CONNECTION),
        # O vocabulário de estado é fechado — e mora no BANCO. ⚠️ LABEL, não o
        # nome final: a NAMING_CONVENTION prefixa `ck_client_connections_`, e
        # passar o nome inteiro geraria `ck_client_connections_ck_client_...`
        # (verificado com `alembic upgrade … --sql` antes do commit).
        sa.CheckConstraint(_CK_STATUS, name=_CK_STATUS_LABEL),
        # Ciphertext e IV vivem e morrem juntos: meio envelope é dado perdido.
        sa.CheckConstraint(_CK_CREDENTIALS_PAIR, name=_CK_CREDENTIALS_PAIR_LABEL),
    )

    # 3. O cache de contas passa a poder apontar para a conexão de origem (09.6).
    op.add_column(
        "omie_accounts_cache",
        sa.Column("connection_id", sa.UUID(), nullable=True),
    )
    op.create_index(_IX_CACHE_CONNECTION, "omie_accounts_cache", ["connection_id"])
    op.create_foreign_key(
        _FK_CACHE_CONNECTION,
        "omie_accounts_cache",
        "client_connections",
        ["connection_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.execute(sa.text(_ABORT_DOWNGRADE_IF_OPEN_WITHOUT_CREDENTIAL))

    op.drop_constraint(_FK_CACHE_CONNECTION, "omie_accounts_cache", type_="foreignkey")
    op.drop_index(_IX_CACHE_CONNECTION, table_name="omie_accounts_cache")
    op.drop_column("omie_accounts_cache", "connection_id")

    op.drop_table("client_connections")

    # Encerrado com NULL vira `''` — a mesma forma que o encerramento grava.
    op.execute(sa.text(_NORMALIZE_CLOSED_CLIENTS))
    for name, type_ in _CREDENTIAL_COLUMNS:
        op.alter_column(
            "clients",
            name,
            existing_type=type_,
            existing_nullable=True,
            nullable=False,
        )
