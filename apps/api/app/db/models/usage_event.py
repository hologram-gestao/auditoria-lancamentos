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
    - **Idempotência no banco:** UNIQUE parcial `(event, session_id)` +
      `ON CONFLICT DO NOTHING` no INSERT — mas só para os eventos cujo grão É
      "no máximo 1 por sessão". Ver `DEDUPED_EVENT_NAMES` abaixo e a ADR-010.

Eventos da Sprint 6 (BACK 06.1), todos de backend:
    - `qualificacao_emitida`   {session_id, veredito, com_glossario}
    - `flag_revisado`          {session_id, procedente}
    - `glossario_editado`      {client_id, n_categorias}  (sem session_id)
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

#: Eventos cujo grão é **no máximo 1 por sessão** — só ELES entram na dedup do
#: índice parcial. É uma **allow-list**, não uma deny-list, de propósito: evento
#: novo nasce SEM dedup, e o pior caso vira "linha a mais" (visível, corrigível
#: na leitura) em vez de "linha que sumiu em silêncio" (invisível, e a razão do
#: outcome sai errada). ADR-010.
#:
#: ⚠️ Strings literais, não `UsageEventName.X.value`: `app.modules.usage_events`
#: importa `app.db.models` no `__init__`, então importar o enum aqui fecharia um
#: ciclo. O teste `test_deduped_event_names_existem_no_enum` trava o drift.
#:
#: Ordem alfabética FIXA: o predicado do índice tem de bater byte-a-byte com o
#: `index_where` do `ON CONFLICT` (ver `UsageEventRepository`).
DEDUPED_EVENT_NAMES: tuple[str, ...] = (
    "autor_navegou_fora",
    "conciliacao_concluida",
    "conciliacao_criada",
    "notificacao_entregue",
)


def deduped_session_index_predicate() -> str:
    """Predicado do índice parcial `uq_usage_events_event_session`.

    Fonte ÚNICA da expressão: o modelo (aqui), o `ON CONFLICT` do repository e
    a migration `a1d7f36c9b52` precisam da MESMA string. Se divergirem, o
    Postgres não infere o índice como árbitro e o INSERT morre com `42P10` —
    que o fail-soft do service engoliria, gravando NADA (foi exatamente a
    reprovação de QA da Sprint 4).
    """
    joined = ", ".join(f"'{name}'" for name in DEDUPED_EVENT_NAMES)
    return f"session_id IS NOT NULL AND event IN ({joined})"


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
        # Idempotência do emissor (ver docstring do módulo). Parcial em DOIS
        # eixos: `session_id IS NOT NULL` (evento sem sessão não tem chave
        # natural de dedup) e `event IN (allow-list)` — eventos multi-ocorrência
        # por sessão (`qualificacao_emitida`, `flag_revisado`) NÃO podem
        # colapsar, senão N-1 emissões sumiriam e a razão do outcome sairia
        # errada. ADR-010.
        Index(
            "uq_usage_events_event_session",
            "event",
            "session_id",
            unique=True,
            postgresql_where=text(deduped_session_index_predicate()),
        ),
    )

    def __repr__(self) -> str:
        return f"<UsageEvent id={self.id} event={self.event!r} session_id={self.session_id}>"
