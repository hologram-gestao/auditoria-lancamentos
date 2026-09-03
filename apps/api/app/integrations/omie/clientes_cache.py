"""Cache L1 (in-memory) de nomes de cliente/fornecedor Omie (86e33bmkb).

**Por que in-memory e NÃO uma tabela**: razão social / nome fantasia é dado
identificável do cliente final — CLAUDE.md §4.5 manda buscar do Omie em tempo
real e manter só em cache TTL. Mesmo racional (e mesma mecânica) do
`categorias_cache`; a diferença é a granularidade: aqui a chave é
`(client_id, codigo_cliente_omie)` porque a resolução usa `ConsultarCliente`
por código — o cadastro de clientes pode ter milhares de entradas e paginar
a lista inteira por render seria pior que N consultas pontuais cacheadas.

Cache NEGATIVO separado (TTL curto): código que o Omie respondeu não conhecer
(faultstring — cadastro excluído, por exemplo) não pode custar um
`ConsultarCliente` a cada render da aba, para sempre. Falha de transporte
NUNCA marca — o próximo render tenta de novo (mesma regra do
`lancamento_cache`).

**Nunca logar** o nome — é PII do cliente final. Só contadores e códigos.
"""

from __future__ import annotations

from uuid import UUID

from cachetools import TTLCache

from app.core.logging import get_logger

log = get_logger(__name__)

#: 6 h — cadastro muda raramente (mesmo TTL do `categorias_cache`).
DEFAULT_TTL_SECONDS = 21_600

#: TTL do negativo: 15 min (mesmo valor do `lancamento_cache.UNRESOLVED_TTL_SECONDS`).
UNRESOLVED_TTL_SECONDS = 900

#: Teto de PARES (client_id, codigo). ~200 clientes x ~50 fornecedores em
#: divergência ativa fica ordens de grandeza abaixo; LRU segura o resto.
DEFAULT_MAXSIZE = 20_000


class OmieClientesCache:
    """Cache TTL de `codigo_cliente_omie` → nome de exibição, por tenant.

    Não é thread-safe — não precisa ser (asyncio single-threaded), como os
    demais caches do pacote.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        unresolved_ttl_seconds: int = UNRESOLVED_TTL_SECONDS,
        maxsize: int = DEFAULT_MAXSIZE,
    ) -> None:
        self._names: TTLCache[tuple[UUID, int], str] = TTLCache(
            maxsize=maxsize,
            ttl=ttl_seconds,
        )
        self._unresolved: TTLCache[tuple[UUID, int], bool] = TTLCache(
            maxsize=maxsize,
            ttl=unresolved_ttl_seconds,
        )

    def get_name(self, *, client_id: UUID, codigo: int) -> str | None:
        """Nome em cache, ou `None` (miss/expirado — caller decide consultar)."""
        return self._names.get((client_id, codigo))

    def set_name(self, *, client_id: UUID, codigo: int, name: str) -> None:
        self._names[(client_id, codigo)] = name

    def known_unresolved(self, *, client_id: UUID, codigo: int) -> bool:
        """`True` se uma consulta RECENTE provou que o código não resolve."""
        return (client_id, codigo) in self._unresolved

    def mark_unresolved(self, *, client_id: UUID, codigo: int) -> None:
        """Marca código que o Omie respondeu não conhecer (fault) ou sem nome.

        NUNCA chamar em falha de transporte — falha não prova nada, e marcar
        silenciaria o retry natural do próximo render.
        """
        self._unresolved[(client_id, codigo)] = True
        log.info(
            "omie_clientes_cache_unresolved",
            client_id=str(client_id),
            codigo=codigo,
        )
