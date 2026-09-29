"""O leitor determinístico do arquivo do cliente, sem banco (BACK 14.3 — R1/R2).

O que este módulo afirma sobre as partes PURAS:
  - formato pelo CONTÊINER (magic bytes): PDF, XLS e desconhecido são recusados com
    motivo acionável (`FORMATO_NAO_SUPORTADO`), e o arquivo precisa ser do formato
    que o mapeamento declara;
  - o cabeçalho é conferido ANTES da primeira linha (o gancho `on_header` roda com
    zero células processadas);
  - qualquer falha de abertura/iteração é a MESMA recusa `ARQUIVO_INVALIDO`, com
    mensagem fixa e `from None` (nada da célula na exceção); os limites (colunas,
    linhas percorridas, linhas de dados, orçamento do zip) são constantes declaradas;
  - conversões pelo mapeamento: data pelo formato DECLARADO, valor pelo separador
    DECLARADO (nunca `float`), sinal pela convenção DECLARADA — nunca inferido;
  - `convert_lines` acumula TODOS os problemas com vocabulário fechado e nunca
    carrega o texto da célula.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from app.core.exceptions import (
    AppError,
    FileFormatNotSupportedError,
    FileHeaderMismatchError,
    FileInvalidError,
)
from app.db.models.client_input_mapping import (
    CategoryMode,
    DecimalSeparator,
    InputDateFormat,
    InputFileFormat,
    SignConvention,
)
from app.db.models.client_movement import MOVEMENT_AMOUNT_PRECISION, MOVEMENT_AMOUNT_SCALE
from app.modules.client_file_ingestion import reader
from app.modules.client_file_ingestion.reader import (
    MAX_AMOUNT_ABS,
    MAX_CATEGORY_LABEL_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_FILE_COLUMNS,
    MAX_FILE_COMPRESSION_RATIO,
    MAX_FILE_ROWS,
    MAX_FILE_SCANNED_ROWS,
    MAX_FILE_UNCOMPRESSED_BYTES,
    MAX_INVALID_LINES_REPORTED,
    SAMPLE_ROWS,
    LineProblem,
    MappingSpec,
    ParsedTable,
    ReadOptions,
    assert_format_matches,
    convert_lines,
    detect_format,
    parse_amount,
    parse_date,
    read_table,
    sample_text,
)

SEGREDO = "PAGTO ACME LTDA SEGREDO DA CELULA"
JUN_INI = date(2026, 6, 1)
JUN_FIM = date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Construtores
# ---------------------------------------------------------------------------


def _csv(rows: list[list[Any]], *, delimiter: str = ";", encoding: str = "utf-8") -> bytes:
    lines = [delimiter.join("" if c is None else str(c) for c in row) for row in rows]
    return ("\n".join(lines) + "\n").encode(encoding)


def _xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _spec(**overrides: Any) -> MappingSpec:
    base: dict[str, Any] = {
        "file_format": InputFileFormat.CSV,
        "csv_delimiter": ";",
        "encoding": "utf-8",
        "date_column": "Data",
        "description_column": "Histórico",
        "amount_column": "Valor",
        "category_column": "Categoria",
        "category_mode": CategoryMode.COLUNA_CATEGORIA,
        "account_column": None,
        "document_column": None,
        "date_format": InputDateFormat.DD_MM_YYYY_SLASH,
        "decimal_separator": DecimalSeparator.COMMA,
        "sign_convention": SignConvention.VALOR_COM_SINAL,
        "nature_column": None,
        "debit_value": None,
        "credit_value": None,
        "debit_column": None,
        "credit_column": None,
    }
    base.update(overrides)
    return MappingSpec(**base)


CSV_OPTIONS = ReadOptions(file_format=InputFileFormat.CSV, csv_delimiter=";", encoding="utf-8")
XLSX_OPTIONS = ReadOptions(file_format=InputFileFormat.XLSX, csv_delimiter=";", encoding="utf-8")
HEADER = ["Data", "Histórico", "Valor", "Categoria"]


def _table(rows: list[list[Any]], columns: list[str] | None = None) -> ParsedTable:
    return ParsedTable(
        columns=columns or HEADER,
        rows=[(i + 2, list(row)) for i, row in enumerate(rows)],
        scanned=len(rows),
    )


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------


class TestFormato:
    def test_csv_e_xlsx_pelo_conteiner(self) -> None:
        assert (
            detect_format(_csv([HEADER, ["01/06/2026", "x", "-1,00", "A"]])) is InputFileFormat.CSV
        )
        assert detect_format(_xlsx([HEADER])) is InputFileFormat.XLSX

    def test_pdf_e_recusado_com_motivo_acionavel(self) -> None:
        with pytest.raises(FileFormatNotSupportedError) as exc:
            detect_format(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
        assert exc.value.status_code == 422
        assert exc.value.code.value == "FORMATO_NAO_SUPORTADO"
        assert "PDF" in exc.value.user_message
        assert "CSV ou XLSX" in exc.value.user_message

    def test_xls_antigo_e_recusado_com_motivo(self) -> None:
        with pytest.raises(FileFormatNotSupportedError) as exc:
            detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
        assert "XLS" in exc.value.user_message
        assert "XLSX ou CSV" in exc.value.user_message

    def test_desconhecido_e_recusado(self) -> None:
        with pytest.raises(FileFormatNotSupportedError):
            detect_format(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09")
        with pytest.raises(FileFormatNotSupportedError):
            detect_format(b"")

    def test_o_arquivo_precisa_ser_do_formato_do_mapeamento(self) -> None:
        assert_format_matches(InputFileFormat.CSV, InputFileFormat.CSV)
        with pytest.raises(FileFormatNotSupportedError) as exc:
            assert_format_matches(InputFileFormat.CSV, InputFileFormat.XLSX)
        assert "XLSX" in exc.value.user_message
        assert "CSV" in exc.value.user_message


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


class TestLeituraCsv:
    def test_cabecalho_e_linhas_numeradas_pelo_arquivo(self) -> None:
        content = _csv(
            [
                HEADER,
                ["01/06/2026", " Aluguel ", "-1.234,56", "Ocupação"],
                [],  # linha vazia: percorrida, não contada como dado
                ["02/06/2026", "Venda", "500,00", None],
            ]
        )
        table = read_table(content, CSV_OPTIONS)
        assert table.columns == HEADER
        # A linha vazia foi percorrida (3) e pulada: os números são os do ARQUIVO.
        assert [line for line, _ in table.rows] == [2, 4]
        assert table.scanned == 3
        # O único pré-processamento: strip; vazio vira None.
        assert table.rows[0][1] == ["01/06/2026", "Aluguel", "-1.234,56", "Ocupação"]
        assert table.rows[1][1] == ["02/06/2026", "Venda", "500,00", None]

    def test_delimitador_e_codificacao_sao_os_declarados_nunca_farejados(self) -> None:
        latin = _csv(
            [HEADER, ["01/06/2026", "Açaí", "-1,00", "A"]], delimiter=",", encoding="latin-1"
        )
        options = ReadOptions(
            file_format=InputFileFormat.CSV, csv_delimiter=",", encoding="latin-1"
        )
        assert read_table(latin, options).rows[0][1][1] == "Açaí"
        # Com `;` declarado, a linha inteira é UMA célula: o leitor não adivinha.
        errado = read_table(latin, ReadOptions(InputFileFormat.CSV, ";", "latin-1"))
        assert errado.columns == [",".join(HEADER)]

    def test_texto_fora_da_codificacao_declarada_e_arquivo_invalido(self) -> None:
        content = _csv([HEADER, ["01/06/2026", "Açaí", "-1,00", "A"]], encoding="latin-1")
        with pytest.raises(FileInvalidError) as exc:
            read_table(content, CSV_OPTIONS)
        assert exc.value.code.value == "ARQUIVO_INVALIDO"
        assert exc.value.__cause__ is None
        assert exc.value.__suppress_context__ is True
        assert "Açaí" not in str(exc.value)

    def test_linha_curta_e_completada_e_linha_longa_e_cortada_na_largura(self) -> None:
        content = _csv([HEADER, ["01/06/2026", "x"], ["02/06/2026", "y", "1,00", "A", "sobra"]])
        table = read_table(content, CSV_OPTIONS)
        assert table.rows[0][1] == ["01/06/2026", "x", None, None]
        assert table.rows[1][1] == ["02/06/2026", "y", "1,00", "A"]

    def test_limite_recorta_a_amostra(self) -> None:
        rows = [[f"{i:02d}/06/2026", "x", "1,00", "A"] for i in range(1, 10)]
        table = read_table(_csv([HEADER, *rows]), CSV_OPTIONS, limit=SAMPLE_ROWS)
        assert len(table.rows) == SAMPLE_ROWS
        assert read_table(_csv([HEADER, *rows]), CSV_OPTIONS, limit=0).rows == []

    def test_vazio_e_cabecalho_vazio_sao_arquivo_invalido(self) -> None:
        with pytest.raises(FileInvalidError):
            read_table(b"", CSV_OPTIONS)
        with pytest.raises(FileInvalidError):
            read_table(b";;;\n", CSV_OPTIONS)

    def test_colunas_vazias_no_fim_do_cabecalho_sao_aparadas(self) -> None:
        table = read_table(_csv([[*HEADER, "", ""], ["01/06/2026", "x", "1,00", "A"]]), CSV_OPTIONS)
        assert table.columns == HEADER


class TestLeituraXlsx:
    def test_le_a_primeira_planilha_com_datas_e_numeros_como_objetos(self) -> None:
        content = _xlsx([HEADER, [datetime(2026, 6, 1), "Aluguel", -1234.56, "Ocupação"]])
        table = read_table(content, XLSX_OPTIONS)
        assert table.columns == HEADER
        (line, cells) = table.rows[0]
        assert line == 2
        assert isinstance(cells[0], datetime)
        assert cells[2] == -1234.56  # a conversão para Decimal é do mapeamento

    def test_cabecalho_numerico_vira_texto(self) -> None:
        table = read_table(_xlsx([[2026, "Histórico"], [1, "x"]]), XLSX_OPTIONS)
        assert table.columns == ["2026", "Histórico"]

    def test_zip_quebrado_e_arquivo_invalido_sem_causa(self) -> None:
        content = b"PK\x03\x04" + b"\xff" * 64
        with pytest.raises(FileInvalidError) as exc:
            read_table(content, XLSX_OPTIONS)
        assert exc.value.__cause__ is None
        assert exc.value.user_message == FileInvalidError.default_user_message

    def test_zip_sem_planilha_e_arquivo_invalido(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("readme.txt", "não é uma planilha")
        with pytest.raises(FileInvalidError):
            read_table(buf.getvalue(), XLSX_OPTIONS)

    def test_bomba_de_descompressao_e_recusada_antes_do_openpyxl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Razão de compressão acima do teto: o openpyxl nunca é chamado."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/worksheets/sheet1.xml", b"0" * (MAX_FILE_COMPRESSION_RATIO * 4096))
        chamado = {"openpyxl": False}

        def _boom(*_a: Any, **_k: Any) -> Any:
            chamado["openpyxl"] = True
            raise AssertionError("openpyxl não pode ser chamado")

        monkeypatch.setattr(reader, "load_workbook", _boom)
        with pytest.raises(FileInvalidError):
            read_table(buf.getvalue(), XLSX_OPTIONS)
        assert chamado["openpyxl"] is False

    def test_descomprimido_acima_do_teto_e_recusado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(reader, "MAX_FILE_UNCOMPRESSED_BYTES", 1024)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("a.xml", b"a" * 900)
            archive.writestr("b.xml", b"b" * 900)
        with pytest.raises(FileInvalidError):
            read_table(buf.getvalue(), XLSX_OPTIONS)

    def test_a_dimensao_declarada_nao_e_confiada(self) -> None:
        """`reset_dimensions`: o arquivo pode mentir `<dimension ref=…>`; lemos o que há."""
        content = _xlsx([HEADER, ["01/06/2026", "x", 1, "A"], ["02/06/2026", "y", 2, "B"]])
        assert len(read_table(content, XLSX_OPTIONS).rows) == 2


class TestLimitesDeclarados:
    def test_constantes(self) -> None:
        assert MAX_FILE_ROWS == 10_000
        assert MAX_FILE_SCANNED_ROWS == 2 * MAX_FILE_ROWS
        assert MAX_FILE_COLUMNS == 64
        assert MAX_FILE_UNCOMPRESSED_BYTES == 60 * 1024 * 1024
        assert MAX_FILE_COMPRESSION_RATIO == 100
        assert SAMPLE_ROWS == 5
        assert MAX_INVALID_LINES_REPORTED == 50

    def test_colunas_demais_e_arquivo_invalido(self) -> None:
        header = [f"c{i}" for i in range(MAX_FILE_COLUMNS + 1)]
        with pytest.raises(FileInvalidError):
            read_table(_csv([header, ["1"] * len(header)]), CSV_OPTIONS)

    def test_linhas_de_dados_demais_e_arquivo_invalido(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reader, "MAX_FILE_ROWS", 3)
        monkeypatch.setattr(reader, "MAX_FILE_SCANNED_ROWS", 100)
        rows = [["01/06/2026", "x", "1,00", "A"] for _ in range(4)]
        with pytest.raises(FileInvalidError):
            read_table(_csv([HEADER, *rows]), CSV_OPTIONS)

    def test_linhas_percorridas_contam_as_vazias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Uma célula perdida na linha 300.000 não pode prender a CPU (lição da S12)."""
        monkeypatch.setattr(reader, "MAX_FILE_SCANNED_ROWS", 5)
        rows: list[list[Any]] = [[] for _ in range(5)] + [["01/06/2026", "x", "1,00", "A"]]
        with pytest.raises(FileInvalidError):
            read_table(_csv([HEADER, *rows]), CSV_OPTIONS)

    def test_amostra_como_texto(self) -> None:
        assert sample_text([None, datetime(2026, 6, 1, 10), date(2026, 6, 2), 1.5, "x"]) == [
            "",
            "2026-06-01",
            "2026-06-02",
            "1.5",
            "x",
        ]


class TestCabecalhoAntesDaPrimeiraLinha:
    def test_o_gancho_roda_com_zero_celulas_processadas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Teste dirigido do R2: a recusa do cabeçalho acontece antes de ler qualquer linha."""
        processadas = {"celulas": 0}
        original = reader._cell

        def _spy(value: Any) -> Any:
            processadas["celulas"] += 1
            return original(value)

        monkeypatch.setattr(reader, "_cell", _spy)

        def _check(columns: list[str]) -> None:
            raise FileHeaderMismatchError(
                "coluna ausente",
                details={"missingColumns": ["Histórico"], "foundColumnCount": len(columns)},
            )

        content = _csv([["Data", "Descrição", "Valor"], ["01/06/2026", SEGREDO, "-1,00"]])
        with pytest.raises(FileHeaderMismatchError) as exc:
            read_table(content, CSV_OPTIONS, on_header=_check)
        assert processadas["celulas"] == 0
        assert exc.value.details == {"missingColumns": ["Histórico"], "foundColumnCount": 3}
        assert SEGREDO not in str(exc.value)

    def test_app_error_atravessa_e_o_resto_vira_arquivo_invalido(self) -> None:
        def _check(_columns: list[str]) -> None:
            raise RuntimeError(SEGREDO)

        with pytest.raises(FileInvalidError) as exc:
            read_table(
                _csv([HEADER, ["01/06/2026", "x", "1,00", "A"]]), CSV_OPTIONS, on_header=_check
            )
        assert isinstance(exc.value, AppError)
        assert SEGREDO not in str(exc.value)
        assert exc.value.__cause__ is None


# ---------------------------------------------------------------------------
# Conversões pelo mapeamento
# ---------------------------------------------------------------------------


class TestData:
    @pytest.mark.parametrize(
        ("value", "fmt", "expected"),
        [
            ("31/12/2026", InputDateFormat.DD_MM_YYYY_SLASH, date(2026, 12, 31)),
            ("31-12-2026", InputDateFormat.DD_MM_YYYY_DASH, date(2026, 12, 31)),
            ("2026-12-31", InputDateFormat.YYYY_MM_DD_DASH, date(2026, 12, 31)),
            ("31/12/26", InputDateFormat.DD_MM_YY_SLASH, date(2026, 12, 31)),
            (datetime(2026, 6, 1, 15, 30), InputDateFormat.DD_MM_YYYY_SLASH, date(2026, 6, 1)),
            (date(2026, 6, 1), InputDateFormat.YYYY_MM_DD_DASH, date(2026, 6, 1)),
        ],
    )
    def test_pelo_formato_declarado(self, value: Any, fmt: InputDateFormat, expected: date) -> None:
        assert parse_date(value, fmt) == expected

    @pytest.mark.parametrize(
        ("value", "fmt"),
        [
            ("2026-12-31", InputDateFormat.DD_MM_YYYY_SLASH),  # formato certo, mapeamento errado
            ("12/31/2026", InputDateFormat.DD_MM_YYYY_SLASH),  # mês e dia trocados: NÃO adivinha
            ("31/02/2026", InputDateFormat.DD_MM_YYYY_SLASH),
            ("", InputDateFormat.DD_MM_YYYY_SLASH),
            (20260601, InputDateFormat.YYYY_MM_DD_DASH),
        ],
    )
    def test_fora_do_formato_declarado_e_erro(self, value: Any, fmt: InputDateFormat) -> None:
        with pytest.raises(ValueError):  # noqa: PT011 - a mensagem é interna
            parse_date(value, fmt)


class TestValor:
    @pytest.mark.parametrize(
        ("value", "sep", "expected"),
        [
            ("1.234,56", DecimalSeparator.COMMA, "1234.56"),
            ("-1.234,56", DecimalSeparator.COMMA, "-1234.56"),
            ("R$ 1.234,56", DecimalSeparator.COMMA, "1234.56"),
            ("R$\u00a01.234,56", DecimalSeparator.COMMA, "1234.56"),
            ("(100,00)", DecimalSeparator.COMMA, "-100.00"),
            ("1.500", DecimalSeparator.COMMA, "1500.00"),  # milhar em grupo de 3
            ("1500,5", DecimalSeparator.COMMA, "1500.50"),
            ("12.345.678,90", DecimalSeparator.COMMA, "12345678.90"),
            (
                "999.999.999.999,99",
                DecimalSeparator.COMMA,
                "999999999999.99",
            ),  # o teto menos 1 centavo
            ("1,234.56", DecimalSeparator.DOT, "1234.56"),
            ("-1500.5", DecimalSeparator.DOT, "-1500.50"),
            ("100", DecimalSeparator.DOT, "100.00"),
            ("0,5", DecimalSeparator.COMMA, "0.50"),
            (100, DecimalSeparator.COMMA, "100.00"),
            (-12.5, DecimalSeparator.DOT, "-12.50"),
            (Decimal("7.10"), DecimalSeparator.DOT, "7.10"),
        ],
    )
    def test_pelo_separador_declarado_em_decimal(
        self, value: Any, sep: DecimalSeparator, expected: str
    ) -> None:
        amount = parse_amount(value, sep)
        assert isinstance(amount, Decimal)
        assert amount == Decimal(expected)
        assert str(amount) == expected

    @pytest.mark.parametrize(
        ("value", "sep"),
        [
            ("abc", DecimalSeparator.COMMA),
            ("", DecimalSeparator.COMMA),
            ("1,234", DecimalSeparator.COMMA),  # três casas: arredondar mudaria o valor
            ("1.2345", DecimalSeparator.DOT),
            ("NaN", DecimalSeparator.DOT),
            ("Infinity", DecimalSeparator.DOT),
            (True, DecimalSeparator.DOT),
            (None, DecimalSeparator.DOT),
            (date(2026, 6, 1), DecimalSeparator.DOT),
            # Retrabalho da 14.3 (QA 86e3f6r4x): os três que davam 500 ou dinheiro errado.
            ("1E+30", DecimalSeparator.COMMA),  # quantize → InvalidOperation → 500
            ("1E+30", DecimalSeparator.DOT),
            ("12345678901234,00", DecimalSeparator.COMMA),  # CNPJ: estourava Numeric(14,2)
            ("1000000000000", DecimalSeparator.DOT),  # exatamente o teto (10^12)
            ("-1000000000000,00", DecimalSeparator.COMMA),
            ("1500.50", DecimalSeparator.COMMA),  # virava 150050 e o arquivo era ACEITO
            ("1.5", DecimalSeparator.COMMA),  # milhar sem grupo de 3
            ("1.50", DecimalSeparator.COMMA),
            ("1500,50", DecimalSeparator.DOT),  # o espelho sob ponto
            ("1,5", DecimalSeparator.DOT),
            ("1.234.5", DecimalSeparator.COMMA),
            ("1,5,0", DecimalSeparator.COMMA),
            ("--10,00", DecimalSeparator.COMMA),
            ("10,00-", DecimalSeparator.COMMA),
            ("0x10", DecimalSeparator.DOT),
            ("1_000", DecimalSeparator.DOT),
        ],
    )
    def test_fora_do_padrao_e_erro(self, value: Any, sep: DecimalSeparator) -> None:
        with pytest.raises(ValueError):  # noqa: PT011 - a mensagem é interna
            parse_amount(value, sep)

    @pytest.mark.parametrize("value", [1e30, 10**12, Decimal("1E+12"), -(10**13)])
    def test_numero_acima_do_teto_da_coluna_e_erro(self, value: Any) -> None:
        """Célula NUMÉRICA do XLSX também passa pelo teto: `Numeric(14,2)` não cabe."""
        with pytest.raises(ValueError):  # noqa: PT011 - a mensagem é interna
            parse_amount(value, DecimalSeparator.COMMA)

    def test_o_teto_e_derivado_da_coluna(self) -> None:
        assert Decimal(10) ** (MOVEMENT_AMOUNT_PRECISION - MOVEMENT_AMOUNT_SCALE) == MAX_AMOUNT_ABS
        assert Decimal("1000000000000") == MAX_AMOUNT_ABS

    def test_o_separador_errado_nao_e_adivinhado_nem_consertado(self) -> None:
        """`1,50` com separador `.` declarado NÃO vira 150: fora do formato é linha inválida.

        Antes do retrabalho, o leitor tirava o separador "de milhar" sem conferir o
        grupo de 3 e aceitava — dinheiro errado entrando calado (R2).
        """
        with pytest.raises(ValueError):  # noqa: PT011 - a mensagem é interna
            parse_amount("1,50", DecimalSeparator.DOT)

    def test_so_levanta_value_error_e_sem_contexto(self) -> None:
        """Quem chama só converte `ValueError` em motivo; nada do decimal escapa."""
        with pytest.raises(ValueError) as exc:  # noqa: PT011 - a mensagem é interna
            parse_amount("1E+30", DecimalSeparator.DOT)
        assert exc.value.__cause__ is None
        assert "1E+30" not in str(exc.value)


class TestFixturesDoQa:
    """As fixtures sintéticas do QA da S14 (`tests/fixtures/file_origin/`) pelo leitor.

    Espelho SEM banco de `test_s14_qa_file_origin_cycle.py::TestValorForaDoPadrao…`:
    a integração prova o 422 e a contagem inalterada; aqui se prova, em qualquer
    ambiente, que a linha 4 é a ÚNICA recusada e pelo motivo fechado — e que os
    arquivos originais fecham os totais que o QA declara.
    """

    FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "file_origin"
    AGO_INI = date(2026, 8, 1)
    AGO_FIM = date(2026, 8, 31)

    def _convert(self, content: bytes, start: date, end: date) -> Any:
        table = read_table(content, CSV_OPTIONS)
        return convert_lines(table, _spec(document_column="Documento"), start=start, end=end)

    @pytest.mark.parametrize(
        ("name", "start", "end", "total"),
        [
            ("extrato_2026_07.csv", date(2026, 7, 1), date(2026, 7, 31), "1772.18"),
            ("extrato_2026_08.csv", date(2026, 8, 1), date(2026, 8, 31), "-2710.42"),
        ],
    )
    def test_os_arquivos_do_qa_fecham_o_total(
        self, name: str, start: date, end: date, total: str
    ) -> None:
        lines, problems = self._convert((self.FIXTURES / name).read_bytes(), start, end)
        assert problems == []
        assert sum(line.amount for line in lines) == Decimal(total)

    @pytest.mark.parametrize("cell", ["1E+30", "12345678901234,00", "1500.50"])
    def test_valor_fora_do_padrao_recusa_so_a_linha_4(self, cell: str) -> None:
        content = (self.FIXTURES / "extrato_2026_08.csv").read_bytes()
        assert content.count(b"-398,12") == 1
        _, problems = self._convert(
            content.replace(b"-398,12", cell.encode()), self.AGO_INI, self.AGO_FIM
        )
        assert problems == [LineProblem(line=4, reason="valor_nao_numerico")]


class TestSinal:
    def test_valor_com_sinal(self) -> None:
        table = _table([["01/06/2026", "x", "-10,00", "A"], ["02/06/2026", "y", "5,00", "A"]])
        lines, problems = convert_lines(table, _spec(), start=JUN_INI, end=JUN_FIM)
        assert problems == []
        assert [line.amount for line in lines] == [Decimal("-10.00"), Decimal("5.00")]

    def test_coluna_natureza_pelos_literais_declarados(self) -> None:
        spec = _spec(
            sign_convention=SignConvention.COLUNA_NATUREZA,
            nature_column="D/C",
            debit_value="D",
            credit_value="C",
        )
        columns = [*HEADER, "D/C"]
        table = _table(
            [
                ["01/06/2026", "x", "10,00", "A", "D"],
                ["02/06/2026", "y", "-10,00", "A", "C"],  # o sinal da célula é ignorado
                ["03/06/2026", "z", "10,00", "A", "X"],
                ["04/06/2026", "w", "10,00", "A", None],
            ],
            columns,
        )
        lines, problems = convert_lines(table, spec, start=JUN_INI, end=JUN_FIM)
        assert [line.amount for line in lines] == [Decimal("-10.00"), Decimal("10.00")]
        assert problems == [
            LineProblem(4, "natureza_desconhecida"),
            LineProblem(5, "campo_obrigatorio_vazio"),
        ]

    def test_colunas_separadas_credito_menos_debito(self) -> None:
        spec = _spec(
            sign_convention=SignConvention.COLUNAS_SEPARADAS,
            amount_column=None,
            debit_column="Débito",
            credit_column="Crédito",
        )
        columns = ["Data", "Histórico", "Débito", "Crédito", "Categoria"]
        table = _table(
            [
                ["01/06/2026", "x", "10,00", None, "A"],
                ["02/06/2026", "y", None, "25,50", "A"],
                ["03/06/2026", "z", "-3,00", "1,00", "A"],  # abs nos dois
                ["04/06/2026", "w", None, None, "A"],
                ["05/06/2026", "v", "abc", None, "A"],
            ],
            columns,
        )
        lines, problems = convert_lines(table, spec, start=JUN_INI, end=JUN_FIM)
        assert [line.amount for line in lines] == [
            Decimal("-10.00"),
            Decimal("25.50"),
            Decimal("-2.00"),
        ]
        assert problems == [
            LineProblem(5, "campo_obrigatorio_vazio"),
            LineProblem(6, "valor_nao_numerico"),
        ]

    def test_sem_convencao_nada_e_inferido(self) -> None:
        """Defesa em profundidade: sem convenção, toda linha é problema — nunca um palpite."""
        table = _table([["01/06/2026", "x", "-10,00", "A"]])
        lines, problems = convert_lines(
            table, _spec(sign_convention=None), start=JUN_INI, end=JUN_FIM
        )
        assert lines == []
        assert problems == [LineProblem(2, "campo_obrigatorio_vazio")]


class TestConvertLines:
    def test_acumula_todos_os_problemas_sem_parar_na_primeira(self) -> None:
        table = _table(
            [
                ["01/06/2026", SEGREDO, "abc", "A"],
                ["99/99/2026", "y", "1,00", "A"],
                [None, "z", "1,00", "A"],
                ["01/06/2026", "w", None, "A"],
                ["01/07/2026", "v", "1,00", "A"],
                ["15/06/2026", "ok", "1,00", "A"],
            ]
        )
        lines, problems = convert_lines(table, _spec(), start=JUN_INI, end=JUN_FIM)
        assert problems == [
            LineProblem(2, "valor_nao_numerico"),
            LineProblem(3, "data_invalida"),
            LineProblem(4, "campo_obrigatorio_vazio"),
            LineProblem(5, "campo_obrigatorio_vazio"),
            LineProblem(6, "data_fora_da_competencia"),
        ]
        assert [line.line for line in lines] == [7]
        # O problema carrega só número e motivo — nunca o texto da célula.
        assert SEGREDO not in repr(problems)

    def test_linha_valida_carrega_os_campos_do_mapeamento(self) -> None:
        spec = _spec(account_column="Conta", document_column="Doc")
        columns = [*HEADER, "Conta", "Doc"]
        table = _table(
            [["10/06/2026", " Aluguel ", "-1.500,00", "Ocupação", 12345, "NF 77"]], columns
        )
        lines, problems = convert_lines(table, spec, start=JUN_INI, end=JUN_FIM)
        assert problems == []
        (line,) = lines
        assert line.line == 2
        assert line.entry_date == date(2026, 6, 10)
        assert line.amount == Decimal("-1500.00")
        assert line.description == "Aluguel"
        assert line.category_label == "Ocupação"
        assert line.account == "12345"  # número da célula do XLSX vira texto
        assert line.document == "NF 77"

    def test_categoria_vazia_e_none_e_sem_coluna_de_categoria_tambem(self) -> None:
        table = _table([["01/06/2026", "x", "1,00", None]])
        (line,), _ = convert_lines(table, _spec(), start=JUN_INI, end=JUN_FIM)
        assert line.category_label is None
        (line,), _ = convert_lines(table, _spec(category_column=None), start=JUN_INI, end=JUN_FIM)
        assert line.category_label is None

    def test_descricao_vazia_e_texto_vazio_nao_problema(self) -> None:
        table = _table([["01/06/2026", None, "1,00", "A"]])
        (line,), problems = convert_lines(table, _spec(), start=JUN_INI, end=JUN_FIM)
        assert problems == []
        assert line.description == ""

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("Histórico", "x" * (MAX_DESCRIPTION_CHARS + 1)),
            ("Categoria", "c" * (MAX_CATEGORY_LABEL_CHARS + 1)),
        ],
    )
    def test_campo_longo_demais_e_problema_nunca_truncado(self, column: str, value: str) -> None:
        cells = {"Data": "01/06/2026", "Histórico": "x", "Valor": "1,00", "Categoria": "A"}
        cells[column] = value
        table = _table([[cells[c] for c in HEADER]])
        lines, problems = convert_lines(table, _spec(), start=JUN_INI, end=JUN_FIM)
        assert lines == []
        assert problems == [LineProblem(2, "campo_longo_demais")]

    def test_mapped_columns_sem_repeticao_e_sem_nulos(self) -> None:
        spec = _spec(
            sign_convention=SignConvention.COLUNA_NATUREZA,
            nature_column="Data",  # a mesma coluna duas vezes: entra uma
            debit_value="D",
            credit_value="C",
        )
        assert spec.mapped_columns == ["Data", "Histórico", "Valor", "Categoria"]

    def test_fim_de_semana_e_ultimo_dia_entram_na_competencia(self) -> None:
        table = _table([["30/06/2026", "x", "1,00", "A"], ["01/06/2026", "y", "1,00", "A"]])
        lines, problems = convert_lines(table, _spec(), start=JUN_INI, end=JUN_FIM)
        assert problems == []
        assert len(lines) == 2
