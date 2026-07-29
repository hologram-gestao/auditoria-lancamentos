"""Schemas Pydantic da instrumentação de outcome (Sprint 4, BACK 04.1).

Aqui vive o **enum FECHADO** de eventos e a **whitelist de chaves de `props` por
evento**. Os dois são a defesa contra dois riscos concretos:

1. **Lixo na métrica** — `event` fora do enum é 422; a leitura D+30 não precisa
   adivinhar o que é ruído.
2. **PII no sink** — nenhum `props` tem campo de texto livre. Todo campo é `int`
   (contadores/durações), `Literal` (enum) ou UUID. Com `extra="forbid"`, chave
   desconhecida vira 422 antes de chegar ao banco: não existe caminho pelo qual
   uma descrição de lançamento, CNPJ ou nome de destinatário entre na tabela.

Origem dos campos: seção `Instrumentação` do `## Outcome & verificação`
(CONTEXT.md). `duracao_s`/`latencia_s`/`segundos_apos_criar` são **inteiros**
(CLAUDE.md §3.4 — nunca float).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UsageEventName(StrEnum):
    """Lista FECHADA de eventos. Nenhum evento nasce fora daqui."""

    # Emitidos pelo BACKEND, no ponto real do fluxo.
    CONCILIACAO_CRIADA = "conciliacao_criada"
    CONCILIACAO_CONCLUIDA = "conciliacao_concluida"
    # Emitidos pelo FRONTEND (só o browser observa navegação e entrega visual).
    NOTIFICACAO_ENTREGUE = "notificacao_entregue"
    AUTOR_NAVEGOU_FORA = "autor_navegou_fora"


#: Eventos que o `POST /api/v1/usage-events` aceita. Os de backend ficam de fora
#: DE PROPÓSITO: `conciliacao_criada`/`conciliacao_concluida` são o denominador e
#: o marco temporal da métrica — aceitá-los do cliente permitiria forjar o
#: resultado da sprint. Quem os emite é o servidor, no ponto real do fluxo.
CLIENT_EMITTED_EVENTS: frozenset[UsageEventName] = frozenset(
    {UsageEventName.NOTIFICACAO_ENTREGUE, UsageEventName.AUTOR_NAVEGOU_FORA}
)

# Teto sanitário das durações: 30 dias em segundos. Não é regra de negócio — é
# guardrail contra valor absurdo (relógio do cliente errado, aba aberta por
# semanas) contaminando a média da leitura D+30.
_MAX_SECONDS = 30 * 24 * 60 * 60


class _StrictProps(BaseModel):
    """Base de todo `props`: chave desconhecida é erro, não é ignorada."""

    model_config = ConfigDict(extra="forbid")


class AutorNavegouForaProps(_StrictProps):
    """`autor_navegou_fora` — **é o evento que prova o outcome da sprint**."""

    segundos_apos_criar: int = Field(ge=0, le=_MAX_SECONDS)


class NotificacaoEntregueProps(_StrictProps):
    """`notificacao_entregue` — mede se a notificação ALCANÇA a pessoa."""

    via: Literal["sino", "toast"]
    latencia_s: int = Field(ge=0, le=_MAX_SECONDS)


class _UsageEventRequestBase(BaseModel):
    """Body comum: `session_id` é obrigatório e o ownership é checado no servidor."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID


class AutorNavegouForaRequest(_UsageEventRequestBase):
    event: Literal["autor_navegou_fora"]
    props: AutorNavegouForaProps


class NotificacaoEntregueRequest(_UsageEventRequestBase):
    event: Literal["notificacao_entregue"]
    props: NotificacaoEntregueProps


#: Union discriminada por `event`. Um `event` fora dos literais acima não casa
#: com nenhum membro → 422 automático do Pydantic (enum fechado sem `if/elif`).
UsageEventRequest = Annotated[
    AutorNavegouForaRequest | NotificacaoEntregueRequest,
    Field(discriminator="event"),
]


class UsageEventPayload(BaseModel):
    """Conteúdo do envelope `{data: ...}` do POST /usage-events.

    `recorded=False` significa que o evento já existia para aquela sessão (a
    idempotência do banco descartou a repetição) — não é erro: o endpoint é
    idempotente por contrato e o front não precisa tratar.
    """

    event: str
    session_id: UUID
    recorded: bool


class UsageEventResponse(BaseModel):
    """Response do POST /api/v1/usage-events."""

    data: UsageEventPayload
