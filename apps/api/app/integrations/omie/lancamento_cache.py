"""Cache L1 (in-memory) de lançamentos Omie individuais (S11 BACK 9.2).

Por que existe (Doc §5.3 + CLAUDE.md §4.5):
    Nenhum dado identificável de cliente final (fornecedor, categoria, etc) pode
    persistir em claro no DB. Mas a Tela de Revisão precisa exibir esses
    metadados nas duas abas (Movimentações e Divergências Omie). A solução é
    cache TTL — caro de buscar do Omie, barato de servir; sumir do storage
    após o TTL satisfaz a regra de "nenhum dado em claro".

L1 (in-memory):
    Dict `(client_id, omie_id) -> (data, expires_at)` via `cachetools.TTLCache`
    (expiração lazy + upper bound LRU). É por processo. Desde a remoção do Redis
    (FASE 0): o processamento da conciliação roda no MESMO processo da API via
    `BackgroundTasks`, então não há mais necessidade do L2 compartilhado que
    existia só para coerência entre o processo da API e o worker ARQ separado.
    Em deploy multi-instância (Cloud Run > 1 réplica), cada réplica tem seu
    próprio L1 — aceitável: a Tela de Revisão re-busca do Omie em cache miss.

Limitação importante do Omie:
    Não existe endpoint `Consultar1LancamentoPorId`. Tudo passa por
    `ListarExtrato(omie_conta_id, periodo)`. Para popular o cache precisamos
    do contexto da SESSÃO (que conhece `omie_conta_id`, `period_start`,
    `period_end`, `tolerance_days`), não apenas dos IDs avulsos.

NÃO logar:
    Nunca emitir `supplier`, `category`, `description`, valor. Logar apenas
    contadores e flags ("hit", "miss", "populated_count").
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID

from cachetools import TTLCache

from app.core.logging import get_logger
from app.integrations.omie.client import OmieClient
from app.integrations.omie.schemas import LancamentoExtrato

log = get_logger(__name__)

# TTL: 2h conforme PLAN_IMPLEMENTACAO S11 e Doc §5.3.
DEFAULT_TTL_SECONDS = 7200

# Limite de entries do L1. Estimativa: ~100 clientes x ~100 lançamentos
# típicos por sessão = ~10k entries; cada entry ocupa ~200 B (DTO + tupla-chave
# UUID + int), totalizando ~2 MB — headroom confortável e impede crescimento
# ilimitado em uvicorn long-running. Ao bater o teto, TTLCache evita por LRU.
# Não precisa ser thread-safe: asyncio é single-threaded.
DEFAULT_L1_MAXSIZE = 10_000


class OmieLancamentoData:
    """DTO descongelado do cache — leve, sem regras de domínio."""

    __slots__ = (
        "amount",
        "category",
        "description",
        "omie_id",
        "status",
        "supplier",
        "transaction_date",
    )

    def __init__(
        self,
        *,
        omie_id: int,
        transaction_date: date,
        description: str,
        amount: Decimal,
        supplier: str | None,
        category: str | None,
        status: str,
    ) -> None:
        self.omie_id = omie_id
        self.transaction_date = transaction_date
        self.description = description
        self.amount = amount
        self.supplier = supplier
        self.category = category
        self.status = status

    @classmethod
    def from_lancamento(cls, item: LancamentoExtrato) -> OmieLancamentoData:
        """Converte `LancamentoExtrato` (Omie) → DTO normalizado.

        Sinal: usa a property `signed_amount` (débito vira negativo, CLAUDE.md §5.6).
        Description/supplier/category vêm das properties do schema, que
        escolhem o campo mais legível dentre os pares disponíveis no
        response do Omie.
        """
        return cls(
            omie_id=item.n_cod_lancamento,
            transaction_date=item.d_data_lancamento,
            description=item.description,
            amount=item.signed_amount,
            supplier=item.supplier,
            category=item.category,
            status=item.c_situacao,
        )


# Factory type para o OmieClient (recebe client_id, retorna OmieClient pronto)
OmieClientFactory = Callable[[UUID], "OmieClient"]


class OmieLancamentoCache:
    """Cache L1 (in-memory) de lançamentos Omie individuais.

    Compartilhado entre BACK 9.2, 9.4 e 9.5. A instância pode ser singleton
    no app (L1 in-memory faz sentido por processo); cada chamada recebe o
    `OmieClient` correspondente via factory.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        l1_maxsize: int = DEFAULT_L1_MAXSIZE,
    ) -> None:
        # L1: chave (client_id, omie_id) → data. TTLCache lida com expiração
        # lazy via time.monotonic e impõe upper bound LRU.
        self._l1: TTLCache[tuple[UUID, int], OmieLancamentoData] = TTLCache(
            maxsize=l1_maxsize,
            ttl=ttl_seconds,
        )

    # ------------------------------------------------------------------
    # Lookup (read path)
    # ------------------------------------------------------------------

    async def get_many(
        self,
        *,
        client_id: UUID,
        omie_ids: list[int],
    ) -> dict[int, OmieLancamentoData]:
        """Resolve IDs lookup-only (sem refetch).

        Lê o L1; IDs sem cache ficam fora do dict. Caller decide se chama
        `populate_from_extrato` em seguida.

        Args:
            client_id: cliente BPO. Faz parte da chave para não vazar entre
                clientes (CLAUDE.md §3.11 — isolamento por carteira).
            omie_ids: lista de IDs (positivos). Lista vazia → dict vazio.

        Returns:
            Dict `{omie_id: OmieLancamentoData}` apenas com IDs presentes no
            cache. IDs sem cache não aparecem (sem KeyError).
        """
        if not omie_ids:
            return {}

        found: dict[int, OmieLancamentoData] = {}
        for oid in omie_ids:
            data = self._l1_get(client_id, oid)
            if data is not None:
                found[oid] = data

        log.info(
            "omie_lancamento_cache_lookup",
            client_id=str(client_id),
            requested=len(omie_ids),
            hits=len(found),
            misses=len(omie_ids) - len(found),
        )
        return found

    # ------------------------------------------------------------------
    # Populate (write path)
    # ------------------------------------------------------------------

    async def populate_from_extrato(
        self,
        *,
        client_id: UUID,
        omie_client: OmieClient,
        omie_conta_id: int,
        period_start: date,
        period_end: date,
    ) -> dict[int, OmieLancamentoData]:
        """Busca `ListarExtrato` no período e popula o L1.

        O Omie não tem endpoint by-id; usamos o extrato no período da sessão
        (já expandido pela tolerância do caller). Reusa o cache para
        chamadas posteriores no mesmo processo.

        Args:
            client_id: para a chave do cache.
            omie_client: cliente já autenticado (via `build_omie_client`).
            omie_conta_id: nCodCC da sessão.
            period_start/period_end: período já expandido com tolerância.

        Returns:
            Dict `{omie_id: data}` de TUDO que veio do extrato — caller
            tipicamente filtra pelos IDs que ele queria.
        """
        raw = await omie_client.listar_extrato(
            n_cod_cc=omie_conta_id,
            data_inicial=period_start,
            data_final=period_end,
        )
        result: dict[int, OmieLancamentoData] = {}
        for item in raw:
            data = OmieLancamentoData.from_lancamento(item)
            result[data.omie_id] = data
            self._l1_put(client_id, data.omie_id, data)

        log.info(
            "omie_lancamento_cache_populated",
            client_id=str(client_id),
            omie_conta_id=omie_conta_id,
            count=len(result),
        )
        return result

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _l1_get(self, client_id: UUID, omie_id: int) -> OmieLancamentoData | None:
        """Lookup L1 — TTLCache trata expiração e LRU sozinho."""
        return self._l1.get((client_id, omie_id))

    def _l1_put(self, client_id: UUID, omie_id: int, data: OmieLancamentoData) -> None:
        self._l1[(client_id, omie_id)] = data
