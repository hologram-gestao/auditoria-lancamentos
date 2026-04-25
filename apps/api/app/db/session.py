"""Engine + sessionmaker async para SQLAlchemy 2.0.

Padrão (CLAUDE.md §3-6):
    - Engine global por processo, criado no startup do FastAPI (`lifespan`).
    - Sessions são per-request via `Depends(get_db)` — nunca usar globalmente.
    - Em testes, `conftest.py` provê uma session em transação que dá rollback no fim.
    - Pool size pequeno em dev (5), maior em prod (configurar via env futuramente).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Cria a engine async do SQLAlchemy a partir das Settings.

    `pool_pre_ping=True` testa a conexão antes de usar (essencial em prod
    onde conexões podem morrer por idle timeout do Postgres ou load balancer).
    """
    return create_async_engine(
        settings.DATABASE_URL,
        echo=False,  # SQL no log apenas em debug — usar `?echo=true` se quiser
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,  # recicla conexões a cada 1h
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessionmaker async — fábrica de AsyncSession por request."""
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,  # mantém objetos utilizáveis após commit
        autoflush=False,
    )


# Engine + sessionmaker globais — populados no lifespan da app
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(settings: Settings) -> None:
    """Inicializa engine + sessionmaker globais. Chamado no startup."""
    global _engine, _session_factory
    _engine = create_engine(settings)
    _session_factory = create_session_factory(_engine)


async def close_db() -> None:
    """Fecha o pool de conexões. Chamado no shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Retorna o sessionmaker global. Erro se `init_db` não foi chamado."""
    if _session_factory is None:
        raise RuntimeError("Session factory não inicializada. Chame init_db(settings) no startup.")
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Async generator de AsyncSession para uso em `Depends()`.

    Cada request recebe uma session nova. Commit é responsabilidade do caller —
    em caso de exceção, rollback automático via context manager.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
