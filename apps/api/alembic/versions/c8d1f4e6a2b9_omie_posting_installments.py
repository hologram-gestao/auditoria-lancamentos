"""omie_posting_installments — parcelas FUTURAS provisionadas no Contas a Pagar.

O que muda (86e3qhhtb):
    - tabela `reconciliation_omie_posting_installments`, filha da intenção de
      lançamento (`reconciliation_omie_postings`): uma linha por parcela futura
      (`N+1` a `M`) com identidade própria (cliente, cartão, fornecedor, valor,
      número e total), datas, chave de integração do título, passo
      (`titulo`/`concluido`), id do título, tentativas, erro do provedor, a linha
      da fatura seguinte que a ENCONTROU e a anomalia que avisou que ela não veio;
    - três UNIQUEs: `(posting_id, installment_number)`, `(client_id, payable_key)`
      e `(claimed_by_file_entry_id)`; quatro CHECKs (passo coerente com o id do
      título, número de parcela futura, valor positivo, encontro só depois do
      título); o índice da identidade que a fatura seguinte procura;
    - o tipo de anomalia `parcela_nao_veio` no catálogo (idempotente,
      `ON CONFLICT (code) DO NOTHING`).

Downgrade ABORTA se existir parcela registrada ou anomalia `parcela_nao_veio`:
derrubar a tabela apagaria a memória de títulos já criados na contabilidade do
cliente, e a fatura seguinte criaria a mesma parcela de novo (o duplicado que é o
critério de rollback, §3.16). Sem parcela nem anomalia, derruba tudo.

NB: nada de `app.*` importado no topo. Rótulos e predicados são SNAPSHOTS de
`db/models/reconciliation_omie_posting_installment.py`;
`tests/unit/test_installment_schema.py` compara as duas fontes.

Revision ID: c8d1f4e6a2b9
Revises: b4e7c2a9d613
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c8d1f4e6a2b9"
down_revision = "b4e7c2a9d613"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_omie_posting_installments"
_KEY_LENGTH = 60
_STEP_LENGTH = 12

# Snapshots de `db/models/reconciliation_omie_posting_installment.py`.
_STEP_CK_LABEL = "step_coherent"
_STEP_CK = "step IN ('titulo', 'concluido') AND ((step = 'concluido') = (payable_id IS NOT NULL))"
_NUMBER_CK_LABEL = "number_in_range"
_NUMBER_CK = (
    "installment_number >= 2 AND installment_total <= 99 "
    "AND installment_number <= installment_total"
)
_AMOUNT_CK_LABEL = "amount_positive"
_AMOUNT_CK = "amount > 0"
_CLAIM_CK_LABEL = "claim_after_title"
_CLAIM_CK = "claimed_by_file_entry_id IS NULL OR step = 'concluido'"

_ANOMALY_CODE = "parcela_nao_veio"
_ANOMALY_NAME = "Parcela provisionada que não veio na fatura"
_ANOMALY_DESCRIPTION = (
    "Uma parcela futura de compra parcelada foi provisionada no Contas a Pagar do "
    "Omie, mas a fatura do mês em que ela deveria cair não trouxe a parcela. O título "
    "continua em aberto no Omie: confira se a compra foi cancelada ou estornada e "
    "decida por lá. A plataforma não exclui o título."
)

_ABORT_IF_INSTALLMENTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
        RAISE EXCEPTION 'Downgrade bloqueado: existem parcelas futuras registradas '
            '(títulos que podem já existir no Omie). Derrubar a tabela apagaria a memória '
            'delas e a fatura seguinte criaria a mesma parcela de novo. Consulte: SELECT '
            'id, installment_number, installment_total, step, payable_id FROM %;', '{_TABLE}';
    END IF;
    IF EXISTS (
        SELECT 1 FROM reconciliation_anomalies a
        JOIN anomaly_types t ON t.id = a.anomaly_type_id
        WHERE t.code = '{_ANOMALY_CODE}'
    ) THEN
        RAISE EXCEPTION 'Downgrade bloqueado: existem anomalias % registradas. Consulte: '
            'SELECT a.id FROM reconciliation_anomalies a JOIN anomaly_types t ON '
            't.id = a.anomaly_type_id WHERE t.code = ''%'';', '{_ANOMALY_CODE}', '{_ANOMALY_CODE}';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("posting_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("omie_conta_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_code", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("installment_number", sa.SmallInteger(), nullable=False),
        sa.Column("installment_total", sa.SmallInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("payable_key", sa.String(length=_KEY_LENGTH), nullable=False),
        sa.Column(
            "step",
            sa.String(length=_STEP_LENGTH),
            nullable=False,
            server_default=sa.text("'titulo'"),
        ),
        sa.Column("payable_id", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("claimed_by_file_entry_id", sa.UUID(), nullable=True),
        sa.Column("missing_anomaly_id", sa.UUID(), nullable=True),
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
            ["posting_id"], ["reconciliation_omie_postings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["claimed_by_file_entry_id"], ["reconciliation_file_entries.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["missing_anomaly_id"], ["reconciliation_anomalies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "posting_id", "installment_number", name="uq_recon_omie_installments_posting_number"
        ),
        sa.UniqueConstraint(
            "client_id", "payable_key", name="uq_recon_omie_installments_client_key"
        ),
        sa.UniqueConstraint(
            "claimed_by_file_entry_id", name="uq_recon_omie_installments_claimed_by"
        ),
        # Rótulos, não nomes finais: a NAMING_CONVENTION prefixa `ck_<tabela>_`.
        sa.CheckConstraint(sa.text(_STEP_CK), name=_STEP_CK_LABEL),
        sa.CheckConstraint(sa.text(_NUMBER_CK), name=_NUMBER_CK_LABEL),
        sa.CheckConstraint(sa.text(_AMOUNT_CK), name=_AMOUNT_CK_LABEL),
        sa.CheckConstraint(sa.text(_CLAIM_CK), name=_CLAIM_CK_LABEL),
    )
    op.create_index(f"ix_{_TABLE}_posting_id", _TABLE, ["posting_id"])
    op.create_index(f"ix_{_TABLE}_client_id", _TABLE, ["client_id"])
    op.create_index(
        "ix_recon_omie_installments_identity",
        _TABLE,
        ["client_id", "omie_conta_id", "supplier_code", "installment_total", "installment_number"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO anomaly_types
                (id, code, name, description, severity, active, created_at, updated_at)
            VALUES (gen_random_uuid(), :code, :name, :description, 'moderate', true, now(), now())
            ON CONFLICT (code) DO NOTHING
            """
        ).bindparams(code=_ANOMALY_CODE, name=_ANOMALY_NAME, description=_ANOMALY_DESCRIPTION)
    )


def downgrade() -> None:
    op.execute(sa.text(_ABORT_IF_INSTALLMENTS))
    op.execute(
        sa.text("DELETE FROM anomaly_types WHERE code = :code").bindparams(code=_ANOMALY_CODE)
    )
    op.drop_index("ix_recon_omie_installments_identity", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_client_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_posting_id", table_name=_TABLE)
    op.drop_table(_TABLE)
