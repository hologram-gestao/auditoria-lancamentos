"""A planilha de importação do plano de contas da Omie (86e3qhhrr), sem banco.

O que este módulo afirma:

1. o MODELO versionado é o vazio: 46 strings do modelo, nenhum documento, nenhuma linha
   de dado, as nove validações de dados cobrindo a área inteira (6 a 10005);
2. o teste-ouro: o plano do Domínio anonimizado (`accounting_chart_dominio_xls/`) com
   os vínculos das relações anonimizadas (`accounting_chart_dominio_parties/`) gera,
   byte a byte, as partes registradas em `tests/fixtures/omie_chart_export/`
   (`expected_parts.json`, SHA-256 de cada parte DESCOMPRIMIDA, que não depende da
   versão da zlib) e as células de `expected_cells.csv`;
3. mesma entrada, mesmos bytes (o arquivo inteiro, zip inclusive);
4. só a aba e as strings mudam: validações, proteção, `Config`, estilos e desenho
   ficam byte a byte iguais ao modelo, e o `openpyxl` lê o resultado;
5. cada motivo de recusa, com os códigos das contas e nunca o texto.

Para regenerar o ouro depois de uma mudança DELIBERADA no gerador:
`UPDATE_OMIE_GOLDEN=1 uv run --extra dev pytest tests/unit/test_omie_chart_sheet.py`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import warnings
import zipfile
from pathlib import Path

import openpyxl
import pytest

from app.db.models.client_accounting_account import (
    AccountingAccountNature,
    AccountingAccountType,
)
from app.modules.client_accounting_chart.nature import derive_nature
from app.modules.client_accounting_chart.omie_sheet import (
    FIRST_DATA_ROW,
    LAST_DATA_ROW,
    MAX_ACCOUNTS,
    SHARED_STRINGS_PART,
    SHEET_PART,
    TEMPLATE_PATH,
    TEMPLATE_VERSION,
    OmieChartLine,
    OmieExportProblem,
    build_omie_chart_xlsx,
    find_problems,
    format_document,
    omie_export_filename,
    problems_details,
)
from app.modules.client_accounting_chart.parties import parse_parties_report
from app.modules.client_accounting_chart.sheet import parse_chart_sheet
from app.modules.client_accounting_chart.sort_key import chart_sort_key

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_PLAN = _FIXTURES / "accounting_chart_dominio_xls" / "plano_dominio.xls"
_CLIENTS = _FIXTURES / "accounting_chart_dominio_parties" / "relacao_clientes.xls"
_SUPPLIERS = _FIXTURES / "accounting_chart_dominio_parties" / "relacao_fornecedores.xls"
_GOLDEN = _FIXTURES / "omie_chart_export"
_GOLDEN_PARTS = _GOLDEN / "expected_parts.json"
_GOLDEN_CELLS = _GOLDEN / "expected_cells.csv"
_COLUMNS = "BCDEFGHIJ"

D = AccountingAccountNature.DEVEDORA
C = AccountingAccountNature.CREDORA
A = AccountingAccountType.ANALITICA
S = AccountingAccountType.SINTETICA


def _line(code: str = "1", **overrides: object) -> OmieChartLine:
    values: dict[str, object] = {
        "code": code,
        "classification": "1.1",
        "name": "Caixa",
        "nature": D,
        "account_type": A,
    }
    values.update(overrides)
    return OmieChartLine(**values)  # type: ignore[arg-type]


def _golden_lines() -> list[OmieChartLine]:
    """O plano anonimizado do Domínio, na ordem do plano, com os vínculos das relações.

    As relações são de outra empresa que o plano; as contas em comum (analíticas no
    plano) recebem o documento, como a importação das relações faria.
    """
    rows = parse_chart_sheet(_PLAN.read_bytes()).rows
    documents: dict[str, str] = {}
    for report in (_CLIENTS, _SUPPLIERS):
        for line in parse_parties_report(report.read_bytes()).lines:
            documents[line.account_code] = line.document
    ordered = sorted(rows, key=lambda r: (chart_sort_key(r.classification, r.code), r.code))
    return [
        OmieChartLine(
            code=row.code,
            classification=row.classification,
            name=row.name,
            nature=derive_nature(row.classification),
            account_type=row.account_type,
            document=documents.get(row.code) if row.account_type is A else None,
        )
        for row in ordered
    ]


def _parts(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        return {info.filename: package.read(info.filename) for info in package.infolist()}


def _cells(content: bytes) -> list[list[str]]:
    with warnings.catch_warnings():
        # O openpyxl avisa que não lê a extensão de validação; aqui só se leem valores.
        warnings.simplefilter("ignore")
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    sheet = workbook["Omie_Plano_Contas"]
    out: list[list[str]] = []
    for row in sheet.iter_rows(min_row=FIRST_DATA_ROW, min_col=2, max_col=10, values_only=True):
        if all(value is None for value in row):
            break
        out.append(["" if value is None else str(value) for value in row])
    workbook.close()
    return out


class TestOModeloVersionadoEOVazio:
    def test_tem_so_as_strings_do_modelo(self) -> None:
        parts = _parts(TEMPLATE_PATH.read_bytes())
        sst = parts[SHARED_STRINGS_PART].decode("utf-8")
        assert sst.count("<si>") == 46
        assert 'uniqueCount="46"' in sst
        assert f"<t>{TEMPLATE_VERSION}</t>" in sst

    def test_nenhuma_linha_de_dado_nem_documento(self) -> None:
        parts = _parts(TEMPLATE_PATH.read_bytes())
        sheet = parts[SHEET_PART].decode("utf-8")
        data = sheet[
            sheet.index(f'<row r="{FIRST_DATA_ROW}"') : sheet.index(f'<row r="{LAST_DATA_ROW}"')
        ]
        assert "<v>" not in data
        for name, raw in parts.items():
            if name.endswith(".xml"):
                text = raw.decode("utf-8")
                assert not re.findall(r"(?<![\w.\-])\d{11,}(?![\w.\-}])", text), name
        assert "lastModifiedBy" not in parts["docProps/core.xml"].decode()
        assert "absPath" not in parts["xl/workbook.xml"].decode()

    def test_as_validacoes_cobrem_a_area_inteira(self) -> None:
        sheet = _parts(TEMPLATE_PATH.read_bytes())[SHEET_PART].decode("utf-8")
        sqrefs = re.findall(r"<xm:sqref>([^<]*)</xm:sqref>", sheet)
        assert sorted(sqrefs) == sorted(
            f"{col}{FIRST_DATA_ROW}:{col}{LAST_DATA_ROW}" for col in "BCDEFGHIJ"
        )
        assert 'x14:dataValidations count="9"' in sheet
        assert "<sheetProtection " in sheet

    def test_a_area_do_modelo_e_a_do_gerador(self) -> None:
        workbook = _parts(TEMPLATE_PATH.read_bytes())["xl/workbook.xml"].decode()
        assert f"Omie_Plano_Contas!$B${FIRST_DATA_ROW}:$J${LAST_DATA_ROW}" in workbook
        assert MAX_ACCOUNTS == 10_000


class TestTesteOuro:
    def test_o_plano_anonimizado_gera_o_arquivo_registrado(self) -> None:
        lines = _golden_lines()
        assert find_problems(lines) == []
        content = build_omie_chart_xlsx(lines)
        parts = _parts(content)
        hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in parts.items()}
        cells = _cells(content)
        if os.environ.get("UPDATE_OMIE_GOLDEN"):
            _GOLDEN.mkdir(parents=True, exist_ok=True)
            _GOLDEN_PARTS.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
            with _GOLDEN_CELLS.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh, delimiter=";", lineterminator="\n")
                writer.writerow(list(_COLUMNS))
                writer.writerows(cells)
        assert hashes == json.loads(_GOLDEN_PARTS.read_text())
        with _GOLDEN_CELLS.open(encoding="utf-8", newline="") as fh:
            expected = list(csv.reader(fh, delimiter=";"))[1:]
        assert cells == expected

    def test_as_contas_em_comum_saem_com_o_documento_mascarado(self) -> None:
        lines = _golden_lines()
        linked = [line for line in lines if line.document is not None]
        assert len(linked) == 26
        cells = {row[1]: row for row in _cells(build_omie_chart_xlsx(lines))}
        for line in linked:
            document = cells[line.code][6]
            assert document == format_document(line.document or "")
            assert re.fullmatch(
                r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}", document
            )
        assert all(cells[line.code][6] == "" for line in lines if line.document is None)

    def test_a_natureza_e_o_tipo_saem_no_vocabulario_do_modelo(self) -> None:
        cells = _cells(build_omie_chart_xlsx(_golden_lines()))
        assert {row[3] for row in cells} == {"Devedora", "Credora"}
        assert {row[4] for row in cells} == {"Analítica", "Totalizadora (Sintética)"}
        assert all(row[5] == row[7] == row[8] == "" for row in cells)


class TestMesmaEntradaMesmosBytes:
    def test_duas_geracoes_dao_o_mesmo_arquivo(self) -> None:
        lines = _golden_lines()
        assert build_omie_chart_xlsx(lines) == build_omie_chart_xlsx(lines)

    def test_so_a_aba_e_as_strings_mudam(self) -> None:
        template = _parts(TEMPLATE_PATH.read_bytes())
        generated = _parts(build_omie_chart_xlsx(_golden_lines()))
        assert list(template) == list(generated)
        changed = {name for name in template if template[name] != generated[name]}
        assert changed == {SHEET_PART, SHARED_STRINGS_PART}
        sheet_t = template[SHEET_PART].decode("utf-8")
        sheet_g = generated[SHEET_PART].decode("utf-8")
        assert sheet_g[sheet_g.index("</sheetData>") :] == sheet_t[sheet_t.index("</sheetData>") :]
        assert sheet_g[: sheet_g.index("<sheetData>")] == sheet_t[: sheet_t.index("<sheetData>")]

    def test_a_contagem_das_strings_bate(self) -> None:
        content = build_omie_chart_xlsx(_golden_lines())
        parts = _parts(content)
        sst = parts[SHARED_STRINGS_PART].decode("utf-8")
        refs = sum(
            part.decode("utf-8").count(' t="s"><v>')
            for name, part in parts.items()
            if name.startswith("xl/worksheets/sheet")
        )
        count = int(re.search(r'count="(\d+)"', sst).group(1))  # type: ignore[union-attr]
        unique = int(re.search(r'uniqueCount="(\d+)"', sst).group(1))  # type: ignore[union-attr]
        assert count == refs
        assert unique == sst.count("<si>")


class TestTexto:
    def test_escapa_e_preserva_espaco(self) -> None:
        content = build_omie_chart_xlsx([_line(name="A & B <c>"), _line(code="2", name=" Caixa ")])
        sst = _parts(content)[SHARED_STRINGS_PART].decode("utf-8")
        assert "<t>A &amp; B &lt;c&gt;</t>" in sst
        assert '<t xml:space="preserve"> Caixa </t>' in sst
        assert [row[2] for row in _cells(content)] == ["A & B <c>", " Caixa "]

    def test_reaproveita_as_strings_do_modelo(self) -> None:
        content = build_omie_chart_xlsx([_line(), _line(code="2")])
        sst = _parts(content)[SHARED_STRINGS_PART].decode("utf-8")
        assert sst.count("<t>Devedora</t>") == 1
        assert sst.count("<t>Analítica</t>") == 1
        assert sst.count("<t>Caixa</t>") == 1

    def test_documento_com_mascara(self) -> None:
        assert format_document("11222333000181") == "11.222.333/0001-81"
        assert format_document("12345678909") == "123.456.789-09"
        with pytest.raises(ValueError, match="tamanho"):
            format_document("123")

    def test_nome_do_arquivo_nao_leva_o_nome_do_cliente(self) -> None:
        from uuid import UUID

        client_id = UUID("12345678-1234-5678-1234-567812345678")
        assert omie_export_filename(client_id) == "Omie_Plano_Contas_12345678.xlsx"


class TestRecusas:
    def test_plano_vazio(self) -> None:
        assert find_problems([]) == [OmieExportProblem("plano_vazio")]
        with pytest.raises(ValueError, match="problema"):
            build_omie_chart_xlsx([])

    def test_plano_maior_que_a_area_do_modelo(self) -> None:
        lines = [_line(code=str(i)) for i in range(MAX_ACCOUNTS + 1)]
        assert find_problems(lines) == [OmieExportProblem("plano_grande_demais")]
        assert find_problems(lines[:MAX_ACCOUNTS]) == []

    def test_cada_motivo_nomeia_so_os_codigos(self) -> None:
        lines = [
            _line(code="10", classification=None, nature=None),
            _line(code="11", name=None),
            _line(code="12", document_failed=True),
            _line(code="13", document="123"),
            _line(code="14", name="=SOMA(A1)"),
            _line(code="15", name="x" * 61),
            _line(code="16", name="Linha\nquebrada"),
            _line(code="17", classification="-1"),
            _line(code="18"),
        ]
        problems = find_problems(lines)
        assert [(p.reason, p.account_codes) for p in problems] == [
            ("conta_sem_classificacao", ("10",)),
            ("conta_sem_natureza", ("10",)),
            ("nome_indecifravel", ("11",)),
            ("documento_indecifravel", ("12", "13")),
            ("texto_formula", ("14", "17")),
            ("texto_longo", ("15",)),
            ("texto_invalido", ("16",)),
        ]
        details = problems_details(problems)
        assert details[3] == {
            "reason": "documento_indecifravel",
            "accountCodes": ["12", "13"],
            "count": 2,
        }
        # Nenhum texto da conta sai, só códigos.
        assert "SOMA" not in json.dumps(details)
        assert "quebrada" not in json.dumps(details)

    def test_os_codigos_param_em_50_e_a_contagem_e_a_real(self) -> None:
        lines = [_line(code=str(i), classification=None) for i in range(60)]
        details = problems_details(find_problems(lines))
        assert details[0]["count"] == 60
        assert len(details[0]["accountCodes"]) == 50  # type: ignore[arg-type]

    def test_limites_no_teto_exato_passam(self) -> None:
        lines = [_line(code="x" * 20, classification="1" + "." * 59, name="n" * 60)]
        assert find_problems(lines) == []
        assert _cells(build_omie_chart_xlsx(lines))[0][:3] == ["1" + "." * 59, "x" * 20, "n" * 60]

    def test_linha_com_problema_nao_entra(self) -> None:
        with pytest.raises(ValueError, match="problema"):
            build_omie_chart_xlsx([_line(nature=None)])

    def test_a_ordem_recebida_e_a_ordem_da_planilha(self) -> None:
        lines = [
            _line(code="3", classification="2", nature=C, account_type=S),
            _line(code="1", classification="1"),
        ]
        cells = _cells(build_omie_chart_xlsx(lines))
        assert [row[:5] for row in cells] == [
            ["2", "3", "Caixa", "Credora", "Totalizadora (Sintética)"],
            ["1", "1", "Caixa", "Devedora", "Analítica"],
        ]
