"""Testes do alerting fail-closed (Sprint 3, BACK 03.6).

Cobre:
    - fail-closed: staging/production sem canal → boot falha; dev só avisa.
    - e-mail só conta como canal se há destino E transporte SMTP.
    - dispatch webhook async (respx): entregue / falhou / não configurado.
    - SEM PII no payload (só code/session_id/client_id/message-template).
    - dispatch_alert_nowait não levanta fora de um event loop.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.alerting import (
    Alert,
    AlertCode,
    AlertConfigError,
    dispatch_alert_nowait,
    send_alert,
    verify_alert_config,
)
from app.core.config import Settings

_WEBHOOK = "https://hooks.test/plantao"


def _settings(**overrides: object) -> Settings:
    """Settings de teste (campos obrigatórios vêm do env de teste)."""
    return Settings(**overrides)  # type: ignore[arg-type]


class TestVerifyAlertConfig:
    def test_prod_without_channel_raises(self) -> None:
        s = _settings(ENVIRONMENT="production", COOKIE_SECURE=True)
        with pytest.raises(AlertConfigError):
            verify_alert_config(s)

    def test_prod_with_webhook_ok(self) -> None:
        s = _settings(ENVIRONMENT="production", COOKIE_SECURE=True, ALERT_WEBHOOK_URL=_WEBHOOK)
        verify_alert_config(s)  # não levanta

    def test_dev_without_channel_only_warns(self) -> None:
        s = _settings(ENVIRONMENT="development")
        verify_alert_config(s)  # não levanta (só warn)

    def test_email_needs_smtp_host_to_count(self) -> None:
        # e-mail SEM transporte SMTP não é entregável → fail-closed em prod.
        s = _settings(ENVIRONMENT="production", COOKIE_SECURE=True, ALERT_EMAIL_TO="plantao@h.com")
        assert not s.has_alert_channel
        with pytest.raises(AlertConfigError):
            verify_alert_config(s)
        # com SMTP host, passa a contar.
        s2 = _settings(
            ENVIRONMENT="production",
            COOKIE_SECURE=True,
            ALERT_EMAIL_TO="plantao@h.com",
            ALERT_SMTP_HOST="smtp.h.com",
        )
        assert s2.has_alert_channel
        verify_alert_config(s2)


class TestAlertPayload:
    def test_webhook_payload_shape(self) -> None:
        alert = Alert(
            code=AlertCode.SESSION_ERROR,
            message="Erro generico ao processar",
            session_id="sess-1",
            client_id="cli-1",
        )
        payload = alert.to_webhook_payload()
        assert payload["code"] == "session_error"
        assert payload["service"] == "adl"
        assert payload["session_id"] == "sess-1"
        assert payload["client_id"] == "cli-1"
        assert payload["text"].startswith("[ADL] session_error:")
        # Só as chaves esperadas — nenhum campo extra (defesa contra PII).
        assert set(payload) == {"text", "service", "code", "session_id", "client_id"}

    def test_payload_omits_absent_ids(self) -> None:
        payload = Alert(code=AlertCode.HEARTBEAT_MISSING, message="x").to_webhook_payload()
        assert "session_id" not in payload
        assert "client_id" not in payload


class TestSendAlert:
    async def test_webhook_delivered(self) -> None:
        s = _settings(ALERT_WEBHOOK_URL=_WEBHOOK)
        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
            res = await send_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"), s)
        assert route.called
        assert res.webhook is True
        assert res.email is None
        assert res.delivered is True

    async def test_webhook_failure_returns_false(self) -> None:
        s = _settings(ALERT_WEBHOOK_URL=_WEBHOOK)
        with respx.mock:
            respx.post(_WEBHOOK).mock(return_value=httpx.Response(500))
            res = await send_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"), s)
        assert res.webhook is False
        assert res.delivered is False

    async def test_no_channel_returns_none(self) -> None:
        s = _settings(ENVIRONMENT="development")
        res = await send_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"), s)
        assert res.webhook is None
        assert res.email is None
        assert res.delivered is False

    async def test_no_pii_in_webhook_body(self) -> None:
        s = _settings(ALERT_WEBHOOK_URL=_WEBHOOK)
        with respx.mock:
            route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
            await send_alert(
                Alert(
                    code=AlertCode.DECRYPT_FAILED,
                    message="Falha de decifragem no export",
                    session_id="sess-9",
                    client_id="cli-9",
                ),
                s,
            )
        body = json.loads(route.calls.last.request.content)
        # Só IDs/código/template — nada de nome/descrição/razão social.
        assert set(body) == {"text", "service", "code", "session_id", "client_id"}
        serialized = json.dumps(body).lower()
        for forbidden in ("descri", "razao", "razão", "cnpj", "app_key", "app_secret"):
            assert forbidden not in serialized


def test_dispatch_alert_nowait_without_loop_does_not_raise() -> None:
    # Contexto síncrono sem event loop → degrada para log, nunca levanta.
    dispatch_alert_nowait(Alert(code=AlertCode.SYNTHETIC, message="x"), _settings())
