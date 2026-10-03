"""Divisão de um PDF em blocos de páginas para a extração em paralelo (86e3ff8xd).

A extração em blocos de 86e39xvxm cobria só CSV e XLSX: o PDF ia inteiro numa
chamada à Anthropic, e o tempo de geração cresce com o número de linhas (o teto
de 150 s comporta ~230). Em 28/09/2026 cinco envios do extrato BB do Laticínio
(~625 KB) estouraram o teto de forma determinística; o usuário contornou
dividindo o PDF à mão.

Aqui o corte é por PÁGINA, com o `pypdf` (já nas dependências, sem uso até
então): cada bloco é um PDF válido com um subconjunto contíguo das páginas, na
ordem; a primeira página sai separada para a identificação do documento (banco
e tipo de conta, ver `AnthropicClient.identify_document`), porque página do
meio não tem cabeçalho.

Regras (decisões D3 e D6 da task):

- blocos EQUILIBRADOS, como em `plan_blocks`: `ceil(páginas / pages_per_block)`
  blocos de tamanho parecido, nunca um bloco minúsculo no fim;
- até `min_pages` páginas (inclusive) o PDF vai inteiro, sem identificação;
- acima de `max_pages` o arquivo é RECUSADO (`PdfTooManyPagesError`, 400 com
  orientação): o orçamento de parede do `/parse` é fixo (~150 s) e
  `ceil(blocos / paralelismo) x tempo de um bloco` tem de caber nele;
- o `pypdf` NUNCA bloqueia: PDF que ele não abre, cifrado que não abre com senha
  vazia ou com falha ao recortar vai INTEIRO, como antes, com `skipped_reason`
  de vocabulário fechado para o log.

Função pura e síncrona: o service a chama por `run_in_threadpool` (§7 Backend —
parse de arquivo de terceiro nunca roda no event loop). Nada de conteúdo em
log: o PDF é dado do cliente final (§3.3, §4.5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from pypdf import PasswordType, PdfReader, PdfWriter

from app.core.exceptions import ValidationAppError

PdfSkipReason = Literal["unreadable", "encrypted", "too_few_pages"]

PDF_SKIP_UNREADABLE: PdfSkipReason = "unreadable"
PDF_SKIP_ENCRYPTED: PdfSkipReason = "encrypted"
PDF_SKIP_TOO_FEW_PAGES: PdfSkipReason = "too_few_pages"


class PdfTooManyPagesError(ValidationAppError):
    """400 — o PDF tem mais páginas do que o `/parse` consegue extrair no teto.

    A mensagem orienta a dividir e anexar as partes na MESMA conciliação (a
    gaveta aceita várias partes). Nenhuma chamada à IA é feita.
    """

    def __init__(self, *, pages: int, max_pages: int) -> None:
        super().__init__(
            f"PDF com {pages} páginas excede o máximo de {max_pages}.",
            user_message=(
                f"O PDF tem {pages} páginas. Envie em partes de até {max_pages} páginas "
                "e anexe todas na mesma conciliação."
            ),
            details={"pages": pages, "maxPages": max_pages},
        )


@dataclass(frozen=True, slots=True)
class PdfBlockPlan:
    """Resultado de `plan_pdf_blocks`.

    `blocks` tem UM elemento (os bytes originais) quando não vale dividir:
    `skipped_reason` diz por quê. Dividido, `first_page` é um PDF de uma página
    (a primeira) para a identificação e `blocks` são PDFs válidos com as páginas
    de cada bloco, em ordem.
    """

    blocks: list[bytes]
    first_page: bytes | None
    pages: int
    skipped_reason: PdfSkipReason | None

    @property
    def is_split(self) -> bool:
        return len(self.blocks) > 1


def plan_pdf_blocks(
    file_bytes: bytes,
    *,
    pages_per_block: int,
    min_pages: int,
    max_pages: int,
) -> PdfBlockPlan:
    """Decide se divide o PDF e monta os blocos por página.

    Args:
        file_bytes: o PDF enviado (já validado por magic bytes).
        pages_per_block: tamanho-alvo do bloco em páginas (`ge=1`).
        min_pages: até este número de páginas (inclusive) o PDF vai inteiro.
        max_pages: acima deste número o arquivo é recusado.

    Returns:
        `PdfBlockPlan`. Sem divisão quando o `pypdf` não lê o arquivo
        (`unreadable`), quando o PDF é cifrado e não abre com senha vazia
        (`encrypted`) ou quando cabe em `min_pages` (`too_few_pages`).

    Raises:
        PdfTooManyPagesError: mais páginas que `max_pages`.
    """
    whole = [file_bytes]
    try:
        reader = PdfReader(BytesIO(file_bytes))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            return PdfBlockPlan(
                blocks=whole, first_page=None, pages=0, skipped_reason=PDF_SKIP_ENCRYPTED
            )
        pages = len(reader.pages)
    except PdfTooManyPagesError:  # pragma: no cover  -- não nasce aqui; defensivo
        raise
    except Exception:
        # Qualquer falha de abertura é o mesmo desfecho: o arquivo vai inteiro,
        # como antes desta task. `from None` não se aplica (não re-levantamos),
        # e a exceção original, que pode citar objetos do PDF, não vai a log.
        return PdfBlockPlan(
            blocks=whole, first_page=None, pages=0, skipped_reason=PDF_SKIP_UNREADABLE
        )

    if pages > max_pages:
        raise PdfTooManyPagesError(pages=pages, max_pages=max_pages)
    if pages <= min_pages:
        return PdfBlockPlan(
            blocks=whole, first_page=None, pages=pages, skipped_reason=PDF_SKIP_TOO_FEW_PAGES
        )

    n_blocks = math.ceil(pages / pages_per_block)
    size = math.ceil(pages / n_blocks)
    try:
        blocks = [
            _pages_as_pdf(reader, range(start, min(start + size, pages)))
            for start in range(0, pages, size)
        ]
        first_page = _pages_as_pdf(reader, range(0, 1))
    except Exception:
        # Página que o `pypdf` não consegue copiar (objeto quebrado, stream
        # inválido): o modelo ainda pode ler o PDF inteiro, então não recusamos.
        return PdfBlockPlan(
            blocks=whole, first_page=None, pages=pages, skipped_reason=PDF_SKIP_UNREADABLE
        )
    return PdfBlockPlan(blocks=blocks, first_page=first_page, pages=pages, skipped_reason=None)


def _pages_as_pdf(reader: PdfReader, indexes: range) -> bytes:
    """Serializa as páginas `indexes` (0-based, contíguas) como um PDF novo."""
    writer = PdfWriter()
    for index in indexes:
        writer.add_page(reader.pages[index])
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
