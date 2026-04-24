"""Criptografia simétrica AES-256-GCM para campos sensíveis.

Usado para criptografar credenciais Omie (`omie_app_key`, `omie_app_secret`),
descrições de movimentações e notas livres do analista. Ver CLAUDE.md §4.

Algoritmo:
    - AES-256-GCM (cifra autenticada — detecta tampering via tag GCM).
    - Chave: 256 bits (32 bytes / 64 chars hex), única, em `OMIE_ENCRYPTION_KEY`.
    - IV: 12 bytes aleatórios por operação (NUNCA reutilizar).
    - Tag GCM: 16 bytes, embutida no ciphertext pela biblioteca `cryptography`.

Persistência:
    Os retornos `ciphertext_hex` e `iv_hex` vão para colunas separadas no DB
    (formato hex, ASCII puro, fácil de armazenar em TEXT/VARCHAR). A chave
    NUNCA persiste no banco — apenas em variável de ambiente.

Rotação de chave:
    Para rotacionar `OMIE_ENCRYPTION_KEY`, é preciso re-criptografar todos os
    registros afetados em uma operação atômica (ver `scripts/rotate-encryption-key.py`
    a ser criado em S16).
"""

from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.exceptions import AppError, ErrorCode

IV_SIZE_BYTES = 12  # 96 bits — recomendação para AES-GCM
KEY_SIZE_BYTES = 32  # 256 bits
KEY_SIZE_HEX_CHARS = KEY_SIZE_BYTES * 2  # 64


class CryptoError(AppError):
    """Falha em operação de criptografia/descriptografia.

    Inclui: chave malformada, dado adulterado (tag GCM inválida),
    payload corrompido, tamanho de IV incorreto.
    """

    code = ErrorCode.INTERNAL_ERROR
    status_code = 500
    default_user_message = "Erro interno ao processar dado seguro."


def _hex_key_to_bytes(hex_key: str) -> bytes:
    """Converte chave hex (64 chars) em 32 bytes. Valida formato."""
    if not hex_key:
        raise CryptoError("Chave de criptografia vazia.")
    if len(hex_key) != KEY_SIZE_HEX_CHARS:
        raise CryptoError(
            f"Chave deve ter {KEY_SIZE_HEX_CHARS} caracteres hex (256 bits). "
            f"Recebido: {len(hex_key)}."
        )
    try:
        return bytes.fromhex(hex_key)
    except ValueError as exc:
        raise CryptoError("Chave não é hexadecimal válido.") from exc


def encrypt(plaintext: str, hex_key: str) -> tuple[str, str]:
    """Criptografa `plaintext` com AES-256-GCM.

    Args:
        plaintext: texto UTF-8 a ser criptografado (pode ser string vazia).
        hex_key: chave AES-256 em hex (64 chars).

    Returns:
        Tupla `(ciphertext_hex, iv_hex)`. Ambos hex strings prontos para persistir.
        O `ciphertext_hex` já contém a tag GCM nos últimos 16 bytes.

    Raises:
        CryptoError: chave inválida ou malformada.
    """
    key_bytes = _hex_key_to_bytes(hex_key)
    iv = os.urandom(IV_SIZE_BYTES)
    aesgcm = AESGCM(key_bytes)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=None)
    return ciphertext.hex(), iv.hex()


def decrypt(ciphertext_hex: str, iv_hex: str, hex_key: str) -> str:
    """Descriptografa um ciphertext gerado por `encrypt()`.

    Args:
        ciphertext_hex: ciphertext em hex (incluindo tag GCM).
        iv_hex: IV em hex (24 chars = 12 bytes) usado na criptografia.
        hex_key: a MESMA chave AES-256 usada para criptografar.

    Returns:
        Plaintext UTF-8.

    Raises:
        CryptoError: chave errada, dado adulterado (tag inválida), formato corrompido.
    """
    key_bytes = _hex_key_to_bytes(hex_key)

    try:
        ciphertext = bytes.fromhex(ciphertext_hex)
        iv = bytes.fromhex(iv_hex)
    except ValueError as exc:
        raise CryptoError("ciphertext ou IV não são hex válidos.") from exc

    if len(iv) != IV_SIZE_BYTES:
        raise CryptoError(f"IV deve ter {IV_SIZE_BYTES} bytes. Recebido: {len(iv)}.")

    aesgcm = AESGCM(key_bytes)
    try:
        plaintext_bytes = aesgcm.decrypt(iv, ciphertext, associated_data=None)
    except InvalidTag as exc:
        # NUNCA expor "chave errada" vs "tampering" — opacidade é segurança.
        raise CryptoError("Falha na descriptografia: dado adulterado ou chave incorreta.") from exc

    return plaintext_bytes.decode("utf-8")
