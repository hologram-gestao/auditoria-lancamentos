"""Endpoints da CARTEIRA de títulos em aberto (Sprint 11, BACK 11.5 — R4 + R5) e
do contexto/relatório de recebíveis (Sprint 15, BACK 15.1/15.2).

    - GET  /api/v1/clients/{client_id}/titles
    - GET  /api/v1/clients/{client_id}/titles/summary
    - POST /api/v1/clients/{client_id}/titles/sync
    - GET  /api/v1/clients/{client_id}/titles/receivables-report
    - POST /api/v1/clients/{client_id}/titles/{title_id}/context
    - GET  /api/v1/clients/{client_id}/titles/{title_id}/context

**Duas travas, ambas necessárias** (mesmo desenho do plano de contas, S10):

1. A MATRIZ, com DUAS permissões distintas decididas no PRD (R5):
   `view_client_receivables` (todo papel com acesso ao tenant) e
   `sync_client_receivables` (todos menos o `client_operator`). Nenhuma rota aqui
   compara `role` na mão. ⚠️ Os NOMES das permissões vêm do PRD e são contrato,
   mesmo com a tabela chamando-se `client_titles`: a carteira inclui **a pagar**.
2. O TENANT: `AccessibleClientDep` na leitura, `OpenClientDep` na escrita — o
   `client_id` do path só passa por `resolve_client_access`, e cliente ENCERRADO
   recusa a sincronização com 409 enquanto as LEITURAS continuam 200 (§4.12).

E a terceira, na camada de dados: todo `SELECT` da carteira nasce de `_base_query`,
que já carrega `AND client_id = <tenant>`.

⚠️ **As rotas literais vêm ANTES de qualquer rota com path param.** `/summary`,
`/sync` e `/receivables-report` são declaradas aqui sem ambiguidade porque
vêm antes de `/{title_id}/context` (Sprint 15) — o FastAPI casa por ordem de
declaração.

⚠️ **Enum inválido em query é 400 `VALIDATION_ERROR`, nunca 422.** Validação de
FORMA é tratada pelo handler global, sem ecoar mensagem nem campo (convenção de
23/09/2026 e §4.8). Os `Literal` dos filtros existem para que valor fora do
vocabulário vire esse 400 — não uma lista vazia que o usuário lê como "este
cliente não tem nada vencido".

⚠️ **O nome do devedor é resolvido em RUNTIME, em LOTE, e nunca lido do banco**
(§4.5). O caminho é o acessor único `resolve_supplier_names` — o MESMO que a aba de
Divergências usa, sobre o MESMO cache (TTL 6 h + negativo de 15 min). Falha de
resolução devolve o código com `supplierNameResolved=false` e **200**: a carteira
inteira não fica ilegível porque uma credencial expirou.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    ManageTitleContextDep,
    OpenClientDep,
    SettingsDep,
    SyncClientReceivablesDep,
    ViewClientReceivablesDep,
    ViewTitleContextDep,
)
from app.core.logging import get_logger
from app.db.models.client_title import ClientTitle, TitleType
from app.db.models.title_context import TitleContextType
from app.integrations.omie.clientes_cache import OmieClientesCache
from app.integrations.omie.supplier_names import resolve_supplier_names
from app.integrations.providers.base import Capability
from app.modules.client_connections.origin import build_capable_client
from app.modules.client_titles.aging import AgingBucket
from app.modules.client_titles.repository import ClientTitlesRepository, TitleContextRepository
from app.modules.client_titles.schemas import (
    ClientTitleResponse,
    ClientTitlesListResponse,
    ReceivablesReportEnvelope,
    ReceivablesReportResponse,
    TitleBucketFilter,
    TitleContextCreateRequest,
    TitleContextEnvelope,
    TitleContextListResponse,
    TitleSituationFilter,
    TitleSortFieldFilter,
    TitleSortOrderFilter,
    TitlesSummaryEnvelope,
    TitlesSummaryResponse,
    TitlesSyncEnvelope,
    TitlesSyncResponse,
    TitleTypeFilter,
)
from app.modules.client_titles.service import (
    ClientTitlesReadService,
    ClientTitlesSyncService,
    ReceivablesReportService,
    TitleContextService,
)
from app.modules.clients.repository import ClientRepository
from app.modules.users.schemas import PaginationMeta

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/titles",
    tags=["client-titles"],
)


def _get_repository(db: DbSessionDep) -> ClientTitlesRepository:
    return ClientTitlesRepository(db)


RepositoryDep = Annotated[ClientTitlesRepository, Depends(_get_repository)]


def _get_read_service(repository: RepositoryDep) -> ClientTitlesReadService:
    return ClientTitlesReadService(repository)


ReadServiceDep = Annotated[ClientTitlesReadService, Depends(_get_read_service)]


def _get_sync_service(
    db: DbSessionDep, settings: SettingsDep, repository: RepositoryDep
) -> ClientTitlesSyncService:
    return ClientTitlesSyncService(
        db,
        repository=repository,
        clients=ClientRepository(db),
        settings=settings,
    )


SyncServiceDep = Annotated[ClientTitlesSyncService, Depends(_get_sync_service)]


def _get_context_repository(db: DbSessionDep) -> TitleContextRepository:
    return TitleContextRepository(db)


ContextRepositoryDep = Annotated[TitleContextRepository, Depends(_get_context_repository)]


def _get_context_service(
    db: DbSessionDep, settings: SettingsDep, repository: ContextRepositoryDep
) -> TitleContextService:
    return TitleContextService(db, repository=repository, settings=settings)


ContextServiceDep = Annotated[TitleContextService, Depends(_get_context_service)]


def _get_report_service(db: DbSessionDep, repository: RepositoryDep) -> ReceivablesReportService:
    return ReceivablesReportService(db, repository=repository)


ReportServiceDep = Annotated[ReceivablesReportService, Depends(_get_report_service)]


@router.get(
    "",
    summary=(
        "Lista a carteira de títulos em aberto do cliente — a pagar e a receber, "
        "de todas as contas correntes e sem recorte de competência, que é o que "
        "faz um título vencido há meses aparecer aqui e não na conciliação do "
        "mês. Paginada (`page`/`pageSize`, máximo 100), com filtros NO SERVIDOR "
        "por tipo (`type`), situação (`situation`: `em_aberto` ou `vencido`), "
        "balde de aging (`bucket`) e ausência de contexto (`hasNoContext`, "
        "Sprint 15 — a fila de trabalho de quem registra), e ordenação por "
        "vencimento ou valor (`sortBy`/`sortOrder`). O nome do devedor "
        "(`supplierName`) é resolvido "
        "em runtime e nunca lido do banco: quando a origem não responde, a linha "
        "volta com `supplierNameResolved=false` e o código — nunca em branco, e "
        "nunca com erro. Carteira de outro cliente jamais aparece."
    ),
)
async def list_client_titles(
    _user: ViewClientReceivablesDep,
    request: Request,
    client: AccessibleClientDep,
    db: DbSessionDep,
    settings: SettingsDep,
    repository: RepositoryDep,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100)."),
    ] = 20,
    title_type: Annotated[
        TitleTypeFilter | None,
        Query(alias="type", description="Filtra por tipo. Ausente = os dois."),
    ] = None,
    situation: Annotated[
        TitleSituationFilter | None,
        Query(
            description=(
                "`em_aberto` = tudo que a carteira cobra; `vencido` = o "
                "subconjunto com vencimento passado. Ausente = inclui também o "
                "que já saiu do aberto."
            )
        ),
    ] = None,
    bucket: Annotated[
        TitleBucketFilter | None,
        Query(description="Filtra por balde de aging. Ausente = todos."),
    ] = None,
    has_no_context: Annotated[
        bool | None,
        Query(
            alias="hasNoContext",
            description=(
                "`true` = só títulos SEM nenhum registro em `title_contexts` (Sprint "
                "15/R2) — a fila de trabalho de quem registra contexto. Combina com "
                "qualquer outro filtro; a tela usa junto de `situation=vencido`."
            ),
        ),
    ] = None,
    sort_by: Annotated[
        TitleSortFieldFilter,
        Query(alias="sortBy", description="Ordena por vencimento ou por valor."),
    ] = "due_date",
    sort_order: Annotated[
        TitleSortOrderFilter,
        Query(alias="sortOrder", description="Crescente ou decrescente."),
    ] = "asc",
) -> ClientTitlesListResponse:
    today = datetime.now(UTC).date()
    rows, total = await repository.list_for_client(
        client.id,
        today=today,
        title_type=TitleType(title_type) if title_type else None,
        situation=situation,
        bucket=AgingBucket(bucket) if bucket else None,
        has_no_context=has_no_context,
        sort_by=sort_by,
        descending=sort_order == "desc",
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    supplier_names = await _resolve_names_for_page(request, db, settings, client=client, rows=rows)
    # R2 da Sprint 15: a linha diz se o título já tem contexto. Só a CONTAGEM,
    # numa query agrupada pela página; `view_title_context` tem as mesmas células
    # de `view_client_receivables` (todos os papéis), então quem lê a lista pode
    # saber que o contexto existe.
    context_counts = await repository.context_counts(client.id, [row.id for row in rows])
    return ClientTitlesListResponse(
        data=[
            ClientTitleResponse.from_row(
                row, supplier_names=supplier_names, today=today, context_counts=context_counts
            )
            for row in rows
        ],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.get(
    "/summary",
    summary=(
        "Agregados e aging da carteira, calculados no SERVIDOR sobre o conjunto "
        "INTEIRO do cliente — nunca sobre a página. Total em aberto, total a "
        "vencer, total vencido e os quatro baldes (1-30, 31-60, 61-90 e 90+ "
        "dias), separadamente para a pagar e a receber; os quatro baldes somam "
        "exatamente o total vencido. `neverSynced=true` significa que a carteira "
        "NUNCA foi sincronizada: a tela oferece sincronizar em vez de mostrar "
        "zeros que pareceriam resultado. Quando a última tentativa falhou, os "
        "números são os da última sincronização íntegra (`syncedAt` diz de "
        "quando) e `syncFailedAt` traz o aviso. `referenceDate` é o 'hoje' do "
        "servidor usado no aging."
    ),
)
async def get_client_titles_summary(
    _user: ViewClientReceivablesDep,
    client: AccessibleClientDep,
    service: ReadServiceDep,
) -> TitlesSummaryEnvelope:
    summary = await service.summary(client)
    return TitlesSummaryEnvelope(data=TitlesSummaryResponse.from_summary(summary))


@router.post(
    "/sync",
    summary=(
        "Sincroniza a carteira com a origem e devolve as contagens do ciclo mais "
        "os agregados resultantes. Requer a permissão `sync_client_receivables` "
        "(plataforma, admin, gerente da carteira e gerente do cliente — o "
        "operador do cliente LÊ mas não sincroniza). Lê todos os títulos não "
        "liquidados de todas as contas, sem recorte de competência, serializando "
        "as chamadas por cliente. Cliente encerrado: 409. Cliente sem origem "
        "capaz: 409 `SEM_CONEXAO`, `ORIGEM_COM_ERRO` ou `CAPACIDADE_AUSENTE`, "
        "conforme o caso — a leitura do que já existe continua funcionando. "
        "Falha no meio preserva a última carteira íntegra e registra a falha, "
        "sem nunca deixar carteira parcial passando por completa."
    ),
)
async def sync_client_titles(
    _user: SyncClientReceivablesDep,
    client: OpenClientDep,
    service: SyncServiceDep,
    read_service: ReadServiceDep,
) -> TitlesSyncEnvelope:
    result = await service.sync(client)
    summary = await read_service.summary(client)
    return TitlesSyncEnvelope(
        data=TitlesSyncResponse(
            titulos_pagar=result.titulos_pagar,
            titulos_receber=result.titulos_receber,
            vencidos=result.vencidos,
            mais_antigo_dias=result.mais_antigo_dias,
            summary=TitlesSummaryResponse.from_summary(summary),
        )
    )


@router.get(
    "/receivables-report",
    summary=(
        "Relatório de recebíveis (Sprint 15) — separa, no SERVIDOR e sobre a "
        "carteira INTEIRA, inadimplência real de vencido-com-contexto. Cada "
        "lado (a pagar/a receber) tem dois grupos: `inadimplencia` (título "
        "vencido sem nenhum contexto, ou cujo contexto mais recente é "
        "`perda_provavel`) e `vencidoComContexto` (mais recente é acordo, "
        "antecipação, nota a cancelar, cobrança suspensa ou outro), cada um "
        "com total e os quatro baldes de aging da Sprint 11. Cliente sem "
        "nenhum contexto: tudo em `inadimplencia`, sem erro — é o baseline. "
        "Não expõe texto decifrado nem identificador de título, só agregados — "
        "por isso usa a MESMA permissão de leitura da carteira "
        "(`view_client_receivables`), não `view_title_context`."
    ),
)
async def get_receivables_report(
    _user: ViewClientReceivablesDep,
    client: AccessibleClientDep,
    service: ReportServiceDep,
) -> ReceivablesReportEnvelope:
    report = await service.report(client)
    return ReceivablesReportEnvelope(data=ReceivablesReportResponse.from_report(report))


# ⚠️ As duas rotas abaixo têm `{title_id}` — vêm DEPOIS de "", "/summary",
# "/sync" e "/receivables-report" de propósito (nenhuma delas tem path param
# depois do prefixo; o FastAPI casa por ordem de declaração).


@router.post(
    "/{title_id}/context",
    summary=(
        "Registra uma entrada de CONTEXTO sobre um título — acordo de pagamento, "
        "antecipação, nota a cancelar, cobrança suspensa, perda provável ou "
        "outro. Append-only: registrar de novo NÃO apaga o histórico, só adiciona "
        "(a classificação do relatório usa o mais recente). O texto nasce cifrado "
        "com a chave do cliente. `type` fora do vocabulário fechado é validação "
        "de FORMA — 400 `VALIDATION_ERROR`, nunca 422. Cliente encerrado: 409. "
        "Título de outro cliente: 404, sem revelar que existe alhures."
    ),
)
async def register_title_context(
    user: ManageTitleContextDep,
    client: OpenClientDep,
    title_id: UUID,
    payload: TitleContextCreateRequest,
    service: ContextServiceDep,
) -> TitleContextEnvelope:
    entry = await service.register(
        client,
        title_id=title_id,
        context_type=TitleContextType(payload.type),
        text=payload.text,
        author=user,
    )
    return TitleContextEnvelope(data=entry)


@router.get(
    "/{title_id}/context",
    summary=(
        "Histórico COMPLETO de contexto de um título, mais recente primeiro. "
        "Nunca paginado (é o registro de um título, não uma coleção sem teto). "
        "O autor de cada entrada é ENXUTO e mascarado por escopo (mesma decisão "
        "de autoria de sessão, §3.15) — usuário de cliente vendo autor de staff "
        "recebe 'Equipe {organização}', sem e-mail. Falha de decifragem de uma "
        "entrada não derruba o histórico: ela volta como `[indecifrável]` com "
        "`decryptFailed=true`. Título de outro cliente: 404, sem revelar que "
        "existe alhures."
    ),
)
async def list_title_context(
    user: ViewTitleContextDep,
    client: AccessibleClientDep,
    title_id: UUID,
    service: ContextServiceDep,
) -> TitleContextListResponse:
    entries = await service.list_history(client, title_id=title_id, viewer=user)
    return TitleContextListResponse(data=entries)


async def _resolve_names_for_page(
    request: Request,
    db: DbSessionDep,
    settings: SettingsDep,
    *,
    client: AccessibleClientDep,
    rows: list[ClientTitle],
) -> dict[int, str]:
    """Nomes de devedor dos códigos DESTA página, em LOTE e fail-soft.

    **Em lote, e é o que evita o N+1:** um único conjunto de códigos para a página
    inteira, uma passada pelo cache, e consulta à origem só do que falta. Um
    `ConsultarCliente` por linha renderizada seria 20 idas à origem por abertura de
    tela — e a origem processa uma requisição por método por credencial.

    **Fail-soft em TODO o caminho, por decisão de produto.** Nem o cache ausente,
    nem o cliente sem origem, nem a origem indisponível podem impedir a carteira de
    ser lida: códigos, valores, vencimentos e o aging são LOCAIS e continuam
    corretos. O que falta é o nome, e a linha diz isso com
    `supplierNameResolved=false`.

    É por isso que o 409 da taxonomia da S9 é **engolido aqui**: cliente sem origem
    conectada continua tendo carteira para ler (a que foi sincronizada antes de a
    conexão sair), e responder 409 numa LEITURA transformaria uma configuração
    pendente numa tela quebrada.
    """
    codes = {row.supplier_code for row in rows if row.supplier_code is not None}
    if not codes:
        return {}

    cache: OmieClientesCache | None = getattr(request.app.state, "omie_clientes_cache", None)
    omie_client = None
    try:
        omie_client = await build_capable_client(
            db, client, Capability.LISTAR_LANCAMENTOS, settings=settings
        )
    except Exception:
        # Só IDs: a mensagem do provedor é texto livre de terceiro (§3.3). Sem
        # origem alcançável a resolução cai no que já estiver em cache — nunca
        # `except: pass`, o caminho fica no log.
        log.info("client_titles_supplier_names_origin_unavailable", client_id=str(client.id))

    return await resolve_supplier_names(
        cache=cache,
        client_id=client.id,
        codes=codes,
        omie_client=omie_client,
    )
