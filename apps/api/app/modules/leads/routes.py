"""Rota pública de captação de leads (86e3fr9ut).

    - POST /api/v1/leads   grava o contato do formulário da landing e avisa o Slack

SEM autenticação: quem chama é visitante da landing, antes de existir usuário. Não
lê nem grava dado escopável (o lead não pertence a cliente nem a organização), por
isso está em `NON_TENANT_ENDPOINTS`, com o motivo. A resposta é a MESMA para lead
gravado, honeypot e limite por e-mail, e não ecoa nada do que foi enviado.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.core.dependencies import DbSessionDep, SettingsDep
from app.core.rate_limit import limiter
from app.modules.leads.notifier import LeadNotifier, SlackLeadNotifier
from app.modules.leads.repository import LeadRepository
from app.modules.leads.schemas import LeadCreate, LeadReceivedResponse
from app.modules.leads.service import LeadService
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

#: Teto do slowapi. ⚠️ Atrás do BFF do Next o IP que a API enxerga é o do proxy
#: (86e3anx10), então este limite vale para TODOS os visitantes juntos, por
#: instância: é rede de segurança contra enxurrada no Slack, não a defesa principal
#: (honeypot + limite por e-mail, no service). Alto o bastante para um evento
#: (dezenas de visitantes, poucos formulários por minuto).
LEADS_RATE_LIMIT = "10/minute"


def get_lead_notifier(settings: SettingsDep) -> LeadNotifier:
    """Provider do notificador — os testes trocam por um dublê via `dependency_overrides`."""
    return SlackLeadNotifier(settings.LEADS_SLACK_WEBHOOK_URL)


LeadNotifierDep = Annotated[LeadNotifier, Depends(get_lead_notifier)]


def _get_service(db: DbSessionDep, notifier: LeadNotifierDep) -> LeadService:
    return LeadService(
        LeadRepository(db),
        notifier=notifier,
        usage_events=UsageEventService(UsageEventRepository(db)),
    )


LeadServiceDep = Annotated[LeadService, Depends(_get_service)]


@router.post(
    "",
    status_code=200,
    summary="Recebe o contato do formulário da landing pública (sem autenticação).",
)
@limiter.limit(LEADS_RATE_LIMIT)
async def create_lead(
    request: Request,  # o slowapi lê o cliente daqui; tem de vir primeiro
    response: Response,  # o slowapi escreve os headers X-RateLimit-* aqui
    payload: LeadCreate,
    service: LeadServiceDep,
) -> LeadReceivedResponse:
    await service.receive(payload)
    return LeadReceivedResponse()
