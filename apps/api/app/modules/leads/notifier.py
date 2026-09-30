"""Aviso de lead novo no canal da ADL no Slack (86e3fr9ut).

Incoming webhook do Slack: um POST com `text` (fallback de notificação) e `blocks`.
Três regras que não podem ser erradas:

    - **fail-soft**: o lead já está gravado quando o aviso sai. Timeout, resposta
      não-2xx ou exceção de transporte viram `False` e UM log com a categoria da
      falha; nada sobe para a rota;
    - **nada do lead e nada da URL em log**: a URL é credencial (quem a tem posta no
      canal) e os campos do lead são dado pessoal. O log leva só a categoria
      (`timeout`, `http_<status>`, `transport`);
    - **mrkdwn escapado**: o Slack interpreta `&`, `<` e `>` (`<url|texto>` vira
      link, `<!channel>` vira menção). Todo campo digitado pelo visitante passa por
      `escape_mrkdwn` antes de entrar no payload, e a mensagem é truncada.

Timeout curto (3 s) porque o aviso é inline: o visitante espera por ele.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from pydantic import SecretStr

log = get_logger(__name__)

LEAD_NOTIFY_TIMEOUT_SECONDS = 3.0

#: Teto da mensagem no aviso. A mensagem inteira continua na tabela.
SLACK_MESSAGE_MAX = 500

#: Brasil sem horário de verão desde 2019: UTC-3 fixo, como o export e a
#: competência (`client_movements/competence.py`) — equivale a America/Sao_Paulo.
_BRT = timezone(timedelta(hours=-3))

_NOT_INFORMED = "não informado"


@dataclass(frozen=True, slots=True)
class LeadNotice:
    """O que o aviso mostra. Montado pelo service a partir do lead gravado."""

    name: str
    email: str
    company: str | None
    whatsapp: str | None
    message: str | None
    received_at: datetime


class LeadNotifier(Protocol):
    async def notify(self, notice: LeadNotice) -> bool:
        """Devolve `True` só se o destino ACEITOU o aviso."""
        ...


def escape_mrkdwn(value: str) -> str:
    """Escapa os três caracteres de controle do mrkdwn do Slack (`&` primeiro)."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def build_slack_payload(notice: LeadNotice) -> dict[str, Any]:
    """Payload do incoming webhook. Pura: testável sem rede."""

    def field(value: str | None) -> str:
        return escape_mrkdwn(value) if value else f"_{_NOT_INFORMED}_"

    message = _truncate(notice.message, SLACK_MESSAGE_MAX) if notice.message else None
    when = notice.received_at.astimezone(_BRT).strftime("%d/%m/%Y %H:%M")
    name = escape_mrkdwn(notice.name)
    lines = [
        f"*Nome:* {name}",
        f"*E-mail:* {escape_mrkdwn(notice.email)}",
        f"*Empresa:* {field(notice.company)}",
        f"*WhatsApp:* {field(notice.whatsapp)}",
        f"*Mensagem:* {field(message)}",
        f"*Recebido em:* {when} (horário de Brasília)",
    ]
    return {
        "text": f"Novo contato pela landing: {name}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Novo contato pela landing"},
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ],
    }


class SlackLeadNotifier:
    """Envia o aviso para o incoming webhook configurado em `LEADS_SLACK_WEBHOOK_URL`."""

    def __init__(
        self,
        webhook_url: SecretStr | None,
        *,
        timeout: float = LEAD_NOTIFY_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        raw = webhook_url.get_secret_value().strip() if webhook_url is not None else ""
        self._url = raw or None
        self._timeout = timeout
        self._transport = transport

    async def notify(self, notice: LeadNotice) -> bool:
        if self._url is None:
            log.info("lead_notification_skipped", reason="no_webhook")
            return False
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.post(self._url, json=build_slack_payload(notice))
        except httpx.TimeoutException:
            log.warning("lead_notification_failed", reason="timeout")
            return False
        except Exception:
            # Sem `exc_info`: a mensagem de erro do httpx carrega a URL.
            log.warning("lead_notification_failed", reason="transport")
            return False
        if not resp.is_success:
            log.warning("lead_notification_failed", reason=f"http_{resp.status_code}")
            return False
        return True
