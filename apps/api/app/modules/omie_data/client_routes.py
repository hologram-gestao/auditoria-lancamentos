"""Leituras do Omie do cliente para o lançamento como Contas a Pagar (86e3n70q0).

- `GET /api/v1/clients/{client_id}/omie/departamentos` — departamentos ativos
  (código, nome, posição na árvore), de um cache em memória por cliente (6 h).
- `GET /api/v1/clients/{client_id}/omie/fornecedores?cnpj=` — o cadastro do
  fornecedor deste CNPJ, ou 404 `FORNECEDOR_NAO_ENCONTRADO`. Nunca grava.
  `?codigo=` (bloco C) devolve o nome do cadastro de um código, para a tela de
  edição do cliente mostrar o fornecedor padrão gravado; a resolução é a das
  divergências (`resolve_supplier_names` + `OmieClientesCache`).

**Tenant.** `AccessibleClientDep` (a decisão única `resolve_client_access`): o
`client_id` do path só serve para endereçar, quem decide se ele é alcançável é a
LINHA do usuário. Sem permissão própria, como a leitura das categorias: quem
alcança o cliente lê o que a gaveta de lançamento vai precisar. Lista canônica:
as duas são COLLECTION.

**Origem.** Cliente só-arquivo é 409 `CAPACIDADE_AUSENTE` (não há Omie para
perguntar); sem conexão, 409 `SEM_CONEXAO` — a taxonomia da S9.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.cnpj import normalize_cnpj
from app.core.config import Settings, get_settings
from app.core.dependencies import AccessibleClientDep, DbSessionDep
from app.core.exceptions import ValidationAppError
from app.integrations.omie.client import OmieClient
from app.integrations.omie.clientes_cache import OmieClientesCache
from app.integrations.omie.departamentos_cache import OmieDepartamentosCache
from app.integrations.providers.base import Capability
from app.modules.client_connections.origin import build_capable_client
from app.modules.omie_data.payables_service import (
    OmieDepartamentosService,
    OmieFornecedorLookupService,
)
from app.modules.omie_data.schemas import (
    OmieDepartamentoListResponse,
    OmieFornecedorResponse,
)

router = APIRouter(prefix="/api/v1/clients/{client_id}/omie", tags=["omie"])


@router.get(
    "/departamentos",
    summary=(
        "Departamentos ATIVOS do Omie do cliente (código, nome e posição na árvore), "
        "para o rateio do título a pagar. Lista completa, sem paginação, de um cache "
        "em memória por cliente (6 h); `refresh=true` ignora o cache. Cliente "
        "só-arquivo: 409 CAPACIDADE_AUSENTE."
    ),
)
async def get_client_omie_departamentos(
    client: AccessibleClientDep,
    db: DbSessionDep,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    refresh: Annotated[bool, Query(description="Ignora o cache e rebusca do Omie.")] = False,
) -> OmieDepartamentoListResponse:
    cache: OmieDepartamentosCache = request.app.state.omie_departamentos_cache

    async def build_client() -> OmieClient:
        # Só no MISS: o hit do cache não paga o unwrap da DEK nem o 409 de origem.
        return await build_capable_client(
            db, client, Capability.LISTAR_LANCAMENTOS, settings=settings
        )

    items = await OmieDepartamentosService(cache).list_departamentos(
        client_id=client.id,
        omie_client_factory=build_client,
        refresh=refresh,
    )
    return OmieDepartamentoListResponse(data=items, total=len(items))


@router.get(
    "/fornecedores",
    summary=(
        "Busca no Omie do cliente o fornecedor de um CNPJ (14 dígitos, com ou sem "
        "máscara) OU de um código (`codigo_cliente_omie`): exatamente um dos dois. "
        "Por CNPJ devolve código, razão social e nome fantasia; por código, o nome de "
        "exibição do cadastro em `razao_social`. Sem cadastro: 404 "
        "FORNECEDOR_NAO_ENCONTRADO. Nunca grava nada. CNPJ inválido, os dois ou "
        "nenhum: 400. Cliente só-arquivo: 409 CAPACIDADE_AUSENTE."
    ),
)
async def get_client_omie_fornecedor(
    client: AccessibleClientDep,
    db: DbSessionDep,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    cnpj: Annotated[
        str | None,
        Query(min_length=14, max_length=18, description="CNPJ do fornecedor."),
    ] = None,
    codigo: Annotated[
        int | None,
        Query(ge=1, description="Código do cadastro no Omie (`codigo_cliente_omie`)."),
    ] = None,
) -> OmieFornecedorResponse:
    if cnpj is not None and codigo is not None:
        raise ValidationAppError("Busca de fornecedor exige cnpj OU codigo, não os dois.")

    async def build_client() -> OmieClient:
        return await build_capable_client(
            db, client, Capability.LISTAR_LANCAMENTOS, settings=settings
        )

    service = OmieFornecedorLookupService()
    if codigo is not None:
        cache: OmieClientesCache = request.app.state.omie_clientes_cache
        item = await service.find_by_code(
            client_id=client.id,
            codigo=codigo,
            cache=cache,
            omie_client_factory=build_client,
        )
        return OmieFornecedorResponse(data=item)

    if cnpj is None:
        raise ValidationAppError("Busca de fornecedor exige cnpj OU codigo.")
    try:
        digits = normalize_cnpj(cnpj)
    except ValueError:
        # Sem ecoar o valor: o 400 diz só que o CNPJ não é válido.
        raise ValidationAppError(
            "CNPJ inválido na busca de fornecedor.",
            user_message="CNPJ inválido. Confira os 14 dígitos.",
        ) from None

    item = await service.find_by_cnpj(
        client_id=client.id,
        cnpj_digits=digits,
        omie_client_factory=build_client,
    )
    return OmieFornecedorResponse(data=item)
