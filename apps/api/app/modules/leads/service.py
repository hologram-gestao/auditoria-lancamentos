"""Regra de negócio da captação de leads (86e3fr9ut), nesta ordem:

    1. **Honeypot** preenchido → sucesso sem gravar nem avisar. O log é só o nome
       do evento: nenhum campo, nem o do próprio honeypot.
    2. **Limite por e-mail**: 3 leads do mesmo e-mail (sem caixa) em 24 h → o 4º
       recebe sucesso sem gravar nem avisar. Silencioso: quem abusa não distingue.
    3. **Grava** o lead com `consent_at` (relógio do servidor) e a versão do texto.
    4. **Avisa o Slack** (fail-soft, timeout curto); aceitou → `notified_at` na
       mesma transação.
    5. **Métrica** `lead_recebido` (fail-soft), só booleanos.

A defesa principal contra abuso é 1 + 2. O `slowapi` da rota é teto global por
instância (atrás do BFF o IP visto é o do proxy, 86e3anx10): rede de segurança,
não a porta.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.db.models.lead import LEAD_SOURCE_LANDING, Lead
from app.modules.leads.notifier import LeadNotice
from app.modules.leads.schemas import CONSENT_TEXT_VERSION

if TYPE_CHECKING:
    from app.modules.leads.notifier import LeadNotifier
    from app.modules.leads.repository import LeadRepository
    from app.modules.leads.schemas import LeadCreate
    from app.modules.usage_events.service import UsageEventService

log = get_logger(__name__)

#: Janela e teto do limite por e-mail.
EMAIL_THROTTLE_WINDOW = timedelta(hours=24)
EMAIL_THROTTLE_MAX = 3


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LeadService:
    def __init__(
        self,
        repository: LeadRepository,
        *,
        notifier: LeadNotifier,
        usage_events: UsageEventService,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repo = repository
        self._notifier = notifier
        self._usage_events = usage_events
        self._clock = clock

    async def receive(self, payload: LeadCreate) -> None:
        """Processa um envio do formulário. Não devolve nada: a resposta é sempre a mesma."""
        if payload.is_honeypot_hit:
            log.info("lead_honeypot_hit")
            return

        recent = await self._repo.count_recent_by_email(payload.email, window=EMAIL_THROTTLE_WINDOW)
        if recent >= EMAIL_THROTTLE_MAX:
            log.info("lead_throttled")
            return

        received_at = self._clock()
        lead = await self._repo.add(
            Lead(
                name=payload.name,
                email=payload.email,
                company=payload.company,
                whatsapp=payload.whatsapp,
                message=payload.message,
                consent_at=received_at,
                consent_text_version=CONSENT_TEXT_VERSION,
                source=LEAD_SOURCE_LANDING,
            )
        )

        notified = await self._notifier.notify(
            LeadNotice(
                name=payload.name,
                email=payload.email,
                company=payload.company,
                whatsapp=payload.whatsapp,
                message=payload.message,
                received_at=received_at,
            )
        )
        if notified:
            await self._repo.mark_notified(lead, at=self._clock())

        await self._usage_events.emit_lead_recebido(
            has_company=payload.company is not None,
            has_whatsapp=payload.whatsapp is not None,
            has_message=payload.message is not None,
            notified=notified,
        )
