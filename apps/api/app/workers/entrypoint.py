"""Entrypoint do worker pra Cloud Run.

Cloud Run exige que todo container responda HTTP numa porta — inclusive
worker, que não é uma API. Sem isso o Cloud Run considera o container
não saudável e reinicia em loop.

Solução: rodar 2 tarefas em paralelo no mesmo processo asyncio.

  1. Loop do ARQ (consome jobs do Redis).
  2. Servidor HTTP mínimo na porta ``PORT`` (8080 default) respondendo
     ``GET /health``. O endpoint pinga o Redis pra confirmar que a fila
     está acessível; em falha retorna 503 e o Cloud Run reinicia.

Subir:
    cd apps/api
    uv run python -m app.workers.entrypoint

Em Cloud Run, o CMD do service sobrescreve o default da imagem da API com
``python -m app.workers.entrypoint``.
"""

from __future__ import annotations

import asyncio
import os
import sys

# psycopg async exige SelectorEventLoop. Em Windows, ProactorEventLoop é
# default — mesma correção dos outros entry points do projeto.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import redis.asyncio as redis_async
import uvicorn
from arq.worker import create_worker
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.workers.arq_worker import WorkerSettings

# Timeout do PING do Redis no /health. Cloud Run probe default é 4s; se
# Redis demorar mais que isso é sinal de problema mesmo, devolvemos 503.
_HEALTH_REDIS_TIMEOUT_S = 3.0


def _create_app() -> FastAPI:
    """Cria a FastAPI mínima do worker — só /health, sem docs/openapi."""
    app = FastAPI(
        title="Auditoria Worker",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> JSONResponse:
        """Health check — pinga o Redis e responde conforme spec INFRA 3.1.

        Sucesso: 200 + ``{"status":"ok","role":"worker","redis":"connected"}``.
        Falha:   503 + ``{"status":"degraded",...,"redis":"disconnected"}`` —
                 Cloud Run reinicia o container.
        """
        settings = get_settings()
        client: redis_async.Redis | None = None
        try:
            # redis-py async ainda sem stubs completos — assinatura inferida.
            client = redis_async.from_url(settings.REDIS_URL)  # type: ignore[no-untyped-call]
            pong = await asyncio.wait_for(client.ping(), timeout=_HEALTH_REDIS_TIMEOUT_S)
            if pong:
                return JSONResponse(
                    {"status": "ok", "role": "worker", "redis": "connected"},
                    status_code=status.HTTP_200_OK,
                )
        except Exception as exc:  # health não pode levantar — qualquer erro vira 503
            get_logger(__name__).warning(
                "worker_health_redis_failed", error=str(exc), error_type=type(exc).__name__
            )
        finally:
            if client is not None:
                await client.aclose()

        return JSONResponse(
            {"status": "degraded", "role": "worker", "redis": "disconnected"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return app


async def _arq_supervisor() -> None:
    """Roda o ARQ worker em loop com backoff — não deixa drop de conexão
    intermitente matar o container.

    Antes: ``arq.async_run()`` direto. Qualquer falha (drop idle do
    Upstash, blip de DNS no Cloud Run) propagava pra ``asyncio.gather``,
    o container morria, Cloud Run reiniciava do zero. Em horários ruins
    isso virava loop de crash a cada 3-5 min.

    Agora: capturamos a exceção, logamos, esperamos com backoff
    exponencial (1s, 2s, 4s, 8s, 16s, 30s, ...) e instanciamos novo
    worker. O servidor HTTP de ``/health`` continua respondendo durante
    todo o ciclo — Cloud Run só reinicia se ele mesmo cair.
    """
    log = get_logger(__name__)
    backoff = 1.0
    max_backoff = 30.0

    while True:
        try:
            # mypy reclama porque WorkerSettings não herda WorkerSettingsBase,
            # mas ARQ usa duck typing — atributos ClassVar bastam.
            arq = create_worker(WorkerSettings)  # type: ignore[arg-type]
            await arq.async_run()
            # async_run só retorna em shutdown limpo; aguarda um pouco e
            # reinicia (não esperado em produção, mas evita busy loop).
            backoff = 1.0
            await asyncio.sleep(1)
        except Exception as exc:  # supervisor não pode propagar — qualquer crash vira backoff+retry
            log.warning(
                "arq_worker_crashed_restarting",
                error=str(exc),
                error_type=type(exc).__name__,
                backoff_seconds=backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


async def main() -> None:
    """Roda ARQ worker + servidor HTTP concorrentemente."""
    settings = get_settings()
    setup_logging(settings)
    log = get_logger(__name__)

    port = int(os.environ.get("PORT", "8080"))
    log.info("worker_entrypoint_starting", port=port, environment=settings.ENVIRONMENT.value)

    server = uvicorn.Server(
        uvicorn.Config(
            _create_app(),
            host="0.0.0.0",  # noqa: S104 — Cloud Run roda atrás do load balancer
            port=port,
            log_config=None,
            access_log=False,
        )
    )

    # ARQ rodando sob supervisor próprio — uvicorn segue vivo independente
    # de blips do Redis. Se o uvicorn morrer (probe /health falhar muito), aí
    # sim Cloud Run reinicia o container.
    await asyncio.gather(server.serve(), _arq_supervisor())


if __name__ == "__main__":
    asyncio.run(main())
