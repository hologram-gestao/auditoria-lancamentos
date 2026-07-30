"""Resolução de sessão de conciliação COM o filtro de tenant no SELECT (S5 / R3).

Ponto ÚNICO por onde toda rota que recebe `session_id` (uma PK, sem `client_id`
na requisição) carrega a sessão. O `SELECT` já sai com
`AND client_id = <tenant do usuário>` quando `scope='client'` — então o recurso
de outro tenant **não é carregado**, e a rota devolve 404 sem nunca ter tocado no
dado alheio. É a defesa que sobrevive a um endpoint novo que esqueça o guard.

Para usuário `system` o filtro é no-op (o escopo dele é a carteira, aplicada por
`resolve_client_access`) — nenhuma regressão para admin/manager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.core.audit import record_cross_tenant_denied
from app.core.authz import scoped_by_tenant
from app.core.dependencies import require_client_access
from app.core.exceptions import ClientNotAccessibleError, NotFoundError
from app.db.models import ReconciliationSession

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.db.models import Client

#: Mensagem única de "não encontrada". Igual para sessão inexistente, descartada
#: e de outro tenant — distinguir permitiria enumerar sessões alheias.
SESSION_NOT_FOUND_MSG = "Conciliação não encontrada."


async def load_session_scoped(
    db: AsyncSession,
    user: CurrentUser,
    session_id: UUID,
) -> ReconciliationSession | None:
    """Carrega a sessão pela PK **com** o filtro de tenant no próprio SELECT."""
    stmt = select(ReconciliationSession).where(
        ReconciliationSession.id == session_id,
        ReconciliationSession.deleted_at.is_(None),
    )
    return (
        await db.execute(scoped_by_tenant(stmt, ReconciliationSession.client_id, user))
    ).scalar_one_or_none()


async def audit_session_tenant_miss(db: AsyncSession, user: CurrentUser, session_id: UUID) -> None:
    """Grava a negação cross-tenant quando o filtro do SELECT já matou a busca.

    Sem isso, o R3 (filtro na query) apagaria o R6 (auditar toda negação
    cross-tenant): a sessão alheia simplesmente "não existe" para o usuário de
    cliente, e a tentativa sumiria da trilha.

    Custa 1 query **só no caminho de falha** (raro por construção), e a resposta
    segue 404 idêntica — a auditoria é server-side, não muda o que o atacante vê.
    Se a sessão não existe em tenant nenhum, não houve cross-tenant: nada é
    gravado (a trilha não é lugar de 404 comum).
    """
    if not user.is_client_scoped:
        return
    target_client_id = await db.scalar(
        select(ReconciliationSession.client_id).where(
            ReconciliationSession.id == session_id,
            ReconciliationSession.deleted_at.is_(None),
        )
    )
    if target_client_id is None or target_client_id == user.client_id:
        return
    await record_cross_tenant_denied(
        db,
        user_id=UUID(user.id),
        user_scope=user.scope,
        actor_client_id=user.client_id,
        target_client_id=target_client_id,
    )


async def require_session_access(
    db: AsyncSession,
    user: CurrentUser,
    session_id: UUID,
) -> ReconciliationSession:
    """Sessão acessível ao usuário, ou 404.

    Duas camadas, nesta ordem:
        1. O `SELECT` já filtra por tenant (usuário de cliente nem carrega a
           linha alheia) — e, quando isso mata a busca, `audit_session_tenant_miss`
           registra a negação cross-tenant (R6);
        2. `require_client_access` aplica a regra de carteira do usuário
           `system` — e grava a trilha `denied` quando nega.

    Manager fora da carteira recebe **404** (e não 403) para não distinguir "não
    existe" de "não é sua" — a conversão anti-enumeração da S3, preservada.
    """
    session_obj = await load_session_scoped(db, user, session_id)
    if session_obj is None:
        await audit_session_tenant_miss(db, user, session_id)
        raise NotFoundError(SESSION_NOT_FOUND_MSG)
    try:
        await require_client_access(session_obj.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(SESSION_NOT_FOUND_MSG) from exc
    return session_obj


async def require_client_for_session(
    db: AsyncSession,
    user: CurrentUser,
    session_id: UUID,
) -> Client:
    """Como `require_session_access`, mas devolve o `Client` já carregado.

    Usado pelas rotas que precisam das credenciais Omie/DEK do cliente — evita
    uma 2ª query, já que `require_client_access` carrega o `Client` de qualquer
    forma.
    """
    session_obj = await load_session_scoped(db, user, session_id)
    if session_obj is None:
        await audit_session_tenant_miss(db, user, session_id)
        raise NotFoundError(SESSION_NOT_FOUND_MSG)
    try:
        return await require_client_access(session_obj.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(SESSION_NOT_FOUND_MSG) from exc
