"""accounting_chart_party_links — vínculo conta contábil ↔ cliente/fornecedor (86e3qhhrn).

O que muda:
    - `client_accounting_accounts.party_document_encrypted` (TEXT, nullable) e
      `party_document_iv` (VARCHAR(24), nullable): o CNPJ ou CPF da pessoa a quem a
      conta pertence no sistema contábil, como vem da "RELAÇÃO DE CLIENTES" e da
      "RELAÇÃO DE FORNECEDORES" do Domínio. SEMPRE cifrado com a DEK do cliente
      (par de AAD novo, §4.1): documento é dado do cliente final (§4.5).
    - `client_accounting_accounts.party_type` (VARCHAR(20), nullable): `cliente` ou
      `fornecedor`, de que relatório veio o vínculo.
    - CHECK `ck_client_accounting_accounts_party_pair`: documento, IV e tipo vivem e
      morrem juntos (vinculada = os três; sem vínculo = nenhum).
    - CHECK `ck_client_accounting_accounts_party_type`: vocabulário fechado.
    - `clients.party_reports_company_fingerprint` (VARCHAR(64), nullable): HMAC-SHA256
      do CNPJ da EMPRESA do cabeçalho dos relatórios já importados — só para comparar
      ("relatório de outra empresa" é recusado), nunca o CNPJ em claro.

Por quê: o escritório parceiro liga cada conta do plano à pessoa (cliente ou
fornecedor) pelo documento, e o vínculo é o insumo da planilha de importação do plano
de contas da Omie (subtask 4 do épico 86e3qhhr0). Reunião com o Gabriel, 14/10/2026.

Sem backfill: nenhuma conta existente tem vínculo (o relatório ainda não foi
importado). Downgrade REAL: derruba os CHECKs e as quatro colunas. Os vínculos se
perdem, e isso é aceitável: eles são DERIVADOS de um relatório que se reimporta.

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Rótulos e predicados dos
CHECKs são SNAPSHOTS de `db/models/client_accounting_account.py`;
`tests/unit/test_client_accounting_account_schema.py` compara as duas fontes.

Revision ID: 7c3e9a1d5f28
Revises: a94a539e1d34
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7c3e9a1d5f28"
down_revision = "a94a539e1d34"
branch_labels = None
depends_on = None

_TABLE = "client_accounting_accounts"
_CLIENTS = "clients"
_COLUMN_DOCUMENT = "party_document_encrypted"
_COLUMN_IV = "party_document_iv"
_COLUMN_TYPE = "party_type"
_COLUMN_FINGERPRINT = "party_reports_company_fingerprint"

# Snapshots de `db/models/client_accounting_account.py` e de `db/models/client.py`.
_IV_HEX_LENGTH = 24
_TYPE_MAX = 20
_FINGERPRINT_HEX_LENGTH = 64
_CK_PARTY_PAIR_LABEL = "party_pair"
_CK_PARTY_PAIR = (
    "(party_document_encrypted IS NULL) = (party_document_iv IS NULL) "
    "AND (party_document_encrypted IS NULL) = (party_type IS NULL)"
)
_CK_PARTY_TYPE_LABEL = "party_type"
_CK_PARTY_TYPE = "party_type IS NULL OR party_type IN ('cliente', 'fornecedor')"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN_DOCUMENT, sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column(_COLUMN_IV, sa.String(_IV_HEX_LENGTH), nullable=True))
    op.add_column(_TABLE, sa.Column(_COLUMN_TYPE, sa.String(_TYPE_MAX), nullable=True))
    # Rótulos, não os nomes finais: a NAMING_CONVENTION prefixa `ck_<tabela>_`.
    op.create_check_constraint(_CK_PARTY_PAIR_LABEL, _TABLE, sa.text(_CK_PARTY_PAIR))
    op.create_check_constraint(_CK_PARTY_TYPE_LABEL, _TABLE, sa.text(_CK_PARTY_TYPE))
    op.add_column(
        _CLIENTS,
        sa.Column(_COLUMN_FINGERPRINT, sa.String(_FINGERPRINT_HEX_LENGTH), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_CLIENTS, _COLUMN_FINGERPRINT)
    op.drop_constraint(_CK_PARTY_TYPE_LABEL, _TABLE, type_="check")
    op.drop_constraint(_CK_PARTY_PAIR_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN_TYPE)
    op.drop_column(_TABLE, _COLUMN_IV)
    op.drop_column(_TABLE, _COLUMN_DOCUMENT)
