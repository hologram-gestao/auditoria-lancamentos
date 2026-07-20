"""Alerting fail-closed para a equipe de plantão da Hologram (Sprint 3, BACK 03.6).

O ADL é sistema interno; o destinatário é a equipe de plantão (nunca um cliente),
via um canal COMPARTILHADO configurável (`ALERT_WEBHOOK_URL` e/ou `ALERT_EMAIL_TO`).

Princípios (CONTEXT.md Req. 4):
    - **Fail-closed**: em staging/production, subir sem NENHUM canal ENTREGÁVEL
      falha o boot (`verify_alert_config`) — nunca roda com alerting mudo. Em
      dev degrada com warning (não trava o `pnpm dev`).
    - **Async real**: webhook via httpx async; e-mail via smtplib em threadpool
      (`asyncio.to_thread`) — nunca bloqueia o event loop.
    - **SEM PII**: o texto do alerta carrega só `code`, `session_id`, `client_id`
      e uma mensagem-template fixa. Nunca descrição de lançamento nem dado do
      arquivo.
    - **Segredo fora de log**: a URL do webhook e a senha SMTP NUNCA são logadas.
    - Falha ao entregar o alerta NUNCA derruba a operação que o disparou (cada
      canal é try/except; o dispatch fire-and-forget é blindado).

Fontes de alerta:
    - Sessão em `error` (job.py) — conta como falha mesmo não sendo 5xx (cobre o
      `ADL-PARSE-TRUNCADO` da Sprint 2).
    - Ausência de heartbeat do processamento (cron `mark_stuck_sessions_as_error`).
    - Falha de decifragem (`[indecifrável]`) em review/export.
    - Gatilho SINTÉTICO (endpoint admin) para a 03.7 provar a entrega ponta a ponta.
"""

from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)

# Mensagem canônica do gatilho SINTÉTICO — compartilhada pelo endpoint HTTP admin
# (`app/modules/system/routes.py`) e pelo CLI de deploy
# (`app/cli/alert_synthetic_check.py`, o Job da INFRA 03.7). Sem PII.
SYNTHETIC_ALERT_MESSAGE = (
    "Teste sintetico de alerta do ADL — a chegada desta mensagem prova que o "
    "canal de plantao esta entregando alertas."
)


class AlertConfigError(RuntimeError):
    """Fail-closed: nenhum canal de alerta entregável em staging/production."""


class AlertCode(StrEnum):
    """Códigos canônicos de alerta — sem PII, contáveis no canal."""

    SESSION_ERROR = "session_error"
    HEARTBEAT_MISSING = "heartbeat_missing"
    DECRYPT_FAILED = "decrypt_failed"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class Alert:
    """Um alerta. SÓ IDs/códigos — NUNCA PII (descrição, nome, dado do arquivo)."""

    code: AlertCode
    message: str
    session_id: str | None = None
    client_id: str | None = None

    def _fields(self) -> dict[str, str]:
        data = {"service": "adl", "code": self.code.value}
        if self.session_id is not None:
            data["session_id"] = self.session_id
        if self.client_id is not None:
            data["client_id"] = self.client_id
        return data

    def to_webhook_payload(self) -> dict[str, Any]:
        # `text` torna o payload compatível com Slack/Discord/webhooks genéricos.
        return {"text": f"[ADL] {self.code.value}: {self.message}", **self._fields()}

    def summary_line(self) -> str:
        extras = " ".join(f"{k}={v}" for k, v in self._fields().items())
        return f"[ADL] {self.code.value}: {self.message} ({extras})"


@dataclass(frozen=True)
class AlertDeliveryResult:
    """Resultado por canal: True=entregue, False=falhou, None=não configurado."""

    webhook: bool | None
    email: bool | None

    @property
    def delivered(self) -> bool:
        return self.webhook is True or self.email is True


def verify_alert_config(settings: Settings) -> None:
    """Fail-closed no boot: exige ao menos um canal ENTREGÁVEL em staging/prod.

    Em development, apenas loga um warning (não trava o dev local). Chamado no
    `lifespan` da app, junto dos demais fail-fast de startup.
    """
    from app.core.config import Environment  # local: evita ciclo no import de módulo

    if settings.has_alert_channel:
        return
    if settings.ENVIRONMENT in (Environment.STAGING, Environment.PRODUCTION):
        raise AlertConfigError(
            "Nenhum canal de alerta configurado (ALERT_WEBHOOK_URL e/ou "
            "ALERT_EMAIL_TO+ALERT_SMTP_HOST). Fail-closed: o serviço não sobe com "
            f"alerting mudo em ENVIRONMENT={settings.ENVIRONMENT.value}."
        )
    log.warning("alerting_not_configured_dev", environment=settings.ENVIRONMENT.value)


async def _send_webhook(alert: Alert, settings: Settings) -> bool:
    url = settings.ALERT_WEBHOOK_URL
    if not url:  # pragma: no cover - guardado pelo caller
        return False
    try:
        async with httpx.AsyncClient(timeout=settings.ALERT_WEBHOOK_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=alert.to_webhook_payload())
            resp.raise_for_status()
        return True
    except Exception:
        # NUNCA logar a URL (segredo). Só o código do alerta.
        log.warning("alert_webhook_failed", code=alert.code.value)
        return False


async def _send_email(alert: Alert, settings: Settings) -> bool:
    host = settings.ALERT_SMTP_HOST
    to_addr = settings.ALERT_EMAIL_TO
    if not host or not to_addr:  # pragma: no cover - guardado pelo caller
        return False

    def _send() -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[ADL] {alert.code.value}"
        msg["From"] = settings.ALERT_EMAIL_FROM
        msg["To"] = to_addr
        msg.set_content(alert.summary_line())
        with smtplib.SMTP(host, settings.ALERT_SMTP_PORT, timeout=10) as server:
            server.starttls()
            if settings.ALERT_SMTP_USER:
                server.login(
                    settings.ALERT_SMTP_USER,
                    settings.ALERT_SMTP_PASSWORD.get_secret_value(),
                )
            server.send_message(msg)

    try:
        await asyncio.to_thread(_send)
        return True
    except Exception:
        # NUNCA logar credencial SMTP nem o corpo. Só o código.
        log.warning("alert_email_failed", code=alert.code.value)
        return False


async def send_alert(alert: Alert, settings: Settings) -> AlertDeliveryResult:
    """Dispara o alerta a TODOS os canais configurados. Nunca levanta — cada
    canal é isolado. Retorna o resultado por canal (usado pelo gatilho sintético)."""
    webhook = await _send_webhook(alert, settings) if settings.has_webhook_alert else None
    email = await _send_email(alert, settings) if settings.has_email_alert else None
    log.info(
        "alert_dispatched",
        code=alert.code.value,
        session_id=alert.session_id,
        client_id=alert.client_id,
        webhook=webhook,
        email=email,
    )
    return AlertDeliveryResult(webhook=webhook, email=email)


# Fire-and-forget: tarefas de alerta em background (contextos síncronos como os
# helpers de decrypt). Mantemos referência forte para o GC não coletar a task no
# meio do voo (padrão recomendado do asyncio).
_background_alert_tasks: set[asyncio.Task[Any]] = set()


async def _safe_send(alert: Alert, settings: Settings) -> None:
    try:
        await send_alert(alert, settings)
    except Exception:  # pragma: no cover - send_alert já é blindado
        log.warning("alert_dispatch_failed", code=alert.code.value)


def dispatch_alert_nowait(alert: Alert, settings: Settings) -> None:
    """Agenda o envio do alerta sem bloquear o chamador (fire-and-forget).

    Para uso em caminhos SÍNCRONOS dentro de uma request async (ex.: helpers de
    decrypt). Se não houver event loop rodando, degrada para log — nunca levanta.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("alert_no_running_loop", code=alert.code.value)
        return
    task = loop.create_task(_safe_send(alert, settings))
    _background_alert_tasks.add(task)
    task.add_done_callback(_background_alert_tasks.discard)
