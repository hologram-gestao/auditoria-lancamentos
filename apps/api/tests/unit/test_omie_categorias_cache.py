"""Cache de categorias por cliente (Sprint 7 / BACK 07.3).

O que precisa ser verdade:
  - **segunda chamada dentro do TTL não bate na Omie** (o motivo do cache é o
    combobox de classificação: uma ida à Omie por tecla digitada seria o
    gargalo que a suposição S-3 do PRD teme);
  - **o cache é POR CLIENTE** — servir a lista de A para B seria vazamento de
    vocabulário contábil entre tenants;
  - **erro da Omie não vira lista vazia** — a Omie responde HTTP 200 com
    `faultstring`, e transformar isso em `[]` faria o operador concluir que o
    cadastro dele está vazio.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.core.exceptions import OmieAuthError, OmieFaultError, OmieTimeoutError
from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.integrations.omie.schemas import CategoriaOmie
from app.modules.omie_data.categorias_service import OmieCategoriasService

_CATEGORIAS = [
    CategoriaOmie.model_validate({"codigo": "2.02.01", "descricao": "Energia elétrica"}),
    CategoriaOmie.model_validate({"codigo": "1.01.01", "descricao": "Vendas de produtos"}),
    CategoriaOmie.model_validate(
        {"codigo": "9.99.99", "descricao": "Desativada", "conta_inativa": "S"}
    ),
]


class _CountingOmieClient:
    """Conta chamadas em vez de ir à rede — é o que prova o cache hit."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls = 0
        self.closed = 0
        self._raises = raises

    async def listar_categorias(self) -> list[CategoriaOmie]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return list(_CATEGORIAS)

    async def aclose(self) -> None:
        self.closed += 1


def _factory(client: _CountingOmieClient) -> Any:
    async def build() -> Any:
        return client

    return build


@pytest.mark.unit
class TestCacheAvoidsTheSecondCall:
    async def test_second_call_within_ttl_does_not_hit_omie(self) -> None:
        client_id = uuid4()
        omie = _CountingOmieClient()
        service = OmieCategoriasService(OmieCategoriasCache())

        first = await service.list_categorias(
            client_id=client_id, omie_client_factory=_factory(omie)
        )
        second = await service.list_categorias(
            client_id=client_id, omie_client_factory=_factory(omie)
        )

        assert omie.calls == 1, "a 2ª chamada dentro do TTL foi à Omie"
        assert first == second

    async def test_refresh_forces_a_new_call(self) -> None:
        client_id = uuid4()
        omie = _CountingOmieClient()
        service = OmieCategoriasService(OmieCategoriasCache())

        await service.list_categorias(client_id=client_id, omie_client_factory=_factory(omie))
        await service.list_categorias(
            client_id=client_id, omie_client_factory=_factory(omie), refresh=True
        )

        assert omie.calls == 2

    async def test_expired_entry_is_refetched(self) -> None:
        """TTL zerado: o cache não segura nada e a Omie é consultada de novo."""
        client_id = uuid4()
        omie = _CountingOmieClient()
        service = OmieCategoriasService(OmieCategoriasCache(ttl_seconds=0))

        await service.list_categorias(client_id=client_id, omie_client_factory=_factory(omie))
        await service.list_categorias(client_id=client_id, omie_client_factory=_factory(omie))

        assert omie.calls == 2


@pytest.mark.unit
class TestCacheIsPerClient:
    async def test_one_tenant_does_not_serve_anothers_list(self) -> None:
        cache = OmieCategoriasCache()
        service = OmieCategoriasService(cache)
        tenant_a, tenant_b = uuid4(), uuid4()
        omie = _CountingOmieClient()

        await service.list_categorias(client_id=tenant_a, omie_client_factory=_factory(omie))
        await service.list_categorias(client_id=tenant_b, omie_client_factory=_factory(omie))

        assert omie.calls == 2, "o tenant B foi servido com o cache do tenant A"

    async def test_invalidate_only_touches_the_given_tenant(self) -> None:
        cache = OmieCategoriasCache()
        tenant_a, tenant_b = uuid4(), uuid4()
        cache.set(tenant_a, list(_CATEGORIAS))
        cache.set(tenant_b, list(_CATEGORIAS))

        cache.invalidate(tenant_a)

        assert cache.get(tenant_a) is None
        assert cache.get(tenant_b) is not None


@pytest.mark.unit
class TestProviderErrorsAreNeverAnEmptyList:
    @pytest.mark.parametrize(
        "error",
        [
            OmieAuthError("credencial inválida"),
            OmieTimeoutError("sem resposta"),
            OmieFaultError("faultstring com HTTP 200"),
        ],
        ids=["auth", "timeout", "fault"],
    )
    async def test_error_propagates_instead_of_returning_empty(self, error: Exception) -> None:
        omie = _CountingOmieClient(raises=error)
        service = OmieCategoriasService(OmieCategoriasCache())

        with pytest.raises(type(error)):
            await service.list_categorias(client_id=uuid4(), omie_client_factory=_factory(omie))

    async def test_failure_is_not_cached(self) -> None:
        """Um erro não pode envenenar o cache: a tentativa seguinte reconsulta."""
        client_id = uuid4()
        failing = _CountingOmieClient(raises=OmieTimeoutError("sem resposta"))
        service = OmieCategoriasService(OmieCategoriasCache())

        with pytest.raises(OmieTimeoutError):
            await service.list_categorias(
                client_id=client_id, omie_client_factory=_factory(failing)
            )

        healthy = _CountingOmieClient()
        items = await service.list_categorias(
            client_id=client_id, omie_client_factory=_factory(healthy)
        )
        assert healthy.calls == 1
        assert items

    async def test_client_is_closed_even_on_failure(self) -> None:
        omie = _CountingOmieClient(raises=OmieFaultError("erro"))
        service = OmieCategoriasService(OmieCategoriasCache())
        with pytest.raises(OmieFaultError):
            await service.list_categorias(client_id=uuid4(), omie_client_factory=_factory(omie))
        assert omie.closed == 1


@pytest.mark.unit
class TestListShape:
    async def test_inactive_categories_are_not_offered(self) -> None:
        service = OmieCategoriasService(OmieCategoriasCache())
        items = await service.list_categorias(
            client_id=uuid4(), omie_client_factory=_factory(_CountingOmieClient())
        )
        assert [i.codigo for i in items] == ["2.02.01", "1.01.01"]

    async def test_sorted_by_description(self) -> None:
        """O combobox lista em ordem legível — não na ordem de chegada da Omie."""
        service = OmieCategoriasService(OmieCategoriasCache())
        items = await service.list_categorias(
            client_id=uuid4(), omie_client_factory=_factory(_CountingOmieClient())
        )
        assert [i.descricao for i in items] == ["Energia elétrica", "Vendas de produtos"]

    def test_unknown_conta_inativa_value_keeps_the_category(self) -> None:
        """Se o nome/valor do campo divergir, o filtro NÃO some com o catálogo.

        Falhar para o lado de mostrar demais é recuperável; sumir com as
        categorias trava o lançamento inteiro do cliente.
        """
        categoria = CategoriaOmie.model_validate(
            {"codigo": "X", "descricao": "Y", "conta_inativa": "talvez"}
        )
        assert categoria.is_active is True
        assert CategoriaOmie.model_validate({"codigo": "X", "descricao": "Y"}).is_active is True
