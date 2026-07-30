"""Endpoint da instrumentação de outcome (Sprint 4, BACK 04.1).

    - POST /api/v1/usage-events

Recebe SÓ os eventos que o backend não consegue observar (`autor_navegou_fora`,
`notificacao_entregue`). Os eventos de servidor (`conciliacao_criada`,
`conciliacao_concluida`) são emitidos no ponto real do fluxo e **não** são
aceitos aqui — ver `CLIENT_EMITTED_EVENTS` em `schemas.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import (
    UsageEventPayload,
    UsageEventRequest,
    UsageEventResponse,
)
from app.modules.usage_events.service import UsageEventService

router = APIRouter(prefix="/api/v1/usage-events", tags=["usage-events"])


def _get_usage_event_service(db: DbSessionDep) -> UsageEventService:
    """Provider para injeção do service no endpoint."""
    return UsageEventService(UsageEventRepository(db))


UsageEventServiceDep = Annotated[UsageEventService, Depends(_get_usage_event_service)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Registra um evento de uso emitido pelo frontend (`autor_navegou_fora`, "
        "`notificacao_entregue`) na tabela `usage_events`, sink da métrica de "
        "outcome da Sprint 4. Autenticado (admin OU manager). O `event` é "
        "validado contra um enum FECHADO e as chaves de `props` contra uma "
        "whitelist por evento — chave desconhecida ou evento fora da lista "
        "retorna 400 VALIDATION_ERROR, e nenhum campo aceita texto livre (sem "
        "PII). O `session_id` precisa pertencer a um cliente da carteira do usuário: "
        "sessão inexistente devolve 404, sessão de outro manager devolve 403. "
        "Idempotente: reenviar o mesmo evento para a mesma sessão responde 201 "
        "com `recorded=false` e não duplica a linha."
    ),
)
async def record_usage_event(
    user: CurrentUserDep,
    db: DbSessionDep,
    service: UsageEventServiceDep,
    payload: UsageEventRequest,
) -> UsageEventResponse:
    recorded = await service.record_client_event(payload, user=user, db=db)
    return UsageEventResponse(
        data=UsageEventPayload(
            event=payload.event,
            session_id=payload.session_id,
            recorded=recorded,
        )
    )
