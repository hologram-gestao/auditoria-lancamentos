"""Rotas de sistema/operação (Sprint 3, BACK 03.6).

`POST /api/v1/system/alert-test` — gatilho SINTÉTICO de alerta. Admin-only.
Dispara um alerta proposital ao(s) canal(is) configurado(s) para PROVAR a
entrega ponta a ponta (consumido pela 03.7). Sem PII — só o código sintético.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.alerting import Alert, AlertCode, send_alert
from app.core.dependencies import AdminDep, SettingsDep
from app.modules.system.schemas import SyntheticAlertResponse, SyntheticAlertResult

router = APIRouter(prefix="/api/v1/system", tags=["system"])

_SYNTHETIC_MESSAGE = (
    "Teste sintetico de alerta do ADL — a chegada desta mensagem prova que o "
    "canal de plantao esta entregando alertas."
)


@router.post(
    "/alert-test",
    summary=(
        "Dispara um alerta SINTÉTICO ao(s) canal(is) de plantão configurado(s) "
        "(webhook e/ou e-mail) para provar a entrega ponta a ponta. Admin-only. "
        "Retorna o resultado por canal (True=entregue, False=falhou, "
        "None=não configurado). Sem PII."
    ),
)
async def trigger_synthetic_alert(
    _admin: AdminDep,
    settings: SettingsDep,
) -> SyntheticAlertResponse:
    result = await send_alert(
        Alert(code=AlertCode.SYNTHETIC, message=_SYNTHETIC_MESSAGE),
        settings,
    )
    return SyntheticAlertResponse(
        data=SyntheticAlertResult(
            delivered=result.delivered,
            webhook=result.webhook,
            email=result.email,
        )
    )
