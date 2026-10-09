"""Endpoints do plano de contas CONTÁBIL do cliente (Sprint 16, BACK 16.1 — R1/R5).

    - GET  /api/v1/clients/{client_id}/accounting-chart
    - POST /api/v1/clients/{client_id}/accounting-chart/import   (multipart)

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
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    ManageClientAccountingChartDep,
    OpenClientDep,
    SettingsDep,
)
from app.db.models.client_accounting_account import (
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    AccountingAccountType,
)
from app.modules.client_accounting_chart.repository import AccountingChartFilters
from app.modules.client_accounting_chart.schemas import (
    AccountingAccountResponse,
    AccountingAccountStatusFilter,
    AccountingChartListResponse,
    ChartImportEnvelope,
    ChartImportPayload,
)
from app.modules.client_accounting_chart.service import AccountingChartService
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
        "máximo 100), ordenado por código, com busca por PREFIXO de código (`code`; o "
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
        "A reimportação casa por código: conta "
        "nova entra, conta existente atualiza nome, tipo e classificação (e volta a "
        "ativa), conta que sumiu da planilha vira INATIVA — nunca é apagada. Responde "
        "as contagens `contas`, `contasNovas`, `contasInativadas`. O nome é cifrado com "
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
