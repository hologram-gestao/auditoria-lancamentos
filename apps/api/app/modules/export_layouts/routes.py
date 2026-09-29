"""Rotas dos layouts de exportação do arquivo contábil (Sprint 13, BACK 13.2 — R1).

    - GET  /api/v1/export-layout-templates                 ler layouts (modelos do CÓDIGO)
    - GET  /api/v1/export-layouts                          ler layouts (da organização)
    - POST /api/v1/export-layouts                          manage_export_layouts
    - POST /api/v1/export-layouts/from-template            manage_export_layouts
    - GET  /api/v1/export-layouts/{layout_id}              ler layouts (com as versões)
    - POST /api/v1/export-layouts/{layout_id}/versions     manage_export_layouts

**Layout POR ORGANIZAÇÃO.** ESCREVER é `manage_export_layouts` (plataforma e admin).
LER é de quem tem `manage_export_layouts` OU `generate_accounting_file` — o gerente
escolhe o layout ao gerar o arquivo (decisão do planejador, pendente de validação
humana, ADR-091-BE); usuário de cliente não lê (403 com linha `denied` na trilha).
Layout de outra organização é **404**, nunca o dado. Organização suspensa: escrita 409.
A rota literal `from-template` vem ANTES de `/{layout_id}`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import DbSessionDep, ManageExportLayoutsDep, ReadExportLayoutsDep
from app.modules.export_layouts.repository import ExportLayoutRepository
from app.modules.export_layouts.schemas import (
    ExportLayoutCreate,
    ExportLayoutEnvelope,
    ExportLayoutFromTemplate,
    ExportLayoutListResponse,
    ExportLayoutTemplateListResponse,
    ExportLayoutVersionCreate,
)
from app.modules.export_layouts.service import ExportLayoutService

router = APIRouter(prefix="/api/v1", tags=["export-layouts"])


def _get_service(db: DbSessionDep) -> ExportLayoutService:
    return ExportLayoutService(ExportLayoutRepository(db))


ServiceDep = Annotated[ExportLayoutService, Depends(_get_service)]


@router.get(
    "/export-layout-templates",
    summary=(
        "Modelos de layout declarados no código (hoje: Domínio, lançamentos contábeis em "
        "CSV), com a definição completa. Sem dado de organização nem de cliente. Requer "
        "`manage_export_layouts` ou `generate_accounting_file`."
    ),
)
async def list_export_layout_templates(
    _user: ReadExportLayoutsDep,
) -> ExportLayoutTemplateListResponse:
    return ExportLayoutTemplateListResponse(data=ExportLayoutService.list_templates())


@router.get(
    "/export-layouts",
    summary=(
        "Lista os layouts de exportação da organização de quem pede (a plataforma vê "
        "todas; `organizationId` restringe), com a última versão. Requer "
        "`manage_export_layouts` ou `generate_accounting_file`."
    ),
)
async def list_export_layouts(
    user: ReadExportLayoutsDep,
    service: ServiceDep,
    organization_id: Annotated[
        UUID | None,
        Query(
            alias="organizationId",
            description="Plataforma: restringe a uma organização. Staff: só a própria.",
        ),
    ] = None,
) -> ExportLayoutListResponse:
    return ExportLayoutListResponse(
        data=await service.list_layouts(viewer=user, requested_organization_id=organization_id)
    )


@router.post(
    "/export-layouts",
    status_code=201,
    summary=(
        "Cria um layout (versão 1) na organização do ator (a plataforma escolhe, "
        "obrigatório). Campo fora do vocabulário, parâmetro inválido ou codificação "
        "desconhecida: 422 `LAYOUT_INVALIDO` com `details.field`, nada gravado. Nome "
        "repetido na organização: 409. Requer `manage_export_layouts`."
    ),
)
async def create_export_layout(
    payload: ExportLayoutCreate,
    actor: ManageExportLayoutsDep,
    service: ServiceDep,
) -> ExportLayoutEnvelope:
    return ExportLayoutEnvelope(
        data=await service.create_layout(
            actor=actor,
            name=payload.name,
            target_system=payload.target_system,
            definition_raw=payload.definition.to_raw(),
            requested_organization_id=payload.organization_id,
        )
    )


@router.post(
    "/export-layouts/from-template",
    status_code=201,
    summary=(
        "Cria o layout da organização a partir de um modelo do código numa ação só "
        "(`templateKey`; nome opcional, padrão o do modelo). Modelo inexistente: 404; "
        "nome repetido: 409. Requer `manage_export_layouts`."
    ),
)
async def create_export_layout_from_template(
    payload: ExportLayoutFromTemplate,
    actor: ManageExportLayoutsDep,
    service: ServiceDep,
) -> ExportLayoutEnvelope:
    return ExportLayoutEnvelope(
        data=await service.create_from_template(
            actor=actor,
            template_key=payload.template_key,
            name=payload.name,
            requested_organization_id=payload.organization_id,
        )
    )


@router.get(
    "/export-layouts/{layout_id}",
    summary=(
        "Um layout com TODAS as versões (da mais nova para a mais antiga), só leitura. "
        "404 fora da própria organização. Requer `manage_export_layouts` ou "
        "`generate_accounting_file`."
    ),
)
async def get_export_layout(
    layout_id: UUID,
    user: ReadExportLayoutsDep,
    service: ServiceDep,
) -> ExportLayoutEnvelope:
    return ExportLayoutEnvelope(data=await service.get_layout(layout_id, viewer=user))


@router.post(
    "/export-layouts/{layout_id}/versions",
    status_code=201,
    summary=(
        "Grava a versão N+1 do layout — as anteriores ficam intactas e consultáveis (um "
        "arquivo já gerado continua explicável pela versão que o gerou). 422 como na "
        "criação; 404 fora da própria organização; 409 se a organização estiver "
        "suspensa. Requer `manage_export_layouts`."
    ),
)
async def create_export_layout_version(
    layout_id: UUID,
    payload: ExportLayoutVersionCreate,
    actor: ManageExportLayoutsDep,
    service: ServiceDep,
) -> ExportLayoutEnvelope:
    return ExportLayoutEnvelope(
        data=await service.create_version(
            layout_id, actor=actor, definition_raw=payload.definition.to_raw()
        )
    )
