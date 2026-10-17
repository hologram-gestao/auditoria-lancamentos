"""accounting_chart_absent_since — a reimportação do plano contábil deixa de inativar (86e3qhhrm).

O que muda:
    - `client_accounting_accounts.absent_since` (TIMESTAMPTZ, nullable): o instante da
      importação em que a conta deixou de vir na planilha. Nula = a conta veio na
      última importação (ou nunca houve importação depois de ela existir). Limpa
      quando a conta volta numa planilha; enquanto ela segue ausente, a data é a da
      PRIMEIRA importação em que sumiu.

Por quê: até aqui a conta que sumia da planilha virava INATIVA, e a inativa deixa de
receber decisão nova e de ser conta do banco. No processo do escritório isso é
perigoso: o plano exportado do Domínio pode vir recortado, e uma reimportação parcial
desligava contas em uso (reunião com o Gabriel, 14/10/2026; decisão do Pedro). Agora a
reimportação só adiciona e atualiza; a ausência vira um SELO (esta coluna) e a
situação da conta só muda pela edição manual.

Sem backfill: nenhuma conta existente sabe em que importação sumiu. As que a regra
antiga inativou continuam inativas (não dá para separar das inativadas à mão) e se
reativam pela edição.

Downgrade REAL: derruba a coluna. O código anterior volta a inativar as ausentes.

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`).

Revision ID: a94a539e1d34
Revises: d9f3b6a2e815
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a94a539e1d34"
down_revision = "d9f3b6a2e815"
branch_labels = None
depends_on = None

_TABLE = "client_accounting_accounts"
_COLUMN = "absent_since"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
