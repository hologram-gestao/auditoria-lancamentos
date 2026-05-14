"""Cache hierárquico (L1 + L2) de lançamentos Omie individuais (S11 BACK 9.2).

Por que existe (Doc §5.3 + CLAUDE.md §4.5):
    Nenhum dado identificável de cliente final (fornecedor, categoria, etc) pode
    persistir em claro no DB. Mas a Tela de Revisão precisa exibir esses
    metadados nas duas abas (Movimentações e Divergências Omie). A solução é
    cache TTL — caro de buscar do Omie, barato de servir; sumir do storage
    após o TTL satisfaz a regra de "nenhum dado em claro".

L1 (in-memory):
    Dict `(client_id, omie_id) -> (data, expires_at)`. Single-process — em
    deploy multi-worker (uvicorn --workers N + ARQ) cada processo tem seu
    próprio L1, mas a coerência é mantida pelo L2 compartilhado. Valor
    aceitável pro MVP — escalável horizontalmente quando virar gargalo.

L2 (Redis):
    Chave `omie_lancamento:{client_id}:{omie_id}`, `SETEX` 7200s (2h).
    Sobrevive a restart do processo. Em testes/dev sem Redis, o constructor
    aceita `redis=None` e degrada graciosamente — só L1.

Limitação importante do Omie:
    Não existe endpoint `Consultar1LancamentoPorId`. Tudo passa por
    `ListarExtrato(omie_conta_id, periodo)`. Para popular o cache precisamos
    do contexto da SESSÃO (que conhece `omie_conta_id`, `period_start`,
    `period_end`, `tolerance_days`), não apenas dos IDs avulsos.

Serialização Redis:
    JSON com keys snake_case + Decimal como string ("1234.56"). `date` como
    ISO 8601. Decoded de volta em `_decode_redis_entry`.

NÃO logar:
    Nunca emitir `supplier`, `category`, `description`, valor. Logar apenas
    contadores e flags ("hit", "miss", "populated_count").
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.logging import get_logger
from app.integrations.omie.client import OmieClient
from app.integrations.omie.schemas import LancamentoExtrato, OmieEntryNatureza

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger(__name__)

# TTL: 2h conforme PLAN_IMPLEMENTACAO S11 e Doc §5.3.
DEFAULT_TTL_SECONDS = 7200
_REDIS_KEY_PREFIX = "omie_lancamento"


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

    def to_dict(self) -> dict[str, Any]:
        """Serializa em dict JSON-safe (Decimal → str, date → ISO)."""
        return {
            "omie_id": self.omie_id,
            "transaction_date": self.transaction_date.isoformat(),
            "description": self.description,
            "amount": str(self.amount),
            "supplier": self.supplier,
            "category": self.category,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OmieLancamentoData:
        """Reconstrói a partir do dict JSON do L2."""
        return cls(
            omie_id=int(raw["omie_id"]),
            transaction_date=date.fromisoformat(raw["transaction_date"]),
            description=str(raw.get("description") or ""),
            amount=Decimal(str(raw["amount"])),
            supplier=raw.get("supplier"),
            category=raw.get("category"),
            status=str(raw["status"]),
        )

    @classmethod
    def from_lancamento(cls, item: LancamentoExtrato) -> OmieLancamentoData:
        """Converte `LancamentoExtrato` (Omie) → DTO normalizado.

        Normalização do sinal (CLAUDE.md §5.6): débito vira negativo.
        """
        amount = item.n_valor_lanc
        if item.c_natureza == OmieEntryNatureza.DEBITO.value:
            amount = -amount
        return cls(
            omie_id=item.n_cod_lanc,
            transaction_date=item.d_dt_lanc,
            description=item.c_descr_lanc or "",
            amount=amount,
            supplier=item.c_fornecedor,
            category=item.c_categ,
            status=item.c_status,
        )


# Factory type para o OmieClient (recebe client_id, retorna OmieClient pronto)
OmieClientFactory = Callable[[UUID], "OmieClient"]


class OmieLancamentoCache:
    """Cache L1 + L2 de lançamentos Omie individuais.

    Compartilhado entre BACK 9.2, 9.4 e 9.5. A instância pode ser singleton
    no app (L1 in-memory faz sentido por processo); cada chamada recebe o
    `OmieClient` correspondente via factory.
    """

    def __init__(
        self,
        *,
        redis: Redis | None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        # L1: chave (client_id, omie_id) → (data, expires_at_monotonic)
        self._l1: dict[tuple[UUID, int], tuple[OmieLancamentoData, float]] = {}

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

        Lê L1 → falta no L1 → tenta L2 → falta no L2 fica fora do dict.
        Caller decide se chama `populate_from_extrato` em seguida.

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
        missing_in_l1: list[int] = []

        # L1
        for oid in omie_ids:
            data = self._l1_get(client_id, oid)
            if data is not None:
                found[oid] = data
            else:
                missing_in_l1.append(oid)

        if not missing_in_l1:
            log.debug(
                "omie_lancamento_cache_l1_full_hit",
                client_id=str(client_id),
                count=len(omie_ids),
            )
            return found

        # L2 — promove ao L1 quando encontra
        if self._redis is not None and missing_in_l1:
            l2_keys = [self._redis_key(client_id, oid) for oid in missing_in_l1]
            values = await self._redis.mget(*l2_keys)
            for oid, raw in zip(missing_in_l1, values, strict=True):
                if raw is None:
                    continue
                try:
                    data_dict = json.loads(raw)
                    data = OmieLancamentoData.from_dict(data_dict)
                except (ValueError, KeyError, TypeError):
                    # Payload corrompido — descarta silenciosamente. Próxima
                    # rodada vai re-popular via extrato. Não logamos o
                    # conteúdo (regra: nada de plaintext de dados Omie).
                    log.warning(
                        "omie_lancamento_cache_l2_corrupted",
                        client_id=str(client_id),
                        omie_id=oid,
                    )
                    continue
                found[oid] = data
                self._l1_put(client_id, oid, data)

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
        """Busca `ListarExtrato` no período e popula L1+L2.

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

        # L2 em batch (pipeline). Falha do Redis NÃO derruba a request — só
        # perde o benefício do L2 nessa rodada.
        if self._redis is not None and result:
            try:
                pipe = self._redis.pipeline()
                for oid, data in result.items():
                    pipe.setex(
                        self._redis_key(client_id, oid),
                        self._ttl,
                        json.dumps(data.to_dict()),
                    )
                await pipe.execute()
            except Exception as exc:
                log.warning(
                    "omie_lancamento_cache_l2_write_failed",
                    client_id=str(client_id),
                    count=len(result),
                    error=type(exc).__name__,
                )

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
        """Lookup L1 com expiração lazy (não roda evict thread)."""
        entry = self._l1.get((client_id, omie_id))
        if entry is None:
            return None
        data, expires_at = entry
        if time.monotonic() >= expires_at:
            self._l1.pop((client_id, omie_id), None)
            return None
        return data

    def _l1_put(self, client_id: UUID, omie_id: int, data: OmieLancamentoData) -> None:
        self._l1[(client_id, omie_id)] = (data, time.monotonic() + self._ttl)

    def _redis_key(self, client_id: UUID, omie_id: int) -> str:
        return f"{_REDIS_KEY_PREFIX}:{client_id}:{omie_id}"
