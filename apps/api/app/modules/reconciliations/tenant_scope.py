"""Resolução de sessão de conciliação COM o filtro de alcance no SELECT (S5 / R3).

Ponto ÚNICO por onde toda rota que recebe `session_id` (uma PK, sem `client_id`
na requisição) carrega a sessão. O `SELECT` já sai restrito ao ALCANCE de quem
pede (`scoped_by_reach`: cliente o próprio tenant; admin a própria organização;
manager a carteira dentro dela; plataforma tudo) — então a sessão de outro
tenant OU de outra organização **não é carregada**, e a rota devolve 404 sem
nunca ter tocado no dado alheio. É a defesa que sobrevive a um endpoint novo
que esqueça o guard (86e36ecqz estendeu de tenant para alcance: o admin da
organização B nem carrega a sessão de um cliente da Hologram).

`require_client_access` continua rodando depois, sobre a sessão carregada: é a
decisão de registro (`resolve_client_access`), e o filtro do SELECT é a MESMA
decisão projetada em `WHERE` — nunca uma segunda regra. Exceção documentada: o
`POST /usage-events` carrega o `client_id` da sessão por leitor próprio
(`scoped_by_tenant` + `require_client_access`, 403 explícito por critério de
aceite da S5) e só chama `audit_session_tenant_miss` daqui.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.core.audit import record_cross_tenant_denied
from app.core.authz import resolve_client_access, scoped_by_reach
from app.core.dependencies import require_client_access
from app.core.exceptions import ClientNotAccessibleError, NotFoundError
from app.db.models import Client, ReconciliationSession

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
    """Carrega a sessão pela PK **com** o filtro de alcance no próprio SELECT."""
    stmt = select(ReconciliationSession).where(
        ReconciliationSession.id == session_id,
        ReconciliationSession.deleted_at.is_(None),
    )
    return (
        await db.execute(scoped_by_reach(stmt, ReconciliationSession.client_id, user))
    ).scalar_one_or_none()


async def audit_session_tenant_miss(db: AsyncSession, user: CurrentUser, session_id: UUID) -> None:
    """Grava a negação cross-tenant/cross-org quando o filtro do SELECT já matou a busca.

    Sem isso, o R3 (filtro na query) apagaria o R6 (auditar toda negação
    cross-tenant): a sessão alheia simplesmente "não existe" para quem não a
    alcança — usuário de outro tenant, admin ou gerente de outra organização
    (86e36ecqz) — e a tentativa sumiria da trilha.

    Custa 2 queries **só no caminho de falha** (raro por construção), e a
    resposta segue 404 idêntica — a auditoria é server-side, não muda o que o
    atacante vê. Se a sessão não existe em tenant nenhum, não houve cross-tenant:
    nada é gravado (a trilha não é lugar de 404 comum). A pergunta "ele
    alcançaria esse cliente?" é feita a `resolve_client_access` — a decisão
    única, a mesma que o SELECT projetou em `WHERE`; um miss que ela libere
    (não deveria existir) não vira linha falsa na trilha.
    """
    target = (
        await db.execute(
            select(ReconciliationSession.client_id, Client.organization_id)
            .join(Client, Client.id == ReconciliationSession.client_id)
            .where(
                ReconciliationSession.id == session_id,
                ReconciliationSession.deleted_at.is_(None),
            )
        )
    ).one_or_none()
    if target is None:
        return
    target_client_id, target_organization_id = target
    if await resolve_client_access(
        db, user, target_client_id, target_organization_id=target_organization_id
    ):
        return
    await record_cross_tenant_denied(
        db,
        user_id=UUID(user.id),
        user_scope=user.scope,
        actor_client_id=user.client_id,
        actor_organization_id=user.organization_id,
        target_client_id=target_client_id,
    )


async def require_session_access(
    db: AsyncSession,
    user: CurrentUser,
    session_id: UUID,
) -> ReconciliationSession:
    """Sessão acessível ao usuário, ou 404.

    Duas camadas, nesta ordem:
        1. O `SELECT` já filtra pelo alcance (usuário de cliente, admin e
           gerente de outra organização nem carregam a linha alheia) — e, quando
           isso mata a busca, `audit_session_tenant_miss` registra a negação
           cross-tenant/cross-org (R6);
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
