"""Endpoints do plano de contas CONTÁBIL do cliente (Sprint 16, BACK 16.1 — R1/R5).

    - GET  /api/v1/clients/{client_id}/accounting-chart
    - POST /api/v1/clients/{client_id}/accounting-chart/import   (multipart)
    - POST /api/v1/clients/{client_id}/accounting-chart/import/preview  (multipart, 86e3qhhrm)
    - GET  /api/v1/clients/{client_id}/accounting-chart/export   (CSV, 86e3qhhrm)
    - POST  /api/v1/clients/{client_id}/accounting-chart/accounts               (86e3nb816)
    - PATCH /api/v1/clients/{client_id}/accounting-chart/accounts/{account_id}  (86e3nb816)
    - POST /api/v1/clients/{client_id}/accounting-chart/parties/import  (multipart, 86e3qhhrn)
    - GET  /api/v1/clients/{client_id}/accounting-chart/parties          (86e3qhhrn)
    - PUT    /api/v1/clients/{client_id}/accounting-chart/accounts/{account_id}/party  (86e3tttaf)
    - DELETE /api/v1/clients/{client_id}/accounting-chart/accounts/{account_id}/party  (86e3tttaf)
    - GET  /api/v1/clients/{client_id}/accounting-chart/omie-export          (XLSX, 86e3qhhrr)
    - GET  /api/v1/clients/{client_id}/accounting-chart/omie-export/preview  (86e3qhhrr)

⚠️ Não é o plano da ORIGEM da S10 (`/chart-of-accounts`, categorias do Omie): este é
o do sistema contábil de DESTINO, onde o escritório lança.

**Duas travas, ambas necessárias** (desenho da S12/S14):

1. A MATRIZ: `manage_client_accounting_chart` (staff: plataforma, admin, gerente da
   carteira) na importação, pelo guard AUDITADO — o `client_manager` alcança o
   cliente e LÊ o plano, mas importar é 403 com 1 linha `denied` em `access_audit`.
   A LEITURA não pede permissão (`AccessibleClientDep`, como o de-para).
2. O TENANT: `AccessibleClientDep` na leitura, `OpenClientDep` na importação — o
   `client_id` do path só passa por `resolve_client_access`, e cliente ENCERRADO
   recusa a importação com 409 enquanto a leitura segue 200 (§4.12).

⚠️ `client` vem ANTES do guard nas assinaturas: o alcance é decidido antes da célula.
O upload é lido em STREAMING com teto (`Settings.max_upload_bytes`) e fica só em
memória (§3.10). A rota literal `/import` não colide com nada: não há rota com path
param depois do prefixo.

A EXPORTAÇÃO é leitura (`AccessibleClientDep`, como a lista): quem lê o plano baixa o
plano. Ela grava 1 linha `export` em `access_audit` (o arquivo sai da plataforma com
nome de conta, dado do cliente final), como a exportação do de-para.

A PLANILHA DA OMIE (86e3qhhrr) é artefato de trabalho do ESCRITÓRIO, como o arquivo
contábil da S13: `generate_accounting_file` (staff), guard AUDITADO declarado antes do
cliente aberto (o `client_manager` e o `client_operator` recebem 403 com linha
`denied`), e cliente encerrado 409 (`OpenClientDep`: a DEK morreu). O download grava 1
linha `export` na trilha; o conteúdo não persiste.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from app.core.audit import AccessAction, record_access
from app.core.dependencies import (
    AccessibleClientDep,
    CurrentUserDep,
    DbSessionDep,
    GenerateAccountingFileDep,
    ManageClientAccountingChartDep,
    OpenClientDep,
    SettingsDep,
)
from app.db.models.client_accounting_account import (
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    AccountingAccountType,
    AccountingPartyType,
)
from app.integrations.omie.client import OmieClient
from app.integrations.omie.fornecedores_directory_cache import OmieFornecedoresDirectoryCache
from app.integrations.omie.schemas import ClienteOmie
from app.integrations.providers.base import Capability
from app.modules.client_accounting_chart.export import EXPORT_MEDIA_TYPE, export_filename
from app.modules.client_accounting_chart.omie_sheet import (
    OMIE_EXPORT_MEDIA_TYPE,
    omie_export_filename,
)
from app.modules.client_accounting_chart.repository import AccountingChartFilters
from app.modules.client_accounting_chart.schemas import (
    AccountingAccountCreateRequest,
    AccountingAccountEnvelope,
    AccountingAccountResponse,
    AccountingAccountStatusFilter,
    AccountingAccountUpdateRequest,
    AccountingChartListResponse,
    ChartImportEnvelope,
    ChartImportPayload,
    ChartImportPreviewEnvelope,
    ChartImportPreviewPayload,
    OmieExportPreviewEnvelope,
    OmieExportPreviewPayload,
    PartiesImportEnvelope,
    PartiesImportPayload,
    PartyLinkEnvelope,
    PartyLinkRequest,
    PartyLinkResponse,
    PartyLinksListResponse,
    PartyLinksSummaryPayload,
)
from app.modules.client_accounting_chart.service import AccountingChartService
from app.modules.client_connections.origin import build_capable_client
from app.modules.omie_data.payables_service import OmieFornecedorDirectoryService
from app.modules.users.schemas import PaginationMeta
from app.utils.upload import parse_content_length, read_upload_within_limit

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/accounting-chart",
    tags=["client-accounting-chart"],
)


def _get_service(db: DbSessionDep, settings: SettingsDep) -> AccountingChartService:
    return AccountingChartService(db, settings=settings)


ServiceDep = Annotated[AccountingChartService, Depends(_get_service)]


@router.get(
    "",
    summary=(
        "Lista o plano de contas CONTÁBIL do cliente (o do sistema contábil de destino, "
        "não o plano da origem): código reduzido, classificação, nome (decifrado na "
        "leitura), tipo (`analitica`/`sintetica`), situação e se a conta pode receber "
        "decisão nova (`postable` = analítica e ativa). Paginado (`page`/`pageSize`, "
        "máximo 100), na ordem da CLASSIFICAÇÃO como texto, igual ao sistema contábil (a "
        "sintética antes das analíticas dela; conta sem classificação ordena pelo código, "
        "numericamente), com busca por "
        "PREFIXO de código (`code`; o "
        "nome é cifrado e não é buscável) e filtros `type` e `status` (`ativa`/"
        "`inativa`) — `type=analitica&status=ativa` é o seletor de conta do de-para. "
        "Visível a todo papel com acesso ao cliente; cliente encerrado continua legível "
        "(nomes saem `[indecifrável]`). Plano de outro cliente nunca aparece."
    ),
)
async def list_accounting_chart(
    client: AccessibleClientDep,
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100)."),
    ] = 20,
    code: Annotated[
        str | None,
        Query(
            max_length=MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
            description="Busca por PREFIXO do código reduzido. Curingas de `LIKE` são literais.",
        ),
    ] = None,
    account_type: Annotated[
        AccountingAccountType | None,
        Query(alias="type", description="`analitica` ou `sintetica`. Ausente = os dois."),
    ] = None,
    status: Annotated[
        AccountingAccountStatusFilter | None,
        Query(description="`ativa` ou `inativa`. Ausente = as duas."),
    ] = None,
) -> AccountingChartListResponse:
    filters = AccountingChartFilters(
        code_prefix=code,
        account_type=account_type.value if account_type is not None else None,
        active=None if status is None else status == "ativa",
    )
    rows, total, names = await service.list_page(
        client, filters=filters, limit=page_size, offset=(page - 1) * page_size
    )
    return AccountingChartListResponse(
        data=[AccountingAccountResponse.from_row(row, names=names) for row in rows],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.post(
    "/import",
    summary=(
        "Importa (ou reimporta) o plano de contas contábil do cliente a partir de uma "
        "planilha no MODELO DA PLATAFORMA — tudo ou nada. Modelo: CSV UTF-8 separado "
        "por `;`, XLSX ou XLS (primeira aba), cabeçalho na linha 1 com as colunas "
        "`codigo_reduzido`, `nome` e `tipo` (obrigatórias) e `classificacao` "
        "(opcional), em qualquer ordem e sem outras colunas; `tipo` é `analitica` ou "
        "`sintetica`; código reduzido com letras, dígitos, `.` e `-` (até 20), nome até "
        "200 caracteres, classificação até 40. Exemplo: `codigo_reduzido;nome;tipo` / "
        "`649;Banco conta movimento;analitica`. Também aceita o plano de contas EXPORTADO "
        "do Domínio em XLSX ou no XLS que o sistema grava, reconhecido pelo cabeçalho `Código`/`T`/`Classificação`/"
        "`Nome`/`Grau` nas 10 primeiras linhas e convertido para o modelo (código, `S` = "
        "sintética e vazio = analítica, classificação, nome e grau, que tem de ser a "
        "profundidade da classificação); o CSV nativo do Domínio não é aceito. "
        "A reimportação casa por código e só adiciona e atualiza: conta "
        "nova entra ativa, conta existente atualiza nome, tipo e classificação, e a "
        "conta que não veio na planilha continua como estava (nunca é inativada nem "
        "apagada) e ganha o selo `absentSince`, que sai quando ela volta. A situação "
        "da conta não muda pela planilha. Antes de gravar, `POST …/import/preview` "
        "mostra o que muda. Responde as contagens `contas`, `contasNovas`, "
        "`contasAlteradas` e `contasAusentes`. O nome é cifrado com "
        "a chave do cliente. Requer `manage_client_accounting_chart` (plataforma, admin "
        "e gerente da carteira; usuários do cliente recebem 403 e a negação fica na "
        "trilha). Cliente encerrado: 409. Recusas, todas 422 e sem gravar nada: "
        "`FORMATO_NAO_SUPORTADO` (não é CSV nem XLSX pelo CONTEÚDO), `ARQUIVO_INVALIDO` "
        "(não abre, ou planilha sem nenhuma conta com `details.reason=sem_contas`), "
        "`CABECALHO_DIVERGENTE` (`details.missingColumns`/`repeatedColumns` — só colunas do "
        "MODELO — mais `details.unexpectedColumnCount`/`foundColumnCount`) e `LINHAS_INVALIDAS` (`details.lines=[{line, "
        "reason}]` com `reason` ∈ `codigo_vazio`, `codigo_longo`, `codigo_invalido`, "
        "`codigo_repetido`, `nome_vazio`, `nome_longo`, `tipo_invalido`, "
        "`classificacao_longa` e, no export do Domínio, também `codigo_ausente`, "
        "`classificacao_ausente`, `classificacao_repetida`, `grau_ausente`, "
        "`grau_divergente`, `linha_irreconhecivel` e `conta_fora_do_bloco` (linha com "
        "cara de conta depois do fim das contas: o arquivo inteiro é recusado), e "
        "`details.total`) — a resposta nunca traz o conteúdo de uma célula."
    ),
)
async def import_accounting_chart(
    request: Request,
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    settings: SettingsDep,
    service: ServiceDep,
    file: Annotated[
        UploadFile,
        File(
            description="CSV (`;`, UTF-8), XLSX ou XLS no modelo, ou o plano exportado do Domínio."
        ),
    ],
) -> ChartImportEnvelope:
    content = await read_upload_within_limit(
        file,
        declared_content_length=parse_content_length(request.headers.get("content-length")),
        max_bytes=settings.max_upload_bytes,
    )
    result = await service.import_sheet(client, actor=actor, content=content)
    return ChartImportEnvelope(data=ChartImportPayload.from_result(result))


@router.post(
    "/import/preview",
    summary=(
        "Mostra o que a importação desta planilha faria, SEM GRAVAR nada: o mesmo leitor "
        "e as mesmas recusas 422 de `POST …/import` (modelo da plataforma e plano "
        "exportado do Domínio). Devolve as contagens (`contasNovas`, `contasAlteradas`, "
        "`contasSemMudanca`, `contasAusentes`, `contasAusentesEmUso` — as ausentes que "
        "decisões do de-para, de qualquer vigência, ou a conta do banco usam —, "
        "`contasQueVoltaram` e `contasInativasNaPlanilha`), `temMudancas` e as listas "
        "`novas` (como na planilha), `alteradas` (o nome novo e os campos que mudam: "
        "`nome`, `tipo`, `classificacao`) e `ausentes` (o nome atual, a situação, `inUse` "
        "e `absentSince`), cada uma com até `listLimit` itens. Mesma permissão da "
        "importação (`manage_client_accounting_chart`; a negação fica na trilha); "
        "cliente encerrado 409."
    ),
)
async def preview_accounting_chart_import(
    request: Request,
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    settings: SettingsDep,
    service: ServiceDep,
    file: Annotated[
        UploadFile,
        File(
            description="CSV (`;`, UTF-8), XLSX ou XLS no modelo, ou o plano exportado do Domínio."
        ),
    ],
) -> ChartImportPreviewEnvelope:
    content = await read_upload_within_limit(
        file,
        declared_content_length=parse_content_length(request.headers.get("content-length")),
        max_bytes=settings.max_upload_bytes,
    )
    preview = await service.preview_sheet(client, content=content)
    return ChartImportPreviewEnvelope(data=ChartImportPreviewPayload.from_preview(preview))


@router.get(
    "/export",
    summary=(
        "Baixa o plano contábil do cliente no MODELO DA PLATAFORMA: CSV UTF-8 (com BOM, "
        "para o Excel), separado por `;`, cabeçalho `codigo_reduzido;nome;tipo;"
        "classificacao` e todas as contas, ativas e inativas, na ordem do plano. É o "
        "mesmo formato que `POST …/import` lê: exportar e reimportar o arquivo não muda "
        "nada. Nome que começaria fórmula no Excel (`= + - @`) sai com um apóstrofo na "
        "frente, que a importação retira. Plano vazio sai só com o cabeçalho. Visível a "
        "todo papel com acesso ao cliente; registra a exportação na trilha de acesso. "
        "Nome de conta que não decifra: 409 `PLANO_CONTABIL_INDECIFRAVEL`."
    ),
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_accounting_chart(
    client: AccessibleClientDep,
    user: CurrentUserDep,
    db: DbSessionDep,
    service: ServiceDep,
) -> Response:
    content, _ = await service.export_csv(client)
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
    return Response(
        content=content,
        media_type=EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="' + export_filename(client.id) + '"'
        },
    )


@router.post(
    "/accounts",
    status_code=201,
    summary=(
        "Inclui UMA conta no plano contábil do cliente, sem reimportar a planilha (a "
        "conta que o escritório acabou de criar no sistema contábil). As MESMAS regras "
        "da planilha: `code` com letras, dígitos, `.` e `-` (até 20), `name` até 200, "
        "`type` `analitica` ou `sintetica`, `classification` opcional até 40 (decide a "
        "posição na lista). A conta nasce ativa e o nome é cifrado com a chave do "
        "cliente. Requer `manage_client_accounting_chart` (plataforma, admin e gerente "
        "da carteira; usuários do cliente recebem 403 e a negação fica na trilha). "
        "Recusas: 409 `CONTA_CONTABIL_CODIGO_EXISTENTE` quando o cliente já tem o código, "
        "ativo ou inativo (`details.code` e `details.accountId` da existente); 422 "
        "`CONTA_CONTABIL_INVALIDA` com `details.field` (`code`, `name`, "
        "`classification`) e `details.reason` no vocabulário da planilha; cliente "
        "encerrado 409."
    ),
)
async def create_accounting_account(
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    service: ServiceDep,
    body: AccountingAccountCreateRequest,
) -> AccountingAccountEnvelope:
    account, names = await service.create_account(
        client,
        actor=actor,
        code=body.code,
        name=body.name,
        account_type=body.account_type,
        classification=body.classification,
        nature=body.nature,
    )
    return AccountingAccountEnvelope(data=AccountingAccountResponse.from_row(account, names=names))


@router.patch(
    "/accounts/{account_id}",
    summary=(
        "Edita nome, tipo, classificação e situação (`active`) de UMA conta do plano "
        "contábil do cliente; o código reduzido não se edita. Campo ausente fica como "
        "está; `classification: null` limpa. Conta de outro cliente ou inexistente: 404. "
        "A conta que decisões do de-para ou a conta do banco usam não passa a sintética "
        "nem a inativa: 422 `CONTA_CONTABIL_EM_USO` com `details.reason` (`sintetica`/"
        "`inativa`), `details.decisionCount` e `details.bindingCount` (só contagens). "
        "Campo inválido: 422 `CONTA_CONTABIL_INVALIDA`, como na inclusão. Mesma permissão "
        "da inclusão; cliente encerrado 409."
    ),
)
async def update_accounting_account(
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    service: ServiceDep,
    account_id: UUID,
    body: AccountingAccountUpdateRequest,
) -> AccountingAccountEnvelope:
    account, names = await service.update_account(
        client, account_id, actor=actor, patch=body.to_patch()
    )
    return AccountingAccountEnvelope(data=AccountingAccountResponse.from_row(account, names=names))


@router.post(
    "/parties/import",
    summary=(
        "Importa a RELAÇÃO DE CLIENTES ou a RELAÇÃO DE FORNECEDORES exportada do Domínio "
        "(XLS ou XLSX, reconhecida pelo título e pelo cabeçalho `Código`/`Conta`/`Nome`/"
        "`Razão Social`/`C.N.P.J./C.P.F.`, em qualquer ordem de colunas) e vincula cada "
        "conta do plano contábil do cliente (a coluna `Conta` é o código reduzido) ao "
        "CNPJ ou CPF da pessoa, cifrado com a chave do cliente. O tipo do vínculo "
        "(`cliente`/`fornecedor`) vem do título do relatório. Por arquivo, tudo ou nada: "
        "conta que não existe no plano ou é sintética recusa o arquivo inteiro com 422 "
        "`LINHAS_INVALIDAS` (`details.lines=[{line, reason, accountCode}]`, `reason` ∈ "
        "`conta_nao_encontrada`, `conta_sintetica`, `conta_ausente`, `conta_invalida`, "
        "`linha_fora_do_bloco`). "
        "Não recusam, mas ficam SEM vínculo e voltam em `naoVinculadas`: conta `0` "
        "(`sem_conta`), `documento_ausente`, `documento_invalido` (dígito verificador "
        "errado no Domínio) e `conta_compartilhada` (a mesma conta em mais de uma pessoa). "
        "Reimportar é idempotente; a conta vinculada que não vem no relatório fica como "
        "está. O CNPJ da empresa no cabeçalho do relatório é comparado (por impressão "
        "digital, nunca em claro) com o dos relatórios já importados: outra empresa é 422 "
        "`RELATORIO_DE_OUTRA_EMPRESA`. Arquivo que não é a relação: 422 "
        "`CABECALHO_DIVERGENTE`; sem título ou sem o CNPJ da empresa: 422 `ARQUIVO_INVALIDO` "
        "com `details.reason` (`relatorio_nao_reconhecido`, `empresa_nao_identificada`, "
        "`sem_pessoas`); CSV ou PDF: 422 `FORMATO_NAO_SUPORTADO`. Requer "
        "`manage_client_accounting_chart`; cliente encerrado 409. Nenhum documento, nome "
        "ou razão social sai na resposta nem vai para o log."
    ),
)
async def import_accounting_parties(
    request: Request,
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    settings: SettingsDep,
    service: ServiceDep,
    file: Annotated[
        UploadFile,
        File(
            description="A RELAÇÃO DE CLIENTES ou a RELAÇÃO DE FORNECEDORES do Domínio (XLS/XLSX)."
        ),
    ],
) -> PartiesImportEnvelope:
    content = await read_upload_within_limit(
        file,
        declared_content_length=parse_content_length(request.headers.get("content-length")),
        max_bytes=settings.max_upload_bytes,
    )
    result = await service.import_parties(client, actor=actor, content=content)
    return PartiesImportEnvelope(data=PartiesImportPayload.from_result(result))


@router.get(
    "/parties",
    summary=(
        "Lista as contas do plano contábil do cliente COM vínculo a um cliente ou "
        "fornecedor (importado das relações do Domínio): conta, nome (decifrado na "
        "leitura), tipo do vínculo e o documento MASCARADO (`**.***.***/0001-91` para "
        "CNPJ, `***.***.*89-00` para CPF; o inteiro nunca sai da API), paginado "
        "(`page`/`pageSize`, máximo 100) na ordem do plano, com o filtro `type` "
        "(`cliente`/`fornecedor`) e o resumo `summary` (`contasVinculadas`, `clientes`, "
        "`fornecedores`). Requer `manage_client_accounting_chart` (documento de terceiro é "
        "configuração do escritório); cliente encerrado continua legível, com o documento "
        "sem máscara nenhuma (`documentResolved=false`)."
    ),
)
async def list_accounting_parties(
    client: AccessibleClientDep,
    actor: ManageClientAccountingChartDep,
    service: ServiceDep,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int,
        Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100)."),
    ] = 20,
    party_type: Annotated[
        AccountingPartyType | None,
        Query(alias="type", description="`cliente` ou `fornecedor`. Ausente = os dois."),
    ] = None,
) -> PartyLinksListResponse:
    rows, total, names, documents, summary = await service.list_parties(
        client, party_type=party_type, limit=page_size, offset=(page - 1) * page_size
    )
    return PartyLinksListResponse(
        data=[PartyLinkResponse.from_row(row, names=names, documents=documents) for row in rows],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
        summary=PartyLinksSummaryPayload.from_summary(summary),
    )


@router.put(
    "/accounts/{account_id}/party",
    summary=(
        "Vincula UMA conta do plano contábil do cliente a um cliente ou fornecedor pelo "
        "CNPJ ou CPF, à mão (sem a relação do Domínio), ou troca o vínculo que ela já "
        "tem. Body `{partyType, document}`: `partyType` `cliente`/`fornecedor`, "
        "`document` com 14 (CNPJ) ou 11 (CPF) dígitos, com ou sem máscara, conferido pelo "
        "dígito verificador e cifrado com a chave do cliente. A resposta é a linha como a "
        "lista a mostra, com o documento MASCARADO. Conta de outro cliente ou inexistente: "
        "404. Recusas: 422 `CONTA_CONTABIL_INVALIDA` com `details.field` e "
        "`details.reason` (`conta_sintetica`, `conta_inativa`, `documento_invalido`); 409 "
        "`DOCUMENTO_JA_VINCULADO` quando OUTRA conta do cliente já tem o documento com o "
        "MESMO tipo (`details.accountCode` e `details.accountId` dela, nunca o documento). Não altera "
        "a empresa conferida nas importações da relação. Requer "
        "`manage_client_accounting_chart`; cliente encerrado 409."
    ),
)
async def set_accounting_party_link(
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    service: ServiceDep,
    account_id: UUID,
    body: PartyLinkRequest,
) -> PartyLinkEnvelope:
    account, names, documents = await service.set_party_link(
        client, account_id, actor=actor, party_type=body.party_type, document=body.document
    )
    return PartyLinkEnvelope(
        data=PartyLinkResponse.from_row(account, names=names, documents=documents)
    )


@router.delete(
    "/accounts/{account_id}/party",
    status_code=204,
    summary=(
        "Desfaz o vínculo de UMA conta do plano contábil do cliente com um cliente ou "
        "fornecedor, importado ou manual: documento e tipo saem juntos, e a planilha da "
        "Omie deixa de levar o documento dessa conta. Idempotente: conta sem vínculo "
        "responde 204. Conta de outro cliente ou inexistente: 404. Requer "
        "`manage_client_accounting_chart`; cliente encerrado 409."
    ),
)
async def remove_accounting_party_link(
    client: OpenClientDep,
    actor: ManageClientAccountingChartDep,
    service: ServiceDep,
    account_id: UUID,
) -> Response:
    await service.remove_party_link(client, account_id, actor=actor)
    return Response(status_code=204)


@router.get(
    "/omie-export/preview",
    summary=(
        "O que a planilha de importação do plano de contas da Omie (modelo "
        "`Omie_Plano_Contas` v1.1.3 do Painel do Contador) levaria, SEM gerar o arquivo: "
        "`contas` (todas as contas do plano), `vinculadas` (as que saem com o CNPJ/CPF "
        "do cliente ou fornecedor), `semVinculo`, `inativas`, `ausentes`, "
        "`naturezaManual`, os `problems` que impedem a geração (cada um com `reason` e "
        "os CÓDIGOS das contas; vazio = a planilha sai) e `omieRegistry`: quantos "
        "documentos vinculados NÃO existem no cadastro do Omie do cliente (na "
        "importação a Omie não cria o cadastro e a conta cai como diversos), lido do "
        "diretório em memória da busca de fornecedor, sem gravar. Cliente sem Omie ou "
        "com a Omie fora do ar: `checked=false` com o `reason`, nunca erro. "
        "`refresh=true` relê o diretório da Omie (depois de cadastrar o que faltava). Requer "
        "`generate_accounting_file` (plataforma, admin e gerente da carteira; usuários "
        "do cliente recebem 403 e a negação fica na trilha); cliente encerrado 409."
    ),
)
async def preview_omie_export(
    request: Request,
    actor: GenerateAccountingFileDep,
    client: OpenClientDep,
    db: DbSessionDep,
    settings: SettingsDep,
    service: ServiceDep,
    refresh: Annotated[
        bool, Query(description="Relê o diretório de cadastros da Omie antes de conferir.")
    ] = False,
) -> OmieExportPreviewEnvelope:
    cache: OmieFornecedoresDirectoryCache = request.app.state.omie_fornecedores_directory_cache

    async def build_client() -> OmieClient:
        # Só no MISS do diretório: o hit não paga o unwrap da DEK nem o 409 de origem.
        return await build_capable_client(
            db, client, Capability.LISTAR_LANCAMENTOS, settings=settings
        )

    async def registry() -> list[ClienteOmie]:
        return await OmieFornecedorDirectoryService(cache).directory(
            client_id=client.id, omie_client_factory=build_client, refresh=refresh
        )

    preview = await service.omie_export_preview(client, registry=registry)
    return OmieExportPreviewEnvelope(data=OmieExportPreviewPayload.from_preview(preview))


@router.get(
    "/omie-export",
    summary=(
        "Baixa a planilha de importação do plano de contas da Omie (modelo "
        "`Omie_Plano_Contas` v1.1.3 do Painel do Contador, XLSX) preenchida com o plano "
        "contábil do cliente: uma linha por conta, na ordem do plano, com classificação, "
        "código reduzido, nome, natureza (`Devedora`/`Credora`), tipo (`Analítica`/"
        "`Totalizadora (Sintética)`) e, na conta vinculada a um cliente ou fornecedor, o "
        "CNPJ/CPF com máscara na coluna `CNPJ do Estabelecimento`, que é por onde a Omie "
        "liga a conta ao cadastro. O arquivo é o modelo da Omie com as células "
        "preenchidas: listas de opções, proteção e aba `Config` intactas. Mesma entrada, "
        "mesmos bytes. Recusa com 409 `PLANILHA_OMIE_RECUSADA` e nada gerado quando o "
        "plano está vazio, passa de 10.000 contas ou alguma conta não tem classificação, "
        "natureza, nome ou documento legível, ou tem texto que começa com `= + - @`, "
        "passa do teto da coluna ou tem quebra de linha (`details.problems=[{reason, "
        "accountCodes, count}]`, só códigos). Requer `generate_accounting_file` (a "
        "negação fica na trilha); cliente encerrado 409. Registra a exportação na trilha "
        "de acesso; o conteúdo não persiste."
    ),
    response_class=Response,
    responses={200: {"content": {OMIE_EXPORT_MEDIA_TYPE: {}}}},
)
async def export_omie_chart(
    actor: GenerateAccountingFileDep,
    client: OpenClientDep,
    db: DbSessionDep,
    service: ServiceDep,
) -> Response:
    export = await service.omie_export(client)
    # A trilha (§4.7): 1 linha `export`, só IDs. Sem `commit` próprio: o fim do request
    # (`get_db_session`) persiste junto com a resposta.
    await record_access(
        db,
        user_id=UUID(actor.id),
        client_id=client.id,
        action=AccessAction.EXPORT,
        user_scope=actor.scope,
        actor_client_id=actor.client_id,
        actor_organization_id=actor.organization_id,
    )
    return Response(
        content=export.content,
        media_type=OMIE_EXPORT_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                'attachment; filename="' + omie_export_filename(client.id) + '"'
            )
        },
    )
