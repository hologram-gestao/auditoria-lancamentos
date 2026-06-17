"""Testes unitários do OmieLancamentoCache (S11 BACK 9.2).

Foco: comportamento determinístico do cache L1 (in-memory) — hit, miss,
populate via mock OmieClient, isolamento por client_id, teto LRU.

Não sobe Postgres. Sem Redis desde a FASE 0: o cache é L1-only (in-process).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.integrations.omie.schemas import LancamentoExtrato


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
            "nCodLancamento": omie_id,
            "cNatureza": natureza,
            "dDataLancamento": f"{dia:02d}/04/2026",
            "nValorDocumento": Decimal(valor),
            "cObservacoes": descr,
            "cRazCliente": fornecedor,
            "cDesCategoria": categoria,
            "cSituacao": status,
        }
    )


# ----------------------------------------------------------------------
# get_many sem entrada
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_many_empty_list_returns_empty_dict() -> None:
    cache = OmieLancamentoCache()
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
    cache = OmieLancamentoCache()

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
    cache = OmieLancamentoCache()

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
    cache = OmieLancamentoCache()
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
    cache = OmieLancamentoCache()

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
# Item 1 (hardening S11): L1 com upper bound (TTLCache LRU)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_l1_cap_evicts_oldest_when_maxsize_exceeded() -> None:
    """Escrever maxsize+1 entries deve manter apenas maxsize via LRU."""
    client_id = uuid4()
    # Cache pequeno para a asserção rodar rápido. O default (10_000) está
    # validado pela lógica idêntica do cachetools.TTLCache; o objetivo deste
    # teste é provar que o teto **é aplicado**, não revalidar o cachetools.
    cache = OmieLancamentoCache(l1_maxsize=3)

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
