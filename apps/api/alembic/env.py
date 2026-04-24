"""Alembic environment.

Usa a URL do banco a partir das Settings (pydantic-settings) para evitar
duplicar a config em dois lugares. Os modelos SQLAlchemy serão importados
em S2 para habilitar --autogenerate.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Injeta URL do DB vinda das Settings (substitui driver async por sync para o Alembic)
_settings = get_settings()
_sync_url = _settings.DATABASE_URL.replace("+psycopg", "").replace("postgresql+", "postgresql+psycopg2://" if False else "postgresql://")
config.set_main_option("sqlalchemy.url", _settings.DATABASE_URL)

# Em S2, importar Base e todos os modelos aqui:
# from app.db.base import Base
# from app.db.models import *  # noqa: F401,F403
# target_metadata = Base.metadata
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL sem conectar)."""
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


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
