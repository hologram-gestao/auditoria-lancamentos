"""Testes unitários do OmieLancamentoCache (S11 BACK 9.2).

Foco: comportamento determinístico do cache — L1 hit, L1 miss → L2,
populate via mock OmieClient, isolamento por client_id.

Não sobe Postgres nem Redis. Usa stub Redis in-memory que implementa só
`mget`, `pipeline().setex().execute()`. Suficiente pra cobrir os caminhos.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.integrations.omie.lancamento_cache import (
    OmieLancamentoCache,
    OmieLancamentoData,
)
from app.integrations.omie.schemas import LancamentoExtrato


class _StubRedisPipeline:
    """Pipeline que coleta SETEX e aplica de uma vez."""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store
        self._ops: list[tuple[str, str]] = []

    def setex(self, key: str, _ttl: int, value: str) -> _StubRedisPipeline:
        self._ops.append((key, value))
        return self

    async def execute(self) -> None:
        for key, value in self._ops:
            self._store[key] = value
        self._ops.clear()


class _StubRedis:
    """Mínimo necessário pra exercitar `OmieLancamentoCache`."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.mget_calls = 0

    async def mget(self, *keys: str) -> list[str | None]:
        self.mget_calls += 1
        return [self.store.get(k) for k in keys]

    def pipeline(self) -> _StubRedisPipeline:
        return _StubRedisPipeline(self.store)


class _FailingMgetRedis(_StubRedis):
    """Igual ao stub padrão, mas `mget` levanta — simula Redis offline."""

    async def mget(self, *keys: str) -> list[str | None]:
        self.mget_calls += 1
        raise ConnectionError("redis offline (simulado)")


class _StubOmieClient:
    """Substitui `OmieClient` — só implementa `listar_extrato`. Conta chamadas."""

    def __init__(self, items: list[LancamentoExtrato]) -> None:
        self.items = items
        self.call_count = 0
        self.last_period: tuple[date, date] | None = None

    async def listar_extrato(
        self,
        *,
        n_cod_cc: int,
        data_inicial: date,
        data_final: date,
    ) -> list[LancamentoExtrato]:
        self.call_count += 1
        self.last_period = (data_inicial, data_final)
        return self.items

    async def aclose(self) -> None:
        return None


def _make_lancamento(
    *,
    omie_id: int,
    natureza: str = "D",
    valor: str = "100.00",
    descr: str = "Compra fornecedor",
    fornecedor: str | None = "Fornecedor X",
    categoria: str | None = "Insumos",
    status: str = "Conciliado",
    dia: int = 10,
) -> LancamentoExtrato:
    return LancamentoExtrato.model_validate(
        {
            "nCodLanc": omie_id,
            "cNatureza": natureza,
            "dDtLanc": f"{dia:02d}/04/2026",
            "nValorLanc": Decimal(valor),
            "cDescrLanc": descr,
            "cFornecedor": fornecedor,
            "cCateg": categoria,
            "cStatus": status,
        }
    )


# ----------------------------------------------------------------------
# get_many sem entrada
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_many_empty_list_returns_empty_dict() -> None:
    cache = OmieLancamentoCache(redis=None)
    result = await cache.get_many(client_id=uuid4(), omie_ids=[])
    assert result == {}


# ----------------------------------------------------------------------
# L1 e populate
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_populate_caches_in_l1_and_subsequent_lookup_hits_only_l1() -> None:
    """População via extrato deve servir todas as chamadas seguintes do L1."""
    client_id = uuid4()
    omie_client = _StubOmieClient([_make_lancamento(omie_id=1001)])
    cache = OmieLancamentoCache(redis=None)

    populated = await cache.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    assert set(populated.keys()) == {1001}

    # 1ª chamada — L1 hit
    result_1 = await cache.get_many(client_id=client_id, omie_ids=[1001])
    assert 1001 in result_1

    # 2ª chamada — não força nova chamada Omie
    result_2 = await cache.get_many(client_id=client_id, omie_ids=[1001])
    assert 1001 in result_2

    # populate só rodou uma vez (na inicialização)
    assert omie_client.call_count == 1


@pytest.mark.asyncio
async def test_get_many_partial_hit_returns_only_found() -> None:
    """IDs não-cacheados ficam fora do dict (sem KeyError)."""
    client_id = uuid4()
    omie_client = _StubOmieClient([_make_lancamento(omie_id=2001)])
    cache = OmieLancamentoCache(redis=None)

    await cache.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    result = await cache.get_many(client_id=client_id, omie_ids=[2001, 9999])
    assert set(result.keys()) == {2001}


@pytest.mark.asyncio
async def test_client_isolation_l1_does_not_leak_between_clients() -> None:
    """Cache é chaveado por client_id — não vaza entre clientes."""
    client_a = uuid4()
    client_b = uuid4()
    cache = OmieLancamentoCache(redis=None)
    omie_a = _StubOmieClient([_make_lancamento(omie_id=3001)])

    await cache.populate_from_extrato(
        client_id=client_a,
        omie_client=omie_a,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    # cliente B procura mesmo ID → cache miss (não vê o L1 do A)
    result_b = await cache.get_many(client_id=client_b, omie_ids=[3001])
    assert result_b == {}


# ----------------------------------------------------------------------
# Sinal débito vs crédito
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_natureza_debito_normalizes_to_negative_amount() -> None:
    client_id = uuid4()
    omie_client = _StubOmieClient(
        [
            _make_lancamento(omie_id=4001, natureza="D", valor="100.00"),
            _make_lancamento(omie_id=4002, natureza="C", valor="50.00"),
        ]
    )
    cache = OmieLancamentoCache(redis=None)

    populated = await cache.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    assert populated[4001].amount == Decimal("-100.00")
    assert populated[4002].amount == Decimal("50.00")


# ----------------------------------------------------------------------
# L2 (Redis) — fallback quando L1 não tem
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_serves_after_l1_eviction() -> None:
    """Simula reset do L1 (novo processo): L2 ainda serve o lookup."""
    client_id = uuid4()
    redis_stub = _StubRedis()

    # Cache "antigo" popula L2 via pipeline
    cache_a = OmieLancamentoCache(redis=redis_stub)  # type: ignore[arg-type]
    omie_client = _StubOmieClient([_make_lancamento(omie_id=5001)])
    await cache_a.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    # Cache "novo" (simulando processo reiniciado) — só vê L2.
    cache_b = OmieLancamentoCache(redis=redis_stub)  # type: ignore[arg-type]
    result = await cache_b.get_many(client_id=client_id, omie_ids=[5001])

    assert 5001 in result
    assert result[5001].omie_id == 5001
    assert redis_stub.mget_calls == 1
    # Após o get_many, L1 do cache_b foi populado a partir do L2 — próxima
    # chamada não toca o Redis novamente.
    await cache_b.get_many(client_id=client_id, omie_ids=[5001])
    assert redis_stub.mget_calls == 1  # ainda 1


@pytest.mark.asyncio
async def test_l2_corrupted_payload_skipped_gracefully() -> None:
    client_id = uuid4()
    redis_stub = _StubRedis()
    redis_stub.store[f"omie_lancamento:{client_id}:6001"] = "not valid json"
    cache = OmieLancamentoCache(redis=redis_stub)  # type: ignore[arg-type]
    result = await cache.get_many(client_id=client_id, omie_ids=[6001])
    assert result == {}  # corrupted → silently skipped


# ----------------------------------------------------------------------
# Serialização Redis (Decimal e date)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redis_roundtrip_preserves_decimal_and_date() -> None:
    client_id = uuid4()
    redis_stub = _StubRedis()
    cache_a = OmieLancamentoCache(redis=redis_stub)  # type: ignore[arg-type]
    omie_client = _StubOmieClient(
        [
            _make_lancamento(
                omie_id=7001,
                natureza="D",
                valor="1234.56",
                dia=20,
            )
        ]
    )
    await cache_a.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )
    # Checa que o JSON cru está bem formado
    raw = redis_stub.store[f"omie_lancamento:{client_id}:7001"]
    parsed: dict[str, Any] = json.loads(raw)
    assert parsed["amount"] == "-1234.56"  # Decimal como string
    assert parsed["transaction_date"] == "2026-04-20"  # ISO

    # Lê via novo cache (sem L1) — Decimal precisa voltar como Decimal
    cache_b = OmieLancamentoCache(redis=redis_stub)  # type: ignore[arg-type]
    result = await cache_b.get_many(client_id=client_id, omie_ids=[7001])
    assert result[7001].amount == Decimal("-1234.56")
    assert isinstance(result[7001].amount, Decimal)
    assert result[7001].transaction_date == date(2026, 4, 20)


# ----------------------------------------------------------------------
# OmieLancamentoData round-trip
# ----------------------------------------------------------------------


def test_omie_lancamento_data_to_dict_from_dict_roundtrip() -> None:
    """Cobertura mínima para a serialização (independente do Redis stub)."""
    original = OmieLancamentoData(
        omie_id=8001,
        transaction_date=date(2026, 4, 15),
        description="Pagamento",
        amount=Decimal("-456.78"),
        supplier="Fornecedor Y",
        category="Aluguel",
        status="Conciliado",
    )
    recovered = OmieLancamentoData.from_dict(original.to_dict())
    assert recovered.omie_id == 8001
    assert recovered.transaction_date == date(2026, 4, 15)
    assert recovered.amount == Decimal("-456.78")
    assert recovered.supplier == "Fornecedor Y"
    assert recovered.category == "Aluguel"
    assert recovered.status == "Conciliado"


# ----------------------------------------------------------------------
# Item 1 (hardening S11): L1 com upper bound (TTLCache LRU)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_cap_evicts_oldest_when_maxsize_exceeded() -> None:
    """Escrever maxsize+1 entries deve manter apenas maxsize via LRU."""
    client_id = uuid4()
    # Cache pequeno para a asserção rodar rápido. O default (10_000) está
    # validado pela lógica idêntica do cachetools.TTLCache; o objetivo deste
    # teste é provar que o teto **é aplicado**, não revalidar o cachetools.
    cache = OmieLancamentoCache(redis=None, l1_maxsize=3)

    # Popula via API pública — usa um stub que devolve N lançamentos distintos.
    items = [_make_lancamento(omie_id=9000 + i, dia=10) for i in range(4)]
    omie_client = _StubOmieClient(items)
    await cache.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    # Após 4 entries com maxsize=3, o L1 deve guardar exatamente 3.
    assert len(cache._l1) == 3


# ----------------------------------------------------------------------
# Item 2 (hardening S11): falha no Redis read não derruba a request
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_read_failure_falls_back_to_l1_only() -> None:
    """`mget` levantando exception → degrada para L1 silenciosamente."""
    client_id = uuid4()
    redis_stub = _FailingMgetRedis()
    cache = OmieLancamentoCache(redis=redis_stub)  # type: ignore[arg-type]

    # Popula só o L1 (pulando o Redis) escrevendo um item via API pública.
    # `populate_from_extrato` chama `pipe.execute` — o pipeline da classe
    # base não falha; só `mget` falha. Assim o L1 contém 6001 mas o L2 está
    # "offline" para leituras subsequentes.
    omie_client = _StubOmieClient([_make_lancamento(omie_id=6001)])
    await cache.populate_from_extrato(
        client_id=client_id,
        omie_client=omie_client,  # type: ignore[arg-type]
        omie_conta_id=42,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )

    # ID presente no L1 → resolvido sem tocar Redis.
    result_l1 = await cache.get_many(client_id=client_id, omie_ids=[6001])
    assert 6001 in result_l1
    assert redis_stub.mget_calls == 0

    # ID ausente do L1 → tenta Redis, `mget` levanta, função NÃO propaga.
    result_miss = await cache.get_many(client_id=client_id, omie_ids=[6002])
    assert result_miss == {}
    assert redis_stub.mget_calls == 1  # tentou exatamente uma vez
