"""Endpoints do mapeamento de entrada do arquivo (Sprint 14, BACK 14.1 — R1/R5).

    - GET /api/v1/clients/{client_id}/input-mapping
    - PUT /api/v1/clients/{client_id}/input-mapping

**Duas travas, ambas necessárias** (mesmo desenho do de-para, S12):

1. A MATRIZ: `manage_input_mapping` (todos menos o `client_operator`) na escrita,
   pelo guard AUDITADO (`require_client_permission`) — a negação do papel que
   alcança o cliente mas não pode configurar vira 1 linha `denied` em
   `access_audit`. A LEITURA não pede permissão: quem alcança o cliente lê (o
   operador precisa ver o resumo do que será aplicado antes de enviar o arquivo).
2. O TENANT: `OpenClientDep` na escrita, `AccessibleClientDep` na leitura — o
   `client_id` do path só passa por `resolve_client_access`, e cliente ENCERRADO
   recusa a escrita com 409 enquanto a leitura segue 200 (§4.12).

⚠️ **Ordem dos parâmetros importa.** `client` vem ANTES do guard de permissão: o
alcance (cross-tenant e cross-org, com a trilha dele) é decidido antes da célula.

⚠️ **Ausente é `{mapping: null}` com 200, nunca 404** — 404 é o código
anti-enumeração do tenant. Forma inválida (enum fora do vocabulário, convenção de
sinal ausente ou incoerente) é 400 `VALIDATION_ERROR` genérico (§4.8).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    ManageInputMappingDep,
    OpenClientDep,
)
from app.modules.client_input_mappings.repository import ClientInputMappingRepository
from app.modules.client_input_mappings.schemas import (
    InputMappingEnvelope,
    InputMappingPayload,
    InputMappingRequest,
    InputMappingWriteEnvelope,
    InputMappingWritePayload,
)
from app.modules.client_input_mappings.service import ClientInputMappingService

router = APIRouter(
    prefix="/api/v1/clients/{client_id}/input-mapping",
    tags=["client-input-mapping"],
)


def _get_service(db: DbSessionDep) -> ClientInputMappingService:
    return ClientInputMappingService(ClientInputMappingRepository(db))


ServiceDep = Annotated[ClientInputMappingService, Depends(_get_service)]


@router.get(
    "",
    summary=(
        "O mapeamento de entrada do arquivo deste cliente — qual coluna é data, "
        "descrição, valor, categoria, conta e documento, o formato (CSV/XLSX), o "
        "delimitador e a codificação do CSV, o formato de data, o separador decimal "
        "e a convenção de sinal. Visível a todo papel com acesso ao cliente, "
        "inclusive o operador (ele precisa ver o resumo do que será aplicado antes "
        "de enviar). Cliente sem mapeamento responde 200 com `mapping: null` — é "
        "estado normal, e a tela conduz a criação a partir dele; nunca 404. "
        "Cliente encerrado continua legível."
    ),
)
async def get_input_mapping(
    client: AccessibleClientDep,
    service: ServiceDep,
) -> InputMappingEnvelope:
    mapping = await service.get(client)
    return InputMappingEnvelope(data=InputMappingPayload(mapping=mapping))


@router.put(
    "",
    summary=(
        "Declara ou SUBSTITUI o mapeamento de entrada do cliente (um por cliente; "
        "configuração, não vigência — alterá-lo muda como TODOS os próximos arquivos "
        "serão lidos, e a tela pede confirmação explícita). Requer a permissão "
        "`manage_input_mapping` (plataforma, admin, gerente da carteira e gerente do "
        "cliente; o operador do cliente recebe 403 e a negação fica na trilha). "
        "Tudo é DECLARADO, nada é inferido: a convenção de sinal é obrigatória, e "
        "os campos exigidos por cada convenção (`valor_com_sinal`: só a coluna de "
        "valor; `coluna_natureza`: coluna de valor + coluna de natureza + literais "
        "de débito e crédito; `colunas_separadas`: colunas de débito e de crédito, "
        "SEM coluna de valor) são verificados na borda e no banco. CSV exige "
        "delimitador e codificação; XLSX não os aceita. Forma inválida: 400 "
        "`VALIDATION_ERROR`. Cliente encerrado: 409. `created` diz se o cliente "
        "não tinha mapeamento (`true`) ou se o anterior foi substituído (`false`)."
    ),
)
async def put_input_mapping(
    client: OpenClientDep,
    actor: ManageInputMappingDep,
    payload: InputMappingRequest,
    service: ServiceDep,
) -> InputMappingWriteEnvelope:
    mapping, created = await service.replace(client, actor=actor, fields=payload)
    return InputMappingWriteEnvelope(
        data=InputMappingWritePayload(mapping=mapping, created=created)
    )
