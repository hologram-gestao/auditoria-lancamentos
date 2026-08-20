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
from app.core.crypto_service import load_client_cipher
from app.core.dependencies import (
    DbSessionDep,
    SyncOmieAccountsDep,
)
from app.core.exceptions import (
    NotFoundError,
    ValidationAppError,
)
from app.db.models import Client
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.integrations.omie.client import OmieClient
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.clients.omie_factory import build_omie_client
from app.modules.omie_data.categorias_service import OmieCategoriasService
from app.modules.omie_data.schemas import (
    OmieCategoriaListResponse,
    OmieLancamentoListResponse,
)
from app.modules.omie_data.service import OmieLancamentoService
from app.modules.reconciliations.review.repository import ReviewRepository
from app.modules.reconciliations.tenant_scope import (
    require_client_for_session,
    require_session_access,
)

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
    "/categorias",
    summary=(
        "Lista as categorias Omie do cliente da sessão (código + descrição), "
        "servidas de um cache em memória por cliente com TTL de 6 h. Lista "
        "COMPLETA, sem paginação — o consumidor é um combobox com busca local. "
        "`refresh=true` ignora o cache."
    ),
)
async def get_omie_categorias(
    user: SyncOmieAccountsDep,
    db: DbSessionDep,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_id: Annotated[UUID, Query(description="UUID da sessão que define o tenant.")],
    refresh: Annotated[
        bool,
        Query(description="Ignora o cache e rebusca do Omie."),
    ] = False,
) -> OmieCategoriaListResponse:
    """Categorias para classificar as compras antes de lançar (BACK 07.3).

    O tenant vem da **sessão** — que por sua vez já é carregada com o filtro de
    tenant dentro do próprio `SELECT` (`require_client_for_session`). Nenhum
    `client_id` é aceito da URL ou do body: sessão de outro tenant é 404 e nem
    chega aqui.
    """
    client = await require_client_for_session(db, user, session_id)

    cache: OmieCategoriasCache = request.app.state.omie_categorias_cache
    service = OmieCategoriasService(cache)

    async def build_client() -> OmieClient:
        """Só chamado no MISS — em staging/prod o unwrap da DEK é uma ida ao
        Cloud KMS, e o ponto do cache é justamente não pagar latência por
        abertura de combobox."""
        cipher = await load_client_cipher(client, settings=settings)
        return build_omie_client(client, settings, cipher)

    items = await service.list_categorias(
        client_id=client.id,
        omie_client_factory=build_client,
        refresh=refresh,
    )
    return OmieCategoriaListResponse(data=items, total=len(items))


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
    user: SyncOmieAccountsDep,
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

    # SELECT da sessão já filtrado por tenant + carteira do `system` (S5/R3).
    sess = await require_session_access(db, user, session_id)

    client = (
        await db.execute(select(Client).where(Client.id == sess.client_id))
    ).scalar_one_or_none()
    if client is None:
        raise NotFoundError(_SESSION_NOT_FOUND_MSG)

    cache: OmieLancamentoCache = request.app.state.omie_lancamento_cache
    service = OmieLancamentoService(ReviewRepository(db), cache)

    cipher = await load_client_cipher(client, settings=settings)
    items = await service.fetch_lancamentos(
        session_id=session_id,
        omie_ids=parsed_ids,
        omie_client_factory=lambda: build_omie_client(client, settings, cipher),
    )
    return OmieLancamentoListResponse(data=items)
