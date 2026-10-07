"""`GET /api/v1/clients/{client_id}/summary` — as pendências do mês (86e3k1q3j).

Leitura sem permissão própria, como o de-para e a carteira: quem alcança o
cliente (`AccessibleClientDep`, a decisão única `resolve_client_access`) lê. O
bloco da carteira obedece à célula `view_client_receivables` DENTRO do serviço:
sem ela vem `null`, nunca 403. Lista canônica: COLLECTION.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import AccessibleClientDep, CurrentUserDep, DbSessionDep
from app.modules.client_mapping.repository import ClientMappingRepository
from app.modules.client_movements.competence import (
    COMPETENCE_PATTERN,
    current_competence,
    parse_competence,
)
from app.modules.client_summary.repository import ClientSummaryRepository
from app.modules.client_summary.schemas import ClientSummaryEnvelope
from app.modules.client_summary.service import ClientSummaryService
from app.modules.client_titles.repository import ClientTitlesRepository
from app.modules.client_titles.service import ClientTitlesReadService
from app.modules.mapping_catalog.repository import MappingCatalogRepository

router = APIRouter(prefix="/api/v1/clients", tags=["client-summary"])


def _get_service(db: DbSessionDep) -> ClientSummaryService:
    return ClientSummaryService(
        ClientSummaryRepository(db),
        mapping=ClientMappingRepository(db),
        catalog=MappingCatalogRepository(db),
        titles=ClientTitlesReadService(ClientTitlesRepository(db)),
    )


ServiceDep = Annotated[ClientSummaryService, Depends(_get_service)]


@router.get(
    "/{client_id}/summary",
    summary=(
        "Resumo do cliente para o menu e o painel: só contagens, códigos e IDs, "
        "nenhum nome. Do mês `month` (`YYYY-MM`; padrão, o mês corrente no fuso do "
        "Brasil): conciliações por status e contas cobertas, anomalias em aberto por "
        "código do tipo e resolvidas, compras da fatura de cartão ainda sem lançamento "
        "no Omie (só compra; estorno fica fora), categorias sem decisão e cobertura "
        "do de-para por destino, e se o destino já foi materializado. Mais os títulos "
        "vencidos da carteira inteira (`null` para quem não lê a carteira) e a "
        "conciliação mais recente. Não lê a origem e não sincroniza nada. Mês mal "
        "formado: 400."
    ),
)
async def get_client_summary(
    client: AccessibleClientDep,
    user: CurrentUserDep,
    service: ServiceDep,
    month: Annotated[
        str | None,
        Query(pattern=COMPETENCE_PATTERN, description="Mês de referência, `YYYY-MM`."),
    ] = None,
) -> ClientSummaryEnvelope:
    competence = parse_competence(month) if month else current_competence()
    return ClientSummaryEnvelope(data=await service.summary(client, user, competence))
