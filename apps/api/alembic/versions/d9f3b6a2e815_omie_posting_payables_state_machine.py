"""omie_posting_payables_state_machine — a intenção de lançamento vira máquina de estados.

O que muda (86e3n70q0, bloco B), em `reconciliation_omie_postings`:
    - `kind` (VARCHAR(10), NOT NULL, server_default `'direto'`): `direto`
      (`IncluirLancCC`, Sprint 7) ou `provisao` (título no Contas a Pagar);
    - `step` (VARCHAR(12), nulável): o próximo passo da provisão (`fornecedor`,
      `titulo`, `baixa`, `concluido`); NULL no `direto`;
    - `supplier_code`, `payable_id`, `payment_id` (BIGINT, nuláveis): os ids que
      cada passo devolveu — só códigos do Omie, nenhum nome (§4.5);
    - três CHECKs: `ck_reconciliation_omie_postings_status_known`,
      `..._kind_shape` e `..._provisao_steps_coherent` (um passo só existe com o
      id do anterior gravado; `confirmed` é exatamente `concluido`).

Por quê: o lançamento como provisão deixa de ser atômico. Fornecedor, título e
baixa são até três escritas no Omie, e o sucesso parcial é estado legítimo; a
retomada lê o passo e os ids gravados e nunca refaz o que já tem id.

Sem backfill: toda linha existente é `direto` pelo `server_default` (é o que ela
sempre foi) e já cumpre os CHECKs. O status ganha CHECK depois de um PRÉ-CHECK
que aborta com a consulta que mostra o valor estranho, em vez de falhar no meio.

Downgrade ABORTA se existir linha `provisao`: derrubar `payable_id` apagaria a
memória de um título já criado na contabilidade do cliente, e o código anterior
leria a linha como `direto` — a provisão que falhou na baixa viraria um
lançamento direto novo, isto é, a MESMA compra duas vezes. Sem provisão, o
downgrade derruba CHECKs e colunas e nada se perde.

NB: nada de `app.*` importado no topo. Rótulos e predicados são SNAPSHOTS de
`db/models/reconciliation_omie_posting.py`;
`tests/unit/test_omie_payables_schema.py` compara as duas fontes.

Revision ID: d9f3b6a2e815
Revises: c4e8a1f7b3d2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d9f3b6a2e815"
down_revision = "c4e8a1f7b3d2"
branch_labels = None
depends_on = None

_TABLE = "reconciliation_omie_postings"
_KIND_LENGTH = 10
_STEP_LENGTH = 12
_KIND_DEFAULT = "direto"

# Snapshots de `db/models/reconciliation_omie_posting.py`.
_STATUS_CK_LABEL = "status_known"
_STATUS_CK = "status IN ('pending', 'confirmed', 'failed')"
_KIND_CK_LABEL = "kind_shape"
_KIND_CK = (
    "(kind = 'direto' AND step IS NULL AND supplier_code IS NULL "
    "AND payable_id IS NULL AND payment_id IS NULL) "
    "OR (kind = 'provisao' AND step IN ('fornecedor', 'titulo', 'baixa', 'concluido') "
    "AND omie_lancamento_id IS NULL)"
)
_STEPS_CK_LABEL = "provisao_steps_coherent"
_STEPS_CK = (
    "kind <> 'provisao' OR ("
    "(step <> 'titulo' OR supplier_code IS NOT NULL) "
    "AND (step NOT IN ('baixa', 'concluido') "
    "OR (supplier_code IS NOT NULL AND payable_id IS NOT NULL)) "
    "AND ((status = 'confirmed') = (step = 'concluido')))"
)

_ABORT_IF_UNKNOWN_STATUS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {_TABLE} WHERE NOT ({_STATUS_CK})) THEN
        RAISE EXCEPTION 'Upgrade bloqueado: % tem status fora de pending/confirmed/failed. '
            'Consulte: SELECT id, status FROM % WHERE status NOT IN '
            '(''pending'', ''confirmed'', ''failed'');', '{_TABLE}', '{_TABLE}';
    END IF;
END
$$;
"""

_ABORT_IF_PROVISAO = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {_TABLE} WHERE kind = 'provisao') THEN
        RAISE EXCEPTION 'Downgrade bloqueado: existem lançamentos como provisão no Omie '
            '(títulos já criados). Derrubar payable_id apagaria a memória deles e a versão '
            'anterior poderia lançar a mesma compra de novo. Consulte: SELECT id, step, '
            'payable_id, payment_id FROM % WHERE kind = ''provisao'';', '{_TABLE}';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(sa.text(_ABORT_IF_UNKNOWN_STATUS))
    op.add_column(
        _TABLE,
        sa.Column(
            "kind",
            sa.String(length=_KIND_LENGTH),
            nullable=False,
            server_default=sa.text(f"'{_KIND_DEFAULT}'"),
        ),
    )
    op.add_column(_TABLE, sa.Column("step", sa.String(length=_STEP_LENGTH), nullable=True))
    op.add_column(_TABLE, sa.Column("supplier_code", sa.BigInteger(), nullable=True))
    op.add_column(_TABLE, sa.Column("payable_id", sa.BigInteger(), nullable=True))
    op.add_column(_TABLE, sa.Column("payment_id", sa.BigInteger(), nullable=True))
    # Rótulos, não nomes finais: a NAMING_CONVENTION prefixa `ck_<tabela>_`.
    op.create_check_constraint(_STATUS_CK_LABEL, _TABLE, sa.text(_STATUS_CK))
    op.create_check_constraint(_KIND_CK_LABEL, _TABLE, sa.text(_KIND_CK))
    op.create_check_constraint(_STEPS_CK_LABEL, _TABLE, sa.text(_STEPS_CK))


def downgrade() -> None:
    op.execute(sa.text(_ABORT_IF_PROVISAO))
    op.drop_constraint(_STEPS_CK_LABEL, _TABLE, type_="check")
    op.drop_constraint(_KIND_CK_LABEL, _TABLE, type_="check")
    op.drop_constraint(_STATUS_CK_LABEL, _TABLE, type_="check")
    op.drop_column(_TABLE, "payment_id")
    op.drop_column(_TABLE, "payable_id")
    op.drop_column(_TABLE, "supplier_code")
    op.drop_column(_TABLE, "step")
    op.drop_column(_TABLE, "kind")
