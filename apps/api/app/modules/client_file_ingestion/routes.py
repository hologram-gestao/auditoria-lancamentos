"""Endpoints da origem por ARQUIVO (Sprint 14, BACK 14.3 — R1/R2/R3/R5).

    - POST /api/v1/clients/{client_id}/file-origin/inspect   (multipart)
    - POST /api/v1/clients/{client_id}/file-origin/process   (multipart)
    - GET  /api/v1/clients/{client_id}/file-origin/imports?competence=

**Duas travas, ambas necessárias** (mesmo desenho das rotas da S12):

1. A MATRIZ: `upload_client_file` (os 5 papéis — enviar a planilha do mês é o dia a
   dia de quem opera o cliente, inclusive o operador) nas duas escritas, pelo guard
   AUDITADO. A LISTA dos arquivos processados não pede permissão: quem alcança o
   cliente lê.
2. O TENANT: `AccessibleClientDep` na inspeção e na lista, `OpenClientDep` no
   processamento — cliente ENCERRADO recusa o processamento com 409 (e a inspeção
   também, no serviço). O `client_id` do path só passa por `resolve_client_access`.

⚠️ `client` vem ANTES do guard nas assinaturas: o alcance é decidido antes da
célula da matriz. O upload é lido em STREAMING com teto (`Settings.max_upload_bytes`)
e fica só em memória (§3.10).

⚠️ Competência malformada e total fora do formato são 400 `VALIDATION_ERROR` genérico
(validação de FORMA, §4.8); toda recusa do ARQUIVO é tipada, com motivo acionável.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from app.core.dependencies import (
    AccessibleClientDep,
    CurrentUserDep,
    DbSessionDep,
    OpenClientDep,
    SettingsDep,
    UploadClientFileDep,
)
from app.db.models.client_input_mapping import CsvDelimiter, InputEncoding
from app.modules.client_file_categories.registry import FileCategoryRegistry
from app.modules.client_file_ingestion.schemas import (
    COMPETENCE_FORM_PATTERN,
    DECLARED_TOTAL_PATTERN,
    FileImportItem,
    FileImportListResponse,
    InspectEnvelope,
    InspectPayload,
    ProcessedEnvelope,
    ProcessedPayload,
)
from app.modules.client_file_ingestion.service import FileIngestionService
from app.modules.client_input_mappings.repository import ClientInputMappingRepository
from app.modules.client_input_mappings.service import ClientInputMappingService
from app.modules.client_movements.competence import COMPETENCE_PATTERN, parse_competence
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.client_movements.service import ClientMovementsSyncService
from app.modules.clients.repository import ClientRepository
from app.modules.reconciliations.service import author_for_viewer
from app.utils.upload import parse_content_length, read_upload_within_limit

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/file-origin",
    tags=["client-file-origin"],
)


def _get_service(db: DbSessionDep, settings: SettingsDep) -> FileIngestionService:
    return FileIngestionService(
        db,
        settings=settings,
        mappings=ClientInputMappingService(ClientInputMappingRepository(db)),
        registry=FileCategoryRegistry(db, settings=settings),
        movements=ClientMovementsSyncService(
            db,
            repository=ClientMovementsRepository(db),
            clients=ClientRepository(db),
            settings=settings,
        ),
    )


ServiceDep = Annotated[FileIngestionService, Depends(_get_service)]


async def _read_file(request: Request, file: UploadFile, settings: SettingsDep) -> bytes:
    return await read_upload_within_limit(
        file,
        declared_content_length=parse_content_length(request.headers.get("content-length")),
        max_bytes=settings.max_upload_bytes,
    )


@router.post(
    "/inspect",
    summary=(
        "Inspeciona o arquivo (CSV ou XLSX) de um cliente com origem por arquivo: devolve "
        "o formato detectado, as colunas do cabeçalho e uma amostra das primeiras linhas, "
        "para a pessoa confirmar o mapeamento salvo ou criar um. NADA é persistido nem "
        "logado — a amostra só existe nesta resposta. Requer a permissão "
        "`upload_client_file` (os 5 papéis) e conexão `arquivo` ativa (409 `SEM_CONEXAO`, "
        "`ORIGEM_COM_ERRO` ou `CAPACIDADE_AUSENTE`). Sem mapeamento salvo, o CSV é lido "
        "com `csvDelimiter`/`encoding` do pedido (padrão `;` e `utf-8-sig`) — declarados, "
        "nunca farejados. PDF e XLS: 422 `FORMATO_NAO_SUPORTADO`; arquivo que não abre: "
        "422 `ARQUIVO_INVALIDO`."
    ),
)
async def inspect_file(
    request: Request,
    client: AccessibleClientDep,
    _actor: UploadClientFileDep,
    settings: SettingsDep,
    service: ServiceDep,
    file: Annotated[UploadFile, File(description="CSV ou XLSX.")],
    csv_delimiter: Annotated[CsvDelimiter | None, Form(alias="csvDelimiter")] = None,
    encoding: Annotated[InputEncoding | None, Form()] = None,
) -> InspectEnvelope:
    content = await _read_file(request, file, settings)
    result = await service.inspect(
        client,
        content,
        csv_delimiter=csv_delimiter.value if csv_delimiter else None,
        encoding=encoding.value if encoding else None,
    )
    return InspectEnvelope(
        data=InspectPayload(
            format=result.file_format,
            columns=result.columns,
            sample=result.sample,
            has_mapping=result.has_mapping,
        )
    )


@router.post(
    "/process",
    status_code=201,
    summary=(
        "Processa o arquivo do mês de um cliente com origem por arquivo, aplicando o "
        "mapeamento salvo SEM interação: cada linha vira um movimento da base "
        "(`source_type=arquivo`, competência informada), com a descrição cifrada pela "
        "chave do cliente e a categoria de origem registrada (grafia preservada). Ou o "
        "arquivo entra INTEIRO, ou nada entra. Requer `upload_client_file` (os 5 papéis); "
        "cliente encerrado: 409. Recusas, na ordem: conexão arquivo (409 da taxonomia), "
        "409 `SEM_MAPEAMENTO` (com `details.foundColumns`), 409 `SINAL_NAO_DECLARADO`, "
        "422 `FORMATO_NAO_SUPORTADO`, 409 `ARQUIVO_JA_PROCESSADO` (mesmo arquivo na mesma "
        "competência — UNIQUE no banco), 422 `CABECALHO_DIVERGENTE` (antes de ler a 1ª "
        "linha; `details.missingColumns`/`foundColumns`), 422 `LINHAS_INVALIDAS` "
        "(`details.lines=[{line, reason}]`, motivo de vocabulário fechado, nunca a célula), "
        "422 `TOTAL_DIVERGENTE` (quando `declaredTotal` vier e diferir da soma com sinal), "
        "422 `ARQUIVO_INVALIDO` (não abre/não itera, mensagem fixa). Arquivo corrigido "
        "(conteúdo diferente) na mesma competência é aceito: as linhas do anterior viram "
        "`ausente_na_origem`. Competência (`YYYY-MM`) e total malformados: 400."
    ),
)
async def process_file(
    request: Request,
    client: OpenClientDep,
    actor: UploadClientFileDep,
    settings: SettingsDep,
    service: ServiceDep,
    file: Annotated[UploadFile, File(description="CSV ou XLSX no formato do mapeamento.")],
    competence: Annotated[
        str,
        Form(pattern=COMPETENCE_FORM_PATTERN, description="Competência do arquivo, `YYYY-MM`."),
    ],
    declared_total: Annotated[
        str | None,
        Form(
            alias="declaredTotal",
            pattern=DECLARED_TOTAL_PATTERN,
            description=(
                "Total informado pela pessoa (soma algébrica dos valores com sinal, até 2 "
                "casas, vírgula ou ponto). Se vier e diferir da soma do arquivo, o envio é "
                "recusado — mesma disciplina de fechamento da conciliação."
            ),
        ),
    ] = None,
) -> ProcessedEnvelope:
    content = await _read_file(request, file, settings)
    total = Decimal(declared_total.replace(",", ".")) if declared_total is not None else None
    result = await service.process(
        client,
        actor=actor,
        content=content,
        competence=parse_competence(competence),
        declared_total=total,
    )
    return ProcessedEnvelope(
        data=ProcessedPayload(
            import_id=result.import_id,
            competence=competence,
            rows=result.rows,
            columns_recognized=result.columns_recognized,
            categories_created=result.categories_created,
            absent=result.absent,
            mapping_id=result.mapping_id,
            processed_at=result.processed_at,
        )
    )


@router.get(
    "/imports",
    summary=(
        "Lista os arquivos processados do cliente (todas as competências ou uma, com "
        "`?competence=YYYY-MM`): competência, linhas que viraram movimento, hash do "
        "conteúdo, data e autor (mascarado por escopo — usuário de cliente vê 'Equipe' "
        "para autor de staff). Não pede permissão além de alcançar o cliente; cliente "
        "encerrado continua legível. Competência malformada: 400."
    ),
)
async def list_file_imports(
    client: AccessibleClientDep,
    viewer: CurrentUserDep,
    service: ServiceDep,
    competence: Annotated[
        str | None,
        Query(pattern=COMPETENCE_PATTERN, description="Filtro opcional, `YYYY-MM`."),
    ] = None,
) -> FileImportListResponse:
    month = parse_competence(competence) if competence else None
    rows = await service.list_imports(client, competence=month)
    return FileImportListResponse(
        data=[
            FileImportItem.from_row(record, author_for_viewer(author, viewer))
            for record, author in rows
        ]
    )
