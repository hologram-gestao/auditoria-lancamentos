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

A regra e o alerta moram em ``app/modules/reconciliations/stuck_sessions.py``
(86e3mpmcw): o script só abre a transação, loga e dispara o alerta.

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

from app.core.alerting import send_alert  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402
from app.modules.reconciliations.stuck_sessions import (  # noqa: E402
    STUCK_THRESHOLD_MINUTES,
    build_heartbeat_missing_alert,
    mark_stuck_sessions_as_error,
)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    log = get_logger(__name__)

    init_db(settings)
    try:
        sf = get_session_factory()
        # ``updated_at`` é mais conservador que ``created_at``: se o job progrediu
        # (gravou uma etapa, 86e3mpmcw) há pouco, não derruba.
        async with sf() as db, db.begin():
            stuck = await mark_stuck_sessions_as_error(db)
        log.info(
            "stuck_sessions_marked_as_error",
            count=len(stuck),
            threshold_minutes=STUCK_THRESHOLD_MINUTES,
            session_ids=[str(s.session_id) for s in stuck],
        )
        # BACK 03.6 — ausência de heartbeat: alerta o plantão com o contexto de cada
        # sessão (só IDs, competência, etapa, minutos e link). Nada marcado = tudo bem.
        if stuck:
            await send_alert(
                build_heartbeat_missing_alert(stuck, frontend_url=str(settings.FRONTEND_URL)),
                settings,
            )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
