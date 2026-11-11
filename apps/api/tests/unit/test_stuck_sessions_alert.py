"""O alerta do watchdog com contexto da sessão (86e3mpmcw).

Antes, `heartbeat_missing` dizia só quantas sessões derrubou: para saber qual
cliente, conta, há quanto tempo e em que etapa, era preciso abrir o banco. Agora o
alerta traz um bloco por sessão (até 5, depois "e mais N"), com o link da tela e a
ação sugerida, e NADA além de IDs, códigos, datas e números: o canal é um webhook
de terceiro.
"""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

import httpx
import respx
from structlog.testing import capture_logs

from app.core.alerting import ALERT_ACTIONS, ALERT_PREFIX, AlertCode, render_alert, send_alert
from app.core.config import Settings
from app.modules.reconciliations.stuck_sessions import (
    STUCK_ALERT_MAX_SESSIONS,
    StuckSession,
    build_heartbeat_missing_alert,
    session_link,
)

_WEBHOOK = "https://hooks.test/plantao"
_FRONT = "https://web.test/"


def _stuck(n: int, *, stage: str | None = "omie", idle: int = 40) -> StuckSession:
    return StuckSession(
        session_id=UUID(int=1000 + n),
        client_id=UUID(int=2000 + n),
        omie_conta_id=900_000 + n,
        reference_month=date(2026, 10, 1),
        processing_stage=stage,
        idle_minutes=idle,
    )


def test_uma_sessao_traz_ids_competencia_etapa_minutos_link_e_acao() -> None:
    alert = build_heartbeat_missing_alert([_stuck(1)], frontend_url=_FRONT)
    assert alert.code is AlertCode.HEARTBEAT_MISSING
    assert alert.context_omitted == 0
    sid, cid = UUID(int=1001), UUID(int=2001)
    assert render_alert(alert).split("\n") == [
        f"{ALERT_PREFIX} heartbeat_missing",
        "1 conciliação(ões) parada(s) em processamento, marcada(s) como erro pelo "
        "watchdog: sem sinal há mais de 25 minutos.",
        "",
        f"sessão: {sid}",
        f"cliente: {cid}",
        "conta Omie: 900001",
        "competência: 2026-10",
        "etapa: omie",
        "último sinal: há 40 min",
        f"link: https://web.test/clientes/{cid}/conciliacao/{sid}",
        "",
        f"Ação sugerida: {ALERT_ACTIONS[AlertCode.HEARTBEAT_MISSING]}",
    ]


def test_sete_sessoes_mostram_cinco_e_mais_duas() -> None:
    stuck = [_stuck(i) for i in range(7)]
    alert = build_heartbeat_missing_alert(stuck, frontend_url=_FRONT)
    assert len(alert.context) == STUCK_ALERT_MAX_SESSIONS == 5
    assert alert.context_omitted == 2
    text_ = render_alert(alert)
    assert text_.startswith(f"{ALERT_PREFIX} heartbeat_missing\n7 conciliação(ões)")
    assert "\ne mais 2\n" in text_
    # As cinco primeiras, na ordem recebida (o watchdog já ordena por tempo sem sinal).
    shown = [dict(group)["sessão"] for group in alert.context]
    assert shown == [str(UUID(int=1000 + i)) for i in range(5)]
    assert str(UUID(int=1005)) not in text_


def test_etapa_ausente_sai_como_nao_registrada() -> None:
    alert = build_heartbeat_missing_alert([_stuck(1, stage=None)], frontend_url=_FRONT)
    assert dict(alert.context[0])["etapa"] == "não registrada"


def test_link_sem_barra_dupla() -> None:
    sid, cid = UUID(int=1), UUID(int=2)
    expected = f"http://localhost:3000/clientes/{cid}/conciliacao/{sid}"
    assert session_link("http://localhost:3000/", client_id=cid, session_id=sid) == expected
    assert session_link("http://localhost:3000", client_id=cid, session_id=sid) == expected


async def test_corpo_do_webhook_so_tem_ids_e_o_log_nao_tem_a_url() -> None:
    settings = Settings(  # type: ignore[call-arg]
        ALERT_WEBHOOK_URL=_WEBHOOK, ALERT_EMAIL_TO=None, ALERT_SMTP_HOST=None
    )
    alert = build_heartbeat_missing_alert([_stuck(1), _stuck(2)], frontend_url=_FRONT)
    with respx.mock, capture_logs() as logs:
        route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
        result = await send_alert(alert, settings)
    assert result.webhook is True
    body = json.loads(route.calls.last.request.content)
    # Chaves de topo: as mesmas de sempre, sem campo novo.
    assert set(body) == {"text", "service", "code"}
    assert body["code"] == "heartbeat_missing"
    # Cada linha de contexto é chave conhecida e valor de ID, data ou número.
    labels = {
        line.split(": ", 1)[0]
        # Título e mensagem (template fixo) ficam de fora: daí em diante é contexto.
        for line in body["text"].split("\n")[2:]
        if ": " in line and not line.startswith("Ação sugerida")
    }
    assert labels == {
        "sessão",
        "cliente",
        "conta Omie",
        "competência",
        "etapa",
        "último sinal",
        "link",
    }
    serialized = json.dumps(body, ensure_ascii=False).lower()
    for forbidden in ("razão", "razao", "cnpj", "descri", "nome", "cliente watchdog"):
        assert forbidden not in serialized
    assert _WEBHOOK not in repr(logs)
    dispatched = [e for e in logs if e["event"] == "alert_dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0]["code"] == "heartbeat_missing"
