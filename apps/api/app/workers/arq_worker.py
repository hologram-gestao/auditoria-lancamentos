"""ARQ worker para jobs assíncronos (S10+).

Subir o worker:
    cd apps/api
    uv run arq app.workers.arq_worker.WorkerSettings

(ou via `pnpm dev:worker` na raiz do monorepo)

Princípios:
    - Worker tem ciclo de vida próprio: chama `init_db` no startup e
      `close_db` no shutdown. NÃO compartilha pool de conexões com o uvicorn
      — cada processo cuida do seu.
    - `max_jobs=4`: cada job pode falar com Omie (15s timeout) + escrever no
      DB. 4 paralelos é conservador; em prod, ajustar via env.
    - `job_timeout=300` (5min): limite duro. Se Omie travar nos 15s + retry,
      ainda há margem para matching + DB.
    - O job propriamente dito (`run_reconciliation_processing`) trata todas
      as exceptions internamente — nunca propaga para o ARQ. Logo,
      `max_tries=1` (sem retry automático): se algo deu errado, a sessão já
      está em `status='error'` e o usuário precisa ser alertado, não
      retentar silenciosamente.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import close_db, get_session_factory, init_db
from app.modules.reconciliations.processing.job import run_reconciliation_processing


async def on_startup(ctx: dict[str, Any]) -> None:
    """Inicializa logging e o pool do SQLAlchemy.

    `ctx` é o dict do ARQ que vive durante toda a vida do worker —
    populamos `settings` e `session_factory` aqui para que cada job
    reuse o mesmo engine (evita reconectar a cada execução).

    `async def` é exigido pelo ARQ (assinatura do hook), mesmo que o corpo
    seja síncrono — `init_db` cria o engine lazily e a 1ª conexão real
    acontece dentro do 1º job.
    """
    import asyncio

    settings: Settings = get_settings()
    setup_logging(settings)
    init_db(settings)

    log = get_logger(__name__)
    log.info("arq_worker_started")

    factory: async_sessionmaker[AsyncSession] = get_session_factory()
    ctx["settings"] = settings
    ctx["session_factory"] = factory
    # Yield para o event loop. Satisfaz o linter S7503 ("async sem await") sem
    # acoplar a setup network-bound antecipado.
    await asyncio.sleep(0)


async def on_shutdown(_ctx: dict[str, Any]) -> None:
    """Fecha o pool de conexões do DB. ARQ exige a assinatura `(ctx)`."""
    log = get_logger(__name__)
    await close_db()
    log.info("arq_worker_shutdown")


def _redis_settings() -> RedisSettings:
    """Lê `REDIS_URL` na primeira chamada — settings são singleton."""
    return RedisSettings.from_dsn(get_settings().REDIS_URL)


class WorkerSettings:
    """Configuração do worker ARQ.

    `arq` espera uma classe (não instância) com esses atributos. Atributos
    são lidos no startup do CLI `arq <module>.WorkerSettings`.
    """

    redis_settings: ClassVar[RedisSettings] = _redis_settings()
    functions: ClassVar[list[Any]] = [run_reconciliation_processing]
    on_startup = staticmethod(on_startup)
    on_shutdown = staticmethod(on_shutdown)
    max_jobs: ClassVar[int] = 4
    job_timeout: ClassVar[int] = 300  # 5 minutos
    keep_result: ClassVar[int] = 60  # mantém resultado no Redis por 1min (debug)
    max_tries: ClassVar[int] = 1  # job já trata erros — sem retry automático
