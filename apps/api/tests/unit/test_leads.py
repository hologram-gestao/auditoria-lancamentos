"""Captação de leads da landing (86e3fr9ut) — unitários, sem banco e sem rede.

Cobre:
    - Os limites do schema são os das colunas, e a migration copia os mesmos números.
    - Forma: consentimento falso, nome curto, e-mail inválido, WhatsApp com letra e
      campo além do teto são recusados; opcional vazio vira `None`.
    - Service: honeypot e limite por e-mail não gravam nem avisam; o lead gravado
      leva consentimento e versão; Slack fora do ar não impede a gravação.
    - Notificador: escapa o mrkdwn, trunca a mensagem, e cada falha loga só a
      categoria. **Nenhum campo do lead nem a URL do webhook em log**, provado com
      `structlog.testing.capture_logs` (o `caplog` não vê o structlog).
    - A setting do webhook não conta como canal de plantão.
    - O evento `lead_recebido` é de backend e fica fora da dedup.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

from app.core.config import Settings
from app.db.models import Lead
from app.db.models import lead as lead_model
from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.modules.leads.notifier import (
    SLACK_MESSAGE_MAX,
    LeadNotice,
    SlackLeadNotifier,
    build_slack_payload,
    escape_mrkdwn,
)
from app.modules.leads.schemas import CONSENT_TEXT_VERSION, LeadCreate
from app.modules.leads.service import EMAIL_THROTTLE_MAX, EMAIL_THROTTLE_WINDOW, LeadService
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    LeadRecebidoProps,
    UsageEventName,
)

WEBHOOK = "https://hooks.slack.test/services/T000/B000/segredo-do-webhook"
NAME = "Maria Contadora"
EMAIL = "maria@escritorio-exemplo.com.br"
COMPANY = "Escritório Exemplo"
WHATSAPP = "+55 (71) 99999-0000"
MESSAGE = "Quero conhecer a plataforma para meus clientes."
_LEAD_VALUES = (NAME, EMAIL, COMPANY, WHATSAPP, MESSAGE, WEBHOOK, "segredo-do-webhook")

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "490bffa3f6e2_landing_leads.py"
)


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": NAME,
        "email": EMAIL,
        "company": COMPANY,
        "whatsapp": WHATSAPP,
        "message": MESSAGE,
        "consent": True,
    }
    body.update(overrides)
    return body


def _notice(**overrides: Any) -> LeadNotice:
    values: dict[str, Any] = {
        "name": NAME,
        "email": EMAIL,
        "company": COMPANY,
        "whatsapp": WHATSAPP,
        "message": MESSAGE,
        "received_at": datetime(2026, 9, 30, 13, 5, tzinfo=UTC),
    }
    values.update(overrides)
    return LeadNotice(**values)


def _assert_no_lead_data(logs: list[dict[str, Any]]) -> None:
    dumped = json.dumps(logs, default=str)
    for value in _LEAD_VALUES:
        assert value not in dumped, f"valor do lead ou do webhook apareceu no log: {value!r}"


# ----------------------------------------------------------------------
# Schema e colunas
# ----------------------------------------------------------------------


class TestLimitsFollowTheColumns:
    def test_schema_limits_are_the_column_limits(self) -> None:
        props = LeadCreate.model_json_schema()["properties"]
        assert props["name"]["maxLength"] == lead_model.LEAD_NAME_MAX
        company = next(o for o in props["company"]["anyOf"] if o.get("type") == "string")
        whatsapp = next(o for o in props["whatsapp"]["anyOf"] if o.get("type") == "string")
        message = next(o for o in props["message"]["anyOf"] if o.get("type") == "string")
        assert company["maxLength"] == lead_model.LEAD_COMPANY_MAX
        assert whatsapp["maxLength"] == lead_model.LEAD_WHATSAPP_MAX
        assert message["maxLength"] == lead_model.LEAD_MESSAGE_MAX

    def test_model_columns_use_the_constants(self) -> None:
        cols = Lead.__table__.columns
        assert cols["name"].type.length == lead_model.LEAD_NAME_MAX  # type: ignore[attr-defined]
        assert cols["email"].type.length == lead_model.LEAD_EMAIL_MAX  # type: ignore[attr-defined]
        assert cols["company"].type.length == lead_model.LEAD_COMPANY_MAX  # type: ignore[attr-defined]
        assert cols["whatsapp"].type.length == lead_model.LEAD_WHATSAPP_MAX  # type: ignore[attr-defined]
        assert cols["message"].type.length == lead_model.LEAD_MESSAGE_MAX  # type: ignore[attr-defined]
        assert cols["consent_text_version"].type.length == lead_model.LEAD_CONSENT_VERSION_MAX  # type: ignore[attr-defined]

    def test_migration_snapshots_match_the_model(self) -> None:
        spec = importlib.util.spec_from_file_location("landing_leads_migration", _MIGRATION)
        assert spec is not None
        assert spec.loader is not None
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        assert mig._NAME_MAX == lead_model.LEAD_NAME_MAX
        assert mig._EMAIL_MAX == lead_model.LEAD_EMAIL_MAX
        assert mig._COMPANY_MAX == lead_model.LEAD_COMPANY_MAX
        assert mig._WHATSAPP_MAX == lead_model.LEAD_WHATSAPP_MAX
        assert mig._MESSAGE_MAX == lead_model.LEAD_MESSAGE_MAX
        assert mig._CONSENT_VERSION_MAX == lead_model.LEAD_CONSENT_VERSION_MAX
        assert mig._SOURCE_MAX == lead_model.LEAD_SOURCE_MAX
        assert mig._SOURCE_LANDING == lead_model.LEAD_SOURCE_LANDING
        assert mig._INDEX == lead_model.IX_LEADS_EMAIL_LOWER_CREATED_AT

    def test_consent_version_fits_its_column(self) -> None:
        assert len(CONSENT_TEXT_VERSION) <= lead_model.LEAD_CONSENT_VERSION_MAX


class TestShape:
    def test_valid_payload_is_accepted_and_trimmed(self) -> None:
        lead = LeadCreate.model_validate(_payload(name=f"  {NAME}  ", company="   "))
        assert lead.name == NAME
        assert lead.company is None
        assert lead.website == ""
        assert not lead.is_honeypot_hit

    @pytest.mark.parametrize("consent", [False, None, "true", 1])
    def test_consent_must_be_literally_true(self, consent: object) -> None:
        with pytest.raises(ValidationError):
            LeadCreate.model_validate(_payload(consent=consent))

    def test_consent_is_required(self) -> None:
        body = _payload()
        del body["consent"]
        with pytest.raises(ValidationError):
            LeadCreate.model_validate(body)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"name": "M"},
            {"name": "   "},
            {"name": "x" * (lead_model.LEAD_NAME_MAX + 1)},
            {"email": "nao-e-email"},
            {"company": "x" * (lead_model.LEAD_COMPANY_MAX + 1)},
            {"whatsapp": "ligue 7199"},
            {"whatsapp": "1" * (lead_model.LEAD_WHATSAPP_MAX + 1)},
            {"message": "x" * (lead_model.LEAD_MESSAGE_MAX + 1)},
            {"website": "x" * 201},
            {"unknown_field": "x"},
        ],
    )
    def test_invalid_shape_is_refused(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            LeadCreate.model_validate(_payload(**overrides))

    def test_filled_honeypot_is_accepted_by_the_schema(self) -> None:
        """O bot tem de receber a mesma resposta: o schema aceita, o service descarta."""
        lead = LeadCreate.model_validate(_payload(website="https://spam.example"))
        assert lead.is_honeypot_hit


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------


class _FakeNotifier:
    def __init__(self, *, accepts: bool) -> None:
        self.accepts = accepts
        self.notices: list[LeadNotice] = []

    async def notify(self, notice: LeadNotice) -> bool:
        self.notices.append(notice)
        return self.accepts


def _service(
    *, recent: int = 0, accepts: bool = True
) -> tuple[LeadService, AsyncMock, _FakeNotifier, AsyncMock]:
    repo = AsyncMock()
    repo.count_recent_by_email.return_value = recent
    repo.add.side_effect = lambda lead: lead
    notifier = _FakeNotifier(accepts=accepts)
    usage = AsyncMock()
    fixed = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    service = LeadService(repo, notifier=notifier, usage_events=usage, clock=lambda: fixed)
    return service, repo, notifier, usage


class TestService:
    async def test_honeypot_does_not_write_nor_notify(self) -> None:
        service, repo, notifier, usage = _service()
        with capture_logs() as logs:
            await service.receive(LeadCreate.model_validate(_payload(website="spam")))
        repo.count_recent_by_email.assert_not_called()
        repo.add.assert_not_called()
        assert notifier.notices == []
        usage.emit_lead_recebido.assert_not_called()
        assert [e["event"] for e in logs] == ["lead_honeypot_hit"]
        assert logs[0].keys() <= {"event", "log_level"}
        _assert_no_lead_data(logs)

    async def test_email_throttle_on_the_fourth_submission(self) -> None:
        service, repo, notifier, usage = _service(recent=EMAIL_THROTTLE_MAX)
        with capture_logs() as logs:
            await service.receive(LeadCreate.model_validate(_payload()))
        repo.count_recent_by_email.assert_awaited_once_with(EMAIL, window=EMAIL_THROTTLE_WINDOW)
        repo.add.assert_not_called()
        assert notifier.notices == []
        usage.emit_lead_recebido.assert_not_called()
        assert [e["event"] for e in logs] == ["lead_throttled"]
        _assert_no_lead_data(logs)

    def test_throttle_is_three_per_day(self) -> None:
        assert EMAIL_THROTTLE_MAX == 3
        assert timedelta(hours=24) == EMAIL_THROTTLE_WINDOW

    async def test_third_submission_is_still_recorded(self) -> None:
        service, repo, _, _ = _service(recent=EMAIL_THROTTLE_MAX - 1)
        await service.receive(LeadCreate.model_validate(_payload()))
        repo.add.assert_awaited_once()

    async def test_records_consent_notifies_and_marks_notified(self) -> None:
        service, repo, notifier, usage = _service(accepts=True)
        with capture_logs() as logs:
            await service.receive(LeadCreate.model_validate(_payload(whatsapp="")))
        lead: Lead = repo.add.await_args.args[0]
        assert lead.name == NAME
        assert lead.email == EMAIL
        assert lead.whatsapp is None
        assert lead.consent_at == datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
        assert lead.consent_text_version == CONSENT_TEXT_VERSION
        assert lead.source == "landing"
        assert len(notifier.notices) == 1
        repo.mark_notified.assert_awaited_once()
        usage.emit_lead_recebido.assert_awaited_once_with(
            has_company=True, has_whatsapp=False, has_message=True, notified=True
        )
        _assert_no_lead_data(logs)

    async def test_slack_down_still_records_the_lead(self) -> None:
        service, repo, _, usage = _service(accepts=False)
        await service.receive(LeadCreate.model_validate(_payload()))
        repo.add.assert_awaited_once()
        repo.mark_notified.assert_not_called()
        assert usage.emit_lead_recebido.await_args.kwargs["notified"] is False


# ----------------------------------------------------------------------
# Notificador
# ----------------------------------------------------------------------


class TestSlackPayload:
    def test_escape_mrkdwn_escapes_the_three_control_chars(self) -> None:
        assert escape_mrkdwn("a & b <!channel> <https://x|y>") == (
            "a &amp; b &lt;!channel&gt; &lt;https://x|y&gt;"
        )

    def test_visitor_text_cannot_mention_or_link(self) -> None:
        payload = build_slack_payload(
            _notice(name="<!channel> Fulano", message="veja <https://mal.example|aqui> & tal")
        )
        text = payload["blocks"][1]["text"]["text"]
        assert "<!channel>" not in text
        assert "<https://mal.example" not in text
        assert "&lt;!channel&gt; Fulano" in text
        assert "&amp; tal" in text
        assert "<!channel>" not in payload["text"]

    def test_message_is_truncated(self) -> None:
        payload = build_slack_payload(_notice(message="a" * 900))
        text = payload["blocks"][1]["text"]["text"]
        line = next(ln for ln in text.splitlines() if ln.startswith("*Mensagem:*"))
        assert len(line.removeprefix("*Mensagem:* ")) == SLACK_MESSAGE_MAX
        assert line.endswith("…")

    def test_missing_optionals_say_not_informed_and_time_is_brasilia(self) -> None:
        payload = build_slack_payload(_notice(company=None, whatsapp=None, message=None))
        text = payload["blocks"][1]["text"]["text"]
        assert "*Empresa:* _não informado_" in text
        assert "*WhatsApp:* _não informado_" in text
        assert "*Mensagem:* _não informado_" in text
        # 13:05 UTC = 10:05 em Brasília.
        assert "*Recebido em:* 30/09/2026 10:05 (horário de Brasília)" in text


def _notifier(handler: Any, url: str | None = WEBHOOK) -> SlackLeadNotifier:
    return SlackLeadNotifier(
        SecretStr(url) if url is not None else None, transport=httpx.MockTransport(handler)
    )


class TestSlackNotifier:
    async def test_delivers_to_the_webhook(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text="ok")

        with capture_logs() as logs:
            assert await _notifier(handler).notify(_notice()) is True
        assert str(seen[0].url) == WEBHOOK
        assert json.loads(seen[0].content)["text"].startswith("Novo contato pela landing")
        assert logs == []

    @pytest.mark.parametrize("url", [None, "", "   "])
    async def test_without_webhook_skips_and_logs_once(self, url: str | None) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("não deveria chamar a rede")

        with capture_logs() as logs:
            assert await _notifier(handler, url).notify(_notice()) is False
        assert [e["event"] for e in logs] == ["lead_notification_skipped"]
        _assert_no_lead_data(logs)

    @pytest.mark.parametrize(
        ("handler_exc", "status", "reason"),
        [
            (httpx.ReadTimeout("timed out"), None, "timeout"),
            (httpx.ConnectError(f"cannot reach {WEBHOOK}"), None, "transport"),
            (None, 500, "http_500"),
            (None, 404, "http_404"),
        ],
    )
    async def test_failure_logs_only_the_category(
        self, handler_exc: Exception | None, status: int | None, reason: str
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if handler_exc is not None:
                raise handler_exc
            assert status is not None
            return httpx.Response(status, text=f"invalid_payload for {NAME}")

        with capture_logs() as logs:
            assert await _notifier(handler).notify(_notice()) is False
        assert len(logs) == 1
        assert logs[0]["event"] == "lead_notification_failed"
        assert logs[0]["reason"] == reason
        assert logs[0].keys() <= {"event", "log_level", "reason"}
        _assert_no_lead_data(logs)


# ----------------------------------------------------------------------
# Configuração e métrica
# ----------------------------------------------------------------------


class TestWebhookIsNotAnAlertChannel:
    def test_leads_webhook_alone_is_not_an_alert_channel(self) -> None:
        settings = Settings(
            DATABASE_URL="postgresql+psycopg://x:y@localhost:5432/z",
            OMIE_ENCRYPTION_KEY="0" * 64,
            JWT_SECRET="1" * 64,
            SEARCH_BLIND_INDEX_KEY="2" * 64,
            LEADS_SLACK_WEBHOOK_URL=SecretStr(WEBHOOK),
            ALERT_WEBHOOK_URL=None,
            ALERT_EMAIL_TO=None,
        )
        assert settings.has_webhook_alert is False
        assert settings.has_alert_channel is False
        assert WEBHOOK not in repr(settings)


class TestLeadRecebidoEvent:
    def test_is_backend_only_and_not_deduped(self) -> None:
        assert UsageEventName.LEAD_RECEBIDO not in CLIENT_EMITTED_EVENTS
        assert UsageEventName.LEAD_RECEBIDO.value not in DEDUPED_EVENT_NAMES

    def test_props_have_no_text_field(self) -> None:
        fields = LeadRecebidoProps.model_fields
        assert set(fields) == {"source", "has_company", "has_whatsapp", "has_message", "notified"}
        with pytest.raises(ValidationError):
            LeadRecebidoProps.model_validate(
                {
                    "source": "landing",
                    "has_company": True,
                    "has_whatsapp": False,
                    "has_message": False,
                    "notified": True,
                    "email": EMAIL,
                }
            )
