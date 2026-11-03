"""Gera a FIXTURE anonimizada do plano de contas da Tract (86e3ydjyq, épico 86e3ydjy9).

A planilha que a Tract mandou em 30/10/2026 ("plano de contas ajustado", feito a partir do
Domínio para a Omie) não segue o modelo da plataforma nem o export do Domínio: é o caso
que o reconhecimento por CONTEÚDO resolve. Ela tem dado pessoal (razão social e CNPJ da
empresa no banner, CNPJ/CPF e razão social de fornecedores) e NUNCA entra no repositório.
O que entra é a cópia que este script produz, com a ESTRUTURA preservada (as duas abas,
posições, tipos de célula, ordem, banner, cabeçalho, coluna de natureza, coluna de
documento só nas contas de fornecedor) e todo texto que pode identificar alguém trocado:

- banner das duas abas: vira um texto fixo fictício (empresa, CNPJ e título);
- aba "Plano de Contas": código, classificação, tipo, grau e natureza ficam; o NOME vira
  `Conta de exemplo <classificação>` (ou `Grupo de exemplo …` na sintética), completado
  até o comprimento do original;
- documento (coluna do plano e aba "Fornecedores Ajustados"): cada CNPJ/CPF vira um
  fictício do MESMO tipo (14 ou 11 dígitos), com a máscara se o original tinha máscara e
  a MESMA validade pelo dígito verificador; o mesmo original vira o mesmo fictício nas
  duas abas (o vínculo entre elas continua provável); `0` e o que não tem forma de
  documento ficam como estão (não identificam ninguém);
- razão social da aba "Fornecedores Ajustados": `Fornecedor de exemplo <n>`;
- metadados do arquivo (autor, último a salvar);
- a coluna de documento do plano é uma fórmula no original (busca o CNPJ na outra aba
  pelo nome); a fixture guarda o VALOR calculado, que é o que a plataforma lê.

O script não imprime célula nenhuma; a conferência final (nenhum texto nem documento do
original sobrou no XML da fixture) imprime só contagens.

Uso (de `apps/api/`; o caminho do original é argumento, nunca escrito aqui):

    uv run python -m scripts.anonymize_tract_chart_sample <original.xlsx> <saida_dir>

Saída: `<saida_dir>/plano.xlsx` e `<saida_dir>/plano_esperado.csv` (as contas que a
importação tem de produzir, no modelo da plataforma: o teste-ouro compara).
"""

from __future__ import annotations

import csv
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import load_workbook

from app.core.cnpj import format_cnpj
from app.core.cpf import format_cpf
from app.modules.client_accounting_chart.recognizer import (
    as_classification,
    recognize_columns,
    type_mark,
)
from app.modules.client_accounting_chart.sheet import MODEL_COLUMNS, parse_chart_sheet
from app.modules.client_file_ingestion.reader import read_sheet_raw_rows
from scripts.anonymize_dominio_parties_sample_xls import _is_valid, fake_document

BANNER = ("EMPRESA EXEMPLO LTDA", "C.N.P.J.: 99.999.999/9999-99", "PLANO DE CONTAS (exemplo)")
FILE_AUTHOR = "Hologram OS (fixture anonimizada)"
_FILLER = " Alfa Beta Gama Delta Épsilon Zeta Eta Teta Iota Capa Lambda Ômega" * 4
_MASK = re.compile(r"[./\-]")


def _padded(base: str, original: str) -> str:
    if len(original) <= len(base):
        return base
    return (base + _FILLER)[: len(original)].rstrip()


class _Documents:
    """O mesmo documento original vira sempre o mesmo fictício, nas duas abas."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self.originals: set[str] = set()

    def fake(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        digits = re.sub(r"\D", "", value)
        if len(digits) not in (11, 14):
            return value
        self.originals.add(digits)
        if digits not in self._map:
            index = len(self._map) + 1
            fake = fake_document(
                index,
                length=len(digits),
                valid=_is_valid(digits),
                leading_zero=digits.startswith("0"),
            )
            self._map[digits] = fake
        fake = self._map[digits]
        if not _MASK.search(value):
            return fake
        return format_cnpj(fake) if len(fake) == 14 else format_cpf(fake)


def _banner(ws: Any, header_row: int) -> None:
    texts = iter(BANNER)
    for row in ws.iter_rows(max_row=header_row - 1):
        for cell in row:
            if isinstance(cell.value, str):
                cell.value = next(texts, "[removido]")


def _chart_sheet(ws: Any, documents: _Documents) -> None:
    raw = [(row[0].row, [c.value for c in row]) for row in ws.iter_rows()]
    recognition = recognize_columns(raw)
    if recognition is None or not recognition.resolved:
        sys.exit("o plano não foi reconhecido: o layout mudou, nada foi gerado")
    name_col = recognition.roles["nome"][0]
    class_col = recognition.roles["classificacao"][0]
    type_col = recognition.roles["tipo"][0]
    doc_col = recognition.roles.get("documento", (None,))[0]
    _banner(ws, recognition.header_line or recognition.block[0])
    block = set(recognition.block)
    for row in ws.iter_rows():
        if row[0].row not in block:
            continue
        name = row[name_col]
        if isinstance(name.value, str):
            classification = as_classification(row[class_col].value) or "?"
            synthetic = type_mark(row[type_col].value)
            kind = "Grupo" if synthetic is not None and synthetic.value == "sintetica" else "Conta"
            name.value = _padded(f"{kind} de exemplo {classification}", name.value)
        if doc_col is not None and doc_col < len(row):
            row[doc_col].value = documents.fake(row[doc_col].value)


def _parties_sheet(ws: Any, documents: _Documents) -> None:
    header_row = next(
        row[0].row
        for row in ws.iter_rows()
        if sum(isinstance(c.value, str) for c in row) >= 3 and row[0].row > 1
    )
    _banner(ws, header_row)
    for index, row in enumerate(ws.iter_rows(min_row=header_row + 1), start=1):
        cells = list(row)
        if len(cells) >= 3 and isinstance(cells[2].value, str):
            cells[2].value = _padded(f"Fornecedor de exemplo {index}", cells[2].value)
        if len(cells) >= 4:
            cells[3].value = documents.fake(cells[3].value)


def anonymize(source: Path, out_dir: Path) -> None:
    # `data_only`: a coluna de documento do plano é uma FÓRMULA (um PROCV na aba de
    # fornecedores, pelo nome). A plataforma lê o valor calculado que o Excel guardou, e
    # é ele que a fixture grava no lugar da fórmula, que dependeria dos nomes originais.
    wb = load_workbook(source, data_only=True)
    documents = _Documents()
    _chart_sheet(wb.worksheets[0], documents)
    if len(wb.worksheets) > 1:
        _parties_sheet(wb.worksheets[1], documents)
    wb.properties.creator = FILE_AUTHOR
    wb.properties.lastModifiedBy = FILE_AUTHOR
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "plano.xlsx"
    wb.save(target)

    parsed = parse_chart_sheet(target.read_bytes())
    with (out_dir / "plano_esperado.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";", lineterminator="\n")
        writer.writerow(["linha", *MODEL_COLUMNS])
        for r in parsed.rows:
            writer.writerow([r.line, r.code, r.name, r.account_type.value, r.classification])
    _check_no_leak(source, target, documents)
    print(f"contas={len(parsed.rows)} layout={parsed.layout} documentos={len(documents.originals)}")


#: Texto que é ESTRUTURA (não identifica ninguém) e pode sobrar na fixture.
_KEEP = frozenset(
    {
        "Código",
        "Classificação",
        "Tipo",
        "Grau",
        "Natureza",
        "Nome da conta",
        "Conta",
        "Razão Social",
        "C.N.P.J./C.P.F.",
        "S",
        "A",
        "Devedora",
        "Credora",
        "\N{EM DASH}",
        "0",
        "Plano de Contas",
        "Fornecedores Ajustados",
    }
)


def _check_no_leak(source: Path, target: Path, documents: _Documents) -> None:
    """Nenhum texto nem documento do original (fora a estrutura) no XML da fixture."""
    wb = load_workbook(source, read_only=True)
    originals = {
        str(v).strip()
        for ws in wb.worksheets
        for row in ws.iter_rows(values_only=True)
        for v in row
        if isinstance(v, str)
        and v.strip()
        and v.strip() not in _KEEP
        and as_classification(v) is None
    }
    props = wb.properties
    originals |= {p for p in (props.creator, props.lastModifiedBy) if p}
    wb.close()
    originals |= documents.originals
    with zipfile.ZipFile(target) as archive:
        blob = "\n".join(
            archive.read(name).decode("utf-8", errors="replace") for name in archive.namelist()
        )
    leaked = sum(1 for text in originals if text in blob or escape(text) in blob)
    print(f"textos_originais={len(originals)} encontrados_na_fixture={leaked}")
    if leaked:
        sys.exit("texto do original sobrou na fixture: NÃO versione o arquivo gerado")
    # A leitura da fixture pelo leitor da plataforma (o mesmo da importação) não pode falhar.
    read_sheet_raw_rows(target.read_bytes())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    anonymize(Path(sys.argv[1]), Path(sys.argv[2]))
