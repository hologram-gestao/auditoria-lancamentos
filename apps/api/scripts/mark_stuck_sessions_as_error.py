"""Marca sessões de conciliação ``stuck`` em ``processing`` como ``error``.

Defesa em profundidade contra o cenário em que o processo da API morre de
forma catastrófica no meio de uma BackgroundTask, antes de qualquer ``except``
rodar (OOM kill / reciclagem de instância do Cloud Run) e a sessão fica em
``status='processing'`` pra sempre.

O caminho normal já está coberto:
    - ``asyncio.timeout(RECONCILIATION_TIMEOUT_SECONDS)`` (900s) →
      ``run_reconciliation_processing`` marca a sessão como ``error`` ao estourar.
    - ``except`` (AppError / Exception / CancelledError) no mesmo handler também
      marca como ``error``.

Este script entra como rede de segurança: rodado por Cloud Scheduler
(de hora em hora, free tier), varre sessões em ``processing`` há mais
de ``STUCK_THRESHOLD_MINUTES`` e marca como ``error``. Threshold maior
que o timeout do processamento pra não colidir com jobs lentos legítimos.

Uso:
    cd apps/api
    uv run python -m scripts.mark_stuck_sessions_as_error

No Cloud Run Job, override:
    --command=python --args=-m,scripts.mark_stuck_sessions_as_error
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Garante que ``apps/api/`` está no sys.path (idem seed_dev.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402

# Margem confortável acima do timeout do processamento (RECONCILIATION_TIMEOUT_SECONDS
# = 900s = 15min). 25min evita marcar como stuck um job que está só processando
# lentamente.
STUCK_THRESHOLD_MINUTES = 25

_ERROR_MSG = (
    "Processamento cancelado: sessao em 'processing' por mais de "
    f"{STUCK_THRESHOLD_MINUTES} minutos sem conclusao (cleanup automatico)."
)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    log = get_logger(__name__)

    init_db(settings)
    try:
        sf = get_session_factory()
        async with sf() as db, db.begin():
            # ``updated_at`` é mais conservador que ``created_at``: se o job
            # progrediu (mudou updated_at) há pouco, não derruba.
            result = await db.execute(
                text(
                    """
                    UPDATE reconciliation_sessions
                    SET status = 'error',
                        error_message = :msg,
                        updated_at = NOW()
                    WHERE status = 'processing'
                      AND updated_at < NOW() - (:minutes || ' minutes')::interval
                    """
                ),
                {"msg": _ERROR_MSG, "minutes": STUCK_THRESHOLD_MINUTES},
            )
        # ``Result.rowcount`` existe em runtime mas o stub do SQLAlchemy 2.x
        # marca como ``-1`` pra Core async — pegamos via ``getattr`` pra
        # satisfazer o mypy strict.
        count = getattr(result, "rowcount", -1)
        log.info(
            "stuck_sessions_marked_as_error",
            count=count,
            threshold_minutes=STUCK_THRESHOLD_MINUTES,
        )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
