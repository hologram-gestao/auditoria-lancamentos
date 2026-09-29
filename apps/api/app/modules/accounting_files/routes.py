"""Rotas do arquivo contábil (Sprint 13, BACK 13.4 — R3).

    - POST /api/v1/clients/{client_id}/accounting-files                          gerar
    - GET  /api/v1/clients/{client_id}/accounting-files                          histórico
    - GET  /api/v1/clients/{client_id}/accounting-files/{generation_id}/download baixar

**Autorização:** as três exigem `generate_accounting_file` (staff; o `manager` só na
carteira, por `resolve_client_access`) — guard AUDITADO: o papel que alcança o cliente mas
não pode a ação (`client_manager`, `client_operator`) recebe 403 com 1 linha `denied`. A
permissão é declarada ANTES do cliente aberto: o operador de um cliente encerrado recebe o
mesmo 403, não o estado do cliente. Listar também pede a permissão (decisão do
planejador, pendente de validação humana, ADR-093-BE).

**Cliente encerrado:** gerar e baixar = 409 (`OpenClientDep` — a DEK morreu e o histórico
deixou de ser legível); o histórico de gerações (só metadados) segue legível
(`AccessibleClientDep`). As escritas commitam no serviço ANTES da resposta.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    GenerateAccountingFileDep,
    OpenClientDep,
    SettingsDep,
)
from app.modules.accounting_files.repository import AccountingFileRepository
from app.modules.accounting_files.schemas import (
    AccountingFileGenerationEnvelope,
    AccountingFileGenerationListResponse,
    GenerateAccountingFileRequest,
)
from app.modules.accounting_files.service import AccountingFileService
from app.modules.client_mapping.accounting import AccountingDecisionSupport
from app.modules.client_mapping.materialization import ClientMappingApplyService
from app.modules.client_mapping.repository import ClientMappingRepository
from app.modules.client_mapping.service import ClientMappingDecisionService
from app.modules.client_movements.competence import COMPETENCE_PATTERN, parse_competence
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.export_layouts.repository import ExportLayoutRepository
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.service import MappingCatalogService

router = APIRouter(prefix="/api/v1/clients/{client_id}/accounting-files", tags=["accounting-files"])


def _get_service(db: DbSessionDep, settings: SettingsDep) -> AccountingFileService:
    catalog = MappingCatalogRepository(db)
    decisions = ClientMappingDecisionService(
        ClientMappingRepository(db),
        catalog=catalog,
        catalog_service=MappingCatalogService(catalog),
        accounting=AccountingDecisionSupport(db, settings=settings),
    )
    return AccountingFileService(
        db,
        repository=AccountingFileRepository(db),
        layouts=ExportLayoutRepository(db),
        # A MESMA leitura de linhas da materialização (S16): snapshot + histórico da vigência.
        apply=ClientMappingApplyService(
            db,
            repository=ClientMappingRepository(db),
            movements=ClientMovementsRepository(db),
            decisions=decisions,
        ),
        settings=settings,
    )


ServiceDep = Annotated[AccountingFileService, Depends(_get_service)]


@router.post(
    "",
    status_code=201,
    summary=(
        "Gera o arquivo contábil da competência no layout escolhido (da organização do "
        "cliente; outra org = 404). Padrão: a ÚLTIMA materialização do destino Conta "
        "contábil; `materializationId` gera uma versão anterior. Registra a geração (só "
        "metadados + SHA-256, nunca o conteúdo) e a trilha `export`. Recusas 409: sem "
        "materialização (`ARQUIVO_SEM_MATERIALIZACAO`, nunca materializa sozinho) e as do "
        "gerador (`ARQUIVO_DESTINO_INVALIDO`, `ARQUIVO_COBERTURA_PARCIAL`, "
        "`ARQUIVO_PARTIDA_INCOMPLETA`, `ARQUIVO_PARTICAO_NAO_FECHA`, "
        "`ARQUIVO_TEXTO_NAO_CABE`, com os códigos das categorias em `details`). Cliente "
        "encerrado: 409. Requer `generate_accounting_file`."
    ),
)
async def generate_accounting_file(
    payload: GenerateAccountingFileRequest,
    actor: GenerateAccountingFileDep,
    client: OpenClientDep,
    service: ServiceDep,
) -> AccountingFileGenerationEnvelope:
    return AccountingFileGenerationEnvelope(
        data=await service.generate(
            client,
            actor=actor,
            layout_id=payload.layout_id,
            competence=payload.competence_date,
            materialization_id=payload.materialization_id,
        )
    )


@router.get(
    "",
    summary=(
        "Histórico de gerações do arquivo contábil do cliente (só metadados, mais recentes "
        "primeiro; `competence` filtra; paginado com `page`/`pageSize`, máximo 100). "
        "Legível também com o cliente encerrado. Requer `generate_accounting_file`."
    ),
)
async def list_accounting_files(
    actor: GenerateAccountingFileDep,
    client: AccessibleClientDep,
    service: ServiceDep,
    competence: Annotated[
        str | None, Query(pattern=COMPETENCE_PATTERN, description="`YYYY-MM`.")
    ] = None,
    page: Annotated[int, Query(ge=1, description="Página, a partir de 1.")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, alias="pageSize", description="Itens por página (máx. 100).")
    ] = 20,
) -> AccountingFileGenerationListResponse:
    data, pagination = await service.list_generations(
        client,
        viewer=actor,
        competence=parse_competence(competence) if competence is not None else None,
        page=page,
        page_size=page_size,
    )
    return AccountingFileGenerationListResponse(data=data, pagination=pagination)


@router.get(
    "/{generation_id}/download",
    summary=(
        "Baixa o arquivo de uma geração: REGENERA a partir da materialização e da versão "
        "de layout registradas e confere o SHA-256. Bate → o arquivo "
        "(`lancamentos_<AAAA-MM>_v<versão da materialização>.csv`, `Content-Type` com o "
        "charset do layout) e a trilha `export`. Não bate → 409 `ARQUIVO_DIVERGENTE` e "
        "alerta, nunca um arquivo diferente. Cliente encerrado: 409. Requer "
        "`generate_accounting_file`."
    ),
    response_class=StreamingResponse,
)
async def download_accounting_file(
    generation_id: UUID,
    actor: GenerateAccountingFileDep,
    client: OpenClientDep,
    service: ServiceDep,
) -> StreamingResponse:
    downloaded = await service.download(client, actor=actor, generation_id=generation_id)
    disposition = (
        f'attachment; filename="{downloaded.file_name}"; '
        f"filename*=UTF-8''{quote(downloaded.file_name)}"
    )
    return StreamingResponse(
        iter([downloaded.content]),
        media_type=downloaded.media_type,
        headers={"Content-Disposition": disposition},
    )
