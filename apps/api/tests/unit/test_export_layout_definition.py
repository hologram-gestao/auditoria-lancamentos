"""A definição do layout de exportação: vocabulário, validação e o modelo Domínio (BACK 13.2).

Três promessas testadas sem banco:
1. o modelo Domínio bate, parâmetro a parâmetro, com a tabela do layout do PRD (lida da
   amostra real `lancamentos_esperados.csv`);
2. o vocabulário de campos é FECHADO e ÚNICO (a mesma enum que o gerador consome);
3. toda definição que não se sustenta é 422 `LAYOUT_INVALIDO` NOMEANDO o campo pelo
   caminho do JSON — e nunca um `ValueError` solto que viraria 500 ou 400 mudo.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.core.exceptions import ExportLayoutDefinitionError
from app.modules.export_layouts.definition import (
    DOMINIO_TEMPLATE,
    TEMPLATES,
    LayoutField,
    LineEnding,
    date_format_to_strftime,
    parse_definition,
)


def _raw(**over: Any) -> dict[str, Any]:
    raw = copy.deepcopy(DOMINIO_TEMPLATE.definition.to_json())
    raw.update(over)
    return raw


def _amount(**over: Any) -> dict[str, Any]:
    raw = _raw()
    raw["amountFormat"].update(over)
    return raw


def _field_of(raw: dict[str, Any]) -> str:
    with pytest.raises(ExportLayoutDefinitionError) as exc:
        parse_definition(raw)
    assert exc.value.status_code == 422
    assert exc.value.code.value == "LAYOUT_INVALIDO"
    field = exc.value.details["field"]
    assert isinstance(field, str)
    return field


class TestModeloDominio:
    """A tabela "O layout do arquivo" do PRD, célula a célula."""

    def test_colunas_e_ordem(self) -> None:
        assert [c.field for c in DOMINIO_TEMPLATE.definition.columns] == [
            LayoutField.DATA,
            LayoutField.CONTA_DEBITO,
            LayoutField.CONTA_CREDITO,
            LayoutField.VALOR,
            LayoutField.HISTORICO,
        ]

    def test_parametros_do_arquivo(self) -> None:
        d = DOMINIO_TEMPLATE.definition
        assert d.separator == ";"
        assert d.has_header is False
        assert d.encoding == "latin-1"
        assert d.line_ending is LineEnding.CRLF
        assert d.line_ending.chars == "\r\n"
        assert d.date_format == "dd/mm/aaaa"
        assert d.strftime == "%d/%m/%Y"

    def test_formato_do_valor(self) -> None:
        a = DOMINIO_TEMPLATE.definition.amount_format
        assert a.prefix == "R$ "
        assert a.thousands_separator == "."
        assert a.decimal_separator == ","
        assert a.decimal_places == 2

    def test_nome_e_sistema(self) -> None:
        assert DOMINIO_TEMPLATE.name == "Domínio: lançamentos contábeis (CSV)"
        assert DOMINIO_TEMPLATE.target_system == "Domínio"
        assert TEMPLATES == {"dominio_lancamentos_csv": DOMINIO_TEMPLATE}

    def test_ida_e_volta_pela_validacao(self) -> None:
        """O modelo não tem atalho: passa pela MESMA validação e volta igual."""
        raw = DOMINIO_TEMPLATE.definition.to_json()
        assert parse_definition(raw) == DOMINIO_TEMPLATE.definition
        assert parse_definition(raw).to_json() == raw

    def test_forma_canonica_camelcase(self) -> None:
        assert DOMINIO_TEMPLATE.definition.to_json() == {
            "columns": [
                {"field": "data", "header": None},
                {"field": "conta_debito", "header": None},
                {"field": "conta_credito", "header": None},
                {"field": "valor", "header": None},
                {"field": "historico", "header": None},
            ],
            "separator": ";",
            "hasHeader": False,
            "encoding": "latin-1",
            "lineEnding": "crlf",
            "dateFormat": "dd/mm/aaaa",
            "amountFormat": {
                "prefix": "R$ ",
                "thousandsSeparator": ".",
                "decimalSeparator": ",",
                "decimalPlaces": 2,
            },
        }


class TestVocabularioFechado:
    def test_exatamente_os_sete_campos_do_prd(self) -> None:
        assert {f.value for f in LayoutField} == {
            "data",
            "conta_debito",
            "conta_credito",
            "valor",
            "historico",
            "competencia",
            "codigo_categoria_origem",
        }

    def test_todos_os_campos_sao_aceitos(self) -> None:
        raw = _raw(columns=[{"field": f.value, "header": None} for f in LayoutField])
        assert len(parse_definition(raw).columns) == len(LayoutField)

    @pytest.mark.parametrize("field", ["descricao", "Data", "valor_bruto", "", "cnpj"])
    def test_campo_fora_do_vocabulario_e_422_nomeando_a_coluna(self, field: str) -> None:
        raw = _raw()
        raw["columns"][2] = {"field": field, "header": None}
        assert _field_of(raw) == "columns[2].field"

    def test_a_mensagem_nomeia_o_campo_e_o_vocabulario(self) -> None:
        raw = _raw()
        raw["columns"][0] = {"field": "descricao", "header": None}
        with pytest.raises(ExportLayoutDefinitionError) as exc:
            parse_definition(raw)
        assert "'descricao'" in exc.value.user_message
        assert "coluna 1" in exc.value.user_message
        assert "conta_debito" in exc.value.user_message


class TestParametrosInvalidos:
    @pytest.mark.parametrize("encoding", ["latin-9000", "klingon", "base64", "rot13", "zlib", ""])
    def test_codificacao_que_o_python_nao_conhece_ou_nao_e_de_texto(self, encoding: str) -> None:
        assert _field_of(_raw(encoding=encoding)) == "encoding"

    @pytest.mark.parametrize("encoding", ["utf-8", "UTF-8", " latin-1 ", "cp1252", "iso-8859-1"])
    def test_codificacoes_reais_sao_aceitas_e_normalizadas(self, encoding: str) -> None:
        assert parse_definition(_raw(encoding=encoding)).encoding == encoding.strip().lower()

    @pytest.mark.parametrize("separator", ["", "\n", "\r\n", "a", "1"])
    def test_separador_invalido(self, separator: str) -> None:
        assert _field_of(_raw(separator=separator)) == "separator"

    def test_separador_igual_ao_decimal(self) -> None:
        assert _field_of(_raw(separator=",")) == "separator"

    @pytest.mark.parametrize("line_ending", ["cr", "", "windows", "\r\n"])
    def test_quebra_de_linha_invalida(self, line_ending: str) -> None:
        assert _field_of(_raw(lineEnding=line_ending)) == "lineEnding"

    @pytest.mark.parametrize(
        "date_format", ["dd/mm", "mm/aaaa", "dd/dd/aaaa", "dd/mm-aaaa", "yyyy-mm-dd", "", "d/m/a"]
    )
    def test_formato_de_data_invalido(self, date_format: str) -> None:
        assert _field_of(_raw(dateFormat=date_format)) == "dateFormat"

    @pytest.mark.parametrize("places", [-1, 0, 1, 5, 99])
    def test_casas_fora_da_faixa(self, places: int) -> None:
        assert _field_of(_amount(decimalPlaces=places)) == "amountFormat.decimalPlaces"

    @pytest.mark.parametrize("decimal", ["", ",,", "1", " ", "-"])
    def test_separador_decimal_invalido(self, decimal: str) -> None:
        assert _field_of(_amount(decimalSeparator=decimal)) == "amountFormat.decimalSeparator"

    def test_milhar_igual_ao_decimal(self) -> None:
        field = _field_of(_amount(thousandsSeparator=","))
        assert field == "amountFormat.thousandsSeparator"

    def test_milhar_igual_ao_separador_de_colunas(self) -> None:
        raw = _raw(separator=".")
        raw["amountFormat"]["thousandsSeparator"] = "."
        assert _field_of(raw) == "amountFormat.thousandsSeparator"

    @pytest.mark.parametrize("prefix", ["R$1", "R$;", "R$\n"])
    def test_prefixo_invalido(self, prefix: str) -> None:
        assert _field_of(_amount(prefix=prefix)) == "amountFormat.prefix"

    def test_prefixo_fora_da_codificacao(self) -> None:
        """`€` não existe em Latin-1: o prefixo nem chega ao arquivo."""
        assert _field_of(_amount(prefix="€ ")) == "amountFormat.prefix"

    @pytest.mark.parametrize("header", ["Data;Lançamento", "Data\nx", "Data — dia"])
    def test_cabecalho_com_separador_quebra_ou_fora_da_codificacao(self, header: str) -> None:
        raw = _raw(hasHeader=True)
        raw["columns"][0]["header"] = header
        assert _field_of(raw) == "columns[0].header"

    def test_sem_colunas(self) -> None:
        assert _field_of(_raw(columns=[])) == "columns"

    def test_sem_milhar_e_quatro_casas_sao_validos(self) -> None:
        """Menos de 2 casas arredondaria dinheiro (recusado acima); mais casas completam."""
        d = parse_definition(_amount(thousandsSeparator="", decimalPlaces=4, prefix=""))
        assert d.amount_format.thousands_separator == ""
        assert d.amount_format.decimal_places == 4


class TestFormatoDeData:
    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [
            ("dd/mm/aaaa", "%d/%m/%Y"),
            ("aaaa-mm-dd", "%Y-%m-%d"),
            ("dd.mm.aa", "%d.%m.%y"),
            ("ddmmaaaa", "%d%m%Y"),
            ("mm/dd/aaaa", "%m/%d/%Y"),
        ],
    )
    def test_tokens(self, fmt: str, expected: str) -> None:
        assert date_format_to_strftime(fmt) == expected

    @pytest.mark.parametrize("fmt", ["dd/mm/aaaa/aa", "aaaa/aa/mm", "dd-mm/aaaa", "dd mm aaaa"])
    def test_invalidos(self, fmt: str) -> None:
        assert date_format_to_strftime(fmt) is None


class TestColisoesComOSeparador:
    """Um texto FORMATADO pelo gerador nunca pode conter o separador de colunas."""

    def test_separador_da_data_no_separador_de_colunas(self) -> None:
        assert _field_of(_raw(separator="/")) == "dateFormat"

    def test_competencia_com_hifen_no_separador(self) -> None:
        raw = _raw(separator="-")
        raw["columns"].append({"field": "competencia", "header": None})
        assert _field_of(raw) == "separator"
