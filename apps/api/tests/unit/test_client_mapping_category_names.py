"""`resolve_category_names` — fonte ÚNICA de nome de categoria no de-para
(86e3fxqqh).

Prova, na camada mais barata (sem DB, resolvers falsos), que a MESMA função
resolve nome pra `omie` (plano de contas) e pra `arquivo` (registry cifrado),
fail-soft nas duas origens — é essa função que
`ClientMappingListService.with_names` (a lista) e
`ClientMappingApplyService._accounting_lines` (a prévia "Partida contábil",
destino `conta_contabil`) chamam, garantindo que as duas telas nunca possam
mostrar nomes diferentes pro mesmo código.
"""

from __future__ import annotations

from app.modules.client_chart_of_accounts.schemas import ResolvedNames
from app.modules.client_file_categories.registry import ResolvedFileCategoryNames
from app.modules.client_mapping.listing import resolve_category_names
from app.modules.client_mapping.service import CHART_SOURCE_TYPE

ARQUIVO = "arquivo"


class _FakeClient:
    id = "11111111-1111-1111-1111-111111111111"


class _OmieNames:
    def __init__(self, categories: dict[str, str]) -> None:
        self._categories = categories

    async def resolve_names(self, client: object) -> ResolvedNames:
        return ResolvedNames(categories=self._categories, dre={})


class _FailingOmieNames:
    async def resolve_names(self, client: object) -> ResolvedNames:
        raise RuntimeError("origem fora do ar")


class _FileNames:
    def __init__(self, result: ResolvedFileCategoryNames) -> None:
        self._result = result

    async def resolve_names(self, client: object) -> ResolvedFileCategoryNames:
        return self._result


async def test_resolve_categoria_omie_com_nome() -> None:
    names = await resolve_category_names(
        _OmieNames({"2.01": "Aluguel"}),
        None,
        _FakeClient(),
        [(CHART_SOURCE_TYPE, "2.01")],
    )
    assert names[(CHART_SOURCE_TYPE, "2.01")] == ("Aluguel", True)


async def test_resolve_categoria_omie_fora_do_ar_e_fail_soft() -> None:
    names = await resolve_category_names(
        _FailingOmieNames(), None, _FakeClient(), [(CHART_SOURCE_TYPE, "2.01")]
    )
    assert names[(CHART_SOURCE_TYPE, "2.01")] == (None, False)


async def test_resolve_categoria_arquivo_com_rotulo_decifrado() -> None:
    names = await resolve_category_names(
        _OmieNames({}),
        _FileNames(ResolvedFileCategoryNames(names={"aluguel-d": "Aluguel"}, failed=frozenset())),
        _FakeClient(),
        [(ARQUIVO, "aluguel-d")],
    )
    assert names[(ARQUIVO, "aluguel-d")] == ("Aluguel", True)


async def test_resolve_categoria_arquivo_indecifravel_nao_conta_como_resolvido() -> None:
    """`[indecifrável]` sai como nome (a tela mostra o marcador), mas
    `resolved=False` — quem chama sabe que não é um rótulo de verdade."""
    names = await resolve_category_names(
        _OmieNames({}),
        _FileNames(
            ResolvedFileCategoryNames(
                names={"aluguel-d": "[indecifrável]"}, failed=frozenset({"aluguel-d"})
            )
        ),
        _FakeClient(),
        [(ARQUIVO, "aluguel-d")],
    )
    assert names[(ARQUIVO, "aluguel-d")] == ("[indecifrável]", False)


async def test_resolve_sem_file_names_devolve_nao_resolvido_para_categoria_de_arquivo() -> None:
    names = await resolve_category_names(_OmieNames({}), None, _FakeClient(), [(ARQUIVO, "x")])
    assert names[(ARQUIVO, "x")] == (None, False)


async def test_resolve_mistura_as_duas_origens_na_mesma_chamada() -> None:
    names = await resolve_category_names(
        _OmieNames({"2.01": "Aluguel"}),
        _FileNames(ResolvedFileCategoryNames(names={"tarifa": "Tarifa"}, failed=frozenset())),
        _FakeClient(),
        [(CHART_SOURCE_TYPE, "2.01"), (ARQUIVO, "tarifa")],
    )
    assert names[(CHART_SOURCE_TYPE, "2.01")] == ("Aluguel", True)
    assert names[(ARQUIVO, "tarifa")] == ("Tarifa", True)
