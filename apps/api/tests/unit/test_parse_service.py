"""Testes unitários do parse_service.

- Rendering de XLSX (Report #4): `_xlsx_to_text` precisa ler TODAS as linhas
  mesmo quando o XLSX vem com a tag `<dimension>` errada/menor que os dados.
- Extração em blocos paralelos para texto (86e39xvxm) e para PDF por páginas
  (86e3ff8xd): orquestração do `ParseService` com um fake do `AnthropicClient`.
- Evento `parse_completed` (D7): chaves e ausência de conteúdo.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

import openpyxl
import pytest
from pypdf.errors import PdfReadError
from structlog.testing import capture_logs

from app.core.exceptions import AnthropicParseError, AnthropicTimeoutError
from app.integrations.anthropic.schemas import (
    DocumentIdentity,
    ExtractedStatement,
    ExtractedStatementBlock,
    ExtractedTransaction,
)
from app.modules.reconciliations.parse_pdf_pages import PdfTooManyPagesError
from app.modules.reconciliations.parse_service import ParseService, _xlsx_to_text
from tests.unit.test_parse_pdf_pages import page_indexes, pdf_with_pages


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


_IDENTITY = DocumentIdentity(bank_name="Banco do Brasil", account_type="checking")


class _FakeExtractor:
    """Fake do `AnthropicClient` visto pelo `ParseService`.

    Devolve, por bloco, um statement cujas transações são as linhas de dados do
    próprio bloco (texto) ou as PÁGINAS do próprio bloco (PDF gerado por
    `pdf_with_pages`, uma transação "PAGINA <i>" por página) — assim a ORDEM da
    junção é verificável. `delays` atrasa blocos específicos para forçar
    conclusão fora de ordem; `fail_at` faz um bloco falhar como timeout;
    `empty_at` devolve um bloco SEM movimentação (D4).
    """

    def __init__(
        self,
        *,
        delays: dict[int, float] | None = None,
        fail_at: int | None = None,
        empty_at: int | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.identify_calls: list[bytes] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._delays = delays or {}
        self._fail_at = fail_at
        self._empty_at = empty_at

    async def identify_document(self, content: bytes) -> DocumentIdentity:
        self.identify_calls.append(content)
        return _IDENTITY

    async def extract_movements(
        self,
        *,
        content: bytes,
        mime_type: str,
        document_kind: str,
        model: str | None = None,
        part: tuple[int, int] | None = None,
        identity: DocumentIdentity | None = None,
    ) -> ExtractedStatement:
        self.calls.append(
            {"content": content, "mime_type": mime_type, "part": part, "identity": identity}
        )
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            index = part[0] if part else 1
            await asyncio.sleep(self._delays.get(index, 0.0))
            if self._fail_at == index:
                raise AnthropicTimeoutError("Timeout simulado no bloco.")
            if mime_type == "application/pdf":
                try:
                    descriptions = [f"PAGINA {i}" for i in page_indexes(content)]
                except PdfReadError:  # PDF falso (ilegível): o modelo leria o que desse
                    descriptions = ["ARQUIVO INTEIRO"]
            else:
                lines = content.decode("utf-8", errors="replace").splitlines()
                data = [line for line in lines if line[:2].isdigit()] or ["00;x;SEM LINHAS;0;0"]
                descriptions = [
                    line.split(";")[2] if ";" in line else line.split("\t")[1] for line in data
                ]
            if self._empty_at == index:
                descriptions = []
            model_cls = ExtractedStatementBlock if part is not None else ExtractedStatement
            return model_cls(
                bank_name="Banco Inter",
                account_type="checking",
                period_start=date(2026, 8, index),
                period_end=date(2026, 8, index + 1),
                opening_balance=Decimal(index),
                closing_balance=Decimal(index * 10),
                transactions=[
                    ExtractedTransaction(
                        date=date(2026, 8, 5), description=description, amount=Decimal("-247.80")
                    )
                    for description in descriptions
                ],
            )
        finally:
            self.in_flight -= 1


def _service(
    fake: _FakeExtractor,
    *,
    concurrency: int = 2,
    pdf_pages_per_block: int = 2,
    pdf_min_pages: int = 4,
    pdf_max_pages: int = 30,
) -> ParseService:
    return ParseService(
        fake,  # type: ignore[arg-type]
        chunk_rows=100,
        chunk_min_rows=150,
        chunk_concurrency=concurrency,
        pdf_pages_per_block=pdf_pages_per_block,
        pdf_min_pages=pdf_min_pages,
        pdf_max_pages=pdf_max_pages,
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


# ----------------------------------------------------------------------
# 86e3ff8xd — PDF dividido por páginas (orquestração do ParseService)
# ----------------------------------------------------------------------


async def _parse_pdf(fake: _FakeExtractor, raw: bytes, **kwargs: int) -> ExtractedStatement:
    return await _service(fake, **kwargs).parse_statement(
        file_bytes=raw, filename="extrato.pdf", max_upload_bytes=_MAX_UPLOAD
    )


@pytest.mark.unit
class TestParseServicePdfPages:
    async def test_pdf_grande_identifica_uma_vez_e_extrai_os_blocos_em_paralelo_limitado(
        self,
    ) -> None:
        fake = _FakeExtractor(delays={1: 0.05})  # o 1º bloco termina por ÚLTIMO
        n_pages = 7

        statement = await _parse_pdf(fake, pdf_with_pages(n_pages), concurrency=2)

        # Identificação: 1x, só com a primeira página.
        assert len(fake.identify_calls) == 1
        assert page_indexes(fake.identify_calls[0]) == [0]
        # Blocos: ceil(7/2) = 4, todos PDF, part correto, identidade em todos.
        expected_blocks = math.ceil(n_pages / 2)
        assert [call["part"] for call in fake.calls] == [
            (i, expected_blocks) for i in range(1, expected_blocks + 1)
        ]
        assert all(call["mime_type"] == "application/pdf" for call in fake.calls)
        assert all(call["identity"] == _IDENTITY for call in fake.calls)
        assert [page_indexes(call["content"]) for call in fake.calls] == [
            [0, 1],
            [2, 3],
            [4, 5],
            [6],
        ]
        assert fake.max_in_flight == 2
        # Junção na ordem das páginas, mesmo com o 1º bloco terminando por último.
        assert [tx.description for tx in statement.transactions] == [
            f"PAGINA {i}" for i in range(n_pages)
        ]
        # Banco e tipo da identificação (D5); saldo inicial do 1º, final do último.
        assert statement.bank_name == "Banco do Brasil"
        assert statement.account_type == "checking"
        assert statement.opening_balance == Decimal(1)
        assert statement.closing_balance == Decimal(expected_blocks * 10)

    async def test_bloco_sem_movimentacao_e_tolerado_e_nao_alarga_o_periodo(self) -> None:
        fake = _FakeExtractor(empty_at=3)  # o bloco 3 (páginas 4 e 5) é só rodapé

        statement = await _parse_pdf(fake, pdf_with_pages(6))

        assert [tx.description for tx in statement.transactions] == [
            "PAGINA 0",
            "PAGINA 1",
            "PAGINA 2",
            "PAGINA 3",
        ]
        # O fake dá ao bloco i o período (i, i+1): o bloco 3 vazio não entra.
        assert (statement.period_start, statement.period_end) == (
            date(2026, 8, 1),
            date(2026, 8, 3),
        )

    async def test_falha_num_bloco_cancela_os_pendentes_e_sobe_o_erro_tipado(self) -> None:
        fake = _FakeExtractor(fail_at=1)

        with pytest.raises(AnthropicTimeoutError):
            await _parse_pdf(fake, pdf_with_pages(8), concurrency=1)

        assert len(fake.identify_calls) == 1
        assert len(fake.calls) == 1  # blocos 2..4 cancelados antes de sair

    async def test_pdf_pequeno_vai_inteiro_sem_identificacao(self) -> None:
        fake = _FakeExtractor()
        raw = pdf_with_pages(4)

        await _parse_pdf(fake, raw, pdf_min_pages=4)

        assert fake.identify_calls == []
        assert len(fake.calls) == 1
        assert fake.calls[0]["part"] is None
        assert fake.calls[0]["identity"] is None
        assert fake.calls[0]["content"] == raw
        assert fake.calls[0]["mime_type"] == "application/pdf"

    async def test_pdf_ilegivel_pelo_pypdf_vai_inteiro_como_antes(self) -> None:
        fake = _FakeExtractor()
        raw = b"%PDF-1.4\n" + b"linha;com;separador\n" * 5000

        with capture_logs() as events:
            await _parse_pdf(fake, raw)

        assert fake.identify_calls == []
        assert len(fake.calls) == 1
        assert fake.calls[0]["part"] is None
        assert fake.calls[0]["content"] == raw
        skipped = [e for e in events if e["event"] == "parse_pdf_split_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "unreadable"

    async def test_pdf_acima_do_maximo_e_recusado_sem_chamar_a_ia(self) -> None:
        fake = _FakeExtractor()

        with pytest.raises(PdfTooManyPagesError) as exc_info:
            await _parse_pdf(fake, pdf_with_pages(31), pdf_max_pages=30)

        assert "31 páginas" in exc_info.value.user_message
        assert fake.identify_calls == []
        assert fake.calls == []

    async def test_todos_os_blocos_vazios_e_erro_acionavel(self) -> None:
        class _AllEmpty(_FakeExtractor):
            async def extract_movements(self, **kwargs: Any) -> ExtractedStatement:
                statement = await super().extract_movements(**kwargs)
                return statement.model_copy(update={"transactions": []})

        fake = _AllEmpty()

        with pytest.raises(AnthropicParseError) as exc_info:
            await _parse_pdf(fake, pdf_with_pages(6))

        assert "Nenhuma movimentação" in exc_info.value.user_message


# ----------------------------------------------------------------------
# D7 — `parse_completed`: um por /parse, só contadores
# ----------------------------------------------------------------------


def _completed(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [e for e in events if e["event"] == "parse_completed"]
    assert len(completed) == 1
    return completed[0]


@pytest.mark.unit
class TestParseCompletedEvent:
    async def test_pdf_dividido(self) -> None:
        fake = _FakeExtractor()
        raw = pdf_with_pages(7)

        with capture_logs() as events:
            await _parse_pdf(fake, raw)

        event = _completed(events)
        assert event["file_type"] == "pdf"
        assert event["bytes_in"] == len(raw)
        assert event["split"] is True
        assert event["blocks"] == 4
        assert event["pages"] == 7
        assert event["transaction_count"] == 7
        assert isinstance(event["duration_ms"], int)
        assert "data_records" not in event
        chunked = [e for e in events if e["event"] == "parse_chunked"]
        assert len(chunked) == 1
        assert chunked[0]["file_type"] == "pdf"
        assert chunked[0]["pages"] == 7
        assert chunked[0]["blocks"] == 4

    async def test_pdf_inteiro(self) -> None:
        fake = _FakeExtractor()
        raw = pdf_with_pages(3)

        with capture_logs() as events:
            await _parse_pdf(fake, raw)

        event = _completed(events)
        assert event["file_type"] == "pdf"
        assert event["split"] is False
        assert event["blocks"] == 1
        assert event["pages"] == 3
        assert not any(e["event"] == "parse_chunked" for e in events)

    async def test_csv_dividido_e_inteiro(self) -> None:
        fake = _FakeExtractor()

        with capture_logs() as events:
            await _service(fake).parse_statement(
                file_bytes=_inter_csv(350), filename="extrato.csv", max_upload_bytes=_MAX_UPLOAD
            )
        event = _completed(events)
        assert (event["file_type"], event["split"], event["blocks"]) == ("csv", True, 4)
        assert event["data_records"] == 350
        assert "pages" not in event

        with capture_logs() as events:
            await _service(fake).parse_statement(
                file_bytes=_inter_csv(120), filename="extrato.csv", max_upload_bytes=_MAX_UPLOAD
            )
        event = _completed(events)
        assert (event["file_type"], event["split"], event["blocks"]) == ("csv", False, 1)
        assert event["data_records"] == 120

    async def test_nenhum_valor_do_evento_e_texto_do_arquivo(self) -> None:
        """Só contadores: nenhuma chave leva string vinda do arquivo ou da extração."""
        fake = _FakeExtractor()
        secret = "PIX ENVIADO FORNECEDOR 7"

        with capture_logs() as events:
            await _service(fake).parse_statement(
                file_bytes=_inter_csv(350),
                filename="extrato-sigiloso.csv",
                max_upload_bytes=_MAX_UPLOAD,
            )

        dumped = json.dumps(events, ensure_ascii=False, default=str)
        assert secret not in dumped
        assert "sigiloso" not in dumped
        event = _completed(events)
        for key, value in event.items():
            if key in ("event", "log_level"):
                continue
            assert isinstance(value, (int, bool, str)), key
            if isinstance(value, str):
                assert key == "file_type", key
