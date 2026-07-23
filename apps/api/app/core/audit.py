"""Escrita da auditoria de acesso (Sprint 3, BACK 03.5).

Grava linhas em `access_audit` para a lista FECHADA de eventos { denied, view,
export }. SÓ IDs — nunca PII. Ver `app.db.models.access_audit.AccessAudit`.

Complementaridade com a instrumentação (03.2): no ponto do `denied`, o evento
`acesso_negado` (structlog → alerting/métrica) e a linha `access_audit` (registro
LGPD durável) convivem — um é telemetria efêmera, o outro é trilha persistente.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from structlog.contextvars import get_contextvars

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
    session_id: UUID | None = None,
    rota: str | None = None,
    commit: bool = False,
) -> None:
    """Insere uma linha de auditoria. SÓ IDs — nunca PII.

    Args:
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
            session_id=session_id,
            action=action.value,
            rota=rota if rota is not None else _current_rota(),
        )
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
