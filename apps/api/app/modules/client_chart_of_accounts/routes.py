"""Endpoints do plano de contas do cliente (Sprint 10, BACK 10.3 — R3 + R4).

    - GET  /api/v1/clients/{client_id}/chart-of-accounts
    - GET  /api/v1/clients/{client_id}/chart-of-accounts/coverage
    - POST /api/v1/clients/{client_id}/chart-of-accounts/sync

**Duas travas, ambas necessárias** (mesmo desenho das conexões de origem):

1. A MATRIZ, com DUAS permissões distintas decididas no PRD (R4):
   `view_client_chart_of_accounts` (todo papel com acesso ao tenant) e
   `sync_client_chart_of_accounts` (todos menos o `client_operator`). Nenhuma
   das existentes servia: `manage_client_categories` é admin-only e deixaria de
   fora o gerente do escritório parceiro; `sync_omie_accounts` é de todos e
   deixaria o operador forçar chamadas à origem. Nenhuma rota aqui compara
   `role` na mão.
2. O TENANT: `AccessibleClientDep` na leitura, `OpenClientDep` na escrita — o
   `client_id` do path só passa por `resolve_client_access`, e cliente
   ENCERRADO recusa a sincronização com 409 enquanto a LEITURA continua 200
   (§4.12).

E a terceira, na camada de dados: todo `SELECT` do plano de contas nasce de
`_base_query`, que já carrega `AND client_id = <tenant>`.

⚠️ **A rota literal vem ANTES da rota com path param** — `/coverage` e `/sync`
são declaradas sem ambiguidade aqui porque nenhuma rota deste módulo tem path
param depois do prefixo; se um `/{entry_id}` aparecer, ele tem de ficar
DEPOIS das duas (o FastAPI casa por ordem de declaração).

⚠️ **A busca é por CÓDIGO.** Não existe busca por nome no servidor: nome não
está no banco (§4.5), e oferecê-lo exigiria persisti-lo.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    OpenClientDep,
    SettingsDep,
    SyncClientChartOfAccountsDep,
    ViewClientChartOfAccountsDep,
)
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.modules.client_chart_of_accounts.repository import ClientChartOfAccountsRepository
from app.modules.client_chart_of_accounts.schemas import (
    MAX_CODE_SEARCH_CHARS,
    ChartOfAccountEntryResponse,
    ChartOfAccountsCoverageEnvelope,
    ChartOfAccountsCoverageResponse,
    ChartOfAccountsListResponse,
    ChartOfAccountsStatusFilter,
)
from app.modules.client_chart_of_accounts.service import ChartOfAccountsSyncService
from app.modules.omie_data.categorias_service import OmieCategoriasService
from app.modules.users.schemas import PaginationMeta

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/chart-of-accounts",
    tags=["client-chart-of-accounts"],
)


def _get_repository(db: DbSessionDep) -> ClientChartOfAccountsRepository:
    return ClientChartOfAccountsRepository(db)


RepositoryDep = Annotated[ClientChartOfAccountsRepository, Depends(_get_repository)]


def _get_service(
    request: Request,
    db: DbSessionDep,
    settings: SettingsDep,
    repository: RepositoryDep,
) -> ChartOfAccountsSyncService:
    """O serviço, com o MESMO cache de categorias que a tela de revisão usa.

    O cache vive em `app.state` (montado no `lifespan`), como em
    `omie_data/routes.py`. Construir um `OmieCategoriasCache()` aqui daria um
    cache POR REQUEST, que nunca acerta: toda abertura de tela iria à origem, e
    o "um único caminho de leitura" viraria "o mesmo caminho, percorrido sempre
    do zero".
    """
    cache: OmieCategoriasCache = request.app.state.omie_categorias_cache
    return ChartOfAccountsSyncService(
        db,
        repository=repository,
        categorias_service=OmieCategoriasService(cache),
        settings=settings,
    )


ServiceDep = Annotated[ChartOfAccountsSyncService, Depends(_get_service)]


@router.get(
    "",
    summary=(
        "Lista o plano de contas do cliente — o que a origem classifica, já "
        "dentro do produto. Paginado (`page`/`pageSize`, máximo 100), com "
        "filtro por situação (`ativa`, `inativa`, `ausente_na_origem`) e por "
        "hierarquia (`parentCode`), e **busca por CÓDIGO** (`code`). Não existe "
        "busca por nome: o nome não é persistido (é resolvido em runtime pelo "
        "mesmo cache da tela de revisão) e vem `null` quando a origem não "
        "responde — a lista é servida assim mesmo. `dreCode` nulo significa "
        "**sem destino declarado**, que é informação e não pendência: "
        "transferências e totalizadoras não têm conta de demonstrativo própria. "
        "Plano de contas de outro cliente nunca aparece."
    ),
)
async def list_chart_of_accounts(
    _user: ViewClientChartOfAccountsDep,
    client: AccessibleClientDep,
    repository: RepositoryDep,
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100)."),
    ] = 20,
    status: Annotated[
        ChartOfAccountsStatusFilter | None,
        Query(description="Filtra por situação. Ausente = todas."),
    ] = None,
    parent_code: Annotated[
        str | None,
        Query(
            alias="parentCode",
            max_length=MAX_CODE_SEARCH_CHARS,
            description="Filtra pelas filhas diretas deste código.",
        ),
    ] = None,
    code: Annotated[
        str | None,
        Query(
            max_length=MAX_CODE_SEARCH_CHARS,
            description="Busca por CÓDIGO (contém). Curingas de `LIKE` são literais.",
        ),
    ] = None,
) -> ChartOfAccountsListResponse:
    rows, total = await repository.list_for_client(
        client.id,
        status=status,
        parent_code=parent_code,
        code_contains=code,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    names = await service.resolve_names(client)
    return ChartOfAccountsListResponse(
        data=[ChartOfAccountEntryResponse.from_row(row, names=names) for row in rows],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.get(
    "/coverage",
    summary=(
        "Cobertura do plano de contas: total, ativas, com destino, sem destino "
        "declarado e com conta contábil — calculadas no SERVIDOR sobre o "
        "conjunto INTEIRO do cliente, não sobre a página. É a resposta para "
        "'quanto do de-para já vem pronto': `comDestino + semDestino == "
        "ativas`. Traz também a data da última sincronização bem-sucedida "
        "(`null` = nunca sincronizou, estado vazio da tela) e a da última que "
        "falhou, quando a mais recente falhou."
    ),
)
async def get_chart_of_accounts_coverage(
    _user: ViewClientChartOfAccountsDep,
    client: AccessibleClientDep,
    repository: RepositoryDep,
) -> ChartOfAccountsCoverageEnvelope:
    coverage = await repository.coverage(client.id)
    synced_at, sync_failed_at = await repository.get_sync_state(client.id)
    return ChartOfAccountsCoverageEnvelope(
        data=ChartOfAccountsCoverageResponse.from_coverage(
            coverage, synced_at=synced_at, sync_failed_at=sync_failed_at
        )
    )


@router.post(
    "/sync",
    summary=(
        "Sincroniza o plano de contas com a origem e devolve a cobertura "
        "resultante. Requer a permissão `sync_client_chart_of_accounts` "
        "(plataforma, admin, gerente da carteira e gerente do cliente — o "
        "operador do cliente LÊ mas não sincroniza). Dentro da validade de 24 h "
        "serve do armazenamento local sem chamar a origem; `force=true` "
        "('Sincronizar agora') ignora a validade e rebusca. Cliente encerrado: "
        "409. Cliente sem origem capaz: 409 `SEM_CONEXAO`, `ORIGEM_COM_ERRO` ou "
        "`CAPACIDADE_AUSENTE`, conforme o caso — a leitura do que já existe "
        "continua funcionando. Falha da origem preserva a última sincronização "
        "bem-sucedida."
    ),
)
async def sync_chart_of_accounts(
    _user: SyncClientChartOfAccountsDep,
    client: OpenClientDep,
    repository: RepositoryDep,
    service: ServiceDep,
    force: Annotated[
        bool,
        Query(description="Ignora a validade de 24 h e rebusca da origem."),
    ] = False,
) -> ChartOfAccountsCoverageEnvelope:
    result = await service.sync(client, force=force)
    _synced_at, sync_failed_at = await repository.get_sync_state(client.id)
    return ChartOfAccountsCoverageEnvelope(
        data=ChartOfAccountsCoverageResponse.from_coverage(
            result.coverage, synced_at=result.synced_at, sync_failed_at=sync_failed_at
        )
    )
