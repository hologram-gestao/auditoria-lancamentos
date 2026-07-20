"""Gatilho SINTÉTICO de alerta como CLI (Sprint 3, BACK 03.6).

Prova a entrega ponta a ponta do alerting SEM depender de um JWT de admin: é o
entrypoint que o deploy executa como Cloud Run Job (INFRA 03.7,
`python -m app.cli.alert_synthetic_check`) — um Job não tem sessão HTTP para
chamar o endpoint admin `POST /api/v1/system/alert-test`.

Dispara o MESMO alerta sintético do endpoint (`SYNTHETIC_ALERT_MESSAGE`) ao(s)
canal(is) configurado(s) e mapeia o resultado para o exit code, para o gate do
deploy reprovar quando a entrega falha:

    exit 0  → o alerta CHEGOU a pelo menos um canal (`delivered`).
    exit 1  → nenhum canal entregou (canal caiu, ou nenhum configurado).

Uso:
    python -m app.cli.alert_synthetic_check

Sem PII — só o código sintético e a mensagem-template fixa (ver `app/core/alerting.py`).
"""

from __future__ import annotations

import asyncio
import sys

from app.core.alerting import (
    SYNTHETIC_ALERT_MESSAGE,
    Alert,
    AlertCode,
    AlertDeliveryResult,
    send_alert,
)
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


async def run_synthetic_check() -> AlertDeliveryResult:
    """Dispara o alerta sintético e devolve o resultado por canal (testável)."""
    settings = get_settings()
    result = await send_alert(
        Alert(code=AlertCode.SYNTHETIC, message=SYNTHETIC_ALERT_MESSAGE),
        settings,
    )
    log.info(
        "alert_synthetic_check",
        delivered=result.delivered,
        webhook=result.webhook,
        email=result.email,
    )
    return result


def main() -> int:
    """Entrypoint: 0 se o alerta foi entregue a algum canal, 1 caso contrário."""
    result = asyncio.run(run_synthetic_check())
    return 0 if result.delivered else 1


if __name__ == "__main__":  # pragma: no cover - exercitado como subprocess/CLI
    sys.exit(main())
