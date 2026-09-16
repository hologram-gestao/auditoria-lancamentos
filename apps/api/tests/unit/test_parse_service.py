"""Testes unitários do parse_service — rendering de XLSX (Report #4).

Foco: `_xlsx_to_text` precisa ler TODAS as linhas mesmo quando o XLSX vem com
a tag `<dimension>` errada/menor que os dados — caso real de extratos exportados
por banco (ex.: Banco Inter / DM Construções, jun/2026), em que o modo
`read_only` do openpyxl sub-lia a planilha e a IA extraía 1 de ~20 lançamentos.
"""

from __future__ import annotations

import asyncio
import io
import math
import re
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

import openpyxl
import pytest

from app.core.exceptions import AnthropicTimeoutError
from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction
from app.modules.reconciliations.parse_service import ParseService, _xlsx_to_text


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


# ----------------------------------------------------------------------
# 86e39xvxm — extração em blocos paralelos (orquestração do ParseService)
# ----------------------------------------------------------------------


_MAX_UPLOAD = 20 * 1024 * 1024
_HEADER = "Data Lançamento;Histórico;Descrição;Valor;Saldo\n"


def _inter_csv(n_rows: int) -> bytes:
    rows = "".join(
        f"05/08/2026;Pix enviado;PIX ENVIADO FORNECEDOR {i};-247,80;12.345,67\n"
        for i in range(n_rows)
    )
    return ("Extrato Conta Corrente\nConta: 1234-5\n\n" + _HEADER + rows).encode("utf-8")


class _FakeExtractor:
    """Fake do `AnthropicClient` visto pelo `ParseService`.

    Devolve, por bloco, um statement cujas transações são as linhas de dados do
    próprio bloco — assim a ORDEM da junção é verificável. `delays` atrasa
    blocos específicos para forçar conclusão fora de ordem; `fail_at` faz um
    bloco falhar como timeout.
    """

    def __init__(
        self,
        *,
        delays: dict[int, float] | None = None,
        fail_at: int | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._delays = delays or {}
        self._fail_at = fail_at

    async def extract_movements(
        self,
        *,
        content: bytes,
        mime_type: str,
        document_kind: str,
        model: str | None = None,
        part: tuple[int, int] | None = None,
    ) -> ExtractedStatement:
        self.calls.append({"content": content, "mime_type": mime_type, "part": part})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            index = part[0] if part else 1
            await asyncio.sleep(self._delays.get(index, 0.0))
            if self._fail_at == index:
                raise AnthropicTimeoutError("Timeout simulado no bloco.")
            lines = content.decode("utf-8", errors="replace").splitlines()
            data = [line for line in lines if line[:2].isdigit()] or ["00;x;SEM LINHAS;0;0"]
            return ExtractedStatement(
                bank_name="Banco Inter",
                account_type="checking",
                period_start=date(2026, 8, 1),
                period_end=date(2026, 8, 31),
                opening_balance=Decimal(index),
                closing_balance=Decimal(index * 10),
                transactions=[
                    ExtractedTransaction(
                        date=date(2026, 8, 5),
                        description=line.split(";")[2] if ";" in line else line.split("\t")[1],
                        amount=Decimal("-247.80"),
                    )
                    for line in data
                ],
            )
        finally:
            self.in_flight -= 1


def _service(fake: _FakeExtractor, *, concurrency: int = 2) -> ParseService:
    return ParseService(
        fake,  # type: ignore[arg-type]
        chunk_rows=100,
        chunk_min_rows=150,
        chunk_concurrency=concurrency,
    )


@pytest.mark.unit
class TestParseServiceChunked:
    async def test_csv_grande_vira_n_chamadas_paralelas_limitadas_e_juntadas_na_ordem(self) -> None:
        fake = _FakeExtractor(delays={1: 0.05})  # o 1º bloco termina por ÚLTIMO
        n_rows = 350

        statement = await _service(fake, concurrency=2).parse_statement(
            file_bytes=_inter_csv(n_rows), filename="extrato.csv", max_upload_bytes=_MAX_UPLOAD
        )

        expected_blocks = math.ceil(n_rows / 100)
        assert [call["part"] for call in fake.calls] == [
            (i, expected_blocks) for i in range(1, expected_blocks + 1)
        ]
        assert fake.max_in_flight == 2
        # Todo bloco leva o cabeçalho; nenhuma linha some nem duplica; ordem do arquivo.
        assert all(_HEADER.encode() in call["content"] for call in fake.calls)
        assert [tx.description for tx in statement.transactions] == [
            f"PIX ENVIADO FORNECEDOR {i}" for i in range(n_rows)
        ]
        # Saldo inicial do 1º bloco, final do último.
        assert statement.opening_balance == Decimal(1)
        assert statement.closing_balance == Decimal(expected_blocks * 10)

    async def test_falha_num_bloco_cancela_os_pendentes_e_sobe_o_erro_tipado(self) -> None:
        fake = _FakeExtractor(fail_at=1)

        with pytest.raises(AnthropicTimeoutError):
            await _service(fake, concurrency=1).parse_statement(
                file_bytes=_inter_csv(350), filename="extrato.csv", max_upload_bytes=_MAX_UPLOAD
            )

        # Com paralelismo 1, os blocos 2..4 esperavam o semáforo: cancelados
        # antes de gastar uma chamada.
        assert len(fake.calls) == 1

    async def test_csv_pequeno_vai_inteiro_numa_chamada(self) -> None:
        fake = _FakeExtractor()
        raw = _inter_csv(120)

        await _service(fake).parse_statement(
            file_bytes=raw, filename="extrato.csv", max_upload_bytes=_MAX_UPLOAD
        )

        assert len(fake.calls) == 1
        assert fake.calls[0]["part"] is None
        assert fake.calls[0]["content"] == raw

    async def test_pdf_nunca_e_dividido(self) -> None:
        fake = _FakeExtractor()
        raw = b"%PDF-1.4\n" + b"linha;com;separador\n" * 5000

        await _service(fake).parse_statement(
            file_bytes=raw, filename="extrato.pdf", max_upload_bytes=_MAX_UPLOAD
        )

        assert len(fake.calls) == 1
        assert fake.calls[0]["part"] is None
        assert fake.calls[0]["mime_type"] == "application/pdf"

    async def test_xlsx_grande_e_dividido_depois_de_renderizado(self) -> None:
        fake = _FakeExtractor()
        n_rows = 320

        statement = await _service(fake).parse_statement(
            file_bytes=_xlsx_with_bad_dimension(n_rows),
            filename="extrato.xlsx",
            max_upload_bytes=_MAX_UPLOAD,
        )

        assert len(fake.calls) == math.ceil(n_rows / 100)
        assert all(call["mime_type"] == "text/plain" for call in fake.calls)
        assert len(statement.transactions) == n_rows
