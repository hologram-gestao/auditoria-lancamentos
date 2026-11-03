"""Reconhecimento das colunas do plano contábil pelo CONTEÚDO (86e3ydjyq).

O teste-ouro importa a FIXTURE anonimizada da Tract (planilha "ajustada" que não segue o
modelo nem o export do Domínio: banner, cabeçalho na linha 5, colunas `Natureza` e de
CNPJ a mais) e compara conta a conta com `plano_esperado.csv`. A prova de generalidade
é que as DUAS fixtures do Domínio, com o cabeçalho removido ou renomeado em memória,
chegam ao MESMO plano pelo reconhecedor, sem regra nenhuma de cabeçalho.

As recusas nomeiam só PAPÉIS (vocabulário nosso): nenhum texto do arquivo entra em
`details` nem na mensagem.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from openpyxl import Workbook, load_workbook
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import (
    ChartMappingRequiredError,
    FileHeaderMismatchError,
    FileLinesInvalidError,
    ValidationAppError,
)
from app.db.models.client_accounting_account import AccountingAccountType
from app.modules.client_accounting_chart import recognizer
from app.modules.client_accounting_chart.recognizer import (
    MIN_FILL_RATIO,
    MIN_FORM_RATIO,
    ColumnMapping,
    as_classification,
    as_integer,
    convert_by_columns,
    derive_type_by_hierarchy,
    is_document_like,
    recognize_columns,
)
from app.modules.client_accounting_chart.schemas import ChartColumnMappingRequest
from app.modules.client_accounting_chart.sheet import (
    MAX_PROBLEM_LINE_RATIO,
    MODEL_COLUMNS,
    parse_chart_sheet,
    refuse_unconfirmed_lines,
    validate_rows,
)
from app.modules.client_file_ingestion.reader import read_sheet_raw_rows

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_TRACT = _FIXTURES / "accounting_chart_tract" / "plano.xlsx"
_TRACT_EXPECTED = _FIXTURES / "accounting_chart_tract" / "plano_esperado.csv"
_DOMINIO = _FIXTURES / "accounting_chart_dominio" / "plano_dominio.xlsx"
_DOMINIO_EXPECTED = _FIXTURES / "accounting_chart_dominio" / "plano_esperado.csv"
_DOMINIO_XLS = _FIXTURES / "accounting_chart_dominio_xls" / "plano_dominio.xls"
_DOMINIO_XLS_EXPECTED = _FIXTURES / "accounting_chart_dominio_xls" / "plano_esperado.csv"

#: Textos de células das planilhas de teste que NUNCA podem sair numa recusa.
_CELL_TEXTS = ("ATIVO", "CAIXA", "Alfa", "Beta", "exemplo", "EXEMPLO", "Fantasia")


def _expected(path: Path) -> list[tuple[str, str, str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [
            (r["codigo_reduzido"], r["nome"], r["tipo"], r["classificacao"])
            for r in csv.DictReader(fh, delimiter=";")
        ]


def _got(rows: Any) -> list[tuple[str, str, str, str]]:
    return [(r.code, r.name, r.account_type.value, r.classification or "") for r in rows]


def _xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _assert_no_cell_text(error: Exception, *texts: str) -> None:
    rendered = repr(getattr(error, "details", {})) + getattr(error, "user_message", "")
    for text in (*_CELL_TEXTS, *texts):
        assert text not in rendered


def _by_recognizer(rows: list[tuple[int, list[Any]]]) -> list[Any]:
    recognition = recognize_columns(rows)
    assert recognition is not None
    assert recognition.resolved, recognition
    conversion = convert_by_columns(rows, recognition, recognition.roles)
    assert conversion.problems == []
    valid, problems = validate_rows(MODEL_COLUMNS, conversion.rows)
    assert problems == []
    return valid


class TestTesteOuroTract:
    def test_entra_inteira_e_bate_conta_a_conta(self) -> None:
        parsed = parse_chart_sheet(_TRACT.read_bytes())
        assert parsed.layout == "reconhecido"
        assert _got(parsed.rows) == _expected(_TRACT_EXPECTED)

    def test_1850_contas_180_sinteticas_todas_com_classificacao(self) -> None:
        rows = parse_chart_sheet(_TRACT.read_bytes()).rows
        assert len(rows) == 1850
        assert sum(r.account_type is AccountingAccountType.SINTETICA for r in rows) == 180
        assert all(r.classification for r in rows)
        assert rows[0].code == "1"
        assert rows[0].classification == "1"

    def test_natureza_e_documento_sao_reconhecidas_e_ignoradas(self) -> None:
        parsed = parse_chart_sheet(_TRACT.read_bytes())
        recognition = parsed.recognition
        assert recognition is not None
        assert recognition.recognized_roles == ["codigo", "classificacao", "tipo", "grau", "nome"]
        assert recognition.roles["natureza"] == (4,)
        assert recognition.roles["documento"] == (6,)
        assert recognition.ignored_columns == 2
        # Nenhum CNPJ nem natureza vira nome de conta.
        assert not any(r.name in {"Devedora", "Credora"} for r in parsed.rows)

    def test_o_perfil_das_colunas_nao_carrega_valor_de_celula(self) -> None:
        recognition = parse_chart_sheet(_TRACT.read_bytes()).recognition
        assert recognition is not None
        forms = [(c.index, c.form, c.suggested_role) for c in recognition.columns]
        assert forms == [
            (0, "inteiro", "codigo"),
            (1, "classificacao", "classificacao"),
            (2, "tipo_sa", "tipo"),
            (3, "inteiro", "grau"),
            (4, "natureza", "natureza"),
            (5, "texto", "nome"),
            (6, "documento", "documento"),
        ]
        assert recognition.columns[6].fill_percent == 14
        assert "exemplo" not in repr(recognition)


class TestGeneralidadeDominio:
    """As fixtures do Domínio pelo CONTEÚDO, sem o cabeçalho que as identifica."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [(_DOMINIO, _DOMINIO_EXPECTED), (_DOMINIO_XLS, _DOMINIO_XLS_EXPECTED)],
    )
    def test_sem_a_linha_do_cabecalho_chega_ao_mesmo_plano(
        self, path: Path, expected: Path
    ) -> None:
        rows = [
            (line, cells) for line, cells in read_sheet_raw_rows(path.read_bytes()) if line != 5
        ]
        assert _got(_by_recognizer(rows)) == _expected(expected)

    @pytest.mark.parametrize(
        ("path", "expected"),
        [(_DOMINIO, _DOMINIO_EXPECTED), (_DOMINIO_XLS, _DOMINIO_XLS_EXPECTED)],
    )
    def test_com_o_cabecalho_renomeado_chega_ao_mesmo_plano(
        self, path: Path, expected: Path
    ) -> None:
        rows = [
            (line, ["Coluna" if isinstance(c, str) else c for c in cells] if line == 5 else cells)
            for line, cells in read_sheet_raw_rows(path.read_bytes())
        ]
        assert _got(_by_recognizer(rows)) == _expected(expected)

    def test_xlsx_com_cabecalho_renomeado_importa_pela_porta_da_planilha(self) -> None:
        wb = load_workbook(io.BytesIO(_DOMINIO.read_bytes()))
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=5, max_row=5):
            for cell in row:
                if isinstance(cell.value, str):
                    cell.value = f"{cell.value} (conta)"
        buffer = io.BytesIO()
        wb.save(buffer)
        parsed = parse_chart_sheet(buffer.getvalue())
        assert parsed.layout == "reconhecido"
        assert _got(parsed.rows) == _expected(_DOMINIO_EXPECTED)

    def test_tipo_vazio_e_analitica_quando_a_coluna_so_marca_s(self) -> None:
        rows = read_sheet_raw_rows(_DOMINIO.read_bytes())
        recognition = recognize_columns(rows)
        assert recognition is not None
        assert recognition.empty_type_is_analytic is True


class TestRecusaQueNomeiaPapeis:
    """Desde a 86e3ydjz4, bloco de contas que o conteúdo não decide pede MAPEAMENTO (a
    frase do que foi reconhecido e do que falta segue a mesma)."""

    def test_so_codigo_e_nome_diz_o_que_reconheceu_e_o_que_falta(self) -> None:
        content = _xlsx([["Código", "Nome"], [1, "ATIVO"], [2, "CAIXA"], [3, "BANCO"]])
        with pytest.raises(ChartMappingRequiredError) as caught:
            parse_chart_sheet(content)
        error = caught.value
        assert error.details["recognizedRoles"] == ["codigo", "nome"]
        assert error.details["missingRoles"] == ["tipo"]
        assert error.user_message.startswith(
            "Reconhecemos as colunas de código e nome; não encontramos a coluna de tipo (S ou A)."
        )
        _assert_no_cell_text(error)

    def test_duas_colunas_de_texto_empatadas_sem_cabecalho_ficam_em_aberto(self) -> None:
        content = _xlsx([[1, "ATIVO X", "Alfa", "S"], [2, "CAIXA Y", "Beta", "A"]])
        with pytest.raises(ChartMappingRequiredError) as caught:
            parse_chart_sheet(content)
        error = caught.value
        assert error.details["recognizedRoles"] == ["codigo", "tipo"]
        assert error.details["missingRoles"] == ["nome"]
        assert error.details["ambiguousRoles"] == ["nome"]
        assert "há mais de uma coluna que pode ser nome" in error.user_message
        _assert_no_cell_text(error)

    def test_o_cabecalho_desempata_duas_colunas_de_texto(self) -> None:
        content = _xlsx(
            [
                ["Código", "Fantasia", "Nome", "Tipo"],
                [1, "Alfa", "ATIVO", "S"],
                [2, "Beta", "CAIXA", "A"],
            ]
        )
        parsed = parse_chart_sheet(content)
        assert parsed.layout == "reconhecido"
        assert [r.name for r in parsed.rows] == ["ATIVO", "CAIXA"]

    def test_so_banner_segue_com_a_recusa_do_modelo(self) -> None:
        with pytest.raises(FileHeaderMismatchError) as caught:
            parse_chart_sheet(_xlsx([["EMPRESA EXEMPLO"], ["C.N.P.J.: 00"]]))
        error = caught.value
        assert error.user_message.startswith("O cabeçalho da planilha não segue o modelo")
        assert error.details["recognizedRoles"] == []
        assert error.details["missingRoles"] == ["codigo", "nome", "tipo"]
        _assert_no_cell_text(error)

    def test_csv_fora_do_modelo_nao_passa_pelo_reconhecedor(self) -> None:
        content = b"conta;descricao;tipo\n1;ATIVO;S\n2;CAIXA;A\n"
        with pytest.raises(FileHeaderMismatchError) as caught:
            parse_chart_sheet(content)
        assert caught.value.details["missingRoles"] == ["codigo", "nome", "tipo"]


class TestColunaAMaisNaoRecusa:
    def test_modelo_com_coluna_extra_entra_pelo_conteudo(self) -> None:
        content = _xlsx(
            [
                ["codigo_reduzido", "nome", "tipo", "observacao"],
                [10, "ATIVO", "sintetica", "x"],
                [11, "CAIXA", "analitica", None],
            ]
        )
        parsed = parse_chart_sheet(content)
        assert parsed.layout == "reconhecido"
        assert [(r.code, r.account_type) for r in parsed.rows] == [
            ("10", AccountingAccountType.SINTETICA),
            ("11", AccountingAccountType.ANALITICA),
        ]

    def test_modelo_exato_continua_sendo_modelo(self) -> None:
        content = _xlsx([["codigo_reduzido", "nome", "tipo"], [10, "ATIVO", "sintetica"]])
        assert parse_chart_sheet(content).layout == "modelo"


class TestLinhasPeloReconhecedor:
    def _plan(self) -> list[list[Any]]:
        """40 contas: uma linha estragada fica abaixo de 5% e a coluna segue reconhecida
        (é a margem de `MIN_FORM_RATIO` para as linhas com problema)."""
        accounts = [
            [1, "1", "S", 1, "ATIVO"],
            [2, "1.1", "S", 2, "CIRCULANTE"],
            *([10 + i, f"1.1.{i}", "A", 3, f"CONTA {i}"] for i in range(1, 39)),
        ]
        return [["Código", "Classificação", "Tipo", "Grau", "Nome"], *accounts]

    def test_grau_divergente_recusa_com_motivo_e_linha(self) -> None:
        plan = self._plan()
        plan[3][3] = 9
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_chart_sheet(_xlsx(plan))
        assert caught.value.details["lines"] == [{"line": 4, "reason": "grau_divergente"}]
        _assert_no_cell_text(caught.value)

    def test_tipo_estranho_e_tipo_invalido(self) -> None:
        plan = self._plan()
        plan[4][2] = "X"
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_chart_sheet(_xlsx(plan))
        assert caught.value.details["lines"] == [{"line": 5, "reason": "tipo_invalido"}]

    def test_conta_depois_do_rodape_recusa_o_arquivo_inteiro(self) -> None:
        plan = [*self._plan(), [], ["Assinatura do contador"], [9, "2.1", "A", 2, "PERDIDA"]]
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_chart_sheet(_xlsx(plan))
        assert caught.value.details["lines"] == [{"line": 44, "reason": "conta_fora_do_bloco"}]

    def test_linha_vazia_no_meio_do_bloco_nao_o_corta(self) -> None:
        plan = self._plan()
        plan.insert(3, [])
        assert len(parse_chart_sheet(_xlsx(plan)).rows) == 40


class TestDerivarTipoPelaHierarquia:
    def test_tem_conta_abaixo_e_sintetica(self) -> None:
        assert derive_type_by_hierarchy(["1", "1.1", "1.1.1", "1.2", None]) == [
            AccountingAccountType.SINTETICA,
            AccountingAccountType.SINTETICA,
            AccountingAccountType.ANALITICA,
            AccountingAccountType.ANALITICA,
            None,
        ]

    def test_prefixo_de_texto_nao_e_filho(self) -> None:
        # `1.10` não está abaixo de `1.1`: só o ponto separa níveis.
        assert derive_type_by_hierarchy(["1.1", "1.10"]) == [
            AccountingAccountType.ANALITICA,
            AccountingAccountType.ANALITICA,
        ]


class TestPredicados:
    @given(st.integers(min_value=0, max_value=10**9 - 1))
    def test_inteiro_numerico_e_em_texto(self, number: int) -> None:
        assert as_integer(number) == number
        assert as_integer(float(number)) == number
        assert as_integer(str(number)) == number

    @given(st.lists(st.integers(min_value=0, max_value=999), min_size=1, max_size=6))
    def test_classificacao_ida_e_volta(self, parts: list[int]) -> None:
        text = ".".join(str(p) for p in parts)
        assert as_classification(text) == text

    @given(st.text(alphabet="abcxyz ", min_size=1))
    def test_texto_nao_e_inteiro_nem_classificacao(self, text: str) -> None:
        assert as_integer(text) is None
        assert as_classification(text) is None

    def test_booleano_nao_e_inteiro(self) -> None:
        assert as_integer(True) is None

    @pytest.mark.parametrize(
        "value",
        ["05.423.963/0001-11", "05423963000111", "123.456.789-09", "12345678909", 0, "0"],
    )
    def test_documento(self, value: Any) -> None:
        assert is_document_like(value)

    @pytest.mark.parametrize("value", [649, "649", "ATIVO", "1.1.2", 123])
    def test_nao_documento(self, value: Any) -> None:
        assert not is_document_like(value)

    def test_constantes_nomeadas(self) -> None:
        assert MIN_FILL_RATIO == 0.9
        assert MIN_FORM_RATIO == 0.95
        assert recognizer.MIN_UNIQUE_RATIO == 0.95

    def test_coluna_de_codigo_com_repeticao_nao_e_codigo(self) -> None:
        rows: list[tuple[int, list[Any]]] = [
            (i + 1, [i % 3 + 1, f"CONTA {i}", "A"]) for i in range(30)
        ]
        recognition = recognize_columns(rows)
        assert recognition is not None
        assert "codigo" in recognition.missing


def _tract_without_type() -> bytes:
    """A fixture da Tract sem a coluna de tipo e sem o cabeçalho: o conteúdo não decide."""
    wb = load_workbook(io.BytesIO(_TRACT.read_bytes()))
    ws = wb.worksheets[0]
    ws.delete_cols(3)
    ws.delete_rows(5)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class TestMapeamento:
    """A etapa de mapeamento (86e3ydjz4): o perfil das colunas e a leitura pela escolha."""

    def test_sem_cabecalho_as_duas_colunas_de_texto_vem_sem_papel(self) -> None:
        content = _xlsx([[i, f"CONTA {i}", f"FANTASIA {i}"] for i in range(1, 21)])
        with pytest.raises(ChartMappingRequiredError) as caught:
            parse_chart_sheet(content)
        error = caught.value
        assert error.status_code == 422
        columns = error.details["colunas"]
        assert [(c["indice"], c["formaDetectada"], c["papelSugerido"]) for c in columns] == [
            (0, "inteiro", "codigo"),
            (1, "texto", None),
            (2, "texto", None),
        ]
        assert all(c["cabecalho"] is None and c["preenchimento"] == 100 for c in columns)
        assert error.user_message.endswith("Diga o que é cada coluna para continuar.")
        assert "CONTA" not in repr(error.details)
        assert "FANTASIA" not in repr(error.details)

    def test_o_cabecalho_sai_mas_nenhum_valor_de_celula(self) -> None:
        with pytest.raises(ChartMappingRequiredError) as caught:
            parse_chart_sheet(_tract_without_type())
        rendered = repr(caught.value.details)
        assert "de exemplo" not in rendered
        assert "99.999" not in rendered
        headers = [c["cabecalho"] for c in caught.value.details["colunas"]]
        # O cabeçalho foi apagado: logo acima do bloco ficou a linha em branco e, acima
        # dela, o BANNER (no original, a razão social). Linha de uma célula só nunca é
        # cabeçalho, então nenhum texto sai.
        assert headers == [None] * len(headers)
        assert caught.value.details["missingRoles"] == ["tipo"]

    def test_tipo_pela_hierarquia_so_por_escolha_e_com_a_contagem(self) -> None:
        mapping = ColumnMapping(
            roles={"codigo": 0, "classificacao": 1, "grau": 2, "nome": 4},
            type_by_hierarchy=True,
        )
        parsed = parse_chart_sheet(_tract_without_type(), mapping=mapping)
        assert parsed.layout == "mapeado"
        assert len(parsed.rows) == 1850
        # A hierarquia NÃO devolve as 180 do arquivo: 28 grupos sintéticos da Tract não
        # têm conta abaixo e viram analíticas. É por isso que o tipo nunca é inferido
        # em silêncio.
        assert parsed.derived_types == (152, 1698)
        by_file = {r.code: r.account_type for r in parse_chart_sheet(_TRACT.read_bytes()).rows}
        changed = [r for r in parsed.rows if r.account_type is not by_file[r.code]]
        assert len(changed) == 28
        assert all(by_file[r.code] is AccountingAccountType.SINTETICA for r in changed)

    def test_o_mapeamento_vence_o_reconhecimento(self) -> None:
        mapping = ColumnMapping(roles={"codigo": 0, "classificacao": 1, "tipo": 2, "nome": 5})
        parsed = parse_chart_sheet(_TRACT.read_bytes(), mapping=mapping)
        assert parsed.layout == "mapeado"
        assert parsed.derived_types is None
        assert _got(parsed.rows) == _expected(_TRACT_EXPECTED)

    def test_indice_fora_das_colunas_do_bloco_e_400(self) -> None:
        mapping = ColumnMapping(roles={"codigo": 0, "tipo": 2, "nome": 40})
        with pytest.raises(ValidationAppError):
            parse_chart_sheet(_TRACT.read_bytes(), mapping=mapping)

    def test_mapeamento_em_csv_e_400(self) -> None:
        mapping = ColumnMapping(roles={"codigo": 0, "tipo": 2, "nome": 1})
        with pytest.raises(ValidationAppError):
            parse_chart_sheet(b"codigo_reduzido;nome;tipo\n1;ATIVO;S\n", mapping=mapping)

    def test_mapeamento_errado_cai_nas_linhas(self) -> None:
        # Nome no lugar do código: todo código é inválido, e a recusa é por linha.
        mapping = ColumnMapping(roles={"codigo": 5, "classificacao": 1, "tipo": 2, "nome": 0})
        with pytest.raises(FileLinesInvalidError):
            parse_chart_sheet(_TRACT.read_bytes(), mapping=mapping)


class TestPedidoDeMapeamento:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"colunas": {"codigo": 0, "nome": 0, "tipo": 1}}',
            '{"colunas": {"codigo": 0, "nome": 2}}',
            '{"colunas": {"codigo": 0, "nome": 2, "tipo": 1}, "tipoPelaHierarquia": true}',
            '{"colunas": {"codigo": 0, "nome": 2}, "tipoPelaHierarquia": true}',
            '{"colunas": {"codigo": 0, "nome": 2, "tipo": 1, "natureza": 3}}',
            '{"colunas": {"codigo": 0, "nome": 2, "tipo": 99}}',
            '{"colunas": {"codigo": -1, "nome": 2, "tipo": 1}}',
            '{"colunas": {"codigo": 0, "nome": 2, "tipo": 1}, "outro": 1}',
        ],
    )
    def test_forma_invalida(self, raw: str) -> None:
        with pytest.raises(PydanticValidationError):
            ChartColumnMappingRequest.model_validate_json(raw)

    def test_forma_valida(self) -> None:
        request = ChartColumnMappingRequest.model_validate_json(
            '{"colunas": {"codigo": 0, "classificacao": 1, "nome": 2}, "tipoPelaHierarquia": true}'
        )
        mapping = request.to_mapping()
        assert mapping.type_by_hierarchy is True
        assert dict(mapping.roles) == {"codigo": 0, "classificacao": 1, "nome": 2}


def _tract_with(**cells: Any) -> bytes:
    """A fixture da Tract com células trocadas (`A10=None`, `C12="X"`…), em memória."""
    wb = load_workbook(io.BytesIO(_TRACT.read_bytes()))
    ws = wb.worksheets[0]
    for ref, value in cells.items():
        ws[ref] = value
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class TestLinhasComProblema:
    """86e3ydjze: linha com problema vai para a prévia, salvo os dois casos de sempre."""

    def test_tres_linhas_com_problema_saem_da_lista_e_voltam_com_o_motivo(self) -> None:
        content = _tract_with(A10=None, C12="X", F20=None)
        parsed = parse_chart_sheet(content, allow_problems=True)
        assert parsed.problems == (
            (10, "codigo_vazio"),
            (12, "tipo_invalido"),
            (20, "nome_vazio"),
        )
        assert len(parsed.rows) == 1847
        assert {r.line for r in parsed.rows}.isdisjoint({10, 12, 20})

    def test_sem_a_permissao_continua_recusando_tudo(self) -> None:
        with pytest.raises(FileLinesInvalidError):
            parse_chart_sheet(_tract_with(C12="X"))

    def test_codigo_repetido_recusa_o_arquivo_inteiro(self) -> None:
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_chart_sheet(_tract_with(A12=6), allow_problems=True)
        assert caught.value.details["lines"] == [{"line": 12, "reason": "codigo_repetido"}]
        assert "repetido" in caught.value.user_message

    #: Com 400 tipos estranhos a COLUNA de tipo deixa de ser reconhecida (mais de 5% fora
    #: da forma); quem lê é o mapeamento, como a tela faria depois de perguntar.
    _MAPPING = ColumnMapping(roles={"codigo": 0, "classificacao": 1, "tipo": 2, "nome": 5})

    def test_acima_do_teto_recusa_o_arquivo_inteiro(self) -> None:
        # 400 linhas com tipo estranho: 21,6% das 1850, acima do teto de 20%.
        content = _tract_with(**{f"C{line}": "X" for line in range(6, 406)})
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_chart_sheet(content, mapping=self._MAPPING, allow_problems=True)
        assert caught.value.details["total"] == 400
        assert "Mais de 20%" in caught.value.user_message

    def test_o_teto_e_uma_constante_nomeada(self) -> None:
        assert MAX_PROBLEM_LINE_RATIO == 0.20
        # 370 = exatamente 20%: ainda passa (o teto é "acima de").
        content = _tract_with(**{f"C{line}": "X" for line in range(6, 376)})
        parsed = parse_chart_sheet(content, mapping=self._MAPPING, allow_problems=True)
        assert len(parsed.problems) == 370

    def test_o_dominio_tambem_vai_para_a_previa(self) -> None:
        wb = load_workbook(io.BytesIO(_DOMINIO.read_bytes()))
        ws = wb.worksheets[0]
        ws["X100"] = 9
        buffer = io.BytesIO()
        wb.save(buffer)
        parsed = parse_chart_sheet(buffer.getvalue(), allow_problems=True)
        assert parsed.layout == "dominio"
        assert parsed.problems == ((100, "grau_divergente"),)
        assert len(parsed.rows) == 562


class TestConfirmacao:
    def test_sem_confirmacao_e_com_problema_e_a_recusa_de_sempre(self) -> None:
        with pytest.raises(FileLinesInvalidError) as caught:
            refuse_unconfirmed_lines([(12, "tipo_invalido")], confirmed=None)
        assert caught.value.details["lines"] == [{"line": 12, "reason": "tipo_invalido"}]

    def test_a_mesma_lista_passa(self) -> None:
        refuse_unconfirmed_lines([(12, "tipo_invalido"), (20, "nome_vazio")], confirmed=[20, 12])

    @pytest.mark.parametrize("confirmed", [[12], [12, 20, 30], [], [99]])
    def test_lista_diferente_recusa(self, confirmed: list[int]) -> None:
        with pytest.raises(FileLinesInvalidError) as caught:
            refuse_unconfirmed_lines(
                [(12, "tipo_invalido"), (20, "nome_vazio")], confirmed=confirmed
            )
        assert "não são as que você confirmou" in caught.value.user_message

    def test_confirmar_linhas_num_arquivo_sem_problema_recusa(self) -> None:
        with pytest.raises(FileLinesInvalidError):
            refuse_unconfirmed_lines([], confirmed=[12])

    def test_sem_problema_e_sem_confirmacao_passa(self) -> None:
        refuse_unconfirmed_lines([], confirmed=None)
        refuse_unconfirmed_lines([], confirmed=[])
