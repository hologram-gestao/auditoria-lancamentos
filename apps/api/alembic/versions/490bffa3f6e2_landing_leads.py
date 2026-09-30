"""landing_leads — tabela `leads` para o formulário de contato da landing pública.

O que muda:
    - cria `leads` (86e3fr9ut): nome, e-mail, empresa, WhatsApp e mensagem do
      formulário, o momento e a versão do consentimento LGPD, a origem e o carimbo
      de quando o aviso no Slack foi aceito;
    - cria o índice `ix_leads_email_lower_created_at` em `(lower(email), created_at)`,
      que atende o limite de envios por e-mail numa janela de 24 h.

Por quê: a raiz do front vira uma página pública que capta contato. O lead é dado
de PROSPECT, sem tenant: não há FK para `clients`/`organizations`/`users`, e ele fica
em claro por decisão do Pedro (28/09/2026), guardando só o mínimo (sem IP, sem user
agent). Ver o docstring de `app/db/models/lead.py`.

Sem backfill: a tabela nasce vazia.

Downgrade REAL: derruba o índice e a tabela. Os leads recebidos até ali SOMEM; quem
precisar deles exporta antes (`COPY leads TO ...`). É o custo de desfazer uma
tabela nova, e não há forma antiga para onde movê-los.

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Os tamanhos abaixo são
SNAPSHOTS das constantes de `db/models/lead.py`; `tests/unit/test_leads.py` compara
as duas fontes.

Revision ID: 490bffa3f6e2
Revises: a4f6c1b83d59
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "490bffa3f6e2"
down_revision = "a4f6c1b83d59"
branch_labels = None
depends_on = None

_TABLE = "leads"
_INDEX = "ix_leads_email_lower_created_at"

# Snapshots de `db/models/lead.py`.
_NAME_MAX = 120
_EMAIL_MAX = 254
_COMPANY_MAX = 120
_WHATSAPP_MAX = 20
_MESSAGE_MAX = 1000
_CONSENT_VERSION_MAX = 20
_SOURCE_MAX = 30
_SOURCE_LANDING = "landing"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=_NAME_MAX), nullable=False),
        sa.Column("email", sa.String(length=_EMAIL_MAX), nullable=False),
        sa.Column("company", sa.String(length=_COMPANY_MAX), nullable=True),
        sa.Column("whatsapp", sa.String(length=_WHATSAPP_MAX), nullable=True),
        sa.Column("message", sa.String(length=_MESSAGE_MAX), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_text_version", sa.String(length=_CONSENT_VERSION_MAX), nullable=False),
        sa.Column(
            "source",
            sa.String(length=_SOURCE_MAX),
            server_default=_SOURCE_LANDING,
            nullable=False,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )
    op.create_index(
        _INDEX,
        _TABLE,
        [sa.text("lower(email)"), "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
