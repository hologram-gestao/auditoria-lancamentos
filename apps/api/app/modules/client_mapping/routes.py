"""Rotas do de-para do cliente (Sprint 12, BACK 12.4 — R2/R4/R6/R7).

    POST /api/v1/clients/{client_id}/mapping/{destination_type}/decisions
    POST /api/v1/clients/{client_id}/mapping/{destination_type}/decisions/batch
    POST /api/v1/clients/{client_id}/mapping/{destination_type}/decisions/confirm-inherited
    GET  /api/v1/clients/{client_id}/mapping/{destination_type}/decisions/history
    POST /api/v1/clients/{client_id}/mapping/{destination_type}/inherit
    GET  /api/v1/clients/{client_id}/mapping/{destination_type}                 (12.5)
    GET  /api/v1/clients/{client_id}/mapping/{destination_type}/export          (12.5)
    POST /api/v1/clients/{client_id}/mapping/{destination_type}/import/preview  (12.5)
    POST /api/v1/clients/{client_id}/mapping/{destination_type}/import          (12.5)
    GET  /api/v1/clients/{client_id}/mapping/{destination_type}/preview         (12.6)
    POST /api/v1/clients/{client_id}/mapping/{destination_type}/materializations (12.6)

**Duas travas.** A MATRIZ: `manage_client_mapping` (células do PRD, R6) na escrita,
pelo guard AUDITADO — a negação vira 1 linha em `access_audit`. A LEITURA é de quem
alcança o cliente (`AccessibleClientDep`; o operador VÊ a tela). O TENANT: o
`client_id` do path passa por `resolve_client_access`; escrita em cliente
encerrado é 409 (`OpenClientDep`).

**O destino é endereçado pelo TIPO** (`demonstrativo_contabil`…) e resolvido na
organização DO CLIENTE: destino que a organização não configurou (ou desativou) é
409 `DESTINO_NAO_CONFIGURADO` nomeando o tipo.

⚠️ `client` vem ANTES do guard nas assinaturas: o alcance é decidido antes da
célula da matriz (e a trilha cross-tenant antes da de permissão).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, Request, Response, UploadFile

from app.core.audit import AccessAction, record_access
from app.core.dependencies import (
    AccessibleClientDep,
    CurrentUserDep,
    DbSessionDep,
    ManageClientMappingDep,
    OpenClientDep,
    SettingsDep,
)
from app.db.models import DecisionType
from app.db.models.client_movement import MAX_MOVEMENT_CATEGORY_CODE_CHARS
from app.db.models.mapping_catalog import DESTINATION_TYPE_PATTERN
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.modules.client_chart_of_accounts.repository import ClientChartOfAccountsRepository
from app.modules.client_chart_of_accounts.service import ChartOfAccountsSyncService
from app.modules.client_mapping.listing import ClientMappingListService, NamedRow
from app.modules.client_mapping.materialization import ClientMappingApplyService
from app.modules.client_mapping.portability import (
    MAX_IMPORT_BYTES,
    ClientMappingPortabilityService,
    client_label,
)
from app.modules.client_mapping.repository import ClientMappingRepository
from app.modules.client_mapping.schemas import (
    ConfirmInheritedEnvelope,
    ConfirmInheritedRequest,
    ConfirmInheritedResponse,
    DecisionBatchRequest,
    DecisionHistoryResponse,
    DecisionItemRequest,
    DecisionViewResponse,
    DecisionWriteEnvelope,
    DecisionWriteRequest,
    DecisionWriteResponse,
    ImportApplyEnvelope,
    ImportApplyResponse,
    ImportPreviewEnvelope,
    ImportPreviewResponse,
    InheritEnvelope,
    InheritRequest,
    InheritResponse,
    MappingListItem,
    MappingListResponse,
    MappingPreviewEnvelope,
    MappingPreviewResponse,
    MappingSituationName,
    MaterializationEnvelope,
    MaterializationRequest,
    MaterializationResponse,
)
from app.modules.client_mapping.service import ClientMappingDecisionService, DecisionInput
from app.modules.client_movements.competence import (
    COMPETENCE_PATTERN,
    format_competence,
    parse_competence,
)
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.service import MappingCatalogService
from app.modules.omie_data.categorias_service import OmieCategoriasService
from app.modules.users.schemas import PaginationMeta
from app.utils.upload import parse_content_length, read_upload_within_limit

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/mapping/{destination_type}",
    tags=["client-mapping"],
)

DestinationTypePath = Annotated[
    str,
    Path(
        pattern=DESTINATION_TYPE_PATTERN,
        description="Tipo do destino (slug, ex.: `demonstrativo_contabil`).",
    ),
]


def _get_decision_service(db: DbSessionDep) -> ClientMappingDecisionService:
    catalog = MappingCatalogRepository(db)
    return ClientMappingDecisionService(
        ClientMappingRepository(db),
        catalog=catalog,
        catalog_service=MappingCatalogService(catalog),
    )


DecisionServiceDep = Annotated[ClientMappingDecisionService, Depends(_get_decision_service)]


def _get_list_service(
    request: Request, db: DbSessionDep, settings: SettingsDep, decisions: DecisionServiceDep
) -> ClientMappingListService:
    """A leitura, com o nome de categoria pelo MESMO acessor do plano de contas.

    O cache de categorias vive em `app.state` — um por processo, o mesmo da tela de
    revisão e do plano de contas (construir um aqui daria um cache por request).
    """
    cache: OmieCategoriasCache = request.app.state.omie_categorias_cache
    names = ChartOfAccountsSyncService(
        db,
        repository=ClientChartOfAccountsRepository(db),
        categorias_service=OmieCategoriasService(cache),
        settings=settings,
    )
    return ClientMappingListService(
        ClientMappingRepository(db),
        decisions=decisions,
        catalog=MappingCatalogRepository(db),
        names=names,
    )


ListServiceDep = Annotated[ClientMappingListService, Depends(_get_list_service)]


def _get_portability_service(
    db: DbSessionDep, decisions: DecisionServiceDep, listing: ListServiceDep
) -> ClientMappingPortabilityService:
    return ClientMappingPortabilityService(
        listing=listing, decisions=decisions, catalog=MappingCatalogRepository(db)
    )


PortabilityServiceDep = Annotated[
    ClientMappingPortabilityService, Depends(_get_portability_service)
]


def _to_input(item: DecisionItemRequest) -> DecisionInput:
    return DecisionInput(
        category_code=item.category_code,
        decision_type=DecisionType(item.decision),
        target_code=item.target_code,
        source_type=item.source_type,
    )


@router.post(
    "/decisions",
    summary=(
        "Grava UMA decisão de de-para (categoria → alvo do catálogo ou `nao_mapear`) "
        "com vigência a partir de `effectiveFrom` (padrão: competência corrente). "
        "Append-only: alterar cria vigência nova; a anterior vale até o mês anterior. "
        "Alvo inexistente/inativo: 422 `ALVO_INEXISTENTE` nomeando o código. Destino "
        "não configurado: 409 `DESTINO_NAO_CONFIGURADO`. Decisão confirmada já "
        "existente na mesma vigência: 409 `DECISAO_DUPLICADA`. Início retroativo: 409 "
        "`COMPETENCIA_MATERIALIZADA` se atingir competência materializada; senão 409 "
        "`RETROATIVA_REQUER_CONFIRMACAO` até `confirmRetroactive=true`. Requer "
        "`manage_client_mapping`; cliente encerrado: 409."
    ),
)
async def write_decision(
    client: OpenClientDep,
    user: ManageClientMappingDep,
    destination_type: DestinationTypePath,
    payload: DecisionWriteRequest,
    service: DecisionServiceDep,
) -> DecisionWriteEnvelope:
    result = await service.write_decisions(
        client,
        destination_type,
        [_to_input(payload)],
        author=user,
        effective_from=payload.effective_from_date,
        confirm_retroactive=payload.confirm_retroactive,
    )
    return DecisionWriteEnvelope(data=DecisionWriteResponse.build(result))


@router.post(
    "/decisions/batch",
    summary=(
        "Grava VÁRIAS decisões na mesma vigência, atômico (até 500). Mesmas regras da "
        "rota unitária; categoria repetida no lote: 400."
    ),
)
async def write_decisions_batch(
    client: OpenClientDep,
    user: ManageClientMappingDep,
    destination_type: DestinationTypePath,
    payload: DecisionBatchRequest,
    service: DecisionServiceDep,
) -> DecisionWriteEnvelope:
    result = await service.write_decisions(
        client,
        destination_type,
        [_to_input(item) for item in payload.decisions],
        author=user,
        effective_from=payload.effective_from_date,
        confirm_retroactive=payload.confirm_retroactive,
    )
    return DecisionWriteEnvelope(data=DecisionWriteResponse.build(result))


@router.post(
    "/decisions/confirm-inherited",
    summary=(
        "Confirma EM LOTE as decisões HERDADAS vigentes do destino. Sem `confirm=true` "
        "nada é gravado e a resposta traz a quantidade que seria afetada (`affected`); "
        "com ela, cada herdada vira decisão confirmada de mesmo efeito. Requer "
        "`manage_client_mapping`."
    ),
)
async def confirm_inherited_decisions(
    client: OpenClientDep,
    user: ManageClientMappingDep,
    destination_type: DestinationTypePath,
    payload: ConfirmInheritedRequest,
    service: DecisionServiceDep,
) -> ConfirmInheritedEnvelope:
    affected, result = await service.confirm_inherited(
        client,
        destination_type,
        author=user,
        confirm=payload.confirm,
        effective_from=payload.effective_from_date,
        confirm_retroactive=payload.confirm_retroactive,
    )
    return ConfirmInheritedEnvelope(
        data=ConfirmInheritedResponse(
            affected=affected,
            applied=result is not None,
            result=DecisionWriteResponse.build(result) if result is not None else None,
        )
    )


@router.get(
    "/decisions/history",
    summary=(
        "Todas as vigências de UMA categoria (por CÓDIGO) no destino, da mais antiga à "
        "mais nova, com a origem (herdada/confirmada). Leitura de quem alcança o cliente."
    ),
)
async def get_decision_history(
    client: AccessibleClientDep,
    destination_type: DestinationTypePath,
    service: DecisionServiceDep,
    category_code: Annotated[
        str,
        Query(
            alias="categoryCode",
            min_length=1,
            max_length=MAX_MOVEMENT_CATEGORY_CODE_CHARS,
            description="Código da categoria na origem.",
        ),
    ],
    source_type: Annotated[
        str,
        Query(alias="sourceType", pattern=DESTINATION_TYPE_PATTERN),
    ] = "omie",
) -> DecisionHistoryResponse:
    views = await service.history(
        client, destination_type, source_type=source_type, category_code=category_code
    )
    return DecisionHistoryResponse(data=[DecisionViewResponse.build(v) for v in views])


@router.post(
    "/inherit",
    summary=(
        "Inicia o de-para do destino a partir do plano de contas sincronizado — "
        "explícito e idempotente. Só o `demonstrativo_contabil` herda: categoria ativa "
        "com conta de demonstrativo vira decisão HERDADA para o alvo de mesmo código; "
        "'sem destino declarado' fica sem decisão (nunca `nao_mapear`); conta sem alvo "
        "no catálogo fica sem decisão e é listada. Os outros destinos respondem "
        "`destino_sem_heranca`; cliente sem plano de contas, `sem_plano_de_contas` "
        "— sem erro. Requer `manage_client_mapping`."
    ),
)
async def inherit_mapping(
    client: OpenClientDep,
    user: ManageClientMappingDep,
    destination_type: DestinationTypePath,
    payload: InheritRequest,
    service: DecisionServiceDep,
) -> InheritEnvelope:
    result = await service.inherit(
        client,
        destination_type,
        author=user,
        effective_from=payload.effective_from_date,
        confirm_retroactive=payload.confirm_retroactive,
    )
    return InheritEnvelope(data=InheritResponse.build(result))


# ---------------------------------------------------------------------------
# Leitura por destino e portabilidade (BACK 12.5)
# ---------------------------------------------------------------------------


@router.get(
    "",
    summary=(
        "O de-para do cliente no destino: UMA linha por categoria do universo (plano "
        "de contas + categorias vistas na base de movimentos + as que já têm decisão), "
        "com a decisão vigente na competência corrente do servidor. Paginado "
        "(`page`/`pageSize`, máximo 100), com filtro por situação no SERVIDOR "
        "(`herdada`, `confirmada`, `nao_mapear`, `sem_decisao`) e busca por CÓDIGO "
        "(`code`, prefixo). O nome da categoria é resolvido em runtime e NÃO é "
        "buscável; origem fora do ar devolve o código com `categoryNameResolved=false`. "
        "`divergent` sinaliza que o plano de contas mudou na origem depois da decisão. "
        "Leitura de quem alcança o cliente."
    ),
)
async def list_client_mapping(
    client: AccessibleClientDep,
    destination_type: DestinationTypePath,
    service: ListServiceDep,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100).")
    ] = 20,
    situation: Annotated[
        MappingSituationName | None, Query(description="Filtra por situação.")
    ] = None,
    code: Annotated[
        str | None,
        Query(max_length=MAX_MOVEMENT_CATEGORY_CODE_CHARS, description="Código começando por."),
    ] = None,
) -> MappingListResponse:
    rows, total, competence = await service.page(
        client,
        destination_type,
        situation=situation,
        code_prefix=code,
        page=page,
        page_size=page_size,
    )
    return MappingListResponse(
        data=[_list_item(named) for named in rows],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
        competence=format_competence(competence),
    )


def _list_item(named: NamedRow) -> MappingListItem:
    row = named.row
    return MappingListItem(
        source_type=row.source_type,
        category_code=row.category_code,
        category_name=named.category_name,
        category_name_resolved=named.category_name_resolved,
        situation=row.situation,
        decision=row.decision_type,
        target_code=row.target_code,
        target_name=named.target_name,
        effective_from=format_competence(row.effective_from) if row.effective_from else None,
        divergent=row.divergent,
        origin_dre_code=row.origin_dre_code,
    )


@router.get(
    "/export",
    summary=(
        "Exporta o de-para do cliente no destino em planilha .xlsx: códigos nas "
        "colunas-chave (categoria, destino, decisão, alvo, vigência) e os nomes "
        "resolvidos NA GERAÇÃO (nunca persistidos). Célula de texto que começaria "
        "fórmula é neutralizada. Registra a exportação na trilha de acesso."
    ),
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
async def export_client_mapping(
    client: AccessibleClientDep,
    user: CurrentUserDep,
    db: DbSessionDep,
    destination_type: DestinationTypePath,
    service: PortabilityServiceDep,
) -> Response:
    content = await service.export(client, destination_type)
    # A trilha (§4.7): 1 linha `export`, só IDs. Sem `commit` próprio — o fim de
    # request (`get_db_session`) persiste junto com a resposta.
    await record_access(
        db,
        user_id=UUID(user.id),
        client_id=client.id,
        action=AccessAction.EXPORT,
        user_scope=user.scope,
        actor_client_id=user.client_id,
        actor_organization_id=user.organization_id,
    )
    filename = client_label(client) + "-" + destination_type + ".xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="' + filename + '"'},
    )


def _get_apply_service(
    db: DbSessionDep, decisions: DecisionServiceDep
) -> ClientMappingApplyService:
    return ClientMappingApplyService(
        db,
        repository=ClientMappingRepository(db),
        movements=ClientMovementsRepository(db),
        decisions=decisions,
    )


ApplyServiceDep = Annotated[ClientMappingApplyService, Depends(_get_apply_service)]


@router.get(
    "/preview",
    summary=(
        "PRÉVIA da aplicação do de-para na competência (`YYYY-MM`): sobre a base de "
        "movimentos PRESENTES, de todas as contas, as QUATRO situações (alvo, "
        "`nao_mapear`, sem decisão, sem categoria de origem) em valor e quantidade, a "
        "cobertura (Σ|valor| com decisão ÷ Σ|valor| com categoria — 'sem categoria' "
        "fica fora) e a contra-métrica `naoMapearPct`, as categorias sem decisão por "
        "|valor| decrescente e o estado da base. Determinística e sem IA. Não "
        "sincroniza. Erros, nesta ordem: competência nunca sincronizada 409 "
        "`BASE_NAO_SINCRONIZADA`; sem movimento 409 `SEM_MOVIMENTOS`; destino não "
        "configurado 409 `DESTINO_NAO_CONFIGURADO`; anterior à primeira vigência 409 "
        "`ANTERIOR_A_PRIMEIRA_VIGENCIA` (`details.earliestCompetence`). Leitura de "
        "quem alcança o cliente."
    ),
)
async def preview_client_mapping(
    client: AccessibleClientDep,
    destination_type: DestinationTypePath,
    service: ApplyServiceDep,
    competence: Annotated[
        str, Query(pattern=COMPETENCE_PATTERN, description="Competência, `YYYY-MM`.")
    ],
) -> MappingPreviewEnvelope:
    preview = await service.preview(client, destination_type, parse_competence(competence))
    return MappingPreviewEnvelope(data=MappingPreviewResponse.build(preview))


@router.post(
    "/materializations",
    status_code=201,
    summary=(
        "MATERIALIZA a prévia confirmada: recalcula no servidor e só grava se o "
        "`previewToken` bater (senão 409 `PREVIA_DESATUALIZADA` — gere a prévia de "
        "novo). Havendo valor sem decisão exige `confirmPartialCoverage=true` (409 "
        "`COBERTURA_PARCIAL_REQUER_CONFIRMACAO`), e a confirmação fica no próprio "
        "registro. Cria a versão N+1 — imutável; reaplicar nunca sobrescreve e não "
        "existe rota que altere ou apague materialização. Emite a métrica "
        "`depara_aplicado`. Requer `manage_client_mapping`; cliente encerrado: 409."
    ),
)
async def materialize_client_mapping(
    client: OpenClientDep,
    user: ManageClientMappingDep,
    destination_type: DestinationTypePath,
    payload: MaterializationRequest,
    service: ApplyServiceDep,
) -> MaterializationEnvelope:
    outcome = await service.materialize(
        client,
        destination_type,
        payload.competence_date,
        preview_token=payload.preview_token,
        confirm_partial_coverage=payload.confirm_partial_coverage,
        author=user,
    )
    return MaterializationEnvelope(data=MaterializationResponse.build(outcome))


async def _read_import(request: Request, file: UploadFile) -> bytes:
    """O arquivo em MEMÓRIA, com teto aplicado em streaming (nunca em disco, §3.10)."""
    return await read_upload_within_limit(
        file,
        declared_content_length=parse_content_length(request.headers.get("content-length")),
        max_bytes=MAX_IMPORT_BYTES,
    )


@router.post(
    "/import/preview",
    summary=(
        "PRÉVIA da importação da planilha exportada — NÃO grava nada. Casa por CÓDIGO "
        "(a coluna de nome é ignorada). Devolve criadas / alteradas / ignoradas e as "
        "linhas recusadas com o motivo (categoria ou alvo inexistente, decisão "
        "inválida…); a linha recusada não derruba o lote. Arquivo .xlsx, até 2 MB e "
        "2.000 linhas. Requer `manage_client_mapping`."
    ),
)
async def preview_client_mapping_import(
    client: OpenClientDep,
    _user: ManageClientMappingDep,
    request: Request,
    destination_type: DestinationTypePath,
    service: PortabilityServiceDep,
    file: Annotated[UploadFile, File(description="Planilha .xlsx do de-para.")],
    effective_from: Annotated[
        str | None,
        Form(alias="effectiveFrom", pattern=COMPETENCE_PATTERN, description="`YYYY-MM`."),
    ] = None,
) -> ImportPreviewEnvelope:
    content = await _read_import(request, file)
    plan = await service.plan(
        client,
        destination_type,
        filename=file.filename,
        content=content,
        effective_from=parse_competence(effective_from) if effective_from else None,
    )
    return ImportPreviewEnvelope(data=ImportPreviewResponse.build(plan))


@router.post(
    "/import",
    summary=(
        "APLICA a importação — exige `confirm=true` (sem ele: 409, a prévia é "
        "obrigatória). Recalcula a prévia no servidor e grava TODAS as linhas válidas "
        "de uma vez (atômico), como vigência nova a partir de `effectiveFrom` (padrão: "
        "competência corrente) — nunca sobrescreve a vigente. Início retroativo segue "
        "as regras da escrita de decisão (`confirmRetroactive`). Requer "
        "`manage_client_mapping`; cliente encerrado: 409."
    ),
)
async def apply_client_mapping_import(
    client: OpenClientDep,
    user: ManageClientMappingDep,
    request: Request,
    destination_type: DestinationTypePath,
    service: PortabilityServiceDep,
    file: Annotated[UploadFile, File(description="Planilha .xlsx do de-para.")],
    confirm: Annotated[bool, Form(description="Confirmação explícita da importação.")] = False,
    effective_from: Annotated[
        str | None,
        Form(alias="effectiveFrom", pattern=COMPETENCE_PATTERN, description="`YYYY-MM`."),
    ] = None,
    confirm_retroactive: Annotated[bool, Form(alias="confirmRetroactive")] = False,
) -> ImportApplyEnvelope:
    content = await _read_import(request, file)
    plan, result = await service.apply(
        client,
        destination_type,
        filename=file.filename,
        content=content,
        author=user,
        confirm=confirm,
        effective_from=parse_competence(effective_from) if effective_from else None,
        confirm_retroactive=confirm_retroactive,
    )
    return ImportApplyEnvelope(
        data=ImportApplyResponse(
            preview=ImportPreviewResponse.build(plan),
            result=DecisionWriteResponse.build(result) if result is not None else None,
        )
    )
