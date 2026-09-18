"""s8_organizations — camada de organizações: tabela, colunas de org, CHECK ternário (86e36ec7p).

Plataforma → Organizações (BPOs/escritórios) → Clientes finais. Esta migration é
a FUNDAÇÃO de dados do épico 86e36ec0q, e por si só não muda comportamento
nenhum: tudo que existe passa a pertencer à organização **Hologram**, e o
sistema segue idêntico até a primeira organização nova ser cadastrada.

O que muda:
    - tabela `organizations` (id, name UNIQUE, active, timestamps) + a linha
      Hologram, com id FIXO (`_HOLOGRAM_ID`, snapshot de
      `app.db.models.organization.HOLOGRAM_ORGANIZATION_ID`);
    - `clients.organization_id` NOT NULL, FK RESTRICT, índice;
    - `users.organization_id` nullable (a plataforma não tem org), FK RESTRICT,
      índice; para `scope='client'` recebe a org do próprio cliente;
    - `client_categories.organization_id` NOT NULL, FK RESTRICT; a UNIQUE global
      `uq_client_categories_name` vira `(organization_id, name)` — cada BPO
      categoriza os próprios clientes (decisão D3);
    - `access_audit.actor_organization_id` nullable, sem FK (padrão da tabela),
      sem backfill;
    - o CHECK binário `ck_users_scope_client_id` dá lugar ao ternário
      `ck_users_scope_consistency`, que cruza scope x role x organization_id x
      client_id (a forma `platform` fica aceita pelo banco desde já; quem a
      produz é a task de authz core).

Backfill "tudo é Hologram" — por CATÁLOGO, como o `is_primary` da `6bb85e6b7d72`:
as três colunas nascem com `server_default` = id da Hologram, então toda linha
existente lê o valor sem reescrita, e o mesmo default cobre a janela de deploy
(a API ANTIGA insere sem o campo e a linha nasce da Hologram, não órfã). O único
UPDATE real é o dos usuários de cliente, que recebem a org do cliente (hoje
igual à Hologram; convergente para qualquer estado). Antes de trocar o CHECK,
uma pré-checagem em plpgsql ABORTA com a consulta de diagnóstico se alguma
linha de `users` não couber no predicado novo — em vez de falhar cega no
`ADD CONSTRAINT`.

Downgrade: só cabe se o banco ainda for "uma organização só" — nenhuma org
além da Hologram, nenhum usuário de plataforma, nenhuma linha apontando para
outra org. Fora disso a migration ABORTA com a consulta que mostra o que
sobraria órfão: apagar uma organização é decisão de dado, não de schema.

NB: nada de `app.*` importado no topo — importar código de app constrói o
Settings inteiro no import do módulo de migration.

Revision ID: 3e8f1a6c9d24
Revises: 6bb85e6b7d72
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3e8f1a6c9d24"
down_revision = "6bb85e6b7d72"
branch_labels = None
depends_on = None

# --- Snapshots congelados do modelo (copiados, não importados) ---------------
# `tests/unit/test_organization_schema.py` compara cada um com a fonte.
_HOLOGRAM_ID = "0706eeb5-9718-4d03-bcda-ef615789e6ac"
_HOLOGRAM_NAME = "Hologram"
_ORG_DEFAULT = f"'{_HOLOGRAM_ID}'::uuid"

_UQ_ORG_NAME = "uq_organizations_name"
_FK_CLIENTS_ORG = "fk_clients_organization_id_organizations"
_IX_CLIENTS_ORG = "ix_clients_organization_id"
_FK_USERS_ORG = "fk_users_organization_id_organizations"
_IX_USERS_ORG = "ix_users_organization_id"
_FK_CATEGORIES_ORG = "fk_client_categories_organization_id_organizations"
_UQ_CATEGORIES_NAME_OLD = "uq_client_categories_name"
_UQ_CATEGORIES_ORG_NAME = "uq_client_categories_organization_id_name"

# ⚠️ LABEL, não o nome final (a NAMING_CONVENTION prefixa `ck_users_`) — ver
# `d5c81a4e9b27`. O antigo é recriado tal qual no downgrade.
_OLD_CK_LABEL = "scope_client_id"
_OLD_CK = "(scope = 'client' AND client_id IS NOT NULL) OR (scope = 'system' AND client_id IS NULL)"
_NEW_CK_LABEL = "scope_consistency"
_NEW_CK = (
    "(scope = 'platform' AND role = 'platform_admin' "
    "AND organization_id IS NULL AND client_id IS NULL) "
    "OR (scope = 'system' AND role IN ('admin', 'manager') "
    "AND organization_id IS NOT NULL AND client_id IS NULL) "
    "OR (scope = 'client' AND role IN ('client_manager', 'client_operator') "
    "AND organization_id IS NOT NULL AND client_id IS NOT NULL)"
)

# O predicado dentro da MENSAGEM do RAISE: aspas simples dobradas, senão o
# literal plpgsql termina no primeiro `'platform'`.
_NEW_CK_IN_MESSAGE = _NEW_CK.replace("'", "''")

# Pré-checagem do upgrade: uma linha fora do predicado faria o `ADD CONSTRAINT`
# falhar com "check constraint is violated by some row" — sem dizer qual.
# S608: só constantes deste módulo entram no f-string — nenhum dado externo.
_ABORT_UPGRADE_IF_INCONSISTENT = f"""
DO $$
DECLARE
    bad_count integer;
BEGIN
    SELECT count(*) INTO bad_count FROM users WHERE NOT ({_NEW_CK});

    IF bad_count > 0 THEN
        RAISE EXCEPTION
            'Upgrade bloqueado: % usuario(s) com scope/role/organization_id/client_id '
            'inconsistentes para o CHECK ternario. Corrija antes (SELECT id, scope, '
            'role, organization_id, client_id FROM users WHERE NOT ({_NEW_CK_IN_MESSAGE})) — '
            'decidir o escopo de um usuario e decisao de dado.',
            bad_count;
    END IF;
END $$;
"""  # noqa: S608

# Guarda do downgrade: a forma antiga do schema só cabe "uma organização só".
_ABORT_DOWNGRADE_IF_MULTI_ORG = f"""
DO $$
DECLARE
    org_count integer;
    platform_count integer;
    foreign_count integer;
BEGIN
    SELECT count(*) INTO org_count FROM organizations;
    SELECT count(*) INTO platform_count FROM users WHERE scope = 'platform';
    SELECT
        (SELECT count(*) FROM clients WHERE organization_id <> '{_HOLOGRAM_ID}'::uuid)
        + (SELECT count(*) FROM users
           WHERE organization_id IS NOT NULL AND organization_id <> '{_HOLOGRAM_ID}'::uuid)
        + (SELECT count(*) FROM client_categories
           WHERE organization_id <> '{_HOLOGRAM_ID}'::uuid)
    INTO foreign_count;

    IF org_count > 1 OR platform_count > 0 OR foreign_count > 0 THEN
        RAISE EXCEPTION
            'Downgrade bloqueado: % organizacao(oes), % usuario(s) de plataforma e '
            '% linha(s) fora da Hologram. A forma antiga do schema so cabe UMA '
            'organizacao. Veja o que sobraria orfao (SELECT id, name FROM organizations '
            'WHERE id <> ''{_HOLOGRAM_ID}''; SELECT id, email FROM users WHERE '
            'scope = ''platform'' OR organization_id <> ''{_HOLOGRAM_ID}''; SELECT id, '
            'name FROM clients WHERE organization_id <> ''{_HOLOGRAM_ID}'') — apagar '
            'uma organizacao e decisao de dado.',
            org_count, platform_count, foreign_count;
    END IF;
END $$;
"""  # noqa: S608


def upgrade() -> None:
    # 1. A tabela e a primeira organização.
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name=_UQ_ORG_NAME),
    )
    # Idempotente: reexecutar (ou um banco onde a linha já exista) não duplica.
    op.execute(
        sa.text(
            "INSERT INTO organizations (id, name, active, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), :name, true, now(), now()) "
            "ON CONFLICT DO NOTHING"
        ).bindparams(id=_HOLOGRAM_ID, name=_HOLOGRAM_NAME)
    )

    # 2. clients → Hologram. `server_default` = backfill por catálogo (fast
    # default do Postgres: linha existente lê o valor sem reescrita) + janela
    # de deploy (API antiga insere sem o campo).
    op.add_column(
        "clients",
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text(_ORG_DEFAULT),
        ),
    )
    op.create_index(_IX_CLIENTS_ORG, "clients", ["organization_id"])
    op.create_foreign_key(
        _FK_CLIENTS_ORG,
        "clients",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 3. users → Hologram (staff) / org do próprio cliente (usuário de cliente).
    op.add_column(
        "users",
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=True,
            server_default=sa.text(_ORG_DEFAULT),
        ),
    )
    # Convergente: usuário de cliente recebe a org do cliente, qualquer que
    # seja; reexecutar é no-op. Hoje toda org é a Hologram — o UPDATE existe
    # para o predicado ser verdadeiro em qualquer estado, não só no atual.
    op.execute(
        sa.text(
            "UPDATE users AS u SET organization_id = c.organization_id "
            "FROM clients AS c "
            "WHERE u.client_id = c.id AND u.scope = 'client' "
            "AND u.organization_id IS DISTINCT FROM c.organization_id"
        )
    )
    op.create_index(_IX_USERS_ORG, "users", ["organization_id"])
    op.create_foreign_key(
        _FK_USERS_ORG,
        "users",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 4. O CHECK vira ternário — depois de provar que toda linha cabe nele.
    op.execute(sa.text(_ABORT_UPGRADE_IF_INCONSISTENT))
    op.drop_constraint(_OLD_CK_LABEL, "users", type_="check")
    op.create_check_constraint(_NEW_CK_LABEL, "users", sa.text(_NEW_CK))

    # 5. client_categories → por organização (D3).
    op.add_column(
        "client_categories",
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text(_ORG_DEFAULT),
        ),
    )
    op.create_foreign_key(
        _FK_CATEGORIES_ORG,
        "client_categories",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(_UQ_CATEGORIES_NAME_OLD, "client_categories", type_="unique")
    op.create_unique_constraint(
        _UQ_CATEGORIES_ORG_NAME, "client_categories", ["organization_id", "name"]
    )

    # 6. Trilha: de que organização veio uma negação cross-org.
    op.add_column(
        "access_audit",
        sa.Column("actor_organization_id", sa.UUID(), nullable=True),
    )


def downgrade() -> None:
    op.execute(sa.text(_ABORT_DOWNGRADE_IF_MULTI_ORG))

    op.drop_column("access_audit", "actor_organization_id")

    op.drop_constraint(_UQ_CATEGORIES_ORG_NAME, "client_categories", type_="unique")
    op.create_unique_constraint(_UQ_CATEGORIES_NAME_OLD, "client_categories", ["name"])
    op.drop_constraint(_FK_CATEGORIES_ORG, "client_categories", type_="foreignkey")
    op.drop_column("client_categories", "organization_id")

    op.drop_constraint(_NEW_CK_LABEL, "users", type_="check")
    op.create_check_constraint(_OLD_CK_LABEL, "users", sa.text(_OLD_CK))
    op.drop_constraint(_FK_USERS_ORG, "users", type_="foreignkey")
    op.drop_index(_IX_USERS_ORG, table_name="users")
    op.drop_column("users", "organization_id")

    op.drop_constraint(_FK_CLIENTS_ORG, "clients", type_="foreignkey")
    op.drop_index(_IX_CLIENTS_ORG, table_name="clients")
    op.drop_column("clients", "organization_id")

    op.drop_table("organizations")
