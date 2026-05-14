"""Rotas do módulo omie_data (BACK 9.2).

`GET /api/v1/omie/lancamentos?ids=...&session_id=...`

Por que `session_id` em vez do `client_id` previsto no checklist do
backlog: o Omie não tem endpoint by-id (limitação documentada em
`omie_data/service.py`). Precisamos do contexto da sessão para resolver
`omie_conta_id` + período em uma chamada `listar_extrato`. O `client_id`
sai naturalmente da sessão e o RBAC continua sendo aplicado sobre ele.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    DbSessionDep,
    ManagerOrAdminDep,
    require_client_access,
)
from app.core.exceptions import (
    ClientNotAccessibleError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models import Client, ReconciliationSession
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.clients.omie_factory import build_omie_client
from app.modules.omie_data.schemas import OmieLancamentoListResponse
from app.modules.omie_data.service import OmieLancamentoService
from app.modules.reconciliations.review.repository import ReviewRepository

router = APIRouter(prefix="/api/v1/omie", tags=["omie"])

MAX_IDS_PER_REQUEST = 100
_SESSION_NOT_FOUND_MSG = "Sessão de conciliação não encontrada."


def _parse_ids(raw: str) -> list[int]:
    """Parse `?ids=1,2,3` em lista deduplicada e validada.

    Falha em ID negativo, duplicata silenciosa (dedup), ou input não numérico.
    Mais de 100 IDs → caller decide (vamos rejeitar na rota).
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValidationAppError(
            "Query `ids` vazia.",
            user_message="Selecione ao menos um lançamento.",
        )
    seen: set[int] = set()
    out: list[int] = []
    for p in parts:
        try:
            value = int(p)
        except ValueError as exc:
            raise ValidationAppError(
                f"ID Omie inválido: {p!r}",
                user_message="IDs de lançamento Omie devem ser numéricos.",
            ) from exc
        if value <= 0:
            raise ValidationAppError(
                f"ID Omie inválido: {value}",
                user_message="IDs de lançamento Omie devem ser positivos.",
            )
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


@router.get(
    "/lancamentos",
    summary=(
        "Resolve dados Omie para uma lista de IDs via cache hierárquico "
        "(L1 in-memory 2h + L2 Redis 2h, com re-fetch via ListarExtrato "
        "quando necessário). Requer `session_id` para resolver o período. "
        "Máximo 100 IDs por request."
    ),
)
async def get_omie_lancamentos(
    user: ManagerOrAdminDep,
    db: DbSessionDep,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    ids: Annotated[str, Query(description="CSV de IDs Omie: '1,2,3'.")],
    session_id: Annotated[UUID, Query(description="UUID da sessão para resolver contexto Omie.")],
) -> OmieLancamentoListResponse:
    parsed_ids = _parse_ids(ids)
    if len(parsed_ids) > MAX_IDS_PER_REQUEST:
        raise ValidationAppError(
            f"Pedido com {len(parsed_ids)} IDs; máximo é {MAX_IDS_PER_REQUEST}.",
            user_message=(
                f"Só é possível resolver até {MAX_IDS_PER_REQUEST} lançamentos "
                "por vez. Quebre em lotes menores."
            ),
        )

    sess = (
        await db.execute(
            select(ReconciliationSession).where(ReconciliationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if sess is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    try:
        await require_client_access(sess.client_id, user, db)
    except ClientNotAccessibleError as exc:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG) from exc

    client = (
        await db.execute(select(Client).where(Client.id == sess.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    cache: OmieLancamentoCache = request.app.state.omie_lancamento_cache
    service = OmieLancamentoService(ReviewRepository(db), cache)

    items = await service.fetch_lancamentos(
        session_id=session_id,
        omie_ids=parsed_ids,
        omie_client_factory=lambda: build_omie_client(client, settings),
    )
    return OmieLancamentoListResponse(data=items)
