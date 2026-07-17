"""Leitura de upload com teto de tamanho (BACK 02.8).

Antes, o `/parse` fazia `await file.read()` — carregava o arquivo INTEIRO num
único objeto de memória e SÓ DEPOIS checava o tamanho: o limite de 20 MB não
protegia a alocação. Aqui:

  1. Pré-checagem barata pelo `Content-Length` do request ANTES de ler qualquer
     byte — rejeita upload absurdo sem entrar no loop de leitura.
  2. Leitura em STREAMING (chunks) com corte no limite: assim que o total passa
     o teto, aborta — nunca constrói um objeto de bytes maior que o limite.

O teto PERMANECE 20 MB (`Settings.MAX_UPLOAD_SIZE_MB`, fonte única) — não baixa.
Um extrato de 14 MB que concilia hoje continua conciliando.
"""

from __future__ import annotations

from typing import Protocol

from app.core.exceptions import ValidationAppError

# Tamanho do chunk de leitura em streaming (1 MB). Balanceia nº de awaits x
# memória transitória.
_CHUNK_SIZE = 1024 * 1024

# Margem sobre o `Content-Length` para a pré-checagem. O `Content-Length` do
# request multipart = arquivo + envelope (boundaries, headers dos campos). Um
# arquivo de EXATAMENTE 20 MB tem Content-Length um pouco > 20 MB — sem margem,
# a pré-checagem rejeitaria um arquivo legítimo no limite. 1 MB cobre o envelope
# com folga (o corte PRECISO por bytes do arquivo é o do streaming abaixo).
_CONTENT_LENGTH_ALLOWANCE = 1024 * 1024


class _ReadableUpload(Protocol):
    """Subset do `starlette.datastructures.UploadFile` usado aqui."""

    async def read(self, size: int = -1, /) -> bytes: ...


def _too_large_error(max_bytes: int) -> ValidationAppError:
    return ValidationAppError(
        f"Upload excede o limite de {max_bytes} bytes.",
        user_message=(
            f"O arquivo excede o limite de {max_bytes // (1024 * 1024)} MB. Envie um arquivo menor."
        ),
    )


async def read_upload_within_limit(
    file: _ReadableUpload,
    *,
    declared_content_length: int | None,
    max_bytes: int,
) -> bytes:
    """Lê o upload em streaming, rejeitando (4xx) acima de `max_bytes`.

    Args:
        file: o `UploadFile` (precisa de `.read(size)`).
        declared_content_length: valor do header `Content-Length` do request
            (ou `None` se ausente/ilegível — ex: transfer-encoding chunked).
        max_bytes: teto do ARQUIVO em bytes (`Settings.max_upload_bytes`).

    Returns:
        Os bytes do arquivo (garantidamente `<= max_bytes`).

    Raises:
        ValidationAppError: acima do limite (4xx acionável). Levantada ANTES
            de carregar o arquivo inteiro na memória.
    """
    # 1. Pré-checagem pelo Content-Length — rejeita upload absurdo sem ler.
    if (
        declared_content_length is not None
        and declared_content_length > max_bytes + _CONTENT_LENGTH_ALLOWANCE
    ):
        raise _too_large_error(max_bytes)

    # 2. Streaming com corte no limite — nunca constrói bytes > max_bytes.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            # Aborta AQUI — não acumula o resto do arquivo.
            raise _too_large_error(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def parse_content_length(raw: str | None) -> int | None:
    """Converte o header `Content-Length` em int, ou `None` se ausente/inválido."""
    if raw is not None and raw.isdigit():
        return int(raw)
    return None
