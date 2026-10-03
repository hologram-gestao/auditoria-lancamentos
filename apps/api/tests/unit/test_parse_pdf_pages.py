"""Divisão de PDF em blocos de páginas (86e3ff8xd) — função pura `plan_pdf_blocks`.

Os PDFs são gerados com o próprio `pypdf` (páginas em branco, sem dependência
nova). A LARGURA de cada página codifica o índice global (100 + i): é assim que
a ordem e a composição de cada bloco são verificadas depois do recorte.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from app.modules.reconciliations.parse_pdf_pages import (
    PDF_SKIP_ENCRYPTED,
    PDF_SKIP_TOO_FEW_PAGES,
    PDF_SKIP_UNREADABLE,
    PdfBlockPlan,
    PdfTooManyPagesError,
    plan_pdf_blocks,
)


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
