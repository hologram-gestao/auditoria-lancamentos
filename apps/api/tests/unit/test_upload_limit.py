"""Testes da leitura de upload com teto (BACK 02.8).

Cobre a pré-checagem por Content-Length (rejeita SEM ler) e o corte em
streaming (rejeita sem carregar o arquivo inteiro).
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationAppError
from app.utils.upload import parse_content_length, read_upload_within_limit

_MB = 1024 * 1024


class _FakeUpload:
    """Fake do UploadFile — conta quantas vezes `.read` foi chamado."""

    def __init__(self, data: bytes) -> None:
        self._buf = data
        self._pos = 0
        self.read_calls = 0

    async def read(self, size: int = -1, /) -> bytes:
        self.read_calls += 1
        if size < 0:
            chunk = self._buf[self._pos :]
            self._pos = len(self._buf)
        else:
            chunk = self._buf[self._pos : self._pos + size]
            self._pos += len(chunk)
        return chunk


class TestReadUploadWithinLimit:
    async def test_normal_file_returns_all_bytes(self) -> None:
        data = b"%PDF-1.7\n" + b"x" * 500
        up = _FakeUpload(data)
        out = await read_upload_within_limit(up, declared_content_length=len(data), max_bytes=_MB)
        assert out == data

    async def test_file_at_limit_is_accepted(self) -> None:
        # Exatamente no teto não é rejeitado (limite exclusivo: só > é erro).
        up = _FakeUpload(b"x" * 10)
        out = await read_upload_within_limit(up, declared_content_length=None, max_bytes=10)
        assert len(out) == 10

    async def test_streaming_cutoff_rejects_over_limit(self) -> None:
        # 1 byte acima do teto → 4xx, mesmo sem Content-Length (chunked).
        up = _FakeUpload(b"x" * 11)
        with pytest.raises(ValidationAppError) as exc:
            await read_upload_within_limit(up, declared_content_length=None, max_bytes=10)
        assert "limite" in exc.value.user_message.lower()

    async def test_content_length_precheck_rejects_without_reading(self) -> None:
        # Content-Length muito acima (> teto + margem) → rejeita ANTES de ler.
        up = _FakeUpload(b"x" * 50)
        with pytest.raises(ValidationAppError):
            await read_upload_within_limit(
                up,
                declared_content_length=10 + 2 * _MB,  # > 10 + 1MB de margem
                max_bytes=10,
            )
        # Nenhuma leitura aconteceu — rejeição pura por header.
        assert up.read_calls == 0

    async def test_content_length_within_allowance_not_prechecked(self) -> None:
        # Content-Length pouco acima do teto (dentro da margem do envelope) NÃO
        # é rejeitado pela pré-checagem; o corte preciso é por bytes do arquivo.
        data = b"x" * 8
        up = _FakeUpload(data)
        out = await read_upload_within_limit(
            up,
            declared_content_length=10 + 500 * 1024,  # < 10 + 1MB
            max_bytes=10,
        )
        assert out == data


class TestParseContentLength:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1024", 1024),
            (None, None),
            ("", None),
            ("abc", None),
            ("-5", None),  # isdigit() rejeita sinal
        ],
    )
    def test_parse(self, raw: str | None, expected: int | None) -> None:
        assert parse_content_length(raw) == expected
