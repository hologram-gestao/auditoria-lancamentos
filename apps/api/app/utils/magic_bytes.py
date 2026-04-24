"""Detecção de tipo real de arquivo via magic bytes (file signatures).

NÃO confiar em extensão (`.pdf`, `.xlsx`) — pode ser facilmente falsificada.
A validação real exige inspeção dos primeiros bytes do conteúdo.

Tipos suportados (S8 — formulário de nova conciliação):
    - PDF: assinatura `%PDF-`
    - XLSX: assinatura ZIP `PK\\x03\\x04` (XLSX é zip de XMLs)
    - XLS: assinatura OLE Compound Document `\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1`
    - CSV: sem magic bytes — heurística textual

Limitações conhecidas:
    - XLSX/DOCX/PPTX compartilham a mesma assinatura ZIP. Para distinguir,
      seria preciso inspecionar `[Content_Types].xml` dentro do ZIP. Para o
      contexto S8, aceitar qualquer ZIP é suficiente porque o filtro de
      extensão (`.xlsx`) já roda antes no frontend (UX).
"""

from enum import StrEnum

from app.core.exceptions import ValidationAppError


class FileType(StrEnum):
    """Tipos de arquivo suportados pelo sistema."""

    PDF = "pdf"
    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"
    UNKNOWN = "unknown"


# Assinaturas binárias canônicas (primeiros bytes do arquivo)
_SIGNATURES: dict[FileType, list[bytes]] = {
    FileType.PDF: [b"%PDF-"],
    FileType.XLSX: [b"PK\x03\x04"],  # ZIP container (XLSX/DOCX/PPTX)
    FileType.XLS: [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"],  # OLE Compound Document
}

_MIN_HEAD_BYTES = 8  # Suficiente para detectar todas as assinaturas binárias
_CSV_SAMPLE_BYTES = 1024  # Amostra para heurística textual


def detect_file_type(content: bytes) -> FileType:
    """Detecta o tipo do arquivo pelos primeiros bytes.

    Args:
        content: bytes brutos do arquivo. Aceita stream curto (>= 8 bytes).

    Returns:
        `FileType` detectado ou `FileType.UNKNOWN` se não bater com nenhuma
        assinatura conhecida e não parecer CSV.
    """
    if not content or len(content) < _MIN_HEAD_BYTES:
        return FileType.UNKNOWN

    # 1. Tentar match com assinaturas binárias
    for file_type, signatures in _SIGNATURES.items():
        for sig in signatures:
            if content.startswith(sig):
                return file_type

    # 2. Heurística CSV — texto válido com separadores e quebras de linha
    sample = content[:_CSV_SAMPLE_BYTES]
    try:
        text = sample.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            text = sample.decode("latin-1", errors="strict")
        except UnicodeDecodeError:
            return FileType.UNKNOWN

    has_separator = any(sep in text for sep in (",", ";", "\t"))
    has_newline = "\n" in text or "\r" in text
    if has_separator and has_newline:
        return FileType.CSV

    return FileType.UNKNOWN


def validate_upload_type(content: bytes, allowed: set[FileType]) -> FileType:
    """Detecta + valida que o tipo está em `allowed`.

    Use em endpoints de upload para rejeitar arquivos com extensão falsificada.

    Args:
        content: bytes do upload.
        allowed: conjunto de tipos aceitos para o contexto.

    Returns:
        O `FileType` detectado (garantidamente em `allowed`).

    Raises:
        ValidationAppError: tipo desconhecido ou fora do conjunto permitido.
    """
    detected = detect_file_type(content)
    if detected == FileType.UNKNOWN:
        raise ValidationAppError(
            "Magic bytes não correspondem a nenhum formato suportado.",
            user_message="Formato de arquivo não suportado. Envie PDF, CSV, XLS ou XLSX.",
        )
    if detected not in allowed:
        raise ValidationAppError(
            f"Tipo {detected} detectado, não permitido neste endpoint.",
            user_message="Formato de arquivo não suportado neste contexto.",
        )
    return detected
