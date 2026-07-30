"""sprint5: tenancy em users (scope + client_id) + papéis de cliente

Revision ID: d5c81a4e9b27
Revises: c4f1a8b62e93
Create Date: 2026-07-30 18:00:00.000000+00:00

Sprint 5 (R1) — BACK 05.1:

  Estende `users` com a noção de TENANT, sem criar segunda tabela nem segundo
  mecanismo de sessão (decisão fechada no PRD):
    - `scope`     : 'system' (equipe Hologram) | 'client' (usuário do cliente).
                    NOT NULL, server_default 'system'.
    - `client_id` : FK para `clients` (ON DELETE RESTRICT), NULÁVEL. É o tenant
                    do usuário quando `scope='client'`.

  Integridade NO BANCO (não só na aplicação), exigida pelo critério de aceite:
    ck_users_scope_client_id →
      (scope='client' AND client_id IS NOT NULL)
      OR (scope='system' AND client_id IS NULL)

  Backfill IDEMPOTENTE: todo usuário existente vira `scope='system'`,
  `client_id NULL` — NENHUM vira usuário de cliente. Reexecutar o UPDATE não
  muda nada (é convergente, não incremental).

  O enum de papel (`UserRole`) ganha `client_manager`/`client_operator` — mas
  `users.role` é `VARCHAR(20)` (não um ENUM do Postgres), então não há DDL de
  tipo a alterar aqui; a fonte única do enum é `app/db/models/user.py`.

  Reversível: o downgrade remove CHECK, FK, índice e as duas colunas. Nenhum
  usuário é perdido (só as colunas somem).

  NB: nada de `app.*` importado no topo — importar código de app constrói o
  Settings inteiro no import do módulo de migration (lição registrada).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5c81a4e9b27"
down_revision: str | None = "c4f1a8b62e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Espelha `app.db.models.user.SCOPE_CLIENT_ID_CHECK` / `SCOPE_CLIENT_ID_CK_LABEL`
# (copiado, não importado — ver NB do docstring). Um teste de integração compara
# as duas fontes para impedir drift silencioso.
#
# ⚠️ LABEL, não o nome final: o `alembic/env.py` passa `target_metadata`, então a
# NAMING_CONVENTION do `Base` também vale aqui — `ck` vira
# `ck_%(table_name)s_%(constraint_name)s`. Passar "ck_users_scope_client_id"
# produziria `ck_users_ck_users_scope_client_id` no banco.
SCOPE_CLIENT_ID_CK_LABEL = "scope_client_id"
SCOPE_CLIENT_ID_CHECK = (
    "(scope = 'client' AND client_id IS NOT NULL) "
    "OR (scope = 'system' AND client_id IS NULL)"
)
CLIENT_ID_INDEX = "ix_users_client_id"
CLIENT_ID_FK = "fk_users_client_id_clients"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="system"),
    )
    op.add_column("users", sa.Column("client_id", sa.UUID(), nullable=True))

    # Backfill idempotente: convergente (não incremental) — reexecutar é no-op.
    # Roda ANTES da CHECK constraint para que uma base com dado inconsistente
    # (não deveria existir: as colunas nascem agora) seja normalizada, e não
    # faça a criação da constraint falhar.
    op.execute(
        sa.text(
            "UPDATE users SET scope = 'system' "
            "WHERE scope IS NULL OR scope NOT IN ('system', 'client')"
        )
    )
    op.execute(
        sa.text("UPDATE users SET client_id = NULL WHERE scope = 'system' AND client_id IS NOT NULL")
    )

    # Índice: toda listagem de usuários por tenant filtra por client_id.
    op.create_index(CLIENT_ID_INDEX, "users", ["client_id"])
    op.create_foreign_key(
        CLIENT_ID_FK,
        "users",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(SCOPE_CLIENT_ID_CK_LABEL, "users", sa.text(SCOPE_CLIENT_ID_CHECK))


def downgrade() -> None:
    # LABEL também aqui: o `drop_constraint` passa pela mesma NAMING_CONVENTION.
    op.drop_constraint(SCOPE_CLIENT_ID_CK_LABEL, "users", type_="check")
    op.drop_constraint(CLIENT_ID_FK, "users", type_="foreignkey")
    op.drop_index(CLIENT_ID_INDEX, table_name="users")
    op.drop_column("users", "client_id")
    op.drop_column("users", "scope")
