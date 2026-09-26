"""s12_client_mapping — catálogo de destinos/alvos e o schema INTEIRO do de-para (BACK 12.3).

Uma migration só, para que as tasks seguintes (12.4 a 12.6) escrevam serviço e rota
sem abrir um segundo head:

    - `mapping_destinations` — destino por ORGANIZAÇÃO, `UNIQUE(organization_id,
      destination_type)`; o tipo é SLUG com CHECK de FORMATO (não de vocabulário: o
      sexto tipo é cadastro, não migração);
    - `mapping_targets` — alvo por destino, `UNIQUE(destination_id, code)`;
    - `client_mapping_decisions` — a chave `(cliente, tipo de origem, categoria,
      destino)` única por `effective_from`; `nao_mapear` é linha, "sem decisão" é
      ausência de linha; CHECK de coerência alvo × tipo; FK do alvo RESTRICT;
    - `client_mapping_materializations` + `_items` — resultado imutável por
      `(cliente, destino, competência, versão)`; itens em SNAPSHOT, sem FK para a
      base de movimentos.

**Seed:** os cinco destinos em toda organização existente, idempotente
(`ON CONFLICT DO NOTHING` sobre a UNIQUE). Organização criada depois ganha os
mesmos cinco pelo serviço de organizações.

Nenhuma coluna de nome de CATEGORIA (§4.5). O nome de ALVO é dado da organização
(o plano de demonstração do escritório), não do cliente final.

Downgrade REAL: derruba as cinco tabelas na ordem inversa das FKs. Perde-se o
de-para e as materializações — por isso o downgrade só é seguro antes de qualquer
materialização entregue (a Sprint 13 ainda não consome).

NB: nada de `app.*` importado — snapshots copiados; `tests/unit/test_client_mapping_schema.py`
compara as duas fontes.

Revision ID: e6b2c9d47f13
Revises: d3a8f5c21e47
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e6b2c9d47f13"
down_revision = "d3a8f5c21e47"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
_DESTINATION_TYPE_MAX = 60
_DESTINATION_NAME_MAX = 120
_TARGET_CODE_MAX = 50
_TARGET_NAME_MAX = 200
_SOURCE_TYPE_MAX = 30
_SOURCE_ID_MAX = 60
_CATEGORY_CODE_MAX = 50
_REF_MAX = 60

_UQ_DESTINATION = "uq_mapping_destinations_organization_id_destination_type"
_UQ_TARGET = "uq_mapping_targets_destination_id_code"
_UQ_DECISION = "uq_client_mapping_decisions_key_effective_from"
_IX_DECISION = "ix_client_mapping_decisions_client_id_destination_id"
_UQ_MATERIALIZATION = "uq_client_mapping_materializations_version"
# Nome explícito: pela convenção passariam do teto de 63 caracteres do Postgres.
_FK_MATERIALIZATION_DESTINATION = "fk_client_mapping_materializations_destination_id"
_FK_ITEM_MATERIALIZATION = "fk_client_mapping_materialization_items_materialization_id"
_IX_ITEM = "ix_client_mapping_materialization_items_materialization_id"

_CK_DESTINATION_TYPE_LABEL = "destination_type_slug"
_CK_DESTINATION_TYPE = "destination_type ~ '^[a-z][a-z0-9_]{0,59}$'"
_CK_DECISION_TYPE_LABEL = "decision_type"
_CK_DECISION_TYPE = "decision_type IN ('alvo', 'nao_mapear')"
_CK_ORIGIN_LABEL = "origin"
_CK_ORIGIN = "origin IN ('herdada', 'confirmada')"
_CK_DECISION_TARGET_LABEL = "decision_target_coherent"
_CK_DECISION_TARGET = (
    "(decision_type = 'alvo' AND target_id IS NOT NULL) "
    "OR (decision_type = 'nao_mapear' AND target_id IS NULL)"
)
_CK_EFFECTIVE_FROM_LABEL = "effective_from_first_day"
_CK_EFFECTIVE_FROM = "EXTRACT(DAY FROM effective_from) = 1"
_CK_COMPETENCE_LABEL = "competence_first_day"
_CK_COMPETENCE = "EXTRACT(DAY FROM competence) = 1"
_CK_VERSION_LABEL = "version_positive"
_CK_VERSION = "version >= 1"
_CK_SITUATION_LABEL = "situation"
_CK_SITUATION = "situation IN ('alvo', 'nao_mapear', 'sem_decisao', 'sem_categoria')"
_CK_ITEM_TARGET_LABEL = "item_target_coherent"
_CK_ITEM_TARGET = (
    "(situation = 'alvo' AND target_code IS NOT NULL) "
    "OR (situation <> 'alvo' AND target_code IS NULL)"
)

#: Os cinco destinos seedados — snapshot de `DEFAULT_DESTINATION_TYPES`.
_DEFAULT_DESTINATIONS = (
    ("demonstrativo_gerencial", "Demonstrativo gerencial"),
    ("demonstrativo_contabil", "Demonstrativo contábil"),
    ("conta_contabil", "Conta contábil"),
    ("natureza_fiscal", "Natureza fiscal"),
    ("fluxo_de_caixa", "Fluxo de caixa"),
)

_TABLES_IN_DROP_ORDER = (
    "client_mapping_materialization_items",
    "client_mapping_materializations",
    "client_mapping_decisions",
    "mapping_targets",
    "mapping_destinations",
)


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "mapping_destinations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("destination_type", sa.String(length=_DESTINATION_TYPE_MAX), nullable=False),
        sa.Column("name", sa.String(length=_DESTINATION_NAME_MAX), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_mapping_destinations_organization_id_organizations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_destinations"),
        sa.UniqueConstraint("organization_id", "destination_type", name=_UQ_DESTINATION),
        sa.CheckConstraint(_CK_DESTINATION_TYPE, name=_CK_DESTINATION_TYPE_LABEL),
    )

    op.create_table(
        "mapping_targets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("destination_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=_TARGET_CODE_MAX), nullable=False),
        sa.Column("name", sa.String(length=_TARGET_NAME_MAX), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["mapping_destinations.id"],
            name="fk_mapping_targets_destination_id_mapping_destinations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_targets"),
        sa.UniqueConstraint("destination_id", "code", name=_UQ_TARGET),
    )

    op.create_table(
        "client_mapping_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=_SOURCE_TYPE_MAX), nullable=False),
        sa.Column("category_code", sa.String(length=_CATEGORY_CODE_MAX), nullable=False),
        sa.Column("destination_id", sa.UUID(), nullable=False),
        sa.Column("decision_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_mapping_decisions_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["mapping_destinations.id"],
            name="fk_client_mapping_decisions_destination_id_mapping_destinations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["mapping_targets.id"],
            name="fk_client_mapping_decisions_target_id_mapping_targets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_client_mapping_decisions_author_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_mapping_decisions"),
        sa.UniqueConstraint(
            "client_id",
            "source_type",
            "category_code",
            "destination_id",
            "effective_from",
            name=_UQ_DECISION,
        ),
        sa.CheckConstraint(_CK_DECISION_TYPE, name=_CK_DECISION_TYPE_LABEL),
        sa.CheckConstraint(_CK_ORIGIN, name=_CK_ORIGIN_LABEL),
        sa.CheckConstraint(_CK_DECISION_TARGET, name=_CK_DECISION_TARGET_LABEL),
        sa.CheckConstraint(_CK_EFFECTIVE_FROM, name=_CK_EFFECTIVE_FROM_LABEL),
    )
    # À mão: o autogenerate não gera índice composto de `__table_args__`.
    op.create_index(_IX_DECISION, "client_mapping_decisions", ["client_id", "destination_id"])

    op.create_table(
        "client_mapping_materializations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("destination_id", sa.UUID(), nullable=False),
        sa.Column("destination_type", sa.String(length=_DESTINATION_TYPE_MAX), nullable=False),
        sa.Column("competence", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("decisions_used", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mapped_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("mapped_count", sa.Integer(), nullable=False),
        sa.Column("not_mapped_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("not_mapped_count", sa.Integer(), nullable=False),
        sa.Column("undecided_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("undecided_count", sa.Integer(), nullable=False),
        sa.Column("uncategorized_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("uncategorized_count", sa.Integer(), nullable=False),
        sa.Column("undecided_categories", sa.Integer(), nullable=False),
        sa.Column(
            "partial_coverage_confirmed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_mapping_materializations_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["mapping_destinations.id"],
            name=_FK_MATERIALIZATION_DESTINATION,
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_client_mapping_materializations_author_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_mapping_materializations"),
        sa.UniqueConstraint(
            "client_id", "destination_id", "competence", "version", name=_UQ_MATERIALIZATION
        ),
        sa.CheckConstraint(_CK_COMPETENCE, name=_CK_COMPETENCE_LABEL),
        sa.CheckConstraint(_CK_VERSION, name=_CK_VERSION_LABEL),
    )

    op.create_table(
        "client_mapping_materialization_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("materialization_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(length=_SOURCE_TYPE_MAX), nullable=False),
        sa.Column("source_movement_id", sa.String(length=_SOURCE_ID_MAX), nullable=False),
        sa.Column("source_account_id", sa.String(length=_REF_MAX), nullable=True),
        sa.Column("movement_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("category_code", sa.String(length=_CATEGORY_CODE_MAX), nullable=True),
        sa.Column("situation", sa.String(length=20), nullable=False),
        sa.Column("target_code", sa.String(length=_TARGET_CODE_MAX), nullable=True),
        sa.Column("decision_effective_from", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["materialization_id"],
            ["client_mapping_materializations.id"],
            name=_FK_ITEM_MATERIALIZATION,
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_mapping_materialization_items_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_mapping_materialization_items"),
        sa.CheckConstraint(_CK_SITUATION, name=_CK_SITUATION_LABEL),
        sa.CheckConstraint(_CK_ITEM_TARGET, name=_CK_ITEM_TARGET_LABEL),
    )
    op.create_index(_IX_ITEM, "client_mapping_materialization_items", ["materialization_id"])

    # Seed IDEMPOTENTE dos cinco destinos em toda organização existente. Os
    # valores vão como bind params (`op.execute` + `bindparams` funciona também no
    # modo offline `--sql`); o SQL montado só carrega os NOMES dos placeholders.
    placeholders = ", ".join(f"(:t{i}, :n{i})" for i in range(len(_DEFAULT_DESTINATIONS)))
    params: dict[str, str] = {}
    for i, (destination_type, name) in enumerate(_DEFAULT_DESTINATIONS):
        params[f"t{i}"] = destination_type
        params[f"n{i}"] = name
    seed_sql = (
        "INSERT INTO mapping_destinations (id, organization_id, destination_type, name) "
        "SELECT gen_random_uuid(), o.id, v.destination_type, v.name "
        "FROM organizations o CROSS JOIN (VALUES " + placeholders + ") "
        "AS v(destination_type, name) "
        "ON CONFLICT ON CONSTRAINT " + _UQ_DESTINATION + " DO NOTHING"
    )
    op.execute(sa.text(seed_sql).bindparams(**params))


def downgrade() -> None:
    op.drop_index(_IX_ITEM, table_name="client_mapping_materialization_items")
    op.drop_index(_IX_DECISION, table_name="client_mapping_decisions")
    for table in _TABLES_IN_DROP_ORDER:
        op.drop_table(table)
