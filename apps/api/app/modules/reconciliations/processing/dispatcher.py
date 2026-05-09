"""Dispatcher do job de processamento (BACK 8.1).

Conecta no Redis configurado em `Settings.REDIS_URL` e enfileira
`run_reconciliation_processing(session_id)` no ARQ. Importado pelo
endpoint `POST /api/v1/reconciliations`.

Por que não chamar `run_reconciliation_processing` direto via `asyncio.create_task`:
    - O endpoint deve responder em < 200ms; a request HTTP NÃO pode segurar
      task in-flight (se uvicorn for reciclado, a task morre silenciosamente).
    - Workers separados permitem escalar horizontalmente independente da API.
    - ARQ provê persistência via Redis: jobs sobrevivem a redeploy.

Convenção: parâmetros do job são tipados como `(ctx, session_id_str)`. ARQ
serializa via msgpack — UUID → string evita pegadinhas de encoding.
"""

from __future__ import annotations

from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings

from app.core.logging import get_logger

log = get_logger(__name__)

# Nome canônico do job — TEM que bater com `WorkerSettings.functions[0].__name__`
# (i.e. `run_reconciliation_processing`). String literal para evitar acoplar
# este módulo ao `job.py` no import-time (worker é processo separado).
RECONCILIATION_JOB_NAME = "run_reconciliation_processing"


async def enqueue_processing(
    session_id: UUID,
    *,
    redis_url: str,
) -> str:
    """Enfileira o job de conciliação no ARQ.

    Args:
        session_id: UUID da sessão recém-criada.
        redis_url: URL do Redis (ex: `redis://localhost:6379/0`).

    Returns:
        Job ID atribuído pelo ARQ. Útil para logging/correlação; o front
        polla via `/status` e não consome esse ID.

    Raises:
        RuntimeError: ARQ recusou enfileirar (Redis offline, fila cheia, etc).
            O endpoint converte em 500 para o caller — a sessão JÁ foi
            commitada com `status='processing'`, então um job_failed manual
            pode ser disparado depois para retomar.
    """
    settings = RedisSettings.from_dsn(redis_url)
    pool = await create_pool(settings)
    try:
        job = await pool.enqueue_job(RECONCILIATION_JOB_NAME, str(session_id))
        if job is None:
            raise RuntimeError(
                f"ARQ não enfileirou o job para session_id={session_id} (fila cheia?)."
            )
        job_id = job.job_id
        log.info(
            "reconciliation_job_enqueued",
            session_id=str(session_id),
            job_id=job_id,
        )
        return job_id
    finally:
        await pool.aclose()
