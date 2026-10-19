"""Extrai o MODELO VAZIO da planilha de plano de contas da Omie (86e3qhhrr).

A amostra que o Gabriel mandou em 19/10/2026 é o modelo `Omie_Plano_Contas` v1.1.3
PREENCHIDO com um cliente real (nomes de conta, CNPJs e CPFs): ela nunca entra no
repositório. O que entra é o que este script tira dela, em
`app/resources/omie/Omie_Plano_Contas_v1_1_3.xlsx`:

- a aba `Omie_Plano_Contas` com as linhas 1 a 5 (instruções e cabeçalho) e a linha
  10006 (o rodapé do modelo) como vieram, e as linhas 6 a 10005 VAZIAS, todas com o
  estilo da linha vazia do modelo (texto, desbloqueada) e a última com a borda de
  baixo que o modelo tem;
- as validações de dados (`x14:dataValidations`) com a área INTEIRA, de 6 a 10005: na
  amostra, colar os dados tirou a validação das linhas coladas (o `sqref` virou
  `B502:B10005`, `H6:H20 H92:H10005`), e o modelo vazio volta ao que o Excel teria;
- a janela de exibição na linha 6 (a amostra estava rolada no meio dos dados);
- `sharedStrings.xml` só com as 46 strings do modelo (as da aba `Config`, das
  instruções e do cabeçalho), sem nenhuma da amostra;
- a aba `Config`, o desenho, o tema, os estilos e a proteção como vieram;
- sem `lastModifiedBy` (o nome de quem preencheu) nem o caminho da pasta local.

A conferência final procura cada string da amostra que não é do modelo, e toda
sequência de 11 ou mais dígitos, em todas as partes do arquivo gerado; imprime só
contagens. O script não imprime célula nenhuma.

Uso (de `apps/api/`; o caminho da amostra é argumento, nunca escrito aqui):

    uv run python -m scripts.extract_omie_chart_template <amostra.xlsx> <saida.xlsx>
"""

from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

from app.modules.client_accounting_chart.omie_sheet import (
    FIRST_DATA_ROW,
    LAST_DATA_ROW,
    SHARED_STRINGS_PART,
    SHEET_PART,
    TEMPLATE_VERSION,
)

#: Quantas strings o modelo tem antes das da amostra (`Config`, instruções, cabeçalho).
TEMPLATE_STRINGS = 46
#: A linha vazia do modelo: os estilos das colunas A a K (texto e desbloqueados).
_EMPTY_ROW_STYLES = ("63", "55", "23", "24", "25", "25", "6", "6", "59", "13", "63")
_COLUMNS = "ABCDEFGHIJK"
_ROW_ATTRS = ' spans="1:11" s="64" customFormat="1" x14ac:dyDescent="0.2"'
_SST_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_ROW = re.compile(r'<row r="(\d+)"[^>]*>.*?</row>', re.S)
_SI = re.compile(r"<si>.*?</si>", re.S)
_STRING_REF = re.compile(r'<c r="[A-Z]+\d+"[^>]*t="s"[^>]*><v>(\d+)</v></c>')
_TEXT = re.compile(r"<t[^>]*>(.*?)</t>", re.S)


def _empty_row(row: int) -> str:
    cells = "".join(
        f'<c r="{column}{row}" s="{style}"/>'
        for column, style in zip(_COLUMNS, _EMPTY_ROW_STYLES, strict=True)
    )
    return f'<row r="{row}"{_ROW_ATTRS}>{cells}</row>'


def _sheet(source: str) -> str:
    start = source.index("<sheetData>") + len("<sheetData>")
    end = source.index("</sheetData>")
    rows = {int(m.group(1)): m.group(0) for m in _ROW.finditer(source, start, end)}
    kept = [rows[r] for r in range(1, FIRST_DATA_ROW)]
    body = [_empty_row(r) for r in range(FIRST_DATA_ROW, LAST_DATA_ROW)]
    # A última linha da área (borda de baixo) e o rodapé vêm do modelo; nenhuma delas
    # pode ter dado da amostra.
    for row in (LAST_DATA_ROW, LAST_DATA_ROW + 1):
        refs = [int(i) for i in _STRING_REF.findall(rows[row])]
        if "<v>" in rows[row] and any(i >= TEMPLATE_STRINGS for i in refs):
            sys.exit(f"a linha {row} da amostra tem dado: o modelo não é o esperado")
        body.append(rows[row])
    head = source[:start]
    head = re.sub(r'topLeftCell="A\d+"', f'topLeftCell="A{FIRST_DATA_ROW}"', head)
    head = re.sub(
        r'<selection pane="bottomLeft"[^>]*/>',
        f'<selection pane="bottomLeft" activeCell="B{FIRST_DATA_ROW}" sqref="B{FIRST_DATA_ROW}"/>',
        head,
    )
    tail = re.sub(
        r"<xm:sqref>([A-Z])[^<]*</xm:sqref>",
        lambda m: f"<xm:sqref>{m.group(1)}{FIRST_DATA_ROW}:{m.group(1)}{LAST_DATA_ROW}</xm:sqref>",
        source[end:],
    )
    return head + "".join(kept + body) + tail


def _shared_strings(source: str, parts: list[str]) -> tuple[str, list[str]]:
    items = _SI.findall(source)
    used = {int(i) for part in parts for i in _STRING_REF.findall(part)}
    if max(used) >= TEMPLATE_STRINGS:
        sys.exit("o modelo usa string além das do modelo: confira a amostra")
    if "".join(_TEXT.findall(items[TEMPLATE_STRINGS - 1])) != TEMPLATE_VERSION:
        sys.exit("a última string do modelo não é a versão: o modelo não é o esperado")
    kept = items[:TEMPLATE_STRINGS]
    count = sum(len(_STRING_REF.findall(part)) for part in parts)
    prolog = source[: source.index("<sst")]
    xml = (
        f'{prolog}<sst xmlns="{_SST_NS}" count="{count}" uniqueCount="{len(kept)}">'
        + "".join(kept)
        + "</sst>"
    )
    template_text = " ".join("".join(_TEXT.findall(item)) for item in kept)
    # Texto da amostra que é pedaço de texto do PRÓPRIO modelo (a classificação `1.1`
    # está em `1.1.3` e no exemplo `1.1.01.001.001`) não diz nada: fica fora da busca.
    sample_texts = [
        text
        for text in ("".join(_TEXT.findall(item)).strip() for item in items[TEMPLATE_STRINGS:])
        if text not in template_text
    ]
    return xml, sample_texts


def extract(source_path: Path, target_path: Path) -> None:
    with zipfile.ZipFile(source_path) as package:
        order = package.infolist()
        parts = {info.filename: package.read(info.filename) for info in order}

    sheet = _sheet(parts[SHEET_PART].decode("utf-8"))
    config = parts["xl/worksheets/sheet2.xml"].decode("utf-8")
    sst, sample_texts = _shared_strings(parts[SHARED_STRINGS_PART].decode("utf-8"), [sheet, config])
    core = re.sub(
        r"<cp:lastModifiedBy>.*?</cp:lastModifiedBy>", "", parts["docProps/core.xml"].decode()
    )
    workbook = re.sub(
        r"<mc:AlternateContent.*?</mc:AlternateContent>", "", parts["xl/workbook.xml"].decode()
    )
    parts[SHEET_PART] = sheet.encode("utf-8")
    parts[SHARED_STRINGS_PART] = sst.encode("utf-8")
    parts["docProps/core.xml"] = core.encode("utf-8")
    parts["xl/workbook.xml"] = workbook.encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for original in order:
            info = zipfile.ZipInfo(original.filename, date_time=_ZIP_DATE_TIME)
            info.compress_type = original.compress_type
            info.external_attr = original.external_attr
            info.create_system = original.create_system
            out.writestr(info, parts[original.filename], compresslevel=9)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(buffer.getvalue())
    _check_no_leak(sample_texts, target_path)
    print(f"strings_do_modelo={TEMPLATE_STRINGS} bytes={len(buffer.getvalue())}")


def _check_no_leak(sample_texts: list[str], target: Path) -> None:
    """Nenhuma string da amostra e nenhum documento sobrou em parte nenhuma do modelo."""
    with zipfile.ZipFile(target) as package:
        texts = {
            info.filename: package.read(info.filename).decode("utf-8", errors="ignore")
            for info in package.infolist()
        }
    # Classificação e código (só dígitos e pontos) são estrutura, em claro (§4.5), e
    # casariam com qualquer largura de coluna do XML: o que fica de fora aqui o teste de
    # documento (11 dígitos ou mais) ainda pega.
    originals = {
        text.strip()
        for text in sample_texts
        if len(text.strip()) >= 3 and not re.fullmatch(r"[\d.\-]+", text.strip())
    }
    leaked = sum(1 for text in originals if any(text in part for part in texts.values()))
    documents = sum(
        # GUID (`{00000000-0000-…}`) e `panose` de fonte não são documento.
        len(re.findall(r"(?<![\w.\-])\d{11,}(?![\w.\-}])", part))
        for name, part in texts.items()
        if name.endswith(".xml")
    )
    print(
        f"textos_da_amostra={len(originals)} encontrados_no_modelo={leaked} "
        f"sequencias_de_documento={documents}"
    )
    if leaked or documents:
        sys.exit("dado da amostra sobrou no modelo: NÃO versione o arquivo gerado")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
