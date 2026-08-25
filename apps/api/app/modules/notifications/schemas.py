"""Schemas das notificações in-app (Sprint 4, BACK 04.4).

O texto que o usuário lê é montado no FRONT a partir destes campos — o backend
nunca devolve uma frase pronta. É o que garante que nenhuma PII do conteúdo do
arquivo entre no aviso: não há campo de texto livre para carregá-la.

`session_id` e `tipo` vão na resposta de propósito: são o que o front precisa
para emitir `notificacao_entregue` em `usage_events` (via + latência) sem uma
segunda chamada — o evento não pode ficar órfão (## Instrumentação do PRD).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from app.modules.users.schemas import PaginationMeta


class NotificationItem(BaseModel):
    """Uma notificação, como o sino a exibe."""

    id: UUID
    session_id: UUID
    client_id: UUID
    # 'processada' | 'erro'. `str` lenient (memória
    # `feedback_pydantic_strict_input_lenient_output`) — um tipo novo em versão
    # futura não pode derrubar a listagem de quem ainda não atualizou.
    tipo: str
    omie_conta_id: int
    reference_month: date
    # Só em `tipo='erro'`. CÓDIGO canônico — a tela mostra "(cód. X)", nunca a
    # mensagem interna (S2/R9).
    error_code: str | None = None
    read_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    """Body de GET /api/v1/notifications — lista paginada."""

    data: list[NotificationItem]
    pagination: PaginationMeta


class UnreadCountPayload(BaseModel):
    """Conteúdo do envelope de GET /api/v1/notifications/unread-count."""

    unread: int


class UnreadCountResponse(BaseModel):
    """Response do contador do sino (polling de 15 s)."""

    data: UnreadCountPayload


class MarkReadPayload(BaseModel):
    """Conteúdo do envelope de POST /api/v1/notifications/{id}/read.

    `already_read=True` quando a notificação já estava lida — não é erro: a
    operação é idempotente por contrato (o front pode reenviar sem tratar).
    """

    id: UUID
    read_at: datetime
    already_read: bool


class MarkReadResponse(BaseModel):
    """Response de POST /api/v1/notifications/{id}/read."""

    data: MarkReadPayload


class MarkAllReadPayload(BaseModel):
    """Conteúdo do envelope de POST /api/v1/notifications/read-all.

    `marked` é quantas saíram de não lidas AGORA — 0 na segunda chamada, que é
    o contrato da idempotência (o front pode reenviar sem tratar erro).
    """

    marked: int


class MarkAllReadResponse(BaseModel):
    """Response de POST /api/v1/notifications/read-all."""

    data: MarkAllReadPayload
