"""O plano da Tract exportado do Domínio em `.xls`, na importação do plano contábil (86e3ydk0c).

O teste-ouro importa a FIXTURE anonimizada do export real (04/11/2026) e compara conta a
conta com `plano_esperado.csv`, como o do `.xls` do Gabriel
(`test_accounting_chart_dominio_xls.py`) faz com a fixture dele.

O que é provado aqui:
- o arquivo entra inteiro pelo caminho do Domínio, sem linha com problema, com e sem a
  prévia: 150 contas, 63 sintéticas, cabeçalho na linha 7 (o banner tem `Emissão:` e
  `Hora:` e uma linha vazia a mais que o da amostra do Gabriel);
- é o export REGRAVADO pelo Excel: abre direto no xlrd (sem o defeito dos BLANKs), e a
  fixture não leva as propriedades do Office nem o usuário do WRITEACCESS do original;
- é um recorte do plano "ajustado" da Tract (`accounting_chart_tract/`): as 150 contas
  estão lá com o mesmo código e o mesmo tipo, e só quatro mudam de classificação: as que
  o Domínio numera com a classificação de OUTRA conta (o caso da 86e3uyzcf), e que o
  ajustado renumerou. É a resposta do critério de aceite ("bate com o plano ajustado nas
  contas em comum"), com as diferenças nomeadas.
"""

from __future__ import annotations

import csv
import io
import struct
from pathlib import Path

import xlrd
from xlrd import compdoc

from app.db.models.client_accounting_account import AccountingAccountType
from app.modules.client_accounting_chart.dominio import convert_dominio, find_dominio_header
from app.modules.client_accounting_chart.sheet import parse_chart_sheet
from app.modules.client_file_ingestion.reader import read_sheet_raw_rows

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FIXTURE = _FIXTURES / "accounting_chart_dominio_tract" / "plano_dominio.xls"
_EXPECTED = _FIXTURES / "accounting_chart_dominio_tract" / "plano_esperado.csv"
_ADJUSTED = _FIXTURES / "accounting_chart_tract" / "plano.xlsx"

_HEADER_LINE = 7
_FIRST_ACCOUNT_ROW = 8
_LAST_ACCOUNT_ROW = 157
_FOOTER_ROW = 161
_WRITEACCESS = 0x005C
#: As quatro analíticas que o Domínio numera com a classificação de OUTRA conta, e que o
#: plano ajustado renumerou: `código → (classificação no export, no ajustado)`.
_RENUMBERED = {
    "1232": ("3.2.2.03.001", "3.2.2.03.011"),
    "1330": ("3.2.2.04.018", "3.2.2.04.030"),
    "1620": ("3.2.2.04.027", "3.2.2.04.034"),
    "2078": ("3.2.1.06.005", "3.2.1.06.008"),
}
#: Quem tem, no plano ajustado, a classificação que o export dá a cada uma delas.
_CLASSIFICATION_OWNER = {"1232": "346", "1330": "1243", "1620": "1418", "2078": "324"}


def _fixture_bytes() -> bytes:
    return _FIXTURE.read_bytes()


def _doc() -> compdoc.CompDoc:
    return compdoc.CompDoc(_fixture_bytes(), logfile=io.StringIO())


class TestTesteOuro:
    def test_a_fixture_entra_inteira_e_bate_conta_a_conta(self) -> None:
        parsed = parse_chart_sheet(_fixture_bytes())
        assert parsed.layout == "dominio"
        assert parsed.problems == ()
        with _EXPECTED.open(encoding="utf-8", newline="") as fh:
            expected = [
                (int(r["linha"]), r["codigo_reduzido"], r["nome"], r["tipo"], r["classificacao"])
                for r in csv.DictReader(fh, delimiter=";")
            ]
        got = [
            (r.line, r.code, r.name, r.account_type.value, r.classification) for r in parsed.rows
        ]
        assert got == expected

    def test_com_a_previa_tambem_nao_tem_linha_com_problema(self) -> None:
        parsed = parse_chart_sheet(_fixture_bytes(), allow_problems=True)
        assert (parsed.layout, len(parsed.rows), parsed.problems) == ("dominio", 150, ())

    def test_150_contas_63_sinteticas_e_a_hierarquia_fecha(self) -> None:
        rows = parse_chart_sheet(_fixture_bytes()).rows
        assert len(rows) == 150
        synthetic = [r for r in rows if r.account_type is AccountingAccountType.SINTETICA]
        assert len(synthetic) == 63
        assert (rows[0].line, rows[-1].line) == (_FIRST_ACCOUNT_ROW, _LAST_ACCOUNT_ROW)
        classifications = {r.classification for r in rows}
        # 1243 e 1330 vêm com a MESMA classificação, como o Domínio aceita (86e3uyzcf).
        assert len(classifications) == 149
        assert [r.code for r in rows if r.classification == "3.2.2.04.018"] == ["1243", "1330"]
        parents = {c.rsplit(".", 1)[0] for c in classifications if c and "." in c}
        assert parents <= classifications
        # Toda sintética tem filho no arquivo: o recorte não deixa grupo vazio.
        assert {r.classification for r in synthetic} == parents

    def test_cabecalho_na_linha_7_e_o_rodape_nao_vira_conta(self) -> None:
        raw = read_sheet_raw_rows(_fixture_bytes())
        assert find_dominio_header(raw) == _HEADER_LINE
        assert convert_dominio(raw, header_line=_HEADER_LINE).problems == []
        assert [line for line, cells in raw if line > _LAST_ACCOUNT_ROW and any(cells)] == [
            _FOOTER_ROW
        ]


class TestRegravadoPeloExcel:
    def test_abre_direto_no_xlrd_sem_o_reparo_dos_blanks(self) -> None:
        # O `.xls` do Gabriel só abre com o reparo; este, como o Excel o salvou, abre puro.
        book = xlrd.open_workbook(file_contents=_fixture_bytes(), logfile=io.StringIO())
        assert book.sheet_by_index(0).nrows == _FOOTER_ROW

    def test_as_propriedades_do_office_ficaram_vazias(self) -> None:
        doc = _doc()
        for name in ("\x05SummaryInformation", "\x05DocumentSummaryInformation"):
            stream = doc.get_named_stream(name)
            assert stream is not None
            assert struct.unpack_from("<I", stream, 24)[0] == 1
            section = struct.unpack_from("<I", stream, 44)[0]
            assert struct.unpack_from("<II", stream, section) == (8, 0)
            assert set(stream[section + 8 :]) == {0}

    def test_o_usuario_do_writeaccess_e_o_ficticio(self) -> None:
        stream = _doc().get_named_stream("Workbook")
        assert stream is not None
        pos = 0
        while True:
            code, length = struct.unpack_from("<HH", stream, pos)
            if code == _WRITEACCESS:
                break
            pos += 4 + length
        nchars, options = struct.unpack_from("<HB", stream, pos + 4)
        assert not options & 0x01
        user = stream[pos + 7 : pos + 7 + nchars].decode("latin-1")
        assert "Hologram OS (fixture anonimizada)".startswith(user)


class TestRecorteDoPlanoAjustado:
    def test_as_150_contas_estao_no_ajustado_com_o_mesmo_codigo_e_tipo(self) -> None:
        export = {r.code: r for r in parse_chart_sheet(_fixture_bytes()).rows}
        adjusted = {r.code: r for r in parse_chart_sheet(_ADJUSTED.read_bytes()).rows}
        assert len(adjusted) == 1850
        assert set(export) <= set(adjusted)
        assert [c for c in export if export[c].account_type != adjusted[c].account_type] == []

    def test_so_as_quatro_renumeradas_mudam_de_classificacao(self) -> None:
        export = {r.code: r for r in parse_chart_sheet(_fixture_bytes()).rows}
        adjusted = {r.code: r for r in parse_chart_sheet(_ADJUSTED.read_bytes()).rows}
        changed = {
            code: (export[code].classification, adjusted[code].classification)
            for code in export
            if export[code].classification != adjusted[code].classification
        }
        assert changed == _RENUMBERED
        by_classification = {r.classification: r.code for r in adjusted.values()}
        owners = {code: by_classification[export[code].classification] for code in changed}
        assert owners == _CLASSIFICATION_OWNER
