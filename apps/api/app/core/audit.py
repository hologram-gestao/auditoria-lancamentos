"""Escrita da auditoria de acesso (Sprint 3, BACK 03.5).

Grava linhas em `access_audit` para a lista FECHADA de eventos { denied, view,
export }. SÓ IDs — nunca PII. Ver `app.db.models.access_audit.AccessAudit`.

Complementaridade com a instrumentação (03.2): no ponto do `denied`, o evento
`acesso_negado` (structlog → alerting/métrica) e a linha `access_audit` (registro
LGPD durável) convivem — um é telemetria efêmera, o outro é trilha persistente.

Sprint 5 (R6): toda linha carrega o ESCOPO e o TENANT DO ATOR
(`user_scope`/`actor_client_id`), além do tenant alvo (`client_id`), e o caminho
de negação cross-tenant passou a ter uma função única —
`record_cross_tenant_denied` — que emite os eventos e grava a linha juntos.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from structlog.contextvars import get_contextvars

from app.core.telemetry import emit_acesso_cross_tenant_negado, emit_acesso_negado
from app.db.models import AccessAudit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AccessAction(StrEnum):
    """Lista FECHADA de ações auditadas. NÃO é 'todo GET' (guardrail de volume)."""

    DENIED = "denied"
    VIEW = "view"
    EXPORT = "export"


def _current_rota() -> str:
    """Path da request corrente, vinculado pelo CorrelationIdMiddleware nos
    contextvars do structlog. Só o path — sem query string (não vaza filtros)."""
    return str(get_contextvars().get("path", ""))


async def record_access(
    db: AsyncSession,
    *,
    user_id: UUID,
    client_id: UUID,
    action: AccessAction,
    user_scope: str,
    actor_client_id: UUID | None,
    session_id: UUID | None = None,
    rota: str | None = None,
    commit: bool = False,
) -> None:
    """Insere uma linha de auditoria. SÓ IDs — nunca PII.

    Args:
        client_id: tenant ALVO (o cliente cujo dado foi acessado).
        user_scope: escopo do ATOR (`system`|`client`). **Obrigatório de
            propósito**: com default, um call site novo gravaria `system`
            silenciosamente e a trilha mentiria sobre quem agiu.
        actor_client_id: tenant do ATOR — `None` para escopo `system`.
        commit: `True` para o caminho `denied`, onde a request termina em erro
            (404) e o `get_db_session` daria ROLLBACK — sem o commit aqui, a
            linha de auditoria se perderia. No ponto do denied, a única escrita
            pendente na sessão é a própria auditoria (a dependency só rodou
            SELECTs), então commitar persiste apenas o registro. Nos caminhos de
            sucesso (`view`/`export`) use `False`: o commit de fim de request
            (`get_db_session`) persiste normalmente.
    """
    db.add(
        AccessAudit(
            user_id=user_id,
            client_id=client_id,
            user_scope=user_scope,
            actor_client_id=actor_client_id,
            session_id=session_id,
            action=action.value,
            rota=rota if rota is not None else _current_rota(),
        )
    )
    if commit:
        await db.commit()
    else:
        await db.flush()


async def record_cross_tenant_denied(
    db: AsyncSession,
    *,
    user_id: UUID,
    user_scope: str,
    actor_client_id: UUID | None,
    target_client_id: UUID,
    rota: str | None = None,
) -> None:
    """Caminho ÚNICO de gravação de acesso negado a cliente/tenant alheio (S5/R6).

    Faz as três coisas que o PRD exige, sempre juntas — por isso é UMA função e
    não três chamadas espalhadas por call site:

        1. `acesso_cross_tenant_negado` (telemetria S5) com EXATAMENTE as quatro
           propriedades declaradas: `user_scope`, `tenant_ator`,
           `tenant_alvo`, `rota`.
        2. `acesso_negado` (telemetria S3) — mantido para não zerar a métrica da
           sprint anterior, que conta esse `event`.
        3. **Uma** linha `denied` em `access_audit`, com `commit=True` (a request
           termina em erro e o `get_db_session` daria ROLLBACK).

    Chamada ANTES da conversão 403→404 anti-enumeração das rotas de leitura — a
    conversão continua intacta: o 404 protege o atacante de aprender, a trilha
    protege a equipe de não saber.

    Somente IDs — nunca PII do tenant alvo.
    """
    resolved_rota = rota if rota is not None else _current_rota()
    emit_acesso_cross_tenant_negado(
        user_scope=user_scope,
        tenant_ator=str(actor_client_id) if actor_client_id else None,
        tenant_alvo=str(target_client_id),
        rota=resolved_rota,
    )
    emit_acesso_negado(
        user_id=str(user_id),
        client_id_alvo=str(target_client_id),
        rota=resolved_rota,
    )
    await record_access(
        db,
        user_id=user_id,
        client_id=target_client_id,
        action=AccessAction.DENIED,
        user_scope=user_scope,
        actor_client_id=actor_client_id,
        rota=resolved_rota,
        commit=True,
    )
