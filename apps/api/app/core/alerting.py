"""Alerting fail-closed para a equipe de plantão da Hologram (Sprint 3, BACK 03.6).

O ADL é sistema interno; o destinatário é a equipe de plantão (nunca um cliente),
via um canal COMPARTILHADO configurável (`ALERT_WEBHOOK_URL` e/ou `ALERT_EMAIL_TO`).

Princípios (CONTEXT.md Req. 4):
    - **Fail-closed**: em staging/production, subir sem NENHUM canal ENTREGÁVEL
      falha o boot (`verify_alert_config`) — nunca roda com alerting mudo. Em
      dev degrada com warning (não trava o `pnpm dev`).
    - **Async real**: webhook via httpx async; e-mail via smtplib em threadpool
      (`asyncio.to_thread`) — nunca bloqueia o event loop.
    - **SEM PII**: o texto do alerta carrega só `code`, `session_id`, `client_id`,
      uma mensagem-template fixa e linhas de CONTEXTO com IDs, códigos, datas e
      números. Nunca nome de cliente ou de conta, descrição de lançamento nem dado
      do arquivo: o canal é um webhook de terceiro.
    - **Acionável** (86e3mpmcw): todo alerta termina com a AÇÃO SUGERIDA do código
      (`ALERT_ACTIONS`, uma frase por `AlertCode`), e o texto é montado num lugar
      só (`render_alert`), o MESMO para o webhook e para o e-mail.
    - **Segredo fora de log**: a URL do webhook e a senha SMTP NUNCA são logadas.
    - Falha ao entregar o alerta NUNCA derruba a operação que o disparou (cada
      canal é try/except; o dispatch fire-and-forget é blindado).

Fontes de alerta:
    - Sessão em `error` (job.py) — conta como falha mesmo não sendo 5xx (cobre o
      `ADL-PARSE-TRUNCADO` da Sprint 2).
    - Ausência de heartbeat do processamento (cron `mark_stuck_sessions_as_error`).
    - Falha de decifragem (`[indecifrável]`) em review/export.
    - Gatilho SINTÉTICO (endpoint admin) para a 03.7 provar a entrega ponta a ponta.

Canal do sintético (separado):
    O gatilho SINTÉTICO dispara a cada push na `main` (gate do deploy) — ele prova
    entrega, não denuncia incidente. Com `ALERT_WEBHOOK_URL_SYNTHETIC` configurada
    ele sai por um webhook PRÓPRIO e NÃO acorda o plantão (nem por e-mail); sem ela,
    segue no canal de plantão como antes. Essa URL **não** conta como canal
    entregável para o fail-closed (ver `Settings.has_webhook_alert`).
"""

from __future__ import annotations

import asyncio
import smtplib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import EmailMessage
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import httpx

from app.core.branding import PRODUCT_NAME
from app.core.logging import get_logger
from app.core.mrkdwn import escape_mrkdwn

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)

# Marca na frente de todo alerta (texto do webhook, resumo e assunto do e-mail). Era
# "[ADL]" até 30/09/2026: filtro de e-mail ou de canal montado sobre o prefixo antigo
# precisa ser refeito.
ALERT_PREFIX = f"[{PRODUCT_NAME}]"

# Mensagem canônica do gatilho SINTÉTICO — compartilhada pelo endpoint HTTP admin
# (`app/modules/system/routes.py`) e pelo CLI de deploy
# (`app/cli/alert_synthetic_check.py`, o Job da INFRA 03.7). Sem PII.
SYNTHETIC_ALERT_MESSAGE = (
    f"Teste sintetico de alerta do {PRODUCT_NAME} — a chegada desta mensagem prova que o "
    "canal de plantao esta entregando alertas."
)


class AlertConfigError(RuntimeError):
    """Fail-closed: nenhum canal de alerta entregável em staging/production."""


class AlertCode(StrEnum):
    """Códigos canônicos de alerta — sem PII, contáveis no canal."""

    SESSION_ERROR = "session_error"
    HEARTBEAT_MISSING = "heartbeat_missing"
    DECRYPT_FAILED = "decrypt_failed"
    # Conta da Anthropic sem crédito (86e39yzxc): parsing e qualificação param
    # até alguém recarregar. Plantão, nunca o canal do sintético.
    ANTHROPIC_CREDIT = "anthropic_credit"
    # S9 (BACK 09.5): alguém desligou `LEGACY_CREDENTIALS_FALLBACK_ENABLED`
    # sem a conversão ter terminado. O fallback fica EFETIVAMENTE ligado (nada
    # quebra), mas a intenção e o estado divergiram — e isso precisa de gente
    # olhando. **Plantão, nunca o canal do sintético.**
    LEGACY_FALLBACK = "legacy_fallback"
    # S13 (BACK 13.4): o download regenerou o arquivo contábil e o SHA-256 não bateu
    # com o registrado na geração — o arquivo NÃO é entregue. Materialização e layout
    # são imutáveis, então divergir é defeito (ou dado adulterado): gente olhando.
    # Nasce no canal de PLANTÃO (§3.14), nunca no do sintético. Só IDs na mensagem.
    ACCOUNTING_FILE_DIVERGENT = "accounting_file_divergent"
    SYNTHETIC = "synthetic"


#: O que fazer, numa frase, para cada `AlertCode` (86e3mpmcw). É o fim de todo
#: alerta: quem está de plantão lê o problema e a primeira ação no mesmo lugar.
#: Código novo sem ação reprova `test_toda_alert_code_tem_acao`.
ALERT_ACTIONS: Mapping[AlertCode, str] = MappingProxyType(
    {
        AlertCode.SESSION_ERROR: (
            "Abra a conciliação, leia o código do erro, corrija a causa (credencial da "
            "origem, arquivo ou crédito da IA) e use Tentar novamente."
        ),
        AlertCode.HEARTBEAT_MISSING: (
            "Abra a conciliação e use Tentar novamente; se repetir, confira a origem do "
            "cliente e o crédito da IA."
        ),
        AlertCode.DECRYPT_FAILED: (
            "Confira se o serviço alcança a chave no Cloud KMS e se o cliente tem a chave "
            "de dados; não troque KEK_KEY_ID nem OMIE_ENCRYPTION_KEY sem o roteiro do runbook."
        ),
        AlertCode.ANTHROPIC_CREDIT: (
            "Recarregue o crédito no console da Anthropic (Billing). Não é preciso "
            "reiniciar nada: a próxima chamada volta a funcionar. Conciliações processadas "
            "sem crédito ficaram sem os vereditos da IA; reprocessar refaz a análise (e "
            "descarta a revisão já feita)."
        ),
        AlertCode.LEGACY_FALLBACK: (
            "Rode scripts/convert_credentials_to_connections.py com --verify até não sobrar "
            "cliente pendente; até lá o fallback segue ligado."
        ),
        AlertCode.ACCOUNTING_FILE_DIVERGENT: (
            "Não gere o arquivo por fora: compare a geração com a materialização e o layout "
            "dela e trate como incidente; o download segue bloqueado."
        ),
        AlertCode.SYNTHETIC: (
            "Nenhuma: é o teste do canal disparado pelo deploy. Se ele deixar de chegar, "
            "confira o webhook de alertas."
        ),
    }
)

#: Uma linha de contexto do alerta: (rótulo, valor). SÓ IDs, códigos, datas e
#: números — nunca nome, descrição ou texto livre (§4.5, §4.7).
AlertContextLine = tuple[str, str]


def _identity(value: str) -> str:
    return value


@dataclass(frozen=True)
class Alert:
    """Um alerta. SÓ IDs/códigos — NUNCA PII (descrição, nome, dado do arquivo).

    `context` são grupos de linhas de chave e valor (um grupo por item, por
    exemplo uma sessão travada); `context_omitted` conta os itens que ficaram de
    fora do teto de quem montou o alerta e vira a linha "e mais N".
    """

    code: AlertCode
    message: str
    session_id: str | None = None
    client_id: str | None = None
    context: tuple[tuple[AlertContextLine, ...], ...] = ()
    context_omitted: int = 0

    def _fields(self) -> dict[str, str]:
        data = {"service": "adl", "code": self.code.value}
        if self.session_id is not None:
            data["session_id"] = self.session_id
        if self.client_id is not None:
            data["client_id"] = self.client_id
        return data

    def to_webhook_payload(self) -> dict[str, Any]:
        # `text` torna o payload compatível com Slack/Discord/webhooks genéricos. As
        # chaves de topo continuam as mesmas (só IDs): o contexto vai no texto.
        return {"text": render_alert(self, escape=escape_mrkdwn), **self._fields()}


def render_alert(alert: Alert, *, escape: Callable[[str], str] = _identity) -> str:
    """A montagem ÚNICA do texto do alerta, usada pelo webhook E pelo e-mail.

    Título (`[Hologram OS] <code>`), a mensagem, o contexto em linhas de chave e
    valor (os IDs de topo primeiro, depois um bloco por grupo, depois "e mais N")
    e a ação sugerida de `ALERT_ACTIONS`. `escape` é aplicado a todo texto abaixo
    do título: o webhook passa `escape_mrkdwn` (o Slack interpreta `&`, `<` e `>`);
    o e-mail, texto puro, não escapa.
    """

    def kv(label: str, value: str) -> str:
        return f"{escape(label)}: {escape(value)}"

    lines = [f"{ALERT_PREFIX} {alert.code.value}", escape(alert.message)]
    top: list[AlertContextLine] = []
    if alert.session_id is not None:
        top.append(("sessão", alert.session_id))
    if alert.client_id is not None:
        top.append(("cliente", alert.client_id))
    for group in (tuple(top), *alert.context):
        if group:
            lines.append("")
            lines.extend(kv(label, value) for label, value in group)
    if alert.context_omitted > 0:
        lines.extend(["", escape(f"e mais {alert.context_omitted}")])
    lines.extend(["", kv("Ação sugerida", ALERT_ACTIONS[alert.code])])
    return "\n".join(lines)


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


def _routes_to_synthetic_channel(alert: Alert, settings: Settings) -> bool:
    """True quando ESTE alerta deve sair pelo canal separado do sintético.

    Só o `SYNTHETIC` desvia, e só quando há webhook próprio configurado. Sem a
    setting, tudo segue para o plantão exatamente como antes.
    """
    return alert.code is AlertCode.SYNTHETIC and bool(settings.ALERT_WEBHOOK_URL_SYNTHETIC)


def _webhook_url_for(alert: Alert, settings: Settings) -> str | None:
    if _routes_to_synthetic_channel(alert, settings):
        return settings.ALERT_WEBHOOK_URL_SYNTHETIC
    return settings.ALERT_WEBHOOK_URL


async def _send_webhook(alert: Alert, settings: Settings, url: str) -> bool:
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
        msg["Subject"] = f"{ALERT_PREFIX} {alert.code.value}"
        msg["From"] = settings.ALERT_EMAIL_FROM
        msg["To"] = to_addr
        msg.set_content(render_alert(alert))
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
    canal é isolado. Retorna o resultado por canal (usado pelo gatilho sintético).

    Exceção única: o SINTÉTICO com canal próprio (`ALERT_WEBHOOK_URL_SYNTHETIC`)
    vai SÓ para lá — sem e-mail de plantão junto, senão o desvio não tira o
    ruído de onde ele incomoda.
    """
    to_synthetic_channel = _routes_to_synthetic_channel(alert, settings)
    url = _webhook_url_for(alert, settings)
    webhook = await _send_webhook(alert, settings, url) if url else None
    email = (
        await _send_email(alert, settings)
        if settings.has_email_alert and not to_synthetic_channel
        else None
    )
    log.info(
        "alert_dispatched",
        code=alert.code.value,
        session_id=alert.session_id,
        client_id=alert.client_id,
        webhook=webhook,
        email=email,
        # Qual canal recebeu — nunca a URL (segredo).
        channel="synthetic" if to_synthetic_channel else "oncall",
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
