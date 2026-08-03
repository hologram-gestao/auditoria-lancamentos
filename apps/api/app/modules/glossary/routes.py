"""Endpoints do glossário do tenant (Sprint 6, BACK 06.3).

    - GET    /api/v1/clients/{client_id}/glossary            (paginado)
    - POST   /api/v1/clients/{client_id}/glossary
    - PATCH  /api/v1/clients/{client_id}/glossary/{entry_id}
    - DELETE /api/v1/clients/{client_id}/glossary/{entry_id}

**Duas travas, ambas necessárias** (mesmo desenho de `users/client_routes.py`):

1. `ManageGlossaryDep` — a MATRIZ (`PERMISSION_MATRIX`, permissão
   `manage_glossary`): gerente do cliente e equipe do sistema ✅; **operador do
   cliente ❌ (403)**. Só nas ESCRITAS: a leitura é liberada a todo papel com
   acesso ao tenant, porque o operador precisa do glossário como referência.
2. `AccessibleClientDep` — o TENANT: o `client_id` do path só passa por
   `resolve_client_access` (a função ÚNICA). Nenhuma rota aqui compara `role` ou
   `client_id` na mão.

E a terceira, dentro da camada de dados: todo `SELECT` leva
`AND client_id = <tenant>` + `scoped_by_tenant` — entrada de outro tenant vira
**404**, nunca o dado (anti-IDOR).

Envelope da §7: `{data: ...}` / `{data, pagination}` no sucesso; o handler global
converte `AppError` em `{error: {code, message, userMessage}}`.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    AccessibleClientDep,
    CurrentUserDep,
    DbSessionDep,
    ManageGlossaryDep,
    SettingsDep,
)
from app.modules.glossary.schemas import (
    CreateGlossaryEntryRequest,
    GlossaryDeletedPayload,
    GlossaryDeletedResponse,
    GlossaryEntryEnvelope,
    GlossaryEntryPlain,
    GlossaryEntryResponse,
    GlossaryListPayload,
    GlossaryListResponse,
    UpdateGlossaryEntryRequest,
)
from app.modules.glossary.service import GlossaryService

router = APIRouter(prefix="/api/v1/clients/{client_id}/glossary", tags=["glossary"])


def _get_glossary_service(db: DbSessionDep, settings: SettingsDep) -> GlossaryService:
    return GlossaryService(db, settings)


GlossaryServiceDep = Annotated[GlossaryService, Depends(_get_glossary_service)]


def _to_response(entry: GlossaryEntryPlain) -> GlossaryEntryResponse:
    return GlossaryEntryResponse(
        id=entry.id,
        kind=entry.kind,
        code=entry.code,
        name=entry.name,
        description=entry.description,
        decrypt_failed=entry.decrypt_failed,
    )


@router.get(
    "",
    summary=(
        "Lista o glossário do tenant (categorias, fornecedores típicos e regras "
        "de auditoria), paginado por `page`/`pageSize` (máx. 100). Visível a "
        "todo papel com acesso ao cliente — inclusive o operador, que o usa como "
        "referência na revisão. O envelope traz `version`, o mesmo contador que "
        "invalida o bloco de contexto da qualificação quando o glossário muda. "
        "Entrada cujo texto não decifra vem com `decryptFailed: true` e "
        "`[indecifrável]` no campo, nunca vazia. Glossário de outro tenant nunca "
        "aparece."
    ),
)
async def list_glossary_entries(
    user: CurrentUserDep,
    client: AccessibleClientDep,
    service: GlossaryServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
) -> GlossaryListResponse:
    entries, version, pagination = await service.list_entries(
        client=client, user=user, page=page, page_size=page_size
    )
    return GlossaryListResponse(
        data=GlossaryListPayload(entries=[_to_response(e) for e in entries], version=version),
        pagination=pagination,
    )


@router.post(
    "",
    status_code=201,
    summary=(
        "Cria uma entrada no glossário do tenant. Requer a permissão "
        "`manage_glossary` (gerente do cliente ou equipe do sistema) — operador "
        "do cliente recebe 403. `kind` aceita apenas categoria/fornecedor/regra; "
        "`name` é obrigatório (máx. 120 caracteres), `code` (máx. 40) e "
        "`description` (máx. 500) são opcionais; campo desconhecido no corpo é "
        "recusado. Passar do limite de 200 entradas ativas devolve 400 "
        "GLOSSARY_LIMIT_EXCEEDED — o teto existe para o bloco de contexto da "
        "qualificação não crescer sem fim. Salvar incrementa a versão do "
        "glossário do cliente."
    ),
)
async def create_glossary_entry(
    payload: CreateGlossaryEntryRequest,
    actor: ManageGlossaryDep,
    client: AccessibleClientDep,
    service: GlossaryServiceDep,
) -> GlossaryEntryEnvelope:
    # `actor` aciona o guard da matriz; `client` já veio validado contra o tenant.
    del actor
    entry = await service.create_entry(
        client=client,
        kind=payload.kind,
        name=payload.name,
        code=payload.code,
        description=payload.description,
    )
    return GlossaryEntryEnvelope(data=_to_response(entry))


@router.patch(
    "/{entry_id}",
    summary=(
        "Substitui os campos de uma entrada do glossário do tenant. Requer a "
        "permissão `manage_glossary`; operador do cliente recebe 403. O corpo "
        "traz o registro completo (não é patch parcial: o texto é cifrado, então "
        "alterar um campo isolado exigiria decifrar o resto). Entrada de outro "
        "tenant devolve 404. Salvar incrementa a versão do glossário."
    ),
)
async def update_glossary_entry(
    entry_id: UUID,
    payload: UpdateGlossaryEntryRequest,
    actor: ManageGlossaryDep,
    client: AccessibleClientDep,
    service: GlossaryServiceDep,
) -> GlossaryEntryEnvelope:
    entry = await service.update_entry(
        client=client,
        user=actor,
        entry_id=entry_id,
        kind=payload.kind,
        name=payload.name,
        code=payload.code,
        description=payload.description,
    )
    return GlossaryEntryEnvelope(data=_to_response(entry))


@router.delete(
    "/{entry_id}",
    summary=(
        "Remove uma entrada do glossário do tenant (remoção lógica: a linha "
        "permanece com `deleted_at` preenchido e some de toda listagem). Requer "
        "a permissão `manage_glossary`; operador do cliente recebe 403. Entrada "
        "de outro tenant devolve 404. A remoção também incrementa a versão do "
        "glossário — é ela que invalida o bloco de contexto da qualificação, e a "
        "resposta devolve a versão nova."
    ),
)
async def delete_glossary_entry(
    entry_id: UUID,
    actor: ManageGlossaryDep,
    client: AccessibleClientDep,
    service: GlossaryServiceDep,
) -> GlossaryDeletedResponse:
    version = await service.delete_entry(client=client, user=actor, entry_id=entry_id)
    return GlossaryDeletedResponse(
        data=GlossaryDeletedPayload(id=entry_id, deleted=True, version=version)
    )
