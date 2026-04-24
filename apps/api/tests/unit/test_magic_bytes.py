"""Testes da detecção de tipo real de arquivo via magic bytes."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationAppError
from app.utils.magic_bytes import FileType, detect_file_type, validate_upload_type


class TestDetectFileType:
    def test_detects_pdf(self) -> None:
        content = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n3 0 obj\n<<>>"
        assert detect_file_type(content) == FileType.PDF

    def test_detects_xlsx(self) -> None:
        # XLSX é um zip — assinatura PK\x03\x04
        content = b"PK\x03\x04\x14\x00\x06\x00\x08\x00"
        assert detect_file_type(content) == FileType.XLSX

    def test_detects_xls(self) -> None:
        # OLE Compound Document
        content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00"
        assert detect_file_type(content) == FileType.XLS

    def test_detects_csv_with_comma(self) -> None:
        content = b"data,descricao,valor\n2026-01-01,Pagamento,1234.56\n"
        assert detect_file_type(content) == FileType.CSV

    def test_detects_csv_with_semicolon(self) -> None:
        content = b"data;descricao;valor\n2026-01-01;Pagamento;1234,56\n"
        assert detect_file_type(content) == FileType.CSV

    def test_detects_csv_with_tab(self) -> None:
        content = b"data\tdescricao\tvalor\n2026-01-01\tPagamento\t1234.56\n"
        assert detect_file_type(content) == FileType.CSV

    def test_csv_with_latin1(self) -> None:
        content = "data,descrição,valor\n2026-01-01,Pagamento à vista,1234.56\n".encode("latin-1")
        assert detect_file_type(content) == FileType.CSV

    def test_empty_content_is_unknown(self) -> None:
        assert detect_file_type(b"") == FileType.UNKNOWN

    def test_too_short_content_is_unknown(self) -> None:
        assert detect_file_type(b"abc") == FileType.UNKNOWN

    def test_binary_garbage_is_unknown(self) -> None:
        # 16 bytes pseudo-aleatórios sem assinatura conhecida
        content = b"\x42\x99\x01\x77\xee\x00\x00\x00\x12\x34\x56\x78\x9a\xbc\xde\xf0"
        assert detect_file_type(content) == FileType.UNKNOWN

    def test_plain_text_without_separator_is_unknown(self) -> None:
        """Texto puro sem separadores típicos de CSV não é classificado."""
        content = b"isso eh apenas um texto qualquer sem virgulas"
        assert detect_file_type(content) == FileType.UNKNOWN

    def test_falsified_pdf_extension_with_xls_content(self) -> None:
        """Mesmo se o arquivo for chamado de .pdf, magic bytes detectam XLS real."""
        xls_content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        # Detecta como XLS independente de qualquer extensão externa
        assert detect_file_type(xls_content) == FileType.XLS


class TestValidateUploadType:
    def test_accepts_allowed_type(self) -> None:
        content = b"%PDF-1.7"
        result = validate_upload_type(content, allowed={FileType.PDF, FileType.CSV})
        assert result == FileType.PDF

    def test_rejects_unknown_type(self) -> None:
        content = b"\x00" * 100
        with pytest.raises(ValidationAppError, match="Magic bytes"):
            validate_upload_type(content, allowed={FileType.PDF})

    def test_rejects_disallowed_type(self) -> None:
        """Detectado mas não permitido neste contexto."""
        content = b"%PDF-1.7"
        with pytest.raises(ValidationAppError, match="não permitido"):
            validate_upload_type(content, allowed={FileType.CSV})

    def test_user_message_in_pt_br_for_unknown(self) -> None:
        content = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08"
        with pytest.raises(ValidationAppError) as exc_info:
            validate_upload_type(content, allowed={FileType.PDF, FileType.XLSX})
        assert "PDF, CSV, XLS ou XLSX" in exc_info.value.user_message
