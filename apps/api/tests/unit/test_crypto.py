"""Testes da criptografia AES-256-GCM.

Critérios:
    - Round-trip: encrypt → decrypt devolve o plaintext original.
    - IV único por chamada (nunca colide).
    - Tag GCM detecta tampering (qualquer alteração no ciphertext falha decrypt).
    - Chave errada falha (mensagem genérica para não vazar informação).
    - Suporta unicode, vazio, payloads grandes.
    - Validação rigorosa de formato (key/iv).
"""

from __future__ import annotations

import pytest

from app.core.crypto import (
    IV_SIZE_BYTES,
    KEY_SIZE_HEX_CHARS,
    CryptoError,
    decrypt,
    encrypt,
)

# Chaves fixas para testes (não use em produção, óbvio)
KEY_A = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
KEY_B = "ff112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


class TestEncryptDecryptRoundTrip:
    """Garante que encrypt → decrypt restaura o plaintext."""

    def test_simple_ascii(self) -> None:
        plaintext = "hello world"
        ct, iv = encrypt(plaintext, KEY_A)
        assert decrypt(ct, iv, KEY_A) == plaintext

    def test_unicode_portuguese(self) -> None:
        plaintext = "Conciliação de R$ 1.234,56 — Açaí com ção"
        ct, iv = encrypt(plaintext, KEY_A)
        assert decrypt(ct, iv, KEY_A) == plaintext

    def test_unicode_emojis(self) -> None:
        plaintext = "🔐 segredo 💰 R$ 10 ✅"
        ct, iv = encrypt(plaintext, KEY_A)
        assert decrypt(ct, iv, KEY_A) == plaintext

    def test_empty_string(self) -> None:
        ct, iv = encrypt("", KEY_A)
        assert decrypt(ct, iv, KEY_A) == ""

    def test_long_payload(self) -> None:
        plaintext = "x" * 100_000
        ct, iv = encrypt(plaintext, KEY_A)
        assert decrypt(ct, iv, KEY_A) == plaintext

    def test_newlines_and_special_chars(self) -> None:
        plaintext = 'linha 1\nlinha 2\r\n\ttab\x00null\x1bescape "aspas"'
        ct, iv = encrypt(plaintext, KEY_A)
        assert decrypt(ct, iv, KEY_A) == plaintext


class TestIVUniqueness:
    """Cada operação de criptografia gera IV novo. Crítico para AES-GCM."""

    def test_iv_changes_each_call(self) -> None:
        """100 calls com mesmo plaintext devem gerar 100 IVs distintos."""
        plaintext = "always the same"
        ivs = {encrypt(plaintext, KEY_A)[1] for _ in range(100)}
        assert len(ivs) == 100, "IV deve ser único por chamada"

    def test_iv_size_is_correct(self) -> None:
        _, iv = encrypt("x", KEY_A)
        assert len(bytes.fromhex(iv)) == IV_SIZE_BYTES

    def test_ciphertext_different_for_same_plaintext(self) -> None:
        """Mesmo plaintext + IVs diferentes → ciphertexts diferentes."""
        cts = {encrypt("repeat me", KEY_A)[0] for _ in range(50)}
        assert len(cts) == 50


class TestTamperingDetection:
    """Tag GCM deve detectar qualquer adulteração."""

    def test_modified_ciphertext_fails(self) -> None:
        ct, iv = encrypt("dado importante", KEY_A)
        # Flipa o último byte do ciphertext (parte da tag)
        ct_bytes = bytearray(bytes.fromhex(ct))
        ct_bytes[-1] ^= 0x01
        tampered = ct_bytes.hex()
        with pytest.raises(CryptoError, match="adulterado ou chave incorreta"):
            decrypt(tampered, iv, KEY_A)

    def test_modified_iv_fails(self) -> None:
        ct, iv = encrypt("dado", KEY_A)
        iv_bytes = bytearray(bytes.fromhex(iv))
        iv_bytes[0] ^= 0xFF
        with pytest.raises(CryptoError, match="adulterado ou chave incorreta"):
            decrypt(ct, iv_bytes.hex(), KEY_A)

    def test_swapped_ciphertext_iv_fails(self) -> None:
        """ciphertext de uma operação + IV de outra → falha."""
        ct1, _iv1 = encrypt("dado 1", KEY_A)
        _ct2, iv2 = encrypt("dado 2", KEY_A)
        with pytest.raises(CryptoError, match="adulterado ou chave incorreta"):
            decrypt(ct1, iv2, KEY_A)


class TestWrongKey:
    """Chave errada deve falhar com mensagem genérica (não vazar 'wrong key')."""

    def test_decrypt_with_different_key_fails(self) -> None:
        ct, iv = encrypt("segredo", KEY_A)
        with pytest.raises(CryptoError, match="adulterado ou chave incorreta"):
            decrypt(ct, iv, KEY_B)


class TestKeyValidation:
    """Validação rigorosa do formato da chave."""

    def test_empty_key_fails(self) -> None:
        with pytest.raises(CryptoError, match="vazia"):
            encrypt("x", "")

    def test_short_key_fails(self) -> None:
        with pytest.raises(CryptoError, match="64 caracteres hex"):
            encrypt("x", "abc")

    def test_long_key_fails(self) -> None:
        with pytest.raises(CryptoError, match="64 caracteres hex"):
            encrypt("x", "00" * 33)

    def test_non_hex_key_fails(self) -> None:
        bad_key = "z" * KEY_SIZE_HEX_CHARS
        with pytest.raises(CryptoError, match="hexadecimal"):
            encrypt("x", bad_key)


class TestPayloadValidation:
    """Validação do ciphertext/iv na descriptografia."""

    def test_invalid_hex_ciphertext(self) -> None:
        with pytest.raises(CryptoError, match="hex válidos"):
            decrypt("not-hex-at-all", "00" * 12, KEY_A)

    def test_invalid_hex_iv(self) -> None:
        with pytest.raises(CryptoError, match="hex válidos"):
            decrypt("aabb", "not-hex", KEY_A)

    def test_wrong_iv_size(self) -> None:
        ct, _ = encrypt("x", KEY_A)
        # IV de 8 bytes (16 hex chars) — formato hex válido mas tamanho errado
        with pytest.raises(CryptoError, match="IV deve ter"):
            decrypt(ct, "00" * 8, KEY_A)
