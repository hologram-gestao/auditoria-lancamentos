"""Ponte entre o envelope cripto (`core.crypto`) e o KMS (`core.kms`).

Constrói `ClientCipher`s desembrulhando a `clients.dek_wrapped` via KMS. É o
ÚNICO lugar que junta DEK + KEK + `OMIE_ENCRYPTION_KEY` legada — os serviços
recebem um `ClientCipher` pronto (sync, sem I/O) e chamam `.encrypt`/`.decrypt`.

Regras (Sprint 3):
    - A DEK em claro só vive em memória, pelo tempo da operação. NUNCA logada.
    - Nomes de tabela/coluna abaixo entram no AAD — **nunca** os altere: mudar
      um deles invalida a decifragem de tudo que já foi gravado com ele.
    - DEK ausente/corrompida → `CryptoError` (4xx via handler), NUNCA fallback
      silencioso para a chave global.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.core.crypto import ClientCipher, FieldLocator
from app.core.kms import generate_dek, get_kms_client

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.kms import KmsClient

# (tabela, coluna) que compõem o AAD de cada campo cifrado (CLAUDE.md §4).
# Congelados — fazem parte do AAD persistido.
AAD_CLIENT_APP_KEY = ("clients", "omie_app_key_encrypted")
AAD_CLIENT_APP_SECRET = ("clients", "omie_app_secret_encrypted")
AAD_FILE_ENTRY_DESCRIPTION = ("reconciliation_file_entries", "description_encrypted")
AAD_FILE_ENTRY_USER_NOTE = ("reconciliation_file_entries", "user_note_encrypted")
AAD_OMIE_ENTRY_USER_NOTE = ("reconciliation_omie_entries", "user_note_encrypted")
AAD_ANOMALY_CONTEXT = ("reconciliation_anomalies", "context_encrypted")
AAD_ANOMALY_RESOLUTION_NOTE = ("reconciliation_anomalies", "resolution_note_encrypted")


def field_locator(pair: tuple[str, str], pk: str | UUID) -> FieldLocator:
    """Monta um `FieldLocator` a partir de uma constante `(tabela, coluna)` + pk."""
    table, column = pair
    return FieldLocator(table=table, column=column, pk=str(pk))


class _HasDek(Protocol):
    """Qualquer objeto (ORM `Client` ou row do backfill) com id + dek_wrapped."""

    @property
    def id(self) -> UUID: ...
    @property
    def dek_wrapped(self) -> bytes | None: ...


def _legacy_hex_key(settings: Settings) -> str:
    return settings.OMIE_ENCRYPTION_KEY.get_secret_value()


async def load_client_cipher(
    client: _HasDek,
    *,
    settings: Settings,
    kms: KmsClient | None = None,
) -> ClientCipher:
    """Cipher para LEITURA: desembrulha a DEK existente (ou `None` se o cliente
    ainda não foi provisionado — nesse caso só linhas bare legadas são legíveis).
    NÃO provisiona DEK nova (não muta o cliente)."""
    kms = kms or get_kms_client(settings)
    dek: bytes | None = None
    if client.dek_wrapped is not None:
        dek = await kms.unwrap_dek(bytes(client.dek_wrapped))
    return ClientCipher(
        client_id=str(client.id),
        dek=dek,
        key_id=kms.key_id,
        legacy_hex_key=_legacy_hex_key(settings),
    )


async def provision_client_cipher(
    client: _HasDek,
    *,
    settings: Settings,
    kms: KmsClient | None = None,
) -> ClientCipher:
    """Cipher para ESCRITA: garante que o cliente tem DEK. Se `dek_wrapped` é
    None (cliente legado ainda não backfillado), gera uma DEK, embrulha via KMS
    e **seta `client.dek_wrapped` in-place** (o caller persiste). Retorna cipher
    apto a cifrar na versão corrente."""
    kms = kms or get_kms_client(settings)
    if client.dek_wrapped is None:
        dek = generate_dek()
        client.dek_wrapped = await kms.wrap_dek(dek)  # type: ignore[misc]
    else:
        dek = await kms.unwrap_dek(bytes(client.dek_wrapped))
    return ClientCipher(
        client_id=str(client.id),
        dek=dek,
        key_id=kms.key_id,
        legacy_hex_key=_legacy_hex_key(settings),
    )


async def new_client_dek(
    client_id: UUID,
    *,
    settings: Settings,
    kms: KmsClient | None = None,
) -> tuple[ClientCipher, bytes]:
    """Para a CRIAÇÃO de cliente: gera DEK antes do objeto existir. Retorna
    `(cipher, dek_wrapped)` — o caller passa `dek_wrapped` no `Client(...)`."""
    kms = kms or get_kms_client(settings)
    dek = generate_dek()
    dek_wrapped = await kms.wrap_dek(dek)
    cipher = ClientCipher(
        client_id=str(client_id),
        dek=dek,
        key_id=kms.key_id,
        legacy_hex_key=_legacy_hex_key(settings),
    )
    return cipher, dek_wrapped
