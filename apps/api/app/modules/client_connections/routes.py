"""Endpoints das origens de dado do cliente (Sprint 9, BACK 09.3 — R5).

    - GET    /api/v1/clients/{client_id}/connections
    - POST   /api/v1/clients/{client_id}/connections
    - POST   /api/v1/clients/{client_id}/connections/{connection_id}/test
    - PATCH  /api/v1/clients/{client_id}/connections/{connection_id}
    - DELETE /api/v1/clients/{client_id}/connections/{connection_id}

**Duas travas, ambas necessárias** (mesmo desenho do glossário):

1. `ManageClientConnectionsDep` — a MATRIZ (permissão `manage_client_connections`):
   plataforma, admin e **manager** ✅; papéis de cliente ❌ (403). O manager
   entra de propósito: ele cria cliente, e sem esta célula o contador do
   escritório parceiro cadastraria a carteira sem conseguir conectar ninguém.
   Só nas ESCRITAS — a LEITURA é liberada a todo papel com acesso ao tenant,
   porque o usuário do cliente precisa ver o ESTADO da origem dele.
2. `AccessibleClientDep` / `OpenClientDep` — o TENANT: o `client_id` do path só
   passa por `resolve_client_access`. Nenhuma rota aqui compara `role` na mão.
   As 4 escritas usam `OpenClientDep`: cliente ENCERRADO recusa com 409 antes de
   qualquer provisionamento de DEK (§4.12).

E a terceira, na camada de dados: todo `SELECT` de conexão leva
`AND client_id = <tenant>` — conexão de outro cliente vira **404**, nunca o dado.

⚠️ **Nenhuma resposta desta rota carrega credencial** — nem em claro, nem
cifrada, nem mascarada. O `ClientConnectionResponse` não tem o campo.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core.dependencies import (
    AccessibleClientDep,
    DbSessionDep,
    ManageClientConnectionsDep,
    OpenClientDep,
    SettingsDep,
)
from app.modules.client_connections.schemas import (
    ClientConnectionEnvelope,
    ClientConnectionListPayload,
    ClientConnectionListResponse,
    ConnectionDeletedPayload,
    ConnectionDeletedResponse,
    CreateConnectionRequest,
    UpdateConnectionRequest,
)
from app.modules.client_connections.service import ClientConnectionService

router = APIRouter(prefix="/api/v1/clients/{client_id}/connections", tags=["client-connections"])


def _get_connection_service(db: DbSessionDep, settings: SettingsDep) -> ClientConnectionService:
    return ClientConnectionService(db, settings)


ConnectionServiceDep = Annotated[ClientConnectionService, Depends(_get_connection_service)]


@router.get(
    "",
    summary=(
        "Lista as origens de dado conectadas ao cliente (0..N). Visível a todo "
        "papel com acesso ao cliente — inclusive o operador, que precisa ver se "
        "a origem está ativa antes de rodar uma conciliação. Cada item traz "
        "`capabilities`, o que aquele tipo de origem sabe fazer (verificar "
        "credencial, listar contas, listar lançamentos, escrever) — é por esse "
        "campo que a tela decide o que oferecer, em vez de tentar e receber 409. "
        "Nenhuma credencial aparece na resposta, nem mascarada. Origem de outro "
        "cliente nunca aparece."
    ),
)
async def list_connections(
    client: AccessibleClientDep,
    service: ConnectionServiceDep,
) -> ClientConnectionListResponse:
    connections = await service.list_connections(client)
    return ClientConnectionListResponse(data=ClientConnectionListPayload(connections=connections))


@router.post(
    "",
    status_code=201,
    summary=(
        "Conecta uma origem ao cliente. Requer a permissão "
        "`manage_client_connections` (plataforma, admin ou gerente da carteira) "
        "— papéis de cliente recebem 403, e cliente encerrado recebe 409. A "
        "credencial é **verificada contra o provedor ANTES de qualquer escrita**: "
        "recusada, nenhuma linha nasce. Aceita mais de uma origem do mesmo tipo "
        "no mesmo cliente, distinguidas pelo `label`; omitir o rótulo usa o "
        "padrão do tipo apenas na primeira conexão daquele tipo. Tipo e rótulo "
        "já existentes devolvem 409 com `details.existingConnectionId`. Rótulo "
        "vazio e tipo desconhecido são 422."
    ),
)
async def create_connection(
    payload: CreateConnectionRequest,
    actor: ManageClientConnectionsDep,
    client: OpenClientDep,
    service: ConnectionServiceDep,
) -> ClientConnectionEnvelope:
    connection = await service.create_connection(
        client=client,
        user=actor,
        provider_type=payload.provider_type,
        label=payload.label,
        credentials=payload.credentials,
    )
    return ClientConnectionEnvelope(data=connection)


@router.post(
    "/{connection_id}/test",
    summary=(
        "Reverifica a credencial JÁ GRAVADA desta origem contra o provedor. "
        "Requer a permissão `manage_client_connections`; cliente encerrado "
        "recebe 409. Sucesso marca a conexão como ativa e carimba a "
        "verificação; credencial recusada marca como `erro` **sem apagar a "
        "credencial** (recusada não é perdida — basta atualizar). Provedor fora "
        "do ar ou lento devolve 5xx e não muda o estado da conexão: é "
        "transitório. Origem de outro cliente devolve 404."
    ),
)
async def test_connection(
    connection_id: UUID,
    actor: ManageClientConnectionsDep,
    client: OpenClientDep,
    service: ConnectionServiceDep,
) -> ClientConnectionEnvelope:
    connection = await service.test_connection(
        client=client, user=actor, connection_id=connection_id
    )
    return ClientConnectionEnvelope(data=connection)


@router.patch(
    "/{connection_id}",
    summary=(
        "Renomeia a origem e/ou troca as credenciais dela. Requer a permissão "
        "`manage_client_connections`; cliente encerrado recebe 409. Os dois "
        "campos são independentes: dá para renomear sem mexer na credencial e "
        "vice-versa, mas o corpo vazio é 422. Credencial nova é verificada "
        "contra o provedor antes de substituir a antiga — recusada, nada muda. "
        "Rótulo que colida com outra origem do mesmo tipo devolve 409 com "
        "`details.existingConnectionId`. Origem de outro cliente devolve 404."
    ),
)
async def update_connection(
    connection_id: UUID,
    payload: UpdateConnectionRequest,
    actor: ManageClientConnectionsDep,
    client: OpenClientDep,
    service: ConnectionServiceDep,
) -> ClientConnectionEnvelope:
    connection = await service.update_connection(
        client=client,
        user=actor,
        connection_id=connection_id,
        label=payload.label,
        credentials=payload.credentials,
    )
    return ClientConnectionEnvelope(data=connection)


@router.delete(
    "/{connection_id}",
    summary=(
        "Remove a origem do cliente. Requer a permissão "
        "`manage_client_connections`; cliente encerrado recebe 409. A remoção é "
        "DEFINITIVA (a linha some, não é remoção lógica) — é isso que permite "
        "reconectar depois o mesmo tipo com o mesmo rótulo. O registro do que "
        "aconteceu fica na trilha de auditoria, não na linha. Origem de outro "
        "cliente devolve 404."
    ),
)
async def delete_connection(
    connection_id: UUID,
    actor: ManageClientConnectionsDep,
    client: OpenClientDep,
    service: ConnectionServiceDep,
) -> ConnectionDeletedResponse:
    await service.delete_connection(client=client, user=actor, connection_id=connection_id)
    return ConnectionDeletedResponse(data=ConnectionDeletedPayload(id=connection_id, deleted=True))
