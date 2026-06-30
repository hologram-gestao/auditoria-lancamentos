"""Testes unitários do parse_service — rendering de XLSX (Report #4).

Foco: `_xlsx_to_text` precisa ler TODAS as linhas mesmo quando o XLSX vem com
a tag `<dimension>` errada/menor que os dados — caso real de extratos exportados
por banco (ex.: Banco Inter / DM Construções, jun/2026), em que o modo
`read_only` do openpyxl sub-lia a planilha e a IA extraía 1 de ~20 lançamentos.
"""

from __future__ import annotations

import io
import re
import zipfile

import openpyxl

from app.modules.reconciliations.parse_service import _xlsx_to_text


def _xlsx_with_bad_dimension(n_rows: int) -> bytes:
    """Gera um XLSX com `n_rows` linhas mas `<dimension>` declarando só `A1:D1`.

    O openpyxl escreve a dimension correta ao salvar; reabrimos o zip e
    adulteramos a tag pra reproduzir o export de banco do Report #4.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Extrato"
    for i in range(1, n_rows + 1):
        ws.append([f"2026-05-{i:02d}", f"PIX LINHA {i}", -100 * i, 1000 * i])
    buf = io.BytesIO()
    wb.save(buf)

    zin = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            content = zin.read(name)
            if re.match(r"xl/worksheets/sheet\d*\.xml$", name):
                content = re.sub(
                    rb'<dimension ref="[^"]*"\s*/>',
                    b'<dimension ref="A1:D1"/>',
                    content,
                )
            zout.writestr(name, content)
    return out.getvalue()


def test_xlsx_to_text_reads_all_rows_despite_bad_dimension() -> None:
    """Regressão Report #4: todas as linhas renderizadas, não só a 1ª."""
    n_rows = 40
    bad_xlsx = _xlsx_with_bad_dimension(n_rows)

    text = _xlsx_to_text(bad_xlsx)

    # A 1ª, uma do meio e a última precisam estar presentes.
    assert "PIX LINHA 1" in text
    assert "PIX LINHA 20" in text
    assert f"PIX LINHA {n_rows}" in text

    # E o total de linhas de dados renderizadas bate com n_rows (cada linha
    # começa com a data; o cabeçalho "# Aba: ..." não conta).
    data_lines = [line for line in text.splitlines() if line.startswith("2026-05-")]
    assert len(data_lines) == n_rows
