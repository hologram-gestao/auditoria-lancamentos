"""O registry de categorias do arquivo — a parte PURA e a leitura do de-para (BACK 14.4).

`split_labels` é o casamento: igualdade de `str`, byte a byte — `Aluguel`,
`aluguel` e `Aluguél` são três categorias. E a listagem do de-para resolve o nome
das linhas `arquivo` pelo registry, fail-soft, com `[indecifrável]` marcado como
NÃO resolvido. O registry contra o banco (criação, cifra, cross-tenant, purga) está
em `tests/integration/test_client_file_categories.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.db.models import Client, MappingDestination
from app.modules.client_chart_of_accounts.schemas import ResolvedNames
from app.modules.client_file_categories.registry import (
    FILE_CATEGORY_UNDECIPHERABLE,
    ResolvedFileCategoryNames,
    split_labels,
)
from app.modules.client_mapping.listing import ClientMappingListService, MappingRow
from app.modules.client_mapping.service import DecisionView

pytestmark = pytest.mark.unit

ORG = uuid4()


class TestSplitLabels:
    def test_conhecidos_e_novos_em_ordem_de_primeira_ocorrencia(self) -> None:
        known, missing = split_labels(
            {"Aluguel": "arq-aaaaaaaaaaaa"},
            ["Energia", "Aluguel", "Energia", "Água"],
        )
        assert known == {"Aluguel": "arq-aaaaaaaaaaaa"}
        assert missing == ["Energia", "Água"]

    def test_grafias_diferentes_nao_se_encontram(self) -> None:
        """Caso negativo do R4: nenhum caminho funde `Aluguel`/`aluguel`/`Aluguél`."""
        known, missing = split_labels(
            {"Aluguel": "arq-aaaaaaaaaaaa"}, ["aluguel", "Aluguél", " Aluguel", "ALUGUEL"]
        )
        assert known == {}
        assert missing == ["aluguel", "Aluguél", " Aluguel", "ALUGUEL"]

    def test_o_mesmo_rotulo_duas_vezes_nao_cria_nada(self) -> None:
        known, missing = split_labels({"Aluguel": "arq-aaaaaaaaaaaa"}, ["Aluguel", "Aluguel"])
        assert known == {"Aluguel": "arq-aaaaaaaaaaaa"}
        assert missing == []

    def test_vazio(self) -> None:
        assert split_labels({}, []) == ({}, [])


# ---------------------------------------------------------------------------
# A leitura do de-para resolve o nome das linhas `arquivo` pelo registry
# ---------------------------------------------------------------------------


def _destination() -> MappingDestination:
    return MappingDestination(
        id=uuid4(),
        organization_id=ORG,
        destination_type="demonstrativo_contabil",
        name="Demonstrativo",
        active=True,
    )


class _Repo:
    def __init__(self, movements: set[tuple[str, str]]) -> None:
        self.movements = movements

    async def chart_category_codes(self, client_id: UUID) -> list[str]:
        return []

    async def movement_category_keys(self, client_id: UUID) -> set[tuple[str, str]]:
        return self.movements

    async def list_decisions(self, client_id: UUID, destination_id: UUID) -> list[Any]:
        return []


class _Decisions:
    def __init__(self, destination: MappingDestination) -> None:
        self.destination = destination

    async def resolve_destination(self, client: Any, kind: str) -> MappingDestination:
        return self.destination

    async def vigentes(self, *a: Any) -> dict[tuple[str, str], DecisionView]:
        return {}


class _Catalog:
    async def get_targets_by_codes(self, destination_id: UUID, codes: Any) -> dict[str, Any]:
        return {}


class _Names:
    async def resolve_names(self, client: Any) -> ResolvedNames:
        return ResolvedNames(categories={}, dre={})


class _FileNames:
    def __init__(self, resolved: ResolvedFileCategoryNames, *, fail: bool = False) -> None:
        self.resolved = resolved
        self.fail = fail
        self.calls = 0

    async def resolve_names(self, client: Any) -> ResolvedFileCategoryNames:
        self.calls += 1
        if self.fail:
            raise RuntimeError("DEK indisponível")
        return self.resolved


def _client() -> Client:
    client = Client(id=uuid4(), name="C", active=True, created_by=uuid4())
    client.organization_id = ORG
    return client


def _service(
    movements: set[tuple[str, str]], file_names: _FileNames | None
) -> tuple[ClientMappingListService, MappingDestination]:
    destination = _destination()
    service = ClientMappingListService(
        _Repo(movements),  # type: ignore[arg-type]
        decisions=_Decisions(destination),  # type: ignore[arg-type]
        catalog=_Catalog(),  # type: ignore[arg-type]
        names=_Names(),
        file_names=file_names,
    )
    return service, destination


async def _page(service: ClientMappingListService) -> list[Any]:
    rows, _, _ = await service.page(
        _client(),
        "demonstrativo_contabil",
        situation=None,
        code_prefix=None,
        page=1,
        page_size=20,
    )
    return rows


class TestLeituraResolveRotuloDoArquivo:
    async def test_linha_arquivo_sai_sem_decisao_com_a_grafia_original(self) -> None:
        file_names = _FileNames(ResolvedFileCategoryNames(names={"arq-1": "Aluguél "}))
        service, _ = _service({("arquivo", "arq-1"), ("omie", "2.01")}, file_names)
        rows = await _page(service)
        arquivo = next(r for r in rows if r.row.source_type == "arquivo")
        assert arquivo.row.situation == "sem_decisao"
        assert arquivo.row.category_code == "arq-1"
        assert arquivo.category_name == "Aluguél "  # grafia ORIGINAL, sem normalizar
        assert arquivo.category_name_resolved is True
        assert arquivo.row.origin_dre_code is None  # sem herança para arquivo
        omie = next(r for r in rows if r.row.source_type == "omie")
        assert omie.category_name is None

    async def test_indecifravel_sai_marcado_e_nao_resolvido(self) -> None:
        file_names = _FileNames(
            ResolvedFileCategoryNames(
                names={"arq-1": FILE_CATEGORY_UNDECIPHERABLE}, failed=frozenset({"arq-1"})
            )
        )
        service, _ = _service({("arquivo", "arq-1")}, file_names)
        (row,) = await _page(service)
        assert row.category_name == "[indecifrável]"
        assert row.category_name_resolved is False

    async def test_registry_fora_do_ar_e_fail_soft(self) -> None:
        service, _ = _service(
            {("arquivo", "arq-1")}, _FileNames(ResolvedFileCategoryNames(), fail=True)
        )
        (row,) = await _page(service)
        assert row.category_name is None
        assert row.category_name_resolved is False

    async def test_sem_registry_montado_a_linha_sai_com_o_codigo(self) -> None:
        service, _ = _service({("arquivo", "arq-1")}, None)
        (row,) = await _page(service)
        assert row.category_name is None
        assert row.category_name_resolved is False

    async def test_sem_linha_arquivo_o_registry_nem_e_consultado(self) -> None:
        file_names = _FileNames(ResolvedFileCategoryNames(names={"arq-1": "X"}))
        service, _ = _service({("omie", "2.01")}, file_names)
        await _page(service)
        assert file_names.calls == 0


class TestRowSemHeranca:
    def test_mapping_row_de_arquivo_nao_tem_dre(self) -> None:
        row = MappingRow(
            source_type="arquivo",
            category_code="arq-1",
            situation="sem_decisao",
            decision_type=None,
            target_code=None,
            effective_from=None,
            divergent=False,
            origin_dre_code=None,
        )
        assert row.origin_dre_code is None
        assert datetime.now(UTC) is not None
