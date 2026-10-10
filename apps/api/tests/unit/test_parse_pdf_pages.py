"""Divisão de PDF em blocos de páginas (86e3ff8xd) — função pura `plan_pdf_blocks`.

Os PDFs são gerados com o próprio `pypdf` (páginas em branco, sem dependência
nova). A LARGURA de cada página codifica o índice global (100 + i): é assim que
a ordem e a composição de cada bloco são verificadas depois do recorte.

`TestCardInvoiceTotalFromHeader` (86e3n70qf) leva o mesmo PDF fictício pelo
`ParseService` inteiro, no desenho da fatura de cartão que disparou o defeito:
total impresso nas páginas 1 e 2, compras só na última, 3 blocos.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from structlog.testing import capture_logs

from app.integrations.anthropic.schemas import (
    DocumentIdentity,
    ExtractedStatement,
    ExtractedStatementBlock,
    ExtractedTransaction,
)
from app.modules.reconciliations.parse_chunking import merge_statements
from app.modules.reconciliations.parse_pdf_pages import (
    PDF_SKIP_ENCRYPTED,
    PDF_SKIP_TOO_FEW_PAGES,
    PDF_SKIP_UNREADABLE,
    PdfBlockPlan,
    PdfTooManyPagesError,
    plan_pdf_blocks,
)
from app.modules.reconciliations.parse_service import ParseService
from app.modules.reconciliations.processing.checksum import compute_checksum


def pdf_with_pages(n: int, *, password: str | None = None) -> bytes:
    """PDF de `n` páginas em branco; a largura da página i é `100 + i`."""
    writer = PdfWriter()
    for index in range(n):
        writer.add_blank_page(width=100 + index, height=200)
    if password is not None:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def page_indexes(pdf: bytes) -> list[int]:
    """Índices globais das páginas de um PDF gerado por `pdf_with_pages`."""
    return [int(float(page.mediabox.width)) - 100 for page in PdfReader(BytesIO(pdf)).pages]


def _plan(
    pdf: bytes, *, pages_per_block: int = 2, min_pages: int = 4, max_pages: int = 30
) -> PdfBlockPlan:
    return plan_pdf_blocks(
        pdf, pages_per_block=pages_per_block, min_pages=min_pages, max_pages=max_pages
    )


@pytest.mark.unit
class TestPlanPdfBlocks:
    def test_divide_equilibrado_e_na_ordem_das_paginas(self) -> None:
        plan = _plan(pdf_with_pages(7), pages_per_block=2)

        assert plan.is_split
        assert plan.skipped_reason is None
        assert plan.pages == 7
        assert len(plan.blocks) == 4  # ceil(7 / 2)
        assert [page_indexes(block) for block in plan.blocks] == [[0, 1], [2, 3], [4, 5], [6]]

    def test_blocos_equilibrados_nao_deixam_bloco_minusculo(self) -> None:
        # 10 páginas com blocos de 3: ceil(10/3) = 4 blocos de ceil(10/4) = 3 → 3,3,3,1
        # é o mesmo desenho do `plan_blocks` (texto); o que importa é que NENHUMA
        # página some nem repete e a ordem global é preservada.
        plan = _plan(pdf_with_pages(10), pages_per_block=3)

        assert [index for block in plan.blocks for index in page_indexes(block)] == list(range(10))
        assert all(len(page_indexes(block)) <= 3 for block in plan.blocks)

    def test_first_page_e_um_pdf_so_com_a_primeira_pagina(self) -> None:
        plan = _plan(pdf_with_pages(6))

        assert plan.first_page is not None
        assert page_indexes(plan.first_page) == [0]

    def test_ate_o_minimo_vai_inteiro_sem_primeira_pagina(self) -> None:
        raw = pdf_with_pages(4)

        plan = _plan(raw, min_pages=4)

        assert not plan.is_split
        assert plan.blocks == [raw]
        assert plan.first_page is None
        assert plan.pages == 4
        assert plan.skipped_reason == PDF_SKIP_TOO_FEW_PAGES

    def test_bytes_que_nao_sao_pdf_vao_inteiros_como_unreadable(self) -> None:
        # O MESMO PDF falso dos testes de integração (`_minimal_pdf_bytes`).
        raw = b"%PDF-1.7\n%fake-pdf-content-for-tests\n" + b"x" * 100

        plan = _plan(raw)

        assert not plan.is_split
        assert plan.blocks == [raw]
        assert plan.first_page is None
        assert plan.skipped_reason == PDF_SKIP_UNREADABLE

    def test_pdf_cifrado_com_senha_vai_inteiro_como_encrypted(self) -> None:
        raw = pdf_with_pages(6, password="segredo")

        plan = _plan(raw)

        assert not plan.is_split
        assert plan.blocks == [raw]
        assert plan.skipped_reason == PDF_SKIP_ENCRYPTED

    def test_pdf_cifrado_com_senha_vazia_abre_e_divide(self) -> None:
        plan = _plan(pdf_with_pages(6, password=""))

        assert plan.is_split
        assert plan.skipped_reason is None
        assert [page_indexes(block) for block in plan.blocks] == [[0, 1], [2, 3], [4, 5]]

    def test_acima_do_maximo_recusa_sem_montar_blocos(self) -> None:
        with pytest.raises(PdfTooManyPagesError) as exc_info:
            _plan(pdf_with_pages(31), max_pages=30)

        error = exc_info.value
        assert error.status_code == 400
        assert "31 páginas" in error.user_message
        assert "até 30 páginas" in error.user_message
        assert "mesma conciliação" in error.user_message
        assert error.details == {"pages": 31, "maxPages": 30}

    def test_no_maximo_exato_ainda_divide(self) -> None:
        plan = _plan(pdf_with_pages(30), max_pages=30)

        assert plan.is_split
        assert len(plan.blocks) == 15

    def test_um_por_bloco_e_valido(self) -> None:
        plan = _plan(pdf_with_pages(5), pages_per_block=1)

        assert [page_indexes(block) for block in plan.blocks] == [[0], [1], [2], [3], [4]]


# ----------------------------------------------------------------------
# 86e3n70qf — total da fatura de cartão vem da identidade da página 1
# ----------------------------------------------------------------------

# Fatura fictícia: nenhum valor, data ou descrição vem de documento real.
_INVOICE_TOTAL = Decimal("612.40")
_INVOICE_DUE = date(2026, 10, 13)
_TOTAL_PAGES = {0, 1}  # o total está impresso nas páginas 1 e 2
_PURCHASE_PAGE = 4  # as compras estão só na última página
_PURCHASES = [
    (date(2026, 9, 3), Decimal("-120.00")),
    (date(2026, 9, 9), Decimal("-75.90")),
    (date(2026, 9, 14), Decimal("-300.00")),
    (date(2026, 9, 21), Decimal("-66.50")),
    (date(2026, 9, 28), Decimal("-50.00")),
]


class _FakeInvoiceReader:
    """Fake do `AnthropicClient` que "lê" a fatura fictícia como o modelo leu a real.

    A página é reconhecida pela largura (`pdf_with_pages`). O bloco que contém a
    página 1 ou 2 vê o total; o bloco da última página vê as compras e, como na
    fatura real, nenhum total (devolve 0). A identidade lê a primeira página.
    """

    def __init__(self, *, identity_has_total: bool = True) -> None:
        self.blocks: list[ExtractedStatement] = []
        self._identity_has_total = identity_has_total

    async def identify_document(self, content: bytes) -> DocumentIdentity:
        assert page_indexes(content) == [0]
        return DocumentIdentity(
            bank_name="Banco Ficticio",
            account_type="credit_card",
            closing_balance=_INVOICE_TOTAL if self._identity_has_total else None,
            invoice_due_date=_INVOICE_DUE,
        )

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
        pages = set(page_indexes(content))
        purchases = _PURCHASES if _PURCHASE_PAGE in pages else []
        block = ExtractedStatementBlock(
            bank_name="Banco Ficticio",
            account_type="credit_card",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            opening_balance=Decimal("0"),
            closing_balance=_INVOICE_TOTAL if pages & _TOTAL_PAGES else Decimal("0"),
            transactions=[
                ExtractedTransaction(date=d, description=f"COMPRA FICTICIA {i}", amount=amount)
                for i, (d, amount) in enumerate(purchases)
            ],
            invoice_due_date=_INVOICE_DUE if 0 in pages else None,
        )
        self.blocks.append(block)
        return block


async def _parse_invoice(reader: _FakeInvoiceReader) -> ExtractedStatement:
    service = ParseService(
        reader,  # type: ignore[arg-type]
        chunk_concurrency=1,  # ordem determinística em `reader.blocks`
        pdf_pages_per_block=2,
        pdf_min_pages=4,
        pdf_max_pages=30,
    )
    return await service.parse_statement(
        file_bytes=pdf_with_pages(5), filename="fatura.pdf", max_upload_bytes=10_000_000
    )


@pytest.mark.unit
class TestCardInvoiceTotalFromHeader:
    async def test_total_da_identidade_fecha_o_checksum(self) -> None:
        reader = _FakeInvoiceReader()

        with capture_logs() as events:
            statement = await _parse_invoice(reader)

        # O desenho do defeito: 3 blocos, 0 + 0 + 5 compras, último bloco sem total.
        assert [len(block.transactions) for block in reader.blocks] == [0, 0, 5]
        assert reader.blocks[-1].closing_balance == Decimal("0")
        assert sum(-amount for _, amount in _PURCHASES) == _INVOICE_TOTAL

        assert statement.closing_balance == _INVOICE_TOTAL
        assert statement.invoice_due_date == _INVOICE_DUE
        assert len(statement.transactions) == 5
        checksum = compute_checksum(statement)
        assert checksum.ok is True
        assert checksum.difference == Decimal("0")

        logged = [e for e in events if e["event"] == "parse_checksum"]
        assert len(logged) == 1
        assert logged[0]["ok"] is True
        assert logged[0]["blocks"] == 3
        assert logged[0]["expected"] == "612.40"
        assert logged[0]["difference"] == "0.00"

    async def test_mesmo_desenho_sem_identidade_toma_o_ultimo_bloco(self) -> None:
        reader = _FakeInvoiceReader()
        await _parse_invoice(reader)

        merged = merge_statements(reader.blocks)  # sem identidade, como o texto

        assert merged.closing_balance == Decimal("0")
        checksum = compute_checksum(merged)
        assert checksum.ok is False
        assert checksum.difference == -_INVOICE_TOTAL

    async def test_identidade_sem_total_impresso_tambem_toma_o_ultimo_bloco(self) -> None:
        statement = await _parse_invoice(_FakeInvoiceReader(identity_has_total=False))

        assert statement.closing_balance == Decimal("0")
        assert compute_checksum(statement).ok is False
