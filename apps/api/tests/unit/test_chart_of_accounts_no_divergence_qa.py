"""R5 (QA 10.6) — a tela nova e a tela de revisão NÃO podem divergir de nome.

O PRD da Sprint 10 tem um invariante que nenhuma das tasks de execução pode
provar sozinha, porque ele é sobre a relação entre **dois consumidores**:

    "Um único caminho de leitura da origem; a persistência nova não duplica a
     chamada" · "o caminho de leitura existente (tela de revisão) não pode
     divergir desta persistência".

Os dois consumidores são:

    - **tela de revisão** → `GET /api/v1/omie/categorias` →
      `OmieCategoriasService.list_categorias` → `OmieCategoriaItem.descricao`;
    - **tela do plano de contas** → `GET /clients/{id}/chart-of-accounts` →
      `ChartOfAccountsSyncService.resolve_names`, que lê
      `OmieCategoriasService.list_raw_categorias` e monta
      `{c.codigo: c.descricao}`.

Este arquivo trava as TRÊS coisas que fazem a divergência ser impossível:

  1. **um cache, uma chamada** — os dois consumidores, atendidos em sequência
     pelo MESMO serviço, produzem `calls == 1` na origem. Se alguém criar uma
     segunda leitura, este número vira 2 e o teste cai;
  2. **mesmo código, mesmo nome** — para todo código que os dois enxergam, a
     descrição é byte a byte a mesma, porque sai do MESMO objeto `CategoriaOmie`;
  3. **a persistência não tem de onde divergir** — a linha gravada
     (`chart_of_accounts_row`) não carrega nenhum nome. Não é só política da
     §4.5: é o que impede a tela nova de ter uma segunda fonte de nome para
     começar a discordar da primeira.

Vale como teste UNITÁRIO de propósito: o que está sob prova é o contrato entre
as duas camadas de leitura, que não depende de banco, de rota nem de
autorização — e a bateria de integração da sprint já cobre as rotas.

⚠️ O desencontro de NAMESPACE é o quarto caso: na resposta real a categoria
`1.01.01` chama-se "BPO Controller - RB" e a conta de demonstrativo `1.01.01`
chama-se "Receita Bruta de Vendas". Um mapa só `código → nome` mostraria o nome
errado na coluna de destino e a tela pareceria perfeitamente certa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.integrations.omie.categorias_cache import OmieCategoriasCache
from app.integrations.omie.schemas import CategoriaOmie
from app.modules.client_chart_of_accounts.schemas import chart_of_accounts_row
from app.modules.omie_data.categorias_service import OmieCategoriasService

pytestmark = pytest.mark.unit

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "omie" / "listar_categorias.response.json"
)

#: Nomes que NUNCA podem aparecer numa linha persistida (§4.5). São os três
#: valores de NOME que a resposta real carrega: descrição da categoria,
#: descrição da conta de demonstrativo e rótulo da conta contábil.
_NAME_FIELDS = ("descricao", "descricaoDRE", "tag_conta_contabil")


def _fixture_categorias() -> list[CategoriaOmie]:
    """As 50 categorias REAIS da conta Hologram, como DTO."""
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    items = payload["categoria_cadastro"]
    assert isinstance(items, list)
    assert len(items) == 50, "a fixture real tem 50 categorias — ver ADR-061-BE"
    return [CategoriaOmie.model_validate(item) for item in items]


class _CountingOmieClient:
    """Conta idas à origem. É o número que decide se há UM caminho ou dois."""

    def __init__(self, categorias: list[CategoriaOmie]) -> None:
        self.calls = 0
        self._categorias = categorias

    async def listar_categorias(self) -> list[CategoriaOmie]:
        self.calls += 1
        return list(self._categorias)

    async def aclose(self) -> None:  # pragma: no cover - o serviço fecha o cliente
        return None


def _factory(client: _CountingOmieClient) -> Any:
    async def build() -> Any:
        return client

    return build


@pytest.mark.unit
class TestUmCaminhoSoAteAOrigem:
    async def test_os_dois_consumidores_gastam_uma_unica_chamada(self) -> None:
        """Revisão primeiro, plano de contas depois: `calls == 1`.

        A ordem importa. Quem popula o cache aqui é a tela ANTIGA; se a
        persistência nova tivesse um fetch próprio, ela não aproveitaria o hit
        e o contador iria a 2.
        """
        client_id = uuid4()
        omie = _CountingOmieClient(_fixture_categorias())
        service = OmieCategoriasService(OmieCategoriasCache())

        await service.list_categorias(client_id=client_id, omie_client_factory=_factory(omie))
        await service.list_raw_categorias(client_id=client_id, omie_client_factory=_factory(omie))

        assert omie.calls == 1

    async def test_tambem_na_ordem_inversa(self) -> None:
        """Plano de contas primeiro, revisão depois — o cache é o mesmo."""
        client_id = uuid4()
        omie = _CountingOmieClient(_fixture_categorias())
        service = OmieCategoriasService(OmieCategoriasCache())

        await service.list_raw_categorias(client_id=client_id, omie_client_factory=_factory(omie))
        await service.list_categorias(client_id=client_id, omie_client_factory=_factory(omie))

        assert omie.calls == 1

    async def test_o_cache_continua_sendo_por_tenant(self) -> None:
        """A leitura crua não pode servir o catálogo de um cliente para outro.

        Regressão possível ao acrescentar um acessor: reusar a chave errada no
        cache vazaria vocabulário contábil entre tenants (§3.15).
        """
        omie = _CountingOmieClient(_fixture_categorias())
        service = OmieCategoriasService(OmieCategoriasCache())

        await service.list_raw_categorias(client_id=uuid4(), omie_client_factory=_factory(omie))
        await service.list_raw_categorias(client_id=uuid4(), omie_client_factory=_factory(omie))

        assert omie.calls == 2


@pytest.mark.unit
class TestMesmoCodigoMesmoNome:
    async def test_a_descricao_e_identica_nos_dois_consumidores(self) -> None:
        """Para todo código que as duas telas enxergam, o nome é o MESMO."""
        client_id = uuid4()
        omie = _CountingOmieClient(_fixture_categorias())
        service = OmieCategoriasService(OmieCategoriasCache())

        revisao = await service.list_categorias(
            client_id=client_id, omie_client_factory=_factory(omie)
        )
        cruas = await service.list_raw_categorias(
            client_id=client_id, omie_client_factory=_factory(omie)
        )

        # É exatamente o mapa que `ChartOfAccountsSyncService.resolve_names`
        # monta para a coluna "Nome" da tela nova.
        nomes_da_tela_nova = {c.codigo: c.descricao for c in cruas}
        nomes_da_revisao = {item.codigo: item.descricao for item in revisao}

        assert nomes_da_revisao, "a revisão precisa enxergar categorias na fixture real"
        for codigo, descricao in nomes_da_revisao.items():
            assert nomes_da_tela_nova[codigo] == descricao, (
                f"divergência de nome no código {codigo}: "
                f"revisão={descricao!r} plano de contas={nomes_da_tela_nova[codigo]!r}"
            )

    async def test_a_leitura_crua_enxerga_as_inativas_que_a_revisao_filtra(self) -> None:
        """Mesmo catálogo, filtros diferentes — e isso é intencional.

        O combobox de classificação não pode oferecer categoria inativa; o plano
        de contas precisa dela (situação `inativa`, e pode haver de-para na
        Sprint 12). "Não divergir" é sobre o NOME do mesmo código, não sobre o
        recorte que cada tela mostra.
        """
        client_id = uuid4()
        omie = _CountingOmieClient(_fixture_categorias())
        service = OmieCategoriasService(OmieCategoriasCache())

        revisao = await service.list_categorias(
            client_id=client_id, omie_client_factory=_factory(omie)
        )
        cruas = await service.list_raw_categorias(
            client_id=client_id, omie_client_factory=_factory(omie)
        )

        assert len(cruas) == 50
        assert len(revisao) == 46, "4 categorias da fixture têm conta_inativa='S' (ADR-061-BE)"
        assert {item.codigo for item in revisao} <= {c.codigo for c in cruas}

    async def test_categoria_e_conta_de_demonstrativo_sao_namespaces_diferentes(self) -> None:
        """`1.01.01` é duas coisas com nomes diferentes — um mapa só erraria."""
        categorias = _fixture_categorias()
        alvo = next(c for c in categorias if c.codigo == "1.01.01")

        assert alvo.descricao == "BPO Controller - RB"
        assert alvo.destino_code == "1.01.01"
        assert alvo.dados_dre.descricao_dre == "Receita Bruta de Vendas"
        assert alvo.dados_dre.descricao_dre != alvo.descricao


@pytest.mark.unit
class TestAPersistenciaNaoTemDeOndeDivergir:
    def test_nenhuma_linha_da_fixture_real_carrega_nome(self) -> None:
        """Varre as 50 linhas: nenhum valor de NOME atravessa o mapeador.

        A checagem é por VALOR, e não por nome de coluna: alguém enfiando a
        descrição num campo de código (`dre_sign`, `parent_code`) passaria por
        uma inspeção de schema e seria pego aqui.
        """
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        proibidos = {
            str(item[campo]).strip()
            for item in payload["categoria_cadastro"]
            for campo in ("descricao", "tag_conta_contabil")
            if str(item.get(campo) or "").strip()
        } | {
            str((item.get("dadosDRE") or {}).get("descricaoDRE") or "").strip()
            for item in payload["categoria_cadastro"]
            if str((item.get("dadosDRE") or {}).get("descricaoDRE") or "").strip()
        }
        assert proibidos, "a fixture real precisa ter nomes para este teste significar algo"

        for categoria in _fixture_categorias():
            valores = {str(v) for v in chart_of_accounts_row(categoria).values() if v is not None}
            vazados = valores & proibidos
            assert not vazados, f"nome persistido na linha de {categoria.codigo}: {vazados}"

    def test_a_linha_nao_declara_nenhuma_chave_de_nome(self) -> None:
        """Cinto do teste acima: nem o NOME da chave aparece no mapa gravado."""
        chaves = set(chart_of_accounts_row(_fixture_categorias()[0]))
        assert not chaves & set(_NAME_FIELDS)
        assert not {k for k in chaves if "descricao" in k or "name" in k or "tag" in k}
