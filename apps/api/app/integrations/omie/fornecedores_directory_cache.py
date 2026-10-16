"""Cache L1 (in-memory) do diretório de cadastros Omie por cliente (86e3qhhrd).

A busca de fornecedor pelo NOME (gaveta "Lançar no Omie" e fornecedor padrão do
cliente) roda sobre a lista COMPLETA de cadastros do cliente, lida da Omie uma vez
por TTL (`OmieClient.listar_clientes`) e guardada aqui. A pessoa digita, para, e o
"contém" roda no servidor sobre esta lista: zero chamadas à Omie por tecla.

Mesmo racional do `departamentos_cache`: razão social, nome fantasia e CNPJ são
dado do cliente final e **não encostam no disco** (§4.5). Nada daqui é logado além
de contadores.

**Diferença para os outros caches: uma trava por cliente.** A busca ao digitar
dispara várias requisições seguidas; sem a trava, todas pegariam o miss ao mesmo
tempo e cada uma leria o diretório inteiro da Omie, que é exatamente a rajada que
cai no `425`. Com ela, a primeira lê e as outras esperam e acham o cache cheio. A
trava é por PROCESSO (como o cache): com N instâncias, são até N leituras por TTL.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID

from cachetools import TTLCache

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.integrations.omie.schemas import ClienteOmie

log = get_logger(__name__)

#: 6 h, como categorias e departamentos. Fornecedor criado no Omie depois da
#: leitura só aparece com "Atualizar lista" (`refresh=true`) ou depois do TTL.
DEFAULT_TTL_SECONDS = 21_600

#: Teto de CLIENTES em cache. ~200 clientes x ~1.000 cadastros leves fica em
#: dezenas de MB no pior caso; o comum é bem menos.
DEFAULT_MAXSIZE = 200


class OmieFornecedoresDirectoryCache:
    """Cache TTL de `list[ClienteOmie]` por `client_id`, com trava por cliente."""

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        maxsize: int = DEFAULT_MAXSIZE,
    ) -> None:
        self._cache: TTLCache[UUID, list[ClienteOmie]] = TTLCache(
            maxsize=maxsize,
            ttl=ttl_seconds,
        )
        self._locks: dict[UUID, asyncio.Lock] = {}

    def lock_for(self, client_id: UUID) -> asyncio.Lock:
        """A trava do cliente: quem pega o miss lê a Omie; os outros esperam.

        As travas não expiram com o TTL, mas são uma por cliente que já buscou,
        então o dicionário cresce no máximo até o número de clientes ativos.
        """
        lock = self._locks.get(client_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[client_id] = lock
        return lock

    def get(self, client_id: UUID) -> list[ClienteOmie] | None:
        """Cadastros em cache para o cliente, ou `None` em miss/expirado."""
        cached = self._cache.get(client_id)
        log.info(
            "omie_fornecedores_directory_cache_lookup",
            client_id=str(client_id),
            hit=cached is not None,
            count=len(cached) if cached is not None else 0,
        )
        return list(cached) if cached is not None else None

    def set(self, client_id: UUID, cadastros: list[ClienteOmie]) -> None:
        """Guarda a lista do cliente. Uma cópia: o caller pode mutar a dele."""
        self._cache[client_id] = list(cadastros)
        log.info(
            "omie_fornecedores_directory_cache_stored",
            client_id=str(client_id),
            count=len(cadastros),
        )

    def invalidate(self, client_id: UUID) -> None:
        """Remove a entrada do cliente (o "Atualizar lista" da tela)."""
        self._cache.pop(client_id, None)
        log.info("omie_fornecedores_directory_cache_invalidated", client_id=str(client_id))
