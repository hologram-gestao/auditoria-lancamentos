"""O `.xls` que o Domínio grava, na importação do plano contábil (86e3n70p6).

O teste-ouro importa a FIXTURE anonimizada do `.xls` real (cópia byte a byte fora o
texto: o contêiner OLE2, os registros BIFF e o defeito do export, com 5.919 BLANKs
órfãos antes da aba) e compara conta a conta com `plano_esperado.csv`, como o teste do
`.xlsx` (`test_accounting_chart_dominio.py`) faz com a fixture dele.

O que é provado aqui:
- o arquivo do Domínio, sem edição, entra inteiro: 370 contas, 141 sintéticas, pelo
  MESMO conversor do `.xlsx` (`dominio.py` não sabe de que contêiner veio a linha);
- a fixture ainda tem o defeito (o xlrd puro a recusa), então o teste prova o reparo;
- as células chegam com o mesmo padrão do `.xlsx`: código e grau inteiros,
  classificação e nome texto;
- as recusas do layout valem no `.xls` (grau divergente, conta fora do bloco), com
  motivo fechado e número de linha, sem o texto da célula;
- `.xls` truncado, HTML com extensão `.xls` e documento OLE2 que não é planilha
  recusam com `FORMATO_NAO_SUPORTADO` e motivo.
"""

from __future__ import annotations

import csv
import io
import json
import struct
from pathlib import Path

import pytest
import xlrd
from xlrd import compdoc

from app.core.exceptions import AppError, FileFormatNotSupportedError, FileLinesInvalidError
from app.db.models.client_accounting_account import AccountingAccountType
from app.modules.client_accounting_chart.dominio import convert_dominio, find_dominio_header
from app.modules.client_accounting_chart.sheet import parse_chart_sheet
from app.modules.client_file_ingestion.reader import read_sheet_raw_rows
from tests.xls_builder import EOF, NUMBER, XF_GENERAL, ole2, record

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FIXTURE = _FIXTURES / "accounting_chart_dominio_xls" / "plano_dominio.xls"
_EXPECTED = _FIXTURES / "accounting_chart_dominio_xls" / "plano_esperado.csv"
_XLSX_FIXTURE = _FIXTURES / "accounting_chart_dominio" / "plano_dominio.xlsx"

_HEADER_LINE = 5
_LAST_ACCOUNT_ROW = 375
#: Conta analítica de grau 5 na linha 100 (0-based 99): o grau mora na coluna X (23).
_ROW = 100
_GRADE_COLUMN = 23
#: Textos que existem na fixture e NUNCA podem aparecer numa resposta de recusa.
_FIXTURE_TEXTS = ("de exemplo", "EXEMPLO", "CPF", "CRC", "00.000.000")


def _fixture_bytes() -> bytes:
    return _FIXTURE.read_bytes()


def _stream() -> bytearray:
    stream = compdoc.CompDoc(_fixture_bytes(), logfile=io.StringIO()).get_named_stream("Workbook")
    assert stream is not None
    return bytearray(stream)


def _records(stream: bytes) -> list[tuple[int, int, int]]:
    """`(código, posição do cabeçalho do registro, tamanho)`, do início ao fim."""
    out: list[tuple[int, int, int]] = []
    pos = 0
    while pos + 4 <= len(stream):
        code, length = struct.unpack_from("<HH", stream, pos)
        out.append((code, pos, length))
        pos += 4 + length
    return out


def _with_number(row: int, col: int, value: float) -> bytes:
    """A fixture com o número da célula `(row, col)` (0-based) trocado, ou acrescentado."""
    stream = _stream()
    for code, pos, _ in _records(bytes(stream)):
        if code == NUMBER and struct.unpack_from("<HH", stream, pos + 4) == (row, col):
            struct.pack_into("<d", stream, pos + 10, value)
            return ole2(bytes(stream))
    # Célula nova: entra antes do EOF da aba (o último registro do stream).
    last_eof = max(pos for code, pos, _ in _records(bytes(stream)) if code == EOF)
    cell = record(NUMBER, struct.pack("<HHHd", row, col, XF_GENERAL, value))
    return ole2(bytes(stream[:last_eof]) + cell + bytes(stream[last_eof:]))


def _body(exc: AppError) -> str:
    return exc.user_message + json.dumps(exc.details, ensure_ascii=False)


def _refused_lines(content: bytes) -> FileLinesInvalidError:
    with pytest.raises(FileLinesInvalidError) as caught:
        parse_chart_sheet(content)
    body = _body(caught.value)
    for text in _FIXTURE_TEXTS:
        assert text not in body, "texto da planilha na resposta de recusa"
    assert "Domínio" in caught.value.user_message
    return caught.value


class TestTesteOuro:
    def test_a_fixture_entra_inteira_e_bate_conta_a_conta(self) -> None:
        parsed = parse_chart_sheet(_fixture_bytes())
        assert parsed.layout == "dominio"
        with _EXPECTED.open(encoding="utf-8", newline="") as fh:
            expected = [
                (int(r["linha"]), r["codigo_reduzido"], r["nome"], r["tipo"], r["classificacao"])
                for r in csv.DictReader(fh, delimiter=";")
            ]
        got = [
            (r.line, r.code, r.name, r.account_type.value, r.classification) for r in parsed.rows
        ]
        assert got == expected

    def test_370_contas_141_sinteticas_e_a_hierarquia_fecha(self) -> None:
        rows = parse_chart_sheet(_fixture_bytes()).rows
        assert len(rows) == 370
        synthetic = [r for r in rows if r.account_type is AccountingAccountType.SINTETICA]
        assert len(synthetic) == 141
        assert (rows[0].line, rows[-1].line) == (_HEADER_LINE + 1, _LAST_ACCOUNT_ROW)
        classifications = {r.classification for r in rows}
        assert len(classifications) == 370
        parents = {c.rsplit(".", 1)[0] for c in classifications if c and "." in c}
        assert parents <= classifications
        assert not [
            r
            for r in rows
            if r.account_type is AccountingAccountType.ANALITICA and r.classification in parents
        ]

    def test_a_fixture_tem_o_defeito_do_export_do_dominio(self) -> None:
        # Sem o reparo do leitor, o arquivo não abre: é isso que o teste-ouro prova.
        with pytest.raises(xlrd.XLRDError):
            xlrd.open_workbook(file_contents=_fixture_bytes(), logfile=io.StringIO())

    def test_o_rodape_nao_vira_conta_nem_recusa(self) -> None:
        raw = read_sheet_raw_rows(_fixture_bytes())
        conversion = convert_dominio(raw, header_line=_HEADER_LINE)
        assert conversion.problems == []
        assert [line for line, cells in raw if line > _LAST_ACCOUNT_ROW and cells] == [377, 379]


class TestMesmaRegraDoXlsx:
    def test_o_cabecalho_e_achado_na_mesma_linha_nos_dois_conteineres(self) -> None:
        for path in (_FIXTURE, _XLSX_FIXTURE):
            assert find_dominio_header(read_sheet_raw_rows(path.read_bytes())) == _HEADER_LINE

    def test_a_celula_chega_com_o_mesmo_padrao_do_xlsx(self) -> None:
        def pattern(content: bytes) -> list[str]:
            cells = dict(read_sheet_raw_rows(content))[_HEADER_LINE + 1]
            return [type(c).__name__ for c in cells if c is not None]

        # código · S · classificação · nome · grau, nos dois.
        assert pattern(_fixture_bytes()) == ["int", "str", "str", "str", "int"]
        assert pattern(_fixture_bytes()) == pattern(_XLSX_FIXTURE.read_bytes())


class TestRecusasDoLayout:
    def test_grau_divergente_recusa_com_a_linha(self) -> None:
        refused = _refused_lines(_with_number(_ROW - 1, _GRADE_COLUMN, 9))
        assert refused.details == {
            "lines": [{"line": _ROW, "reason": "grau_divergente"}],
            "total": 1,
        }

    def test_conta_depois_do_rodape_recusa_o_arquivo_inteiro(self) -> None:
        refused = _refused_lines(_with_number(381 - 1, 0, 999))
        assert refused.details == {
            "lines": [{"line": 381, "reason": "conta_fora_do_bloco"}],
            "total": 1,
        }


class TestArquivoQueNaoEPlanilha:
    @pytest.mark.parametrize(
        "content",
        [
            _FIXTURE.read_bytes()[:1024],
            "<html><head><meta charset='utf-8'></head><body><table><tr><td>Código</td>"
            "<td>Nome</td></tr>\n</table></body></html>".encode(),
            ole2(b"\x00" * 64, name="WordDocument"),
        ],
        ids=["xls_truncado", "html_salvo_como_xls", "documento_office_que_nao_e_planilha"],
    )
    def test_recusa_com_formato_nao_suportado_e_motivo(self, content: bytes) -> None:
        with pytest.raises(FileFormatNotSupportedError) as caught:
            parse_chart_sheet(content)
        assert caught.value.status_code == 422
        assert caught.value.user_message != FileFormatNotSupportedError.default_user_message
        for text in _FIXTURE_TEXTS:
            assert text not in _body(caught.value)
