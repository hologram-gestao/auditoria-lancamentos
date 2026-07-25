"""Modelo UsageEvent — sink mínimo de instrumentação de outcome (Sprint 4, BACK 04.1).

Nasce nesta sprint porque o repo NÃO tem infra de analytics (`grep track|capture|
analytics` = vazio) e a métrica da Sprint 4 (`autor_navegou_fora ÷ conciliações
criadas`, leitura em D+30) precisa de agregação. É uma tabela mínima, não um
produto de analytics: `event` + `session_id` + `props` jsonb + `created_at`.

Eventos declarados no `## Outcome & verificação` do PRD (CONTEXT.md):
    - `conciliacao_criada`     {session_id, client_id, n_arquivos, criado_por} — backend
    - `conciliacao_concluida`  {session_id, duracao_s, status}                 — backend
    - `notificacao_entregue`   {session_id, via, latencia_s}                   — frontend
    - `autor_navegou_fora`     {session_id, segundos_apos_criar}               — frontend

Guardrails:
    - **SEM PII.** Só IDs e enums. O campo `destinatario` do rascunho do PRD ficou
      de fora de propósito (seria PII). A whitelist de chaves por evento vive em
      `app.modules.usage_events.schemas` e é validada no servidor.
    - **Sem FK** (mesma decisão de `access_audit`): log append-only e durável,
      independente do ciclo de vida da sessão que referencia. `session_id` é FK
      *lógica* para `reconciliation_sessions`.
    - **Idempotência no banco:** UNIQUE parcial `(event, session_id)` onde
      `session_id IS NOT NULL` + `ON CONFLICT DO NOTHING` no INSERT. Um evento
      por sessão é exatamente o grão da métrica (o denominador é "conciliações",
      não "emissões"), e torna o emissor seguro contra retry/duplo-clique/
      reprocessamento. Trade-off consciente: um `/reprocess` não gera um 2º
      `conciliacao_concluida` — a leitura D+30 conta sessões, não execuções.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import UUIDPrimaryKeyMixin


class UsageEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "usage_events"

    # Nome do evento. `String` (não enum do Postgres) de propósito: a lista de
    # eventos evolui a cada sprint de produto e um enum no banco exigiria
    # migration a cada novo evento. O enum FECHADO que vale é o do servidor
    # (`app.modules.usage_events.schemas.UsageEventName`) — validado na borda.
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    # FK lógica p/ `reconciliation_sessions` (sem constraint — ver docstring).
    session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # Só IDs/enums/inteiros. Nunca texto livre — a whitelist por evento garante.
    props: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        # Leitura do D+30: "quantos X no período" — (event, created_at) resolve
        # pelo índice sem varrer a tabela.
        Index("ix_usage_events_event_created", "event", "created_at"),
        # Idempotência do emissor (ver docstring do módulo). Parcial porque
        # eventos sem sessão (nenhum hoje) não têm chave natural de dedup.
        Index(
            "uq_usage_events_event_session",
            "event",
            "session_id",
            unique=True,
            postgresql_where=text("session_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<UsageEvent id={self.id} event={self.event!r} session_id={self.session_id}>"
