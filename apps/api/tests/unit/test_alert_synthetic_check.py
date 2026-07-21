"""Testes do CLI de gatilho sintético (Sprint 3, BACK 03.6).

Prova o contrato que a INFRA 03.7 consome: `python -m app.cli.alert_synthetic_check`
mapeia entrega → exit code (0 entregou, 1 não entregou). Sem isso, o Job de
smoke-alert do deploy não teria como reprovar quando o canal cai.
"""

from __future__ import annotations

import pytest

from app.cli import alert_synthetic_check as cli
from app.core.alerting import (
    SYNTHETIC_ALERT_MESSAGE,
    Alert,
    AlertCode,
    AlertDeliveryResult,
)


def _patch_send(monkeypatch: pytest.MonkeyPatch, result: AlertDeliveryResult) -> list[Alert]:
    """Substitui send_alert por um fake async e captura o alerta disparado."""
    captured: list[Alert] = []

    async def _fake_send(alert: Alert, _settings: object) -> AlertDeliveryResult:
        captured.append(alert)
        return result

    monkeypatch.setattr(cli, "send_alert", _fake_send)
    return captured


def test_main_exit_0_when_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_send(monkeypatch, AlertDeliveryResult(webhook=True, email=None))
    assert cli.main() == 0
    # Dispara o alerta sintético canônico (mesma msg/código do endpoint HTTP).
    assert len(captured) == 1
    assert captured[0].code is AlertCode.SYNTHETIC
    assert captured[0].message == SYNTHETIC_ALERT_MESSAGE


def test_main_exit_1_when_not_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_send(monkeypatch, AlertDeliveryResult(webhook=False, email=None))
    assert cli.main() == 1


def test_main_exit_1_when_no_channel_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nenhum canal → send_alert devolve None/None → não entregue → exit 1.
    _patch_send(monkeypatch, AlertDeliveryResult(webhook=None, email=None))
    assert cli.main() == 1
