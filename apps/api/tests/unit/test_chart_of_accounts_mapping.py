"""Tradução categoria da origem → linha do plano de contas (BACK 10.1).

Cobre os dois casos negativos do R1, que são simétricos e fáceis de confundir:

    - **campo desconhecido** na resposta da origem: ignorado, a sincronização
      segue. A Omie acrescenta campo sem avisar, e derrubar a sincronização do
      cliente inteiro por causa de um campo que não usamos seria transformar
      uma melhoria deles num incidente nosso;
    - **campo declarado obrigatório ausente**: levanta erro NOMEANDO o campo, e
      nada é gravado. Sem `codigo` não existe identidade de linha — gravar o
      resto seria uma linha pela metade, que o de-para da Sprint 12 herdaria.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.db.models.client_chart_of_accounts import ChartOfAccountsStatus
from app.integrations.omie.schemas import CategoriaOmie
from app.modules.client_chart_of_accounts.schemas import chart_of_accounts_row

pytestmark = pytest.mark.unit

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "omie" / "listar_categorias.response.json"
)


def _fixture_categorias() -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    items = payload["categoria_cadastro"]
    assert isinstance(items, list)
    return items


class TestToleranciaDoContrato:
    def test_campo_desconhecido_nao_quebra(self) -> None:
        """Campo novo da origem é ignorado — comportamento padrão do Pydantic v2,
        confirmado aqui para que um `extra='forbid'` distraído não passe."""
        categoria = CategoriaOmie.model_validate(
            {
                "codigo": "1.01.01",
                "descricao": "Receita",
                "campo_que_a_omie_inventou_ontem": "qualquer coisa",
                "dadosDRE": {"codigoDRE": "1.01", "campoNovoDRE": 42},
            }
        )
        assert categoria.codigo == "1.01.01"
        assert categoria.destino_code == "1.01"

    @pytest.mark.parametrize("faltando", ["codigo", "descricao"])
    def test_campo_obrigatorio_ausente_levanta_erro_nomeando_o_campo(self, faltando: str) -> None:
        payload: dict[str, Any] = {"codigo": "1.01.01", "descricao": "Receita"}
        del payload[faltando]

        with pytest.raises(ValidationError) as exc:
            CategoriaOmie.model_validate(payload)

        erros = exc.value.errors()
        assert [e["loc"] for e in erros] == [(faltando,)], (
            "o erro precisa NOMEAR o campo — é o que a mensagem de falha da "
            "sincronização mostra a quem vai consertar o cadastro"
        )

    def test_bloco_de_demonstrativo_ausente_nao_e_erro(self) -> None:
        """13 das 50 categorias reais vêm com `dadosDRE: {}` e estão CORRETAS."""
        categoria = CategoriaOmie.model_validate({"codigo": "0.01", "descricao": "Transf."})
        assert categoria.destino_code is None
        assert categoria.dados_dre.nivel_dre is None

    def test_string_vazia_da_origem_vira_ausencia(self) -> None:
        """A origem manda `""`, não omite. `""` e ausente são o MESMO fato."""
        categoria = CategoriaOmie.model_validate(
            {
                "codigo": "0.01",
                "descricao": "Transferência",
                "categoria_superior": "",
                "id_conta_contabil": "",
                "dadosDRE": {"codigoDRE": ""},
            }
        )
        assert categoria.parent_code is None
        assert categoria.conta_contabil_code is None
        assert categoria.destino_code is None

    def test_raiz_da_hierarquia_nao_vira_codigo(self) -> None:
        """`categoria_superior='0'` é raiz; `'0'` não é uma categoria existente."""
        raiz = CategoriaOmie.model_validate(
            {"codigo": "1.01", "descricao": "Receitas", "categoria_superior": "0"}
        )
        filha = CategoriaOmie.model_validate(
            {"codigo": "1.01.01", "descricao": "Vendas", "categoria_superior": "1.01"}
        )
        assert raiz.parent_code is None
        assert filha.parent_code == "1.01"


class TestLinhaDoPlanoDeContas:
    def test_linha_carrega_codigos_flags_e_situacao(self) -> None:
        categoria = CategoriaOmie.model_validate(
            {
                "codigo": "1.02.01",
                "descricao": "Rendimento Aplicações Renda Fixa - RF",
                "categoria_superior": "1.02",
                "conta_inativa": "N",
                "id_conta_contabil": "3.1.3.01.00003",
                "tag_conta_contabil": "RENDIMENTOS APLICAÇÕES RENDA FIXA - FIN",
                "totalizadora": "N",
                "transferencia": "N",
                "nao_exibir": "N",
                "dadosDRE": {
                    "codigoDRE": "1.11.02",
                    "descricaoDRE": "Receitas Financeiras",
                    "nivelDRE": 3,
                    "sinalDRE": "+",
                },
            }
        )

        assert chart_of_accounts_row(categoria) == {
            "category_code": "1.02.01",
            "parent_code": "1.02",
            "dre_code": "1.11.02",
            "dre_level": 3,
            "dre_sign": "+",
            "conta_contabil_code": "3.1.3.01.00003",
            "totalizadora": False,
            "transferencia": False,
            "nao_exibir": False,
            "status": ChartOfAccountsStatus.ATIVA.value,
        }

    def test_linha_nao_carrega_nenhum_nome(self) -> None:
        """A prova de que a §4.5 sobrevive ao caminho de escrita.

        O DTO tem `descricao`, `descricaoDRE` e `tag_conta_contabil`; a linha
        gravada não pode ter nenhum dos três valores, em chave nenhuma.
        """
        nomes = (
            "Rendimento Aplicações Renda Fixa - RF",
            "Receitas Financeiras",
            "RENDIMENTOS APLICAÇÕES RENDA FIXA - FIN",
        )
        categoria = CategoriaOmie.model_validate(
            {
                "codigo": "1.02.01",
                "descricao": nomes[0],
                "tag_conta_contabil": nomes[2],
                "dadosDRE": {"codigoDRE": "1.11.02", "descricaoDRE": nomes[1]},
            }
        )

        gravado = chart_of_accounts_row(categoria)
        for nome in nomes:
            assert nome not in gravado.values(), f"nome vazou para o banco: {nome!r}"

    def test_categoria_inativa_vira_situacao_inativa(self) -> None:
        categoria = CategoriaOmie.model_validate(
            {"codigo": "1.02.02", "descricao": "Antiga", "conta_inativa": "S"}
        )
        assert chart_of_accounts_row(categoria)["status"] == ChartOfAccountsStatus.INATIVA.value

    def test_sem_destino_declarado_nao_e_inferido(self) -> None:
        """Transferência sem `codigoDRE` fica com destino NULO — nunca deduzido
        do pai, do código, nem de nada."""
        categoria = CategoriaOmie.model_validate(
            {
                "codigo": "0.01",
                "descricao": "Transferência",
                "categoria_superior": "0",
                "totalizadora": "S",
                "transferencia": "S",
                "nao_exibir": "S",
                "dadosDRE": {},
            }
        )
        linha = chart_of_accounts_row(categoria)
        assert linha["dre_code"] is None
        assert linha["totalizadora"] is True
        assert linha["transferencia"] is True
        assert linha["nao_exibir"] is True

    def test_fixture_real_inteira_vira_linhas_com_a_cobertura_esperada(self) -> None:
        """A contagem que a métrica da sprint vai ler, sobre a resposta REAL.

        ⚠️ Os números são os da fixture, e **não** os do exemplo do PRD ("50
        categorias · 50 ativas · 37 com destino"): a resposta capturada tem 4
        categorias com `conta_inativa='S'`, então são **46 ativas**, das quais
        33 com destino e 5 com conta contábil. O exemplo do PRD é ilustrativo;
        a fixture é a evidência. Ver `.claude/memory/decisions.md`.
        """
        categorias = [CategoriaOmie.model_validate(raw) for raw in _fixture_categorias()]
        linhas = [chart_of_accounts_row(c) for c in categorias]

        ativas = [row for row in linhas if row["status"] == ChartOfAccountsStatus.ATIVA.value]
        assert len(linhas) == 50
        assert len(ativas) == 46
        assert sum(1 for row in ativas if row["dre_code"] is not None) == 33
        assert sum(1 for row in ativas if row["conta_contabil_code"] is not None) == 5

        # Nenhuma linha pela metade: toda linha tem identidade.
        assert all(row["category_code"] for row in linhas)
        assert len({row["category_code"] for row in linhas}) == 50
