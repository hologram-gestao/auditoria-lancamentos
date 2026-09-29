"""Leitura da planilha do plano contábil — o modelo da plataforma (BACK 16.1 — R1).

Função pura, sem banco: o que este módulo prova é o "tudo ou nada" ANTES de qualquer
gravação — a planilha inteira é validada e a recusa é tipada (422), com linha x motivo
de vocabulário fechado e NUNCA o conteúdo de uma célula.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.core.exceptions import (
    AppError,
    ErrorCode,
    FileFormatNotSupportedError,
    FileHeaderMismatchError,
    FileInvalidError,
    FileLinesInvalidError,
)
from app.db.models import AccountingAccountType
from app.modules.client_accounting_chart.sheet import (
    REQUIRED_COLUMNS,
    ChartSheetRow,
    parse_account_type,
    parse_chart_sheet,
)

pytestmark = pytest.mark.unit

_SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "accounting_sample"
    / "cliente_exemplo_2026_08"
    / "plano_contabil.csv"
)

#: Conteúdo "sigiloso" das células — nenhuma recusa pode ecoá-lo.
_SECRET_NAME = "Alugueis a receber - Inquilino Sigiloso"


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _body(exc: AppError) -> str:
    """O que iria na resposta HTTP: mensagem ao usuário + `details` (o `metadata` não vai)."""
    return exc.user_message + json.dumps(exc.details, ensure_ascii=False)


class TestAmostraReal:
    def test_o_plano_da_amostra_entra_inteiro(self) -> None:
        rows = parse_chart_sheet(_SAMPLE.read_bytes())
        assert len(rows) == 20
        assert all(isinstance(r, ChartSheetRow) for r in rows)
        by_code = {r.code: r for r in rows}
        assert by_code["649"].name == "Banco conta movimento"
        assert by_code["649"].account_type is AccountingAccountType.ANALITICA
        assert by_code["649"].classification is None
        # Linha física: cabeçalho = 1, a primeira conta = 2.
        assert rows[0].line == 2

    def test_repr_da_linha_nao_carrega_o_nome(self) -> None:
        rows = parse_chart_sheet(_csv("codigo_reduzido;nome;tipo", f"662;{_SECRET_NAME};analitica"))
        assert _SECRET_NAME not in repr(rows[0])


class TestModelo:
    def test_colunas_em_qualquer_ordem_caixa_indiferente_e_classificacao_opcional(self) -> None:
        rows = parse_chart_sheet(
            _csv(
                "Tipo;CLASSIFICACAO;Nome;Codigo_Reduzido",
                "Sintética;1.1;Ativo circulante;10",
                "ANALITICA;1.1.1.02.001;Banco;649",
            )
        )
        assert [(r.code, r.account_type, r.classification) for r in rows] == [
            ("10", AccountingAccountType.SINTETICA, "1.1"),
            ("649", AccountingAccountType.ANALITICA, "1.1.1.02.001"),
        ]

    def test_bom_do_excel_e_aceito(self) -> None:
        content = b"\xef\xbb\xbf" + _csv("codigo_reduzido;nome;tipo", "649;Banco;analitica")
        assert [r.code for r in parse_chart_sheet(content)] == ["649"]

    def test_linha_em_branco_e_pulada(self) -> None:
        rows = parse_chart_sheet(
            _csv("codigo_reduzido;nome;tipo", "649;Banco;analitica", ";;", "650;Rend.;analitica")
        )
        assert [r.code for r in rows] == ["649", "650"]
        assert rows[1].line == 4

    def test_xlsx_primeira_aba_e_codigo_numerico_vira_texto(self) -> None:
        content = _xlsx(
            [
                ["codigo_reduzido", "nome", "tipo"],
                [649, "Banco conta movimento", "analitica"],
                [650.0, "Rendimentos", "Analítica"],
            ]
        )
        rows = parse_chart_sheet(content)
        assert [(r.code, r.account_type) for r in rows] == [
            ("649", AccountingAccountType.ANALITICA),
            ("650", AccountingAccountType.ANALITICA),
        ]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("analitica", AccountingAccountType.ANALITICA),
            ("Analítica", AccountingAccountType.ANALITICA),
            (" SINTÉTICA ", AccountingAccountType.SINTETICA),
            ("A", None),
            ("analitico", None),
            (None, None),
        ],
    )
    def test_tipo(self, raw: object, expected: AccountingAccountType | None) -> None:
        assert parse_account_type(raw) is expected


class TestRecusasSemGravarNada:
    def test_codigo_repetido_recusa_o_arquivo_nomeando_a_linha(self) -> None:
        content = _csv(
            "codigo_reduzido;nome;tipo",
            f"662;{_SECRET_NAME};analitica",
            "649;Banco;analitica",
            f"662;{_SECRET_NAME} 2;analitica",
        )
        with pytest.raises(FileLinesInvalidError) as excinfo:
            parse_chart_sheet(content)
        exc = excinfo.value
        assert exc.status_code == 422
        assert exc.code is ErrorCode.LINHAS_INVALIDAS
        assert exc.details == {"lines": [{"line": 4, "reason": "codigo_repetido"}], "total": 1}
        assert _SECRET_NAME not in _body(exc)
        assert "662" not in _body(exc)

    @pytest.mark.parametrize(
        ("line", "reason"),
        [
            (";Banco;analitica", "codigo_vazio"),
            ("123456789012345678901;Banco;analitica", "codigo_longo"),
            ("64 9;Banco;analitica", "codigo_invalido"),
            ('"64;9";Banco;analitica', "codigo_invalido"),
            ("649;;analitica", "nome_vazio"),
            ("649;" + "x" * 201 + ";analitica", "nome_longo"),
            ("649;Banco;analitico", "tipo_invalido"),
            ("649;Banco;", "tipo_invalido"),
        ],
    )
    def test_motivos_de_linha_sao_vocabulario_fechado(self, line: str, reason: str) -> None:
        with pytest.raises(FileLinesInvalidError) as excinfo:
            parse_chart_sheet(_csv("codigo_reduzido;nome;tipo", line))
        assert excinfo.value.details["lines"] == [{"line": 2, "reason": reason}]

    def test_classificacao_longa(self) -> None:
        with pytest.raises(FileLinesInvalidError) as excinfo:
            parse_chart_sheet(
                _csv("codigo_reduzido;nome;tipo;classificacao", "649;Banco;analitica;" + "1" * 41)
            )
        assert excinfo.value.details["lines"] == [{"line": 2, "reason": "classificacao_longa"}]

    def test_todas_as_linhas_invalidas_sao_listadas_de_uma_vez(self) -> None:
        with pytest.raises(FileLinesInvalidError) as excinfo:
            parse_chart_sheet(
                _csv(
                    "codigo_reduzido;nome;tipo",
                    ";Banco;analitica",
                    "650;Ok;analitica",
                    "651;Ruim;x",
                )
            )
        assert excinfo.value.details == {
            "lines": [
                {"line": 2, "reason": "codigo_vazio"},
                {"line": 4, "reason": "tipo_invalido"},
            ],
            "total": 2,
        }

    def test_cabecalho_divergente_recusa_antes_de_ler_as_linhas(self) -> None:
        with pytest.raises(FileHeaderMismatchError) as excinfo:
            parse_chart_sheet(_csv("codigo;nome da conta;tipo", f"662;{_SECRET_NAME};analitica"))
        exc = excinfo.value
        assert exc.status_code == 422
        assert exc.code is ErrorCode.CABECALHO_DIVERGENTE
        assert exc.details["missingColumns"] == ["codigo_reduzido", "nome"]
        assert exc.details["unexpectedColumns"] == ["codigo", "nome da conta"]
        assert exc.details["expectedColumns"] == [*REQUIRED_COLUMNS, "classificacao"]
        assert _SECRET_NAME not in _body(exc)

    def test_coluna_repetida_no_cabecalho(self) -> None:
        with pytest.raises(FileHeaderMismatchError) as excinfo:
            parse_chart_sheet(_csv("codigo_reduzido;nome;tipo;Nome", "649;Banco;analitica;x"))
        assert excinfo.value.details["repeatedColumns"] == ["nome"]

    def test_planilha_sem_nenhuma_conta_e_recusada(self) -> None:
        """Na reimportação, uma planilha vazia inativaria o plano INTEIRO."""
        with pytest.raises(FileInvalidError) as excinfo:
            parse_chart_sheet(_csv("codigo_reduzido;nome;tipo", ";;"))
        assert excinfo.value.status_code == 422
        assert excinfo.value.code is ErrorCode.ARQUIVO_INVALIDO
        assert excinfo.value.details == {"reason": "sem_contas"}

    def test_pdf_e_recusado_pelo_conteudo(self) -> None:
        with pytest.raises(FileFormatNotSupportedError) as excinfo:
            parse_chart_sheet(b"%PDF-1.7\n" + b"x" * 100)
        assert excinfo.value.status_code == 422

    def test_texto_sem_estrutura_renomeado_para_xlsx_e_recusado_pelo_conteudo(self) -> None:
        """A extensão não conta: o que decide é o conteúdo."""
        with pytest.raises(FileFormatNotSupportedError):
            parse_chart_sheet(b"isto nao e uma planilha de verdade, so texto corrido")

    def test_zip_que_nao_e_xlsx_vira_arquivo_invalido_sem_500(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("leia-me.txt", _SECRET_NAME)
        with pytest.raises(FileInvalidError) as excinfo:
            parse_chart_sheet(buffer.getvalue())
        exc = excinfo.value
        assert exc.code is ErrorCode.ARQUIVO_INVALIDO
        assert exc.__cause__ is None
        assert exc.__suppress_context__ is True
        assert _SECRET_NAME not in _body(exc)

    def test_csv_fora_de_utf8_vira_arquivo_invalido_from_none(self) -> None:
        content = "codigo_reduzido;nome;tipo\n649;Balanço é Latin-1;analitica\n".encode("latin-1")
        with pytest.raises(FileInvalidError) as excinfo:
            parse_chart_sheet(content)
        assert excinfo.value.__suppress_context__ is True
