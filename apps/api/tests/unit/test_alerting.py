"""Testes do alerting fail-closed (Sprint 3, BACK 03.6).

Cobre:
    - fail-closed: staging/production sem canal → boot falha; dev só avisa.
    - e-mail só conta como canal se há destino E transporte SMTP.
    - dispatch webhook async (respx): entregue / falhou / não configurado.
    - SEM PII no payload (só code/session_id/client_id/message-template).
    - dispatch_alert_nowait não levanta fora de um event loop.
    - roteamento do gatilho SINTÉTICO para canal próprio, SEM afrouxar o fail-closed.
"""

from __future__ import annotations

import json
from email.message import EmailMessage
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from app.core.alerting import (
    ALERT_ACTIONS,
    ALERT_PREFIX,
    Alert,
    AlertCode,
    AlertConfigError,
    dispatch_alert_nowait,
    render_alert,
    send_alert,
    verify_alert_config,
)
from app.core.config import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

_WEBHOOK = "https://hooks.test/plantao"
_WEBHOOK_SYNTHETIC = "https://hooks.test/sintetico"

# Todos os alertas REAIS — nenhum deles pode escapar para o canal do sintético.
_REAL_CODES = [AlertCode.SESSION_ERROR, AlertCode.DECRYPT_FAILED, AlertCode.HEARTBEAT_MISSING]


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
        # 86e3mpmcw: o título é a primeira linha; a mensagem vem logo abaixo.
        assert payload["text"].startswith(
            f"{ALERT_PREFIX} session_error\nErro generico ao processar"
        )
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


class _FakeSMTP:
    """SMTP de mentira — o teste não pode abrir socket nem depender de DNS."""

    def __init__(self, sent: list[str]) -> None:
        self._sent = sent

    @classmethod
    def factory(cls, sent: list[str]) -> Callable[..., _FakeSMTP]:
        def _build(*_args: object, **_kwargs: object) -> _FakeSMTP:
            return cls(sent)

        return _build

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def starttls(self) -> None:
        return None

    def send_message(self, msg: EmailMessage) -> None:
        self._sent.append(str(msg["To"]))
        _FakeSMTP.bodies.append(msg.get_content())
        _FakeSMTP.subjects.append(str(msg["Subject"]))

    bodies: list[str] = []  # noqa: RUF012 - captura de teste
    subjects: list[str] = []  # noqa: RUF012 - captura de teste


class TestSyntheticChannelRouting:
    """O sintético (gate do deploy, várias vezes por dia) sai do canal de plantão.

    Os dois últimos testes são o que protege o fail-closed: a URL do sintético é
    canal de TESTE — se ela passasse a contar como canal entregável, um serviço
    sem plantão de verdade subiria calado em produção.
    """

    def _oncall_only(self, **overrides: object) -> Settings:
        """Zera os canais herdados do ambiente para o teste decidir sozinho."""
        base: dict[str, object] = {
            "ALERT_WEBHOOK_URL": None,
            "ALERT_WEBHOOK_URL_SYNTHETIC": None,
            "ALERT_EMAIL_TO": None,
            "ALERT_SMTP_HOST": None,
        }
        return _settings(**{**base, **overrides})

    async def test_synthetic_goes_to_its_own_webhook(self) -> None:
        s = self._oncall_only(
            ALERT_WEBHOOK_URL=_WEBHOOK, ALERT_WEBHOOK_URL_SYNTHETIC=_WEBHOOK_SYNTHETIC
        )
        with respx.mock:
            oncall = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
            synthetic = respx.post(_WEBHOOK_SYNTHETIC).mock(return_value=httpx.Response(200))
            res = await send_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"), s)
        assert synthetic.called
        assert not oncall.called
        assert res.webhook is True
        assert res.delivered is True

    async def test_synthetic_falls_back_to_oncall_webhook(self) -> None:
        # Enquanto o secret não existir no ambiente, o comportamento é o de antes.
        s = self._oncall_only(ALERT_WEBHOOK_URL=_WEBHOOK)
        with respx.mock:
            oncall = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
            res = await send_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"), s)
        assert oncall.called
        assert res.webhook is True

    @pytest.mark.parametrize("code", _REAL_CODES)
    async def test_real_alert_never_uses_synthetic_webhook(self, code: AlertCode) -> None:
        s = self._oncall_only(
            ALERT_WEBHOOK_URL=_WEBHOOK, ALERT_WEBHOOK_URL_SYNTHETIC=_WEBHOOK_SYNTHETIC
        )
        with respx.mock:
            oncall = respx.post(_WEBHOOK).mock(return_value=httpx.Response(200))
            synthetic = respx.post(_WEBHOOK_SYNTHETIC).mock(return_value=httpx.Response(200))
            await send_alert(Alert(code=code, message="incidente"), s)
        assert oncall.called
        assert not synthetic.called

    async def test_synthetic_with_own_channel_does_not_email_oncall(self) -> None:
        # Decisão de produto: desviar o sintético só resolve se ele parar de
        # incomodar TODOS os canais de plantão — inclusive o e-mail.
        s = self._oncall_only(
            ALERT_WEBHOOK_URL_SYNTHETIC=_WEBHOOK_SYNTHETIC,
            ALERT_WEBHOOK_URL=_WEBHOOK,
            ALERT_EMAIL_TO="plantao@h.com",
            ALERT_SMTP_HOST="smtp.h.com",
        )
        with respx.mock:
            respx.post(_WEBHOOK_SYNTHETIC).mock(return_value=httpx.Response(200))
            res = await send_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"), s)
        # `None` = canal não acionado (não é "falhou"): o SMTP nem foi tocado.
        assert res.email is None
        assert res.webhook is True

    async def test_real_alert_still_emails_oncall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: list[str] = []
        monkeypatch.setattr("app.core.alerting.smtplib.SMTP", _FakeSMTP.factory(sent))
        s = self._oncall_only(
            ALERT_WEBHOOK_URL_SYNTHETIC=_WEBHOOK_SYNTHETIC,
            ALERT_EMAIL_TO="plantao@h.com",
            ALERT_SMTP_HOST="smtp.h.com",
        )
        with respx.mock:
            synthetic = respx.post(_WEBHOOK_SYNTHETIC).mock(return_value=httpx.Response(200))
            res = await send_alert(Alert(code=AlertCode.SESSION_ERROR, message="erro"), s)
        assert not synthetic.called
        assert res.webhook is None  # sem webhook de plantão configurado
        assert res.email is True
        assert sent == ["plantao@h.com"]

    def test_synthetic_webhook_is_not_a_deliverable_channel(self) -> None:
        s = self._oncall_only(ALERT_WEBHOOK_URL_SYNTHETIC=_WEBHOOK_SYNTHETIC)
        assert not s.has_webhook_alert
        assert not s.has_alert_channel

    def test_prod_with_only_synthetic_webhook_still_raises(self) -> None:
        s = self._oncall_only(
            ENVIRONMENT="production",
            COOKIE_SECURE=True,
            ALERT_WEBHOOK_URL_SYNTHETIC=_WEBHOOK_SYNTHETIC,
        )
        with pytest.raises(AlertConfigError):
            verify_alert_config(s)


def test_dispatch_alert_nowait_without_loop_does_not_raise() -> None:
    # Contexto síncrono sem event loop → degrada para log, nunca levanta.
    dispatch_alert_nowait(Alert(code=AlertCode.SYNTHETIC, message="x"), _settings())


class TestAlertActionsAndRendering:
    """86e3mpmcw — todo alerta diz o que fazer, e o texto tem UMA montagem."""

    def test_toda_alert_code_tem_acao(self) -> None:
        assert set(ALERT_ACTIONS) == set(AlertCode)
        for code, action in ALERT_ACTIONS.items():
            assert action.strip(), code
            assert action.endswith("."), code

    def test_acoes_pedidas_na_task(self) -> None:
        assert ALERT_ACTIONS[AlertCode.HEARTBEAT_MISSING] == (
            "Abra a conciliação e use Tentar novamente; se repetir, confira a origem do "
            "cliente e o crédito da IA."
        )
        assert ALERT_ACTIONS[AlertCode.ANTHROPIC_CREDIT] == (
            "Recarregue o crédito no console da Anthropic (Billing). Não é preciso "
            "reiniciar nada: a próxima chamada volta a funcionar. Conciliações processadas "
            "sem crédito ficaram sem os vereditos da IA; reprocessar refaz a análise (e "
            "descarta a revisão já feita)."
        )

    def test_render_tem_titulo_mensagem_contexto_e_acao(self) -> None:
        alert = Alert(
            code=AlertCode.SESSION_ERROR,
            message="Falha ao processar",
            session_id="sess-1",
            client_id="cli-1",
            context=((("etapa", "omie"), ("conta Omie", "42")),),
            context_omitted=3,
        )
        assert render_alert(alert).split("\n") == [
            f"{ALERT_PREFIX} session_error",
            "Falha ao processar",
            "",
            "sessão: sess-1",
            "cliente: cli-1",
            "",
            "etapa: omie",
            "conta Omie: 42",
            "",
            "e mais 3",
            "",
            f"Ação sugerida: {ALERT_ACTIONS[AlertCode.SESSION_ERROR]}",
        ]

    def test_render_sem_contexto_vai_direto_para_a_acao(self) -> None:
        text_ = render_alert(Alert(code=AlertCode.SYNTHETIC, message="ping"))
        assert text_.split("\n") == [
            f"{ALERT_PREFIX} synthetic",
            "ping",
            "",
            f"Ação sugerida: {ALERT_ACTIONS[AlertCode.SYNTHETIC]}",
        ]

    def test_webhook_escapa_mrkdwn_e_o_email_nao(self) -> None:
        alert = Alert(
            code=AlertCode.HEARTBEAT_MISSING,
            message="a & b <!channel>",
            context=((("link", "https://x/<y>"),),),
        )
        webhook = alert.to_webhook_payload()["text"]
        assert "<!channel>" not in webhook
        assert "a &amp; b &lt;!channel&gt;" in webhook
        assert "link: https://x/&lt;y&gt;" in webhook
        assert "a & b <!channel>" in render_alert(alert)

    async def test_email_usa_a_mesma_montagem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: list[str] = []
        _FakeSMTP.bodies.clear()
        _FakeSMTP.subjects.clear()
        monkeypatch.setattr("app.core.alerting.smtplib.SMTP", _FakeSMTP.factory(sent))
        s = _settings(
            ALERT_WEBHOOK_URL=None,
            ALERT_WEBHOOK_URL_SYNTHETIC=None,
            ALERT_EMAIL_TO="plantao@h.com",
            ALERT_SMTP_HOST="smtp.h.com",
        )
        alert = Alert(
            code=AlertCode.HEARTBEAT_MISSING,
            message="1 parada",
            context=((("etapa", "gravacao"),),),
        )
        res = await send_alert(alert, s)
        assert res.email is True
        assert _FakeSMTP.subjects == [f"{ALERT_PREFIX} heartbeat_missing"]
        assert _FakeSMTP.bodies[0].rstrip("\n") == render_alert(alert)

    async def test_falha_do_webhook_loga_so_o_codigo(self) -> None:
        s = _settings(ALERT_WEBHOOK_URL=_WEBHOOK)
        with respx.mock, capture_logs() as logs:
            respx.post(_WEBHOOK).mock(return_value=httpx.Response(500))
            await send_alert(
                Alert(code=AlertCode.HEARTBEAT_MISSING, message="x", context=((("k", "v"),),)),
                s,
            )
        failed = [e for e in logs if e["event"] == "alert_webhook_failed"]
        assert failed == [
            {"event": "alert_webhook_failed", "code": "heartbeat_missing", "log_level": "warning"}
        ]
        assert _WEBHOOK not in repr(logs)
