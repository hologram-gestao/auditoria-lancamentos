"""Cache HIT de `/omie-data/lancamentos` não constrói client (S9 / BACK 09.6 — retrabalho R1).

A migração da 09.6 inverteu o contrato da fábrica nesta rota: o client passou a
ser construído ANTES da chamada e entregue como `lambda: omie_client`. O serviço
só invoca a fábrica no MISS, e o `aclose()` mora no `finally` logo abaixo —
então, em cache HIT, o `OmieClient` (e o `httpx.AsyncClient` dentro dele) nascia
e **nunca era fechado**. Num processo Cloud Run de vida longa
(`min-instances>=1` + `--no-cpu-throttling`, §10) isso é vazamento de conexão a
cada request, e esta é a rota que a tela de revisão chama repetidamente.

Junto vinham outras duas: o unwrap da DEK no Cloud KMS em TODA request, e um 409
de origem em request que o cache resolveria sozinho.

Nenhum teste distinguia hit de miss aqui. Estes distinguem — e a fábrica é
espiã: conta invocações e fechamentos.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.integrations.omie.lancamento_cache import OmieLancamentoData
from app.modules.omie_data.service import OmieLancamentoService

pytestmark = pytest.mark.unit

SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")
CLIENT_ID = UUID("22222222-2222-4222-8222-222222222222")


def _dado(omie_id: int) -> OmieLancamentoData:
    return OmieLancamentoData(
        omie_id=omie_id,
        transaction_date=date(2026, 4, 10),
        description="Compra",
        amount=Decimal("100.00"),
        supplier=None,
        category=None,
        status="conciliado",
    )


class _SessionStub:
    client_id = CLIENT_ID
    omie_conta_id = 4214850
    reference_month = date(2026, 4, 1)


class _RepoStub:
    """O mínimo que o serviço usa do `ReviewRepository`."""

    async def get_session(self, session_id: UUID) -> _SessionStub:
        assert session_id == SESSION_ID
        return _SessionStub()

    @staticmethod
    def expand_period(period_start: date, period_end: date, tolerance_days: int) -> Any:
        from datetime import timedelta

        return (
            period_start - timedelta(days=tolerance_days),
            period_end + timedelta(days=tolerance_days),
        )


class _CacheStub:
    """Devolve o que foi semeado; registra se o refetch foi acionado."""

    def __init__(self, cached: dict[int, OmieLancamentoData]) -> None:
        self._cached = cached
        self.populated = 0

    async def get_many(self, *, client_id: UUID, omie_ids: list[int]) -> Any:
        assert client_id == CLIENT_ID
        return {oid: self._cached[oid] for oid in omie_ids if oid in self._cached}

    async def populate_from_extrato(self, **kwargs: Any) -> dict[int, OmieLancamentoData]:
        self.populated += 1
        return {oid: _dado(oid) for oid in (7, 8, 9)}


class _EspiaDeFabrica:
    """Conta quantos clients foram construídos e quantos foram fechados."""

    def __init__(self) -> None:
        self.construidos = 0
        self.fechados = 0

    async def __call__(self) -> Any:
        self.construidos += 1
        espia = self

        class _Client:
            async def aclose(self) -> None:
                espia.fechados += 1

        return _Client()

    @property
    def abertos(self) -> int:
        return self.construidos - self.fechados


def _service(cache: _CacheStub) -> OmieLancamentoService:
    return OmieLancamentoService(_RepoStub(), cache)  # type: ignore[arg-type]


class TestCacheHit:
    async def test_hit_total_nao_constroi_client_nenhum(self) -> None:
        """Todos os IDs em cache: a fábrica NÃO é chamada.

        É a asserção que o `lambda: omie_client` não conseguia satisfazer — lá
        o client já vinha construído, invocada a fábrica ou não.
        """
        cache = _CacheStub({1: _dado(1), 2: _dado(2)})
        fabrica = _EspiaDeFabrica()

        items = await _service(cache).fetch_lancamentos(
            session_id=SESSION_ID, omie_ids=[1, 2], omie_client_factory=fabrica
        )

        assert [i.omie_id for i in items] == [1, 2]
        assert fabrica.construidos == 0
        assert cache.populated == 0
        # E nada ficou aberto — o vazamento da reprovação.
        assert fabrica.abertos == 0

    async def test_miss_constroi_uma_vez_e_fecha(self) -> None:
        """A outra ponta: no MISS a fábrica é usada, e o `finally` fecha."""
        cache = _CacheStub({7: _dado(7)})
        fabrica = _EspiaDeFabrica()

        items = await _service(cache).fetch_lancamentos(
            session_id=SESSION_ID, omie_ids=[7, 8], omie_client_factory=fabrica
        )

        assert {i.omie_id for i in items} == {7, 8}
        assert fabrica.construidos == 1
        assert cache.populated == 1
        assert fabrica.abertos == 0

    async def test_o_erro_da_origem_nao_estoura_no_hit(self) -> None:
        """Consequência 3 da reprovação: cliente com cache quente e origem
        removida continua sendo servido pelo cache, sem 409."""
        cache = _CacheStub({1: _dado(1)})

        async def fabrica_que_recusa() -> Any:
            raise AssertionError("a origem não pode ser resolvida em cache hit")

        items = await _service(cache).fetch_lancamentos(
            session_id=SESSION_ID, omie_ids=[1], omie_client_factory=fabrica_que_recusa
        )

        assert [i.omie_id for i in items] == [1]


class TestContratoDaFabrica:
    def test_os_dois_servicos_declaram_a_mesma_forma(self) -> None:
        """Foi a divergência de assinatura (um síncrono, outro `Awaitable`) que
        abriu espaço para o caller construir o client por fora."""
        import inspect

        from app.modules.omie_data.categorias_service import OmieCategoriasService

        def anotacao(func: Any) -> str:
            return str(inspect.signature(func).parameters["omie_client_factory"].annotation)

        assert anotacao(OmieLancamentoService.fetch_lancamentos) == anotacao(
            OmieCategoriasService.list_categorias
        )
