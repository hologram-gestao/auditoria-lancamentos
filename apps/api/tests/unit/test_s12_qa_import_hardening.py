"""QA da Sprint 12 (86e3ezyvt): importação do de-para contra planilha MALFORMADA.

Achado da revisão da BACK 12.5, reproduzido antes de virar teste: `parse_import` só
tratava `BadZipFile/KeyError/ValueError/OSError` na ABERTURA do arquivo, e a iteração
das linhas não tinha guarda nenhuma. Três formas de arquivo, cada uma com poucos KB e
magic bytes válidos, escapavam da validação:

- XML da planilha truncado → `ParseError` (subclasse de `SyntaxError`) → 500;
- célula numérica com texto → `ValueError` com o TEXTO DA CÉLULA na mensagem → 500,
  e o handler de 500 loga `exc_info` (conteúdo de arquivo em log, §3.3);
- uma célula perdida na linha 300.000, coluna XFD → o openpyxl em `read_only` preenche
  as linhas vazias até ela: 199 s de CPU síncrona medidos dentro de um handler `async`
  (o event loop do worker inteiro para).

Regra: toda falha de leitura de planilha é o MESMO 400 de arquivo inválido, sem ecoar
conteúdo, e o custo de ler é limitado ANTES de iterar (linhas e colunas percorridas,
não só as não-vazias).
"""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Callable

import pytest
from openpyxl import Workbook

from app.core.exceptions import AppError
from app.modules.client_mapping.portability import parse_import, validate_import_file

SHEET = "xl/worksheets/sheet1.xml"
SEGREDO = "SEGREDO_DA_CELULA"


def _good_book() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["tipo_origem", "codigo_categoria", "decisao", "codigo_alvo"])
    ws.append(["omie", "1.01.01", "alvo", "A1"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _patch(raw: bytes, fn: Callable[[bytes], bytes]) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            dst.writestr(info, fn(data) if info.filename == SHEET else data)
    return out.getvalue()


def _truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]


def _text_in_numeric_cell(data: bytes) -> bytes:
    cell = f'<row r="3"><c r="A3"><v>{SEGREDO}</v></c></row></sheetData>'.encode()
    return data.replace(b"</sheetData>", cell)


def _sparse_far_cell(data: bytes) -> bytes:
    far = b'<row r="300000"><c r="XFD300000" t="inlineStr"><is><t>x</t></is></c></row>'
    return data.replace(b"</sheetData>", far + b"</sheetData>").replace(
        b'<dimension ref="A1:D2" />', b'<dimension ref="A1:XFD300000" />'
    )


@pytest.mark.parametrize(
    "mutation",
    [_truncated, _text_in_numeric_cell],
    ids=["xml-truncado", "texto-em-celula-numerica"],
)
def test_planilha_malformada_e_400_sem_eco(mutation: Callable[[bytes], bytes]) -> None:
    content = _patch(_good_book(), mutation)
    validate_import_file("de-para.xlsx", content)  # magic bytes e tamanho passam

    with pytest.raises(AppError) as exc:
        parse_import(content)

    assert exc.value.status_code == 400
    assert exc.value.code == "VALIDATION_ERROR"
    assert SEGREDO not in str(exc.value)
    assert SEGREDO not in (exc.value.user_message or "")
    # A exceção original (com o texto da célula) não viaja encadeada até o log.
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


def _parse_status(content: bytes) -> int:
    try:
        parse_import(content)
    except AppError as exc:
        return exc.status_code
    return 200


def test_celula_distante_nao_prende_a_cpu() -> None:
    """Recusar (400) ou ler só o que existe: os dois servem. Levar minutos, não."""
    content = _patch(_good_book(), _sparse_far_cell)
    validate_import_file("de-para.xlsx", content)

    started = time.monotonic()
    status = _parse_status(content)
    elapsed = time.monotonic() - started

    assert status in (200, 400)

    assert elapsed < 5, f"leitura levou {elapsed:.1f}s — custo não limitado antes de iterar"
