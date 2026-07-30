"""Round-trip das migrations Alembic (Sprint 5 — BACK 05.1 / 05.2).

Os testes de schema usam `Base.metadata.create_all` (conftest), o que NÃO exercita
as migrations. Este módulo fecha esse buraco: roda `upgrade head → downgrade -1 →
upgrade head` num banco descartável, provando que a migration da sprint é
reversível de verdade e que o backfill é idempotente.

Roda num banco PRÓPRIO (criado no mesmo container do `pg_container`) para não
colidir com o schema que o `db_engine` monta via `create_all`.

Os testes aqui são **síncronos** de propósito: o `alembic/env.py` chama
`asyncio.run(...)` internamente, o que estouraria dentro de um teste async
(event loop já rodando).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.core.config import get_settings

pytestmark = pytest.mark.integration

# tests/integration/test_migrations.py → apps/api
API_ROOT = Path(__file__).resolve().parents[2]

ROUNDTRIP_DB = "alembic_roundtrip"


@pytest.fixture
def migrations_db_url(db_url: str) -> Iterator[str]:
    """Cria (e derruba) um banco vazio dedicado ao round-trip das migrations."""
    admin_url = db_url
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{ROUNDTRIP_DB}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{ROUNDTRIP_DB}"'))
    try:
        yield admin_url.rsplit("/", 1)[0] + f"/{ROUNDTRIP_DB}"
    finally:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{ROUNDTRIP_DB}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture
def alembic_cfg(migrations_db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """Config do Alembic apontando para o banco descartável.

    O `alembic/env.py` lê a URL das Settings (`get_settings()`), não do
    `alembic.ini` — por isso a injeção é via env var + limpeza do cache do
    `lru_cache`, e não `set_main_option("sqlalchemy.url", ...)`.
    """
    monkeypatch.setenv("DATABASE_URL", migrations_db_url)
    get_settings.cache_clear()

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    try:
        yield cfg
    finally:
        get_settings.cache_clear()


def _scalar(url: str, sql: str, **params: object) -> object:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text(sql), params).scalar()
    finally:
        engine.dispose()


def _execute(url: str, sql: str, **params: object) -> None:
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(sql), params)
    finally:
        engine.dispose()


# Revisões da Sprint 5, da mais antiga para a mais nova. Downgrade percorre ao
# contrário. Referenciar por ID (e não por "-1") mantém o teste correto quando
# uma migration nova entrar por cima.
S5_TENANCY_REV = "d5c81a4e9b27"
S5_AUDIT_ACTOR_REV = "e9a4b71c3d68"

_INSERT_LEGACY_USER = (
    "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
    "created_at, updated_at) VALUES (gen_random_uuid(), 'Legado', "
    "'legado-rt@hologram.com.br', 'x', 'manager', true, 'system', now(), now())"
)


def _columns(url: str, table: str, *cols: str) -> object:
    placeholders = ", ".join(f"'{c}'" for c in cols)
    return _scalar(
        url,
        "SELECT count(*) FROM information_schema.columns "
        f"WHERE table_name = '{table}' AND column_name IN ({placeholders})",
    )


class TestMigrationRoundTrip:
    def test_upgrade_downgrade_upgrade(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        """As DUAS migrations da sprint sobem, descem e sobem sem perder usuário."""
        url = migrations_db_url

        command.upgrade(alembic_cfg, "head")

        # Um usuário "legado" (como os que já existem na base real) antes do downgrade.
        _execute(url, _INSERT_LEGACY_USER)

        # Desce a da auditoria: as colunas de ator somem, a de tenancy fica.
        command.downgrade(alembic_cfg, S5_TENANCY_REV)
        assert _columns(url, "access_audit", "user_scope", "actor_client_id") == 0
        assert _columns(url, "users", "scope", "client_id") == 2

        # Desce a de tenancy: as colunas somem, o usuário permanece.
        command.downgrade(alembic_cfg, "-1")
        assert _scalar(url, "SELECT count(*) FROM users") == 1
        assert _columns(url, "users", "scope", "client_id") == 0

        command.upgrade(alembic_cfg, "head")

        # Backfill: o usuário pré-existente volta como 'system', sem tenant.
        assert _scalar(url, "SELECT count(*) FROM users") == 1
        assert (
            _scalar(
                url,
                "SELECT count(*) FROM users WHERE scope = 'system' AND client_id IS NULL",
            )
            == 1
        )
        assert _columns(url, "access_audit", "user_scope", "actor_client_id") == 2

    def test_check_constraint_existe_apos_upgrade(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """A integridade scope/client_id é do BANCO, não só da aplicação."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")

        assert (
            _scalar(
                url,
                "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_users_scope_client_id'",
            )
            == 1
        )
        assert (
            _scalar(
                url,
                "SELECT count(*) FROM pg_indexes "
                "WHERE tablename = 'users' AND indexname = 'ix_users_client_id'",
            )
            == 1
        )

        with pytest.raises(sa.exc.IntegrityError):
            _execute(
                url,
                "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
                "created_at, updated_at) VALUES (gen_random_uuid(), 'Bad', "
                "'bad-rt@austral.com.br', 'x', 'client_operator', true, 'client', now(), now())",
            )

    def test_backfill_e_idempotente(self, alembic_cfg: Config, migrations_db_url: str) -> None:
        """Reexecutar o backfill (downgrade+upgrade repetidos) converge, não acumula."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, "head")
        _execute(
            url,
            "INSERT INTO users (id, name, email, password_hash, role, active, scope, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'Idem', "
            "'idem-rt@hologram.com.br', 'x', 'admin', true, 'system', now(), now())",
        )

        for _ in range(2):
            command.downgrade(alembic_cfg, "-2")
            command.upgrade(alembic_cfg, "head")

        assert _scalar(url, "SELECT count(*) FROM users") == 1
        assert _scalar(url, "SELECT count(*) FROM users WHERE scope = 'system'") == 1

    def test_backfill_da_auditoria_marca_linhas_antigas_como_system(
        self, alembic_cfg: Config, migrations_db_url: str
    ) -> None:
        """Linha de `access_audit` gravada antes da S5 vira `system` sem tenant."""
        url = migrations_db_url
        command.upgrade(alembic_cfg, S5_AUDIT_ACTOR_REV)
        command.downgrade(alembic_cfg, S5_TENANCY_REV)

        # Linha "da S3": sem as colunas de ator, porque elas ainda não existem.
        _execute(
            url,
            "INSERT INTO access_audit (id, user_id, client_id, action, rota, timestamp) "
            "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'view', "
            "'/api/v1/reconciliations/x', now())",
        )

        command.upgrade(alembic_cfg, "head")

        assert (
            _scalar(
                url,
                "SELECT count(*) FROM access_audit "
                "WHERE user_scope = 'system' AND actor_client_id IS NULL",
            )
            == 1
        )
