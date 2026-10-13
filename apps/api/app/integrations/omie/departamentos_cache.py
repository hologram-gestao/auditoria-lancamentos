"""Cache L1 (in-memory) dos departamentos Omie por cliente (86e3n70q0, bloco B).

Mesmo racional e mesma mecânica do `categorias_cache`: o **nome** do
departamento é dado do cliente final e não encosta no disco (§4.5). Na Omie da
Hologram ele carrega nome de CLIENTE ("TSR-DIR-BPO Financeiro - <cliente>"),
então a regra pesa mais aqui do que nas categorias. O que persiste em qualquer
lugar do sistema é só o CÓDIGO (`cCodDep`, no título); o nome é lido daqui.

**Nunca logar** a descrição — só contadores.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from cachetools import TTLCache

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.integrations.omie.schemas import DepartamentoOmie

log = get_logger(__name__)

#: 6 h, como as categorias: cadastro que muda raramente, com `refresh=true` como
#: caminho explícito para quem acabou de criar um departamento no Omie.
DEFAULT_TTL_SECONDS = 21_600

#: Teto de CLIENTES em cache. ~200 clientes x ~200 departamentos fica em poucos MB.
DEFAULT_MAXSIZE = 200


class OmieDepartamentosCache:
    """Cache TTL de `list[DepartamentoOmie]` por `client_id` (asyncio, sem lock)."""

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        maxsize: int = DEFAULT_MAXSIZE,
    ) -> None:
        self._cache: TTLCache[UUID, list[DepartamentoOmie]] = TTLCache(
            maxsize=maxsize,
            ttl=ttl_seconds,
        )

    def get(self, client_id: UUID) -> list[DepartamentoOmie] | None:
        """Departamentos em cache para o cliente, ou `None` em miss/expirado."""
        cached = self._cache.get(client_id)
        log.info(
            "omie_departamentos_cache_lookup",
            client_id=str(client_id),
            hit=cached is not None,
            count=len(cached) if cached is not None else 0,
        )
        return list(cached) if cached is not None else None

    def set(self, client_id: UUID, departamentos: list[DepartamentoOmie]) -> None:
        """Guarda a lista do cliente. Uma cópia — o caller pode mutar a dele."""
        self._cache[client_id] = list(departamentos)
        log.info(
            "omie_departamentos_cache_stored",
            client_id=str(client_id),
            count=len(departamentos),
        )

    def invalidate(self, client_id: UUID) -> None:
        """Remove a entrada do cliente (o `refresh=true` da rota)."""
        self._cache.pop(client_id, None)
        log.info("omie_departamentos_cache_invalidated", client_id=str(client_id))
