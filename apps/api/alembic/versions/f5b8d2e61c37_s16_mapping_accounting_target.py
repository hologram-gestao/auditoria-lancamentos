"""s16_mapping_accounting_target — de-para em `conta_contabil` aponta para o plano do cliente (BACK 16.2).

No destino `conta_contabil` (e só nele), a decisão aponta uma conta do PLANO CONTÁBIL
DO CLIENTE (`client_accounting_accounts`, 16.1) e carrega o HISTÓRICO PADRÃO cifrado
(novo par de AAD `("client_mapping_decisions", "history_encrypted")`, 17º em
`core/crypto_service.py`). O item da materialização ganha o snapshot do CÓDIGO
REDUZIDO (coluna própria) e o id da vigência que decidiu a linha.

O que muda:
    - `client_mapping_decisions`: `accounting_account_id` (FK para o plano, nulável,
      com índice), `history_encrypted` + `history_iv` (nuláveis); o CHECK
      `decision_target_coherent` passa a aceitar alvo = catálogo XOR conta do plano; o
      CHECK novo `history_coherent` amarra o par ciphertext/IV e só aceita histórico
      com conta do plano.
    - `client_mapping_materialization_items`: `accounting_account_code` e
      `decision_id` (nuláveis, sem FK — snapshot); o CHECK `item_target_coherent`
      passa a aceitar código do catálogo XOR código do plano na situação `alvo`.

Sem backfill: as decisões e itens existentes seguem válidos nos CHECKs novos (alvo do
catálogo, conta nula). As decisões legadas em `conta_contabil` apontando o catálogo
NÃO são convertidas (namespaces diferentes) — a leitura as marca para refazer.

Downgrade REAL, com guarda: se já existe decisão com conta do plano (ou item com o
código dela), a forma antiga dos CHECKs não cabe — o downgrade ABORTA com mensagem
acionável em vez de apagar decisão/materialização (dado do cliente).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_client_mapping_accounting_schema.py`
compara as duas fontes.

Revision ID: f5b8d2e61c37
Revises: e3a7c1f95b40
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f5b8d2e61c37"
down_revision = "e3a7c1f95b40"
branch_labels = None
depends_on = None

_DECISIONS = "client_mapping_decisions"
_ITEMS = "client_mapping_materialization_items"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_IV_HEX_LENGTH = 24
_ACCOUNT_CODE_MAX = 20

_FK_ACCOUNT = "fk_client_mapping_decisions_accounting_account_id"
_IX_ACCOUNT = "ix_client_mapping_decisions_accounting_account_id"

# ⚠️ LABELS, não os nomes finais: a NAMING_CONVENTION prefixa `ck_<tabela>_` — também
# no `op.drop_constraint`/`create_check_constraint` (precedente `3e8f1a6c9d24`).
_CK_DECISION_TARGET_LABEL = "decision_target_coherent"
_CK_DECISION_TARGET = (
    "(decision_type = 'alvo' AND (target_id IS NOT NULL) <> (accounting_account_id IS NOT NULL)) "
    "OR (decision_type = 'nao_mapear' AND target_id IS NULL AND accounting_account_id IS NULL)"
)
_CK_HISTORY_LABEL = "history_coherent"
_CK_HISTORY = (
    "((history_encrypted IS NULL) = (history_iv IS NULL)) "
    "AND (history_encrypted IS NULL OR accounting_account_id IS NOT NULL)"
)
_CK_ITEM_TARGET_LABEL = "item_target_coherent"
_CK_ITEM_TARGET = (
    "(situation = 'alvo' AND (target_code IS NOT NULL) <> (accounting_account_code IS NOT NULL)) "
    "OR (situation <> 'alvo' AND target_code IS NULL AND accounting_account_code IS NULL)"
)

# Os predicados da S12 (`e6b2c9d47f13`), que o downgrade restaura.
_CK_DECISION_TARGET_S12 = (
    "(decision_type = 'alvo' AND target_id IS NOT NULL) "
    "OR (decision_type = 'nao_mapear' AND target_id IS NULL)"
)
_CK_ITEM_TARGET_S12 = (
    "(situation = 'alvo' AND target_code IS NOT NULL) "
    "OR (situation <> 'alvo' AND target_code IS NULL)"
)


# Guarda do downgrade: a forma antiga dos CHECKs não aceita decisão com conta do
# plano nem item com o código dela — e apagar decisão ou materialização é decisão
# de DADO, não de migration.
_ABORT_IF_ACCOUNTING = """
DO $$
DECLARE
    decisions_count integer;
    items_count integer;
BEGIN
    SELECT count(*) INTO decisions_count
    FROM client_mapping_decisions WHERE accounting_account_id IS NOT NULL;
    SELECT count(*) INTO items_count
    FROM client_mapping_materialization_items WHERE accounting_account_code IS NOT NULL;

    IF decisions_count > 0 OR items_count > 0 THEN
        RAISE EXCEPTION
            'Downgrade bloqueado: % decisao(oes) do de-para apontam conta do plano '
            'contabil do cliente e % item(ns) de materializacao guardam o codigo dela. '
            'A forma antiga (S12) so aceita alvo do catalogo. Nada foi alterado: '
            'decida o destino desses dados antes (SELECT id, client_id FROM '
            'client_mapping_decisions WHERE accounting_account_id IS NOT NULL).',
            decisions_count, items_count;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.add_column(_DECISIONS, sa.Column("accounting_account_id", sa.UUID(), nullable=True))
    op.add_column(_DECISIONS, sa.Column("history_encrypted", sa.Text(), nullable=True))
    op.add_column(
        _DECISIONS, sa.Column("history_iv", sa.String(length=_IV_HEX_LENGTH), nullable=True)
    )
    op.create_foreign_key(
        _FK_ACCOUNT,
        _DECISIONS,
        "client_accounting_accounts",
        ["accounting_account_id"],
        ["id"],
    )
    op.create_index(_IX_ACCOUNT, _DECISIONS, ["accounting_account_id"])
    op.drop_constraint(_CK_DECISION_TARGET_LABEL, _DECISIONS, type_="check")
    op.create_check_constraint(_CK_DECISION_TARGET_LABEL, _DECISIONS, sa.text(_CK_DECISION_TARGET))
    op.create_check_constraint(_CK_HISTORY_LABEL, _DECISIONS, sa.text(_CK_HISTORY))

    op.add_column(
        _ITEMS,
        sa.Column("accounting_account_code", sa.String(length=_ACCOUNT_CODE_MAX), nullable=True),
    )
    op.add_column(_ITEMS, sa.Column("decision_id", sa.UUID(), nullable=True))
    op.drop_constraint(_CK_ITEM_TARGET_LABEL, _ITEMS, type_="check")
    op.create_check_constraint(_CK_ITEM_TARGET_LABEL, _ITEMS, sa.text(_CK_ITEM_TARGET))


def downgrade() -> None:
    op.execute(sa.text(_ABORT_IF_ACCOUNTING))

    op.drop_constraint(_CK_ITEM_TARGET_LABEL, _ITEMS, type_="check")
    op.create_check_constraint(_CK_ITEM_TARGET_LABEL, _ITEMS, sa.text(_CK_ITEM_TARGET_S12))
    op.drop_column(_ITEMS, "decision_id")
    op.drop_column(_ITEMS, "accounting_account_code")

    op.drop_constraint(_CK_HISTORY_LABEL, _DECISIONS, type_="check")
    op.drop_constraint(_CK_DECISION_TARGET_LABEL, _DECISIONS, type_="check")
    op.create_check_constraint(_CK_DECISION_TARGET_LABEL, _DECISIONS, sa.text(_CK_DECISION_TARGET_S12))
    op.drop_index(_IX_ACCOUNT, table_name=_DECISIONS)
    op.drop_constraint(_FK_ACCOUNT, _DECISIONS, type_="foreignkey")
    op.drop_column(_DECISIONS, "history_iv")
    op.drop_column(_DECISIONS, "history_encrypted")
    op.drop_column(_DECISIONS, "accounting_account_id")
