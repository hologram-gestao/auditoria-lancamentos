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

Envelope versionado (Sprint 3, BACK 03.3):
    A partir da Sprint 3 os valores NOVOS são gravados com uma DEK-por-cliente e
    um envelope versionado e amarrado à linha:

        coluna `_encrypted` = `v<n>:<key_id>:<ciphertext_hex>`
        coluna `_iv`        = `iv_hex`
        AAD = client_id ‖ tabela ‖ coluna ‖ pk

    A leitura é MULTI-CHAVE: uma linha SEM o prefixo `v<n>:<key_id>:` (bare
    `ciphertext_hex`) é tratada como LEGADO — decifrada com a chave global
    `OMIE_ENCRYPTION_KEY` e sem AAD (`associated_data=None`), até ser convertida
    pelo backfill (BACK 03.4). Ver `ClientCipher`.

Rotação de chave:
    A rotação/backfill vive em `scripts/rotate_encryption_key.py` (BACK 03.4):
    re-cifra por lotes, online, cada linha bare para `v1:<key_id>:` + AAD + DEK
    do cliente. O `key_id` por linha diz qual chave/versão aplicar, o que torna
    a rotação interrompível e retomável.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.exceptions import AppError, ErrorCode

IV_SIZE_BYTES = 12  # 96 bits — recomendação para AES-GCM
KEY_SIZE_BYTES = 32  # 256 bits
KEY_SIZE_HEX_CHARS = KEY_SIZE_BYTES * 2  # 64

# Versão corrente do envelope. Toda ESCRITA usa esta versão.
CURRENT_ENVELOPE_VERSION = 1
# Separador do AAD (unit separator ASCII) — inambíguo entre os 4 componentes.
_AAD_SEP = "\x1f"


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


# ----------------------------------------------------------------------
# Envelope versionado com DEK-por-cliente + AAD (Sprint 3, BACK 03.3)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class FieldLocator:
    """Localiza o campo cifrado no banco — compõe o AAD que amarra o ciphertext
    à SUA linha/coluna. Copiar o ciphertext para outra linha/coluna faz a
    decifragem falhar (o AAD não bate)."""

    table: str
    column: str
    pk: str  # UUID da linha, em string


def build_aad(client_id: str, locator: FieldLocator) -> bytes:
    """AAD = client_id ‖ tabela ‖ coluna ‖ pk (bytes UTF-8, separados por \\x1f)."""
    return _AAD_SEP.join((client_id, locator.table, locator.column, locator.pk)).encode("utf-8")


def _parse_envelope(envelope: str) -> tuple[int, str, str] | None:
    """Separa `v<n>:<key_id>:<ciphertext_hex>`.

    Retorna `(version, key_id, ciphertext_hex)` ou `None` se a string for uma
    linha LEGADA (bare `ciphertext_hex`, sem prefixo). Um ciphertext hex nunca
    contém `v` nem `:`, então o discriminador é seguro.
    """
    if not envelope.startswith("v"):
        return None
    parts = envelope.split(":", 2)
    if len(parts) != 3:
        return None
    ver_token, key_id, ciphertext_hex = parts
    version_digits = ver_token[1:]
    if not version_digits.isdigit() or not key_id:
        return None
    return int(version_digits), key_id, ciphertext_hex


class ClientCipher:
    """Cifra/decifra campos de UM cliente com a DEK dele + AAD por linha.

    Construção: use `app.core.crypto_service.build_client_cipher`, que desembrulha
    a `clients.dek_wrapped` via KMS. Esta classe é PURA (sem I/O) — recebe a DEK
    em claro já desembrulhada.

    - `encrypt` grava SEMPRE na versão corrente (`v<n>:<key_id>:` + AAD + DEK).
    - `decrypt` é MULTI-CHAVE: linha `v<n>:<key_id>:` → DEK + AAD; linha bare
      (legado) → chave global `legacy_hex_key` + `associated_data=None`.
    """

    def __init__(
        self, *, client_id: str, dek: bytes | None, key_id: str, legacy_hex_key: str
    ) -> None:
        self._client_id = client_id
        self._dek = dek
        self._key_id = key_id
        self._legacy_hex_key = legacy_hex_key

    def _require_dek(self) -> bytes:
        if self._dek is None:
            # DEK ausente/corrompida → erro tratado, NUNCA fallback silencioso
            # para a chave global (o fallback é o defeito, não o remédio).
            raise CryptoError("DEK do cliente ausente — impossível cifrar/decifrar com AAD.")
        return self._dek

    def encrypt(self, plaintext: str, locator: FieldLocator) -> tuple[str, str]:
        """Cifra `plaintext` na versão corrente. Retorna `(envelope, iv_hex)`."""
        dek = self._require_dek()
        iv = os.urandom(IV_SIZE_BYTES)
        aesgcm = AESGCM(dek)
        aad = build_aad(self._client_id, locator)
        ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), aad)
        envelope = f"v{CURRENT_ENVELOPE_VERSION}:{self._key_id}:{ciphertext.hex()}"
        return envelope, iv.hex()

    def decrypt(self, envelope: str, iv_hex: str, locator: FieldLocator) -> str:
        """Decifra um valor gravado por `encrypt` (v-envelope) OU legado (bare)."""
        parsed = _parse_envelope(envelope)
        if parsed is None:
            # Linha LEGADA: chave global, sem AAD. Convertida pelo backfill (03.4).
            return decrypt(envelope, iv_hex, self._legacy_hex_key)

        _version, _key_id, ciphertext_hex = parsed
        dek = self._require_dek()
        try:
            ciphertext = bytes.fromhex(ciphertext_hex)
            iv = bytes.fromhex(iv_hex)
        except ValueError as exc:
            raise CryptoError("ciphertext ou IV não são hex válidos.") from exc
        if len(iv) != IV_SIZE_BYTES:
            raise CryptoError(f"IV deve ter {IV_SIZE_BYTES} bytes. Recebido: {len(iv)}.")

        aad = build_aad(self._client_id, locator)
        aesgcm = AESGCM(dek)
        try:
            plaintext_bytes = aesgcm.decrypt(iv, ciphertext, aad)
        except InvalidTag as exc:
            # AAD errado (ciphertext de outra linha), tag adulterada ou DEK errada.
            raise CryptoError(
                "Falha na descriptografia: dado adulterado, fora de contexto ou chave incorreta."
            ) from exc
        return plaintext_bytes.decode("utf-8")

    @staticmethod
    def is_legacy(envelope: str) -> bool:
        """True se a string está no formato bare legado (sem prefixo `v<n>:`)."""
        return _parse_envelope(envelope) is None
