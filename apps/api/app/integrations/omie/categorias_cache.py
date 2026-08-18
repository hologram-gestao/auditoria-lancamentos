"""Cache L1 (in-memory) das categorias Omie por cliente (Sprint 7 / BACK 07.3).

**Por que in-memory e NÃO uma tabela no banco.** O CLAUDE.md §4.5 nomeia
`categorias` na lista do que "nunca persiste em claro — sempre buscado do Omie
em tempo real e mantido apenas em cache com TTL". Existem dois padrões de cache
no repo e só um serve aqui:

  - `clients/accounts_cache.py` persiste em `omie_accounts_cache` — vale para
    contas correntes, que o §4.5 trata separadamente e cujo nome já vive no
    banco por decisão anterior;
  - `omie/lancamento_cache.py` é `cachetools.TTLCache` em processo, criado
    exatamente para o dado que **não pode** encostar no disco.

Este módulo segue o segundo. Nenhuma dependência nova, nenhuma camada nova.

**Nunca logar** descrição de categoria — só contadores (`count`, `hit`/`miss`).
Descrição de categoria é vocabulário contábil do cliente final.

Em deploy multi-instância (Cloud Run > 1 réplica) cada réplica tem o seu L1 —
aceitável pelo mesmo motivo do cache de lançamentos: o miss custa uma chamada
ao Omie, não uma resposta errada.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from cachetools import TTLCache

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.integrations.omie.schemas import CategoriaOmie

log = get_logger(__name__)

#: 6 h. Categorias são cadastro contábil — mudam raramente, e o operador que
#: acabou de criar uma no Omie tem um caminho explícito (`refresh=true`) em vez
#: de um TTL curto que faria toda tela pagar a latência da Omie.
DEFAULT_TTL_SECONDS = 21_600

#: Teto de CLIENTES em cache (não de categorias). Cada entrada guarda a lista
#: inteira de um cliente; ~200 clientes x ~300 categorias x ~120 B fica na casa
#: de poucos MB. Ao bater o teto, o `TTLCache` evita por LRU.
DEFAULT_MAXSIZE = 200


class OmieCategoriasCache:
    """Cache TTL de `list[CategoriaOmie]` por `client_id`.

    Não é thread-safe — não precisa ser: asyncio é single-threaded, como no
    `OmieLancamentoCache`.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        maxsize: int = DEFAULT_MAXSIZE,
    ) -> None:
        self._cache: TTLCache[UUID, list[CategoriaOmie]] = TTLCache(
            maxsize=maxsize,
            ttl=ttl_seconds,
        )

    def get(self, client_id: UUID) -> list[CategoriaOmie] | None:
        """Categorias em cache para o cliente, ou `None` em miss/expirado."""
        cached = self._cache.get(client_id)
        log.info(
            "omie_categorias_cache_lookup",
            client_id=str(client_id),
            hit=cached is not None,
            count=len(cached) if cached is not None else 0,
        )
        return list(cached) if cached is not None else None

    def set(self, client_id: UUID, categorias: list[CategoriaOmie]) -> None:
        """Guarda a lista do cliente. Uma cópia — o caller pode mutar a dele."""
        self._cache[client_id] = list(categorias)
        log.info(
            "omie_categorias_cache_stored",
            client_id=str(client_id),
            count=len(categorias),
        )

    def invalidate(self, client_id: UUID) -> None:
        """Remove a entrada do cliente (usado pelo `refresh=true` da rota)."""
        self._cache.pop(client_id, None)
        log.info("omie_categorias_cache_invalidated", client_id=str(client_id))
