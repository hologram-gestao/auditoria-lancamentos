"""Fixtures globais de pytest.

Para testes que tocam DB usamos **testcontainers**: um container Postgres
descartável é subido por sessão de testes, schema é criado, e cada teste
recebe uma session em transação que dá rollback no fim — testes não vazam
estado entre si.

Estratégia de fixtures:
    - `pg_container` (session scope): sobe Postgres uma única vez.
    - `db_url` (session scope): URL do Postgres do container.
    - `db_engine` (session scope): engine async + cria schema via `create_all`.
    - `db_session` (function scope): session em transação com rollback automático.
    - `client` (function scope): HTTP client com app FastAPI sem DB (legado, S0/S1).
    - `client_with_db` (function scope): HTTP client com `get_db_session` injetado
      apontando para a `db_session` da fixture (pra testes integration).

Skipping: se Docker não estiver disponível, os fixtures de DB pulam com mensagem
clara, mas testes unitários puros (S1) continuam rodando.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

# psycopg async não suporta ProactorEventLoop (default Windows)
if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import get_settings
from app.core.rate_limit import limiter as _rate_limiter
from app.db.models import Base
from app.db.session import get_db_session
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


@pytest.fixture(autouse=True)
def _force_real_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutraliza `MOCK_PARSE=true` se vier do `.env` local (P1-007).

    `MOCK_PARSE=true` faz o ParseService devolver um statement fixo (Padaria
    Pão Quente) sem chamar a Anthropic — útil pra demos, mas quebra testes
    que esperam payload real (ex: `test_admin_can_parse_any_client` espera
    Sicredi). Forçamos `false` em toda execução de teste e zeramos o cache
    do singleton `get_settings` para que o novo valor seja lido.
    """
    monkeypatch.setenv("MOCK_PARSE", "false")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """Limpa contadores in-memory do slowapi entre testes.

    Sem isso, testes em sequência (mesmo IP 127.0.0.1) acumulam contagens e
    estouram o limit do /auth/login. Testes específicos que validam o limit
    sobem o contador intencionalmente — esta fixture garante isolamento.
    """
    _rate_limiter.reset()
    yield
    _rate_limiter.reset()


# ----------------------------------------------------------------------
# HTTP client sem DB (testes de S1 — auth/dependencies usando app stub)
# ----------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client async conectado à app FastAPI em memória."""
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac


# ----------------------------------------------------------------------
# Postgres descartável via testcontainers
# ----------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """Sobe um Postgres efêmero. Pula testes se Docker não estiver disponível."""
    try:
        from testcontainers.postgres import PostgresContainer as _PgContainer
    except ImportError:
        pytest.skip("testcontainers não instalado — instale o extra dev")

    try:
        container = _PgContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as exc:  # docker daemon offline, etc.
        pytest.skip(f"Docker indisponível para testcontainers: {exc}")

    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def db_url(pg_container: PostgresContainer) -> str:
    """URL do Postgres do container, no formato esperado pelo SQLAlchemy async."""
    return pg_container.get_connection_url()


@pytest.fixture(scope="session")
async def db_engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine async com schema criado via `Base.metadata.create_all`.

    Não usamos `alembic upgrade head` aqui — para testes, criar do metadata
    é mais rápido e equivalente (a 1ª migration foi gerada do metadata, então
    o resultado é idêntico).
    """
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """AsyncSession isolada por teste, robusta a `commit()` da aplicação.

    A session roda dentro de uma transação EXTERNA na conexão; com
    `join_transaction_mode="create_savepoint"`, qualquer `commit()` do código
    da app (ex.: o handler commitando a sessão de conciliação antes de agendar
    a BackgroundTask) opera sobre um SAVEPOINT — os dados ficam visíveis dentro
    do teste, mas o `rollback()` da transação externa no teardown desfaz tudo,
    sem vazar estado entre testes. Isso espelha o commit real de produção
    (`get_db_session`), em vez de mascará-lo.
    """
    async with db_engine.connect() as connection:
        trans = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if trans.is_active:
                await trans.rollback()


# ----------------------------------------------------------------------
# HTTP client com DB injetado
# ----------------------------------------------------------------------


@pytest.fixture
async def client_with_db(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Como `client`, mas com `get_db_session` retornando a session da fixture.

    Use em testes que precisam validar persistência via endpoints HTTP.
    """

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    fastapi_app.dependency_overrides[get_db_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)
