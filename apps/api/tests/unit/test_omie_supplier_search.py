"""Fornecedor pelo NOME e sugestão pela descrição da compra (86e3qhhrd).

O que estes testes travam:

- `OmieClient.listar_clientes`: o `param` é o da busca por CNPJ CAPTURADA sem o
  `clientesFiltro` (mesmo método, mesma página), pagina pelo `total_de_paginas`,
  e o `5113` de diretório vazio vira `[]`;
- `search_directory`: "contém" por pedaço, sem acento e sem caixa, só ativos, quem
  começa pelo texto primeiro, teto e contagem;
- `suggest_for_description`: a MESMA `supplier_affinity` do cruzamento, zero
  tokens não sugere nada;
- `OmieFornecedorDirectoryService`: buscas simultâneas no primeiro acesso fazem
  UMA leitura da Omie (a trava por cliente), `refresh` relê;
- o pedido de lançamento: `codigo_fornecedor` só na provisão, exclusivo com o
  CNPJ, e dentro do teto de BIGINT.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from app.core.config import get_settings
from app.integrations.omie.client import LISTAR_CLIENTES_PAGE_SIZE, OmieClient, OmieCredentials
from app.integrations.omie.fornecedores_directory_cache import OmieFornecedoresDirectoryCache
from app.integrations.omie.schemas import OMIE_CODE_MAX, ClienteOmie
from app.modules.clients.schemas import UpdateClientRequest
from app.modules.omie_data.payables_service import (
    SUPPLIER_SEARCH_LIMIT,
    OmieFornecedorDirectoryService,
    normalize_search_text,
    search_directory,
    suggest_for_description,
)
from app.modules.reconciliations.omie_posting.schemas import OmiePostingLineRequest

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "omie"


def _fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((_FIXTURES / f"{name}.json").read_text("utf-8"))
    return payload


def _cadastro(
    codigo: int,
    razao: str | None,
    *,
    fantasia: str | None = None,
    cnpj: str | None = None,
    inativo: str = "N",
) -> ClienteOmie:
    return ClienteOmie(
        codigo_cliente_omie=codigo,
        razao_social=razao,
        nome_fantasia=fantasia,
        cnpj_cpf=cnpj,
        inativo=inativo,
    )


DIRETORIO = [
    _cadastro(1, "PAPELARIA DEMO LTDA", fantasia="Papelaria Demo", cnpj="12.345.678/0001-95"),
    _cadastro(2, "POSTO DEMO COMBUSTÍVEIS LTDA", cnpj="98.765.432/0001-98"),
    _cadastro(3, "SOFTWARE DEMO GMBH", fantasia="Software Demo", cnpj=""),
    _cadastro(4, "PAPELARIA ANTIGA DEMO LTDA", cnpj="44.556.677/0001-86", inativo="S"),
    _cadastro(5, "COMERCIAL DA PAPELARIA SA", cnpj="11.222.333/0001-81"),
]


class TestListarClientes:
    @respx.mock
    async def test_paginates_with_the_captured_param_without_filter(self) -> None:
        client = OmieClient(
            credentials=OmieCredentials(app_key=SecretStr("k"), app_secret=SecretStr("s")),
            settings=get_settings(),
        )
        captured = _fixture("listar_clientes_por_cnpj.request")["param"]
        page1 = {**_fixture("listar_clientes_por_cnpj.response"), "total_de_paginas": 2}
        page2 = {
            **page1,
            "pagina": 2,
            "clientes_cadastro": [
                {
                    "codigo_cliente_omie": 9,
                    "razao_social": "EXTERIOR GMBH",
                    "cnpj_cpf": "",
                    "inativo": "N",
                }
            ],
        }
        route = respx.post("https://app.omie.com.br/api/v1/geral/clientes/").mock(
            side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
        )

        items = await client.listar_clientes()

        expected = {k: v for k, v in captured.items() if k != "clientesFiltro"}
        sent = json.loads(route.calls[0].request.content)["param"][0]
        assert sent == expected
        assert sent["registros_por_pagina"] == LISTAR_CLIENTES_PAGE_SIZE
        assert json.loads(route.calls[1].request.content)["param"][0]["pagina"] == 2
        assert [c.codigo_cliente_omie for c in items] == [3052086626, 9]
        # O CNPJ da resposta real chega ao modelo (máscara como a Omie grava).
        assert items[0].cnpj_cpf == "00.394.460/0001-00"

    @respx.mock
    async def test_5113_on_an_empty_directory_is_empty(self) -> None:
        client = OmieClient(
            credentials=OmieCredentials(app_key=SecretStr("k"), app_secret=SecretStr("s")),
            settings=get_settings(),
        )
        respx.post("https://app.omie.com.br/api/v1/geral/clientes/").mock(
            return_value=httpx.Response(
                500, headers={"OmieAPI-Error": "5113 - Nao existem registros para a pagina [1]!"}
            )
        )
        assert await client.listar_clientes() == []


class TestSearchDirectory:
    def test_contains_by_piece_ignoring_accent_case_and_order(self) -> None:
        items, total = search_directory(DIRETORIO, "demo combustiveis")
        assert [i.codigo for i in items] == [2]
        assert total == 1

    def test_inactive_stays_out(self) -> None:
        items, _ = search_directory(DIRETORIO, "antiga")
        assert items == []

    def test_starts_with_comes_before_contains(self) -> None:
        items, total = search_directory(DIRETORIO, "papelaria")
        # 1 começa por "PAPELARIA"; 5 só contém. O 4 é inativo.
        assert [i.codigo for i in items] == [1, 5]
        assert total == 2

    def test_cnpj_digits_also_match(self) -> None:
        items, _ = search_directory(DIRETORIO, "98765")
        assert [i.codigo for i in items] == [2]

    def test_supplier_without_cnpj_comes_with_null_cnpj(self) -> None:
        [item], _ = search_directory(DIRETORIO, "software")
        assert item.codigo == 3
        assert item.cnpj_cpf is None

    def test_limit_and_total(self) -> None:
        many = [_cadastro(100 + n, f"LOJA DEMO {n:03d}") for n in range(SUPPLIER_SEARCH_LIMIT + 5)]
        items, total = search_directory(many, "loja")
        assert len(items) == SUPPLIER_SEARCH_LIMIT
        assert total == SUPPLIER_SEARCH_LIMIT + 5
        assert items[0].razao_social == "LOJA DEMO 000"

    def test_blank_query_finds_nothing(self) -> None:
        assert search_directory(DIRETORIO, " .-") == ([], 0)

    def test_normalization(self) -> None:
        assert normalize_search_text("  Papelária-Demo  LTDA. ") == "PAPELARIA DEMO LTDA"


class TestSuggestForDescription:
    def test_only_the_best_affinity_comes_back(self) -> None:
        """Os outros cadastros "DEMO" têm 1 token em comum; o de software, 2."""
        sugestoes = suggest_for_description(DIRETORIO, "SOFTWARE DEMO*ASSINATURA 3/12")
        assert [s.codigo for s in sugestoes] == [3]

    def test_ties_on_the_best_affinity_all_come_back(self) -> None:
        sugestoes = suggest_for_description(DIRETORIO, "COMPRA DEMO 0001")
        assert [s.codigo for s in sugestoes] == [1, 2, 3]

    def test_more_tokens_in_common_first(self) -> None:
        sugestoes = suggest_for_description(DIRETORIO, "POSTO DEMO COMBUSTIVEIS BR")
        assert sugestoes[0].codigo == 2

    def test_no_token_in_common_suggests_nothing(self) -> None:
        assert suggest_for_description(DIRETORIO, "CAFETERIA DO LARGO") == []

    def test_inactive_and_missing_description(self) -> None:
        assert all(s.codigo != 4 for s in suggest_for_description(DIRETORIO, "PAPELARIA ANTIGA"))
        assert suggest_for_description(DIRETORIO, None) == []


class TestDirectoryService:
    async def test_concurrent_first_searches_read_omie_once(self) -> None:
        cache = OmieFornecedoresDirectoryCache()
        service = OmieFornecedorDirectoryService(cache)
        client_id = uuid4()
        reads: list[int] = []

        class _Fake:
            async def listar_clientes(self) -> list[ClienteOmie]:
                reads.append(1)
                await asyncio.sleep(0.01)
                return list(DIRETORIO)

            async def aclose(self) -> None:
                return None

        async def factory() -> Any:
            return _Fake()

        results = await asyncio.gather(
            *(service.directory(client_id=client_id, omie_client_factory=factory) for _ in range(5))
        )

        assert len(reads) == 1
        assert all(len(r) == len(DIRETORIO) for r in results)
        await service.directory(client_id=client_id, omie_client_factory=factory, refresh=True)
        assert len(reads) == 2


class TestPostingLineCodigoFornecedor:
    def test_chosen_code_is_accepted_in_the_provision(self) -> None:
        line = OmiePostingLineRequest(
            file_entry_id=uuid4(), cod_categoria="2.01.03", codigo_fornecedor=700_000_003
        )
        assert line.codigo_fornecedor == 700_000_003

    @pytest.mark.parametrize(
        "extra",
        [
            {"codigo_fornecedor": 1, "cnpj_fornecedor": "12.345.678/0001-95"},
            {"codigo_fornecedor": 1, "modo": "direto"},
            {"codigo_fornecedor": 0},
            {"codigo_fornecedor": OMIE_CODE_MAX + 1},
        ],
        ids=["com-cnpj", "no-direto", "zero", "acima-do-bigint"],
    )
    def test_invalid_combinations(self, extra: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            OmiePostingLineRequest(file_entry_id=uuid4(), cod_categoria="2.01.03", **extra)

    def test_default_supplier_code_above_bigint_is_refused(self) -> None:
        """O 500 latente vizinho: o fornecedor padrão do cliente aceitava qualquer
        inteiro, e acima do BIGINT o banco estourava."""
        with pytest.raises(ValidationError):
            UpdateClientRequest(omie_default_supplier_code=OMIE_CODE_MAX + 1)
        assert (
            UpdateClientRequest(omie_default_supplier_code=OMIE_CODE_MAX).omie_default_supplier_code
            == OMIE_CODE_MAX
        )
