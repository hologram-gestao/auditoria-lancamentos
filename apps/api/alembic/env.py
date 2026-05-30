"""Alembic environment — async-aware.

Lê DATABASE_URL das Settings (sem duplicar config). Suporta tanto a URL async
(`postgresql+psycopg://...`) usada pela app quanto a versão sync usada pelo
Alembic em modo online — convertemos uma na outra automaticamente.

A importação de `app.db.models` registra TODOS os modelos no `Base.metadata`,
habilitando `alembic revision --autogenerate`.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig

# psycopg async não suporta ProactorEventLoop (default do Windows). Em qualquer
# plataforma, SelectorEventLoop funciona — fixar evita erro de Interface.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Injeta URL do DB vinda das Settings (já vem com driver async +psycopg)
_settings = get_settings()
# Escapa % → %% porque o ConfigParser interno do alembic aplica
# interpolação BasicInterpolation em set_main_option — senhas
# URL-encoded (%3D, %2B etc) quebram com ValueError.
config.set_main_option("sqlalchemy.url", _settings.DATABASE_URL.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Modo offline: emite SQL sem conectar (útil para revisar migrations)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Modo online async — abre conexão real e roda migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
