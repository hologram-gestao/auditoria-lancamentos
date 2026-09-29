"""s13_export_layouts — layouts de exportação do arquivo contábil, por organização e versionados (BACK 13.2 — R1).

O que muda:
    - tabela `export_layouts` — identidade do layout na organização (nome, sistema alvo),
      `UNIQUE(organization_id, name)`, FK para a organização (RESTRICT) e autoria RESTRICT;
    - tabela `export_layout_versions` — a DEFINIÇÃO em JSONB, uma linha IMUTÁVEL por
      versão, `UNIQUE(layout_id, version)`, `CHECK (version >= 1)`, FK para o layout
      (RESTRICT) e autoria RESTRICT.

Nenhum campo cifrado (layout é configuração da organização, não dado do cliente final).
Sem seed: o modelo Domínio é dado declarado no CÓDIGO, e a organização cria o layout
dela a partir do modelo por uma rota. Aditivo. Downgrade REAL: `DROP TABLE` das duas
(perde-se a configuração de layouts — refeita pela tela).

NB: nada de `app.*` importado no topo (ver `d5c81a4e9b27`). Constantes abaixo são
SNAPSHOTS copiados do modelo; `tests/unit/test_export_layout_schema.py` compara as fontes.

Revision ID: b3d9e5f17a20
Revises: a8c4e7d25f19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b3d9e5f17a20"
down_revision = "a8c4e7d25f19"
branch_labels = None
depends_on = None

_LAYOUTS = "export_layouts"
_VERSIONS = "export_layout_versions"

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_NAME_MAX = 120
_TARGET_SYSTEM_MAX = 60

_UQ_ORG_NAME = "uq_export_layouts_organization_id_name"
_UQ_VERSION = "uq_export_layout_versions_layout_id_version"
_CK_VERSION_LABEL = "version_positive"
_CK_VERSION_SQL = "version >= 1"
_FK_ORG = "fk_export_layouts_organization_id_organizations"
_FK_CREATED_BY = "fk_export_layouts_created_by_users"
_FK_LAYOUT = "fk_export_layout_versions_layout_id_export_layouts"
_FK_AUTHOR = "fk_export_layout_versions_author_id_users"


def upgrade() -> None:
    op.create_table(
        _LAYOUTS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=_NAME_MAX), nullable=False),
        sa.Column("target_system", sa.String(length=_TARGET_SYSTEM_MAX), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
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
            ["organization_id"], ["organizations.id"], name=_FK_ORG, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=_FK_CREATED_BY, ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_LAYOUTS}"),
        sa.UniqueConstraint("organization_id", "name", name=_UQ_ORG_NAME),
    )
    op.create_table(
        _VERSIONS,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("layout_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["layout_id"], [f"{_LAYOUTS}.id"], name=_FK_LAYOUT, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=_FK_AUTHOR, ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_VERSIONS}"),
        sa.UniqueConstraint("layout_id", "version", name=_UQ_VERSION),
        # O rótulo vira `ck_export_layout_versions_version_positive` pela naming
        # convention do `target_metadata` (precedente `e6b2c9d47f13`).
        sa.CheckConstraint(_CK_VERSION_SQL, name=_CK_VERSION_LABEL),
    )


def downgrade() -> None:
    op.drop_table(_VERSIONS)
    op.drop_table(_LAYOUTS)
