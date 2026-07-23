"""Envelope de chaves: wrap/unwrap da DEK-por-cliente via KEK (Sprint 3, BACK 03.3).

Modelo (Req. 1 da Sprint 3):
    - Uma **KEK** (Key Encryption Key) que **nunca sai do KMS**.
    - Cada cliente tem sua **DEK** (Data Encryption Key), gerada na criação e
      guardada **cifrada pela KEK** em `clients.dek_wrapped`.
    - A DEK em claro existe **apenas em memória**, pelo tempo da operação.

Dois modos:
    - **Cloud KMS** (produção): `KEK_KMS_KEY_NAME` setado → wrap/unwrap chamam
      `encrypt`/`decrypt` no Google Cloud KMS. A KEK não é baixada — o código
      só pede ao KMS para cifrar/decifrar a DEK. É a task de infra (03.1) que
      provisiona a KEK e concede `cloudkms.cryptoKeyVersions.useToEncrypt/Decrypt`.
    - **Local** (dev/test — não há sandbox de KMS, mesma política do Omie):
      `KEK_KMS_KEY_NAME=None` → a KEK é derivada de `OMIE_ENCRYPTION_KEY` via
      HKDF (domínio separado por `key_id`). O isolamento DEK-por-cliente + AAD
      vale igual nos dois modos; só o blast radius da KEK depende do KMS real.

NUNCA logar DEK (embrulhada ou não) nem a KEK (CLAUDE.md §3).
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.crypto import CryptoError

if TYPE_CHECKING:
    from app.core.config import Settings

DEK_SIZE_BYTES = 32  # 256 bits — DEK também é AES-256
_WRAP_IV_SIZE_BYTES = 12


def generate_dek() -> bytes:
    """Gera uma DEK aleatória de 256 bits. Só existe em memória."""
    return os.urandom(DEK_SIZE_BYTES)


class KmsClient(Protocol):
    """Contrato de wrap/unwrap da DEK. Compartilhado com a task de infra 03.1."""

    @property
    def key_id(self) -> str:
        """Identificador da geração de chave gravado no envelope."""
        ...

    async def wrap_dek(self, dek: bytes) -> bytes:
        """Cifra a DEK com a KEK. Retorna o blob embrulhado (para `dek_wrapped`)."""
        ...

    async def unwrap_dek(self, wrapped: bytes) -> bytes:
        """Decifra o blob embrulhado e devolve a DEK em claro (só em memória)."""
        ...


class LocalKmsClient:
    """Wrapper LOCAL para dev/test — deriva a KEK de `OMIE_ENCRYPTION_KEY`.

    Não substitui o KMS em produção: aqui a KEK vive no processo (derivada do
    segredo global). O que ele garante e é o que os testes cobrem: cada cliente
    tem uma DEK distinta, então a DEK de A não decifra nada de B (o dado é
    cifrado com a DEK de B + AAD). O blast radius da KEK só é fechado com o
    KMS real (produção).
    """

    def __init__(self, *, master_hex_key: str, key_id: str) -> None:
        self._key_id = key_id
        self._kek = self._derive_kek(master_hex_key, key_id)

    @staticmethod
    def _derive_kek(master_hex_key: str, key_id: str) -> bytes:
        try:
            master = bytes.fromhex(master_hex_key)
        except ValueError as exc:  # pragma: no cover - Settings já valida o hex
            raise CryptoError("Chave mestra do KMS local não é hex válido.") from exc
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=DEK_SIZE_BYTES,
            salt=None,
            info=f"adl-kek:{key_id}".encode(),
        )
        return hkdf.derive(master)

    @property
    def key_id(self) -> str:
        return self._key_id

    async def wrap_dek(self, dek: bytes) -> bytes:
        iv = os.urandom(_WRAP_IV_SIZE_BYTES)
        # AAD do wrap = key_id: um blob embrulhado sob key_id A não desembrulha sob B.
        ct = AESGCM(self._kek).encrypt(iv, dek, self._key_id.encode("utf-8"))
        return iv + ct

    async def unwrap_dek(self, wrapped: bytes) -> bytes:
        if len(wrapped) <= _WRAP_IV_SIZE_BYTES:
            raise CryptoError("dek_wrapped local malformado (curto demais).")
        iv, ct = wrapped[:_WRAP_IV_SIZE_BYTES], wrapped[_WRAP_IV_SIZE_BYTES:]
        from cryptography.exceptions import InvalidTag

        try:
            return AESGCM(self._kek).decrypt(iv, ct, self._key_id.encode("utf-8"))
        except InvalidTag as exc:
            raise CryptoError(
                "Falha ao desembrulhar a DEK (KEK errada ou dado adulterado)."
            ) from exc


class CloudKmsClient:
    """Wrapper de produção — a KEK vive no Cloud KMS e nunca sai de lá.

    Faz `encrypt`/`decrypt` da DEK no KMS. O SDK do google-cloud-kms é síncrono;
    rodamos em threadpool (`asyncio.to_thread`) para não bloquear o event loop.
    A dependência `google-cloud-kms` é provisionada pela task de infra (03.1);
    importamos de forma lazy para não quebrar dev/test/CI que rodam no modo local.
    """

    def __init__(self, *, kms_key_name: str, key_id: str) -> None:
        self._kms_key_name = kms_key_name
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def _client(self) -> object:
        try:
            from google.cloud import kms
        except ImportError as exc:  # pragma: no cover - só em prod com KMS configurado
            raise CryptoError(
                "KEK_KMS_KEY_NAME setado mas google-cloud-kms não está instalado. "
                "A task de infra (03.1) provisiona essa dependência.",
            ) from exc
        return kms.KeyManagementServiceClient()

    async def wrap_dek(self, dek: bytes) -> bytes:  # pragma: no cover - requer KMS real
        def _call() -> bytes:
            client = self._client()
            resp = client.encrypt(  # type: ignore[attr-defined]
                request={"name": self._kms_key_name, "plaintext": dek}
            )
            return bytes(resp.ciphertext)

        return await asyncio.to_thread(_call)

    async def unwrap_dek(self, wrapped: bytes) -> bytes:  # pragma: no cover - requer KMS real
        def _call() -> bytes:
            client = self._client()
            resp = client.decrypt(  # type: ignore[attr-defined]
                request={"name": self._kms_key_name, "ciphertext": wrapped}
            )
            return bytes(resp.plaintext)

        return await asyncio.to_thread(_call)


def get_kms_client(settings: Settings) -> KmsClient:
    """Fábrica do KMS client conforme a config.

    `KEK_KMS_KEY_NAME` setado → Cloud KMS (produção). Caso contrário, wrapper
    local derivado de `OMIE_ENCRYPTION_KEY` (dev/test).
    """
    key_id = settings.KEK_KEY_ID
    if settings.KEK_KMS_KEY_NAME:
        return CloudKmsClient(kms_key_name=settings.KEK_KMS_KEY_NAME, key_id=key_id)
    return LocalKmsClient(
        master_hex_key=settings.OMIE_ENCRYPTION_KEY.get_secret_value(),
        key_id=key_id,
    )
