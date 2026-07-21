"""Testes dos helpers de `crypto_service` (Sprint 3, BACK 03.3).

Cobre: geração/wrap/unwrap da DEK-por-cliente e a construção do `ClientCipher`
nos três modos (novo cliente, provisão de legado, leitura). Usa o KMS local
(derivado de `OMIE_ENCRYPTION_KEY`) — mesmo caminho de dev/test.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    field_locator,
    load_client_cipher,
    new_client_dek,
    provision_client_cipher,
)
from app.core.kms import get_kms_client


class _FakeClient:
    """Stub mínimo de `Client` (só o que os helpers acessam)."""

    def __init__(self, *, dek_wrapped: bytes | None = None) -> None:
        self.id = uuid4()
        self.dek_wrapped = dek_wrapped


@pytest.fixture
def settings() -> Settings:
    return Settings()  # env de teste (CI-safe) já exportado


class TestNewClientDek:
    async def test_returns_cipher_and_wrapped_dek(self, settings: Settings) -> None:
        client_id = uuid4()
        cipher, dek_wrapped = await new_client_dek(client_id, settings=settings)
        assert isinstance(dek_wrapped, bytes)
        # round-trip com o cipher recém-criado
        loc = field_locator(AAD_CLIENT_APP_KEY, client_id)
        env, iv = cipher.encrypt("app-key-secreta", loc)
        assert env.startswith("v1:")
        assert cipher.decrypt(env, iv, loc) == "app-key-secreta"

    async def test_wrapped_dek_unwraps_to_same_key(self, settings: Settings) -> None:
        client_id = uuid4()
        _, dek_wrapped = await new_client_dek(client_id, settings=settings)
        kms = get_kms_client(settings)
        dek = await kms.unwrap_dek(dek_wrapped)
        assert len(dek) == 32


class TestProvisionClientCipher:
    async def test_generates_dek_for_legacy_client(self, settings: Settings) -> None:
        client = _FakeClient(dek_wrapped=None)
        cipher = await provision_client_cipher(client, settings=settings)
        assert client.dek_wrapped is not None  # setado in-place
        loc = field_locator(AAD_CLIENT_APP_KEY, client.id)
        env, iv = cipher.encrypt("x", loc)
        assert cipher.decrypt(env, iv, loc) == "x"

    async def test_reuses_existing_dek(self, settings: Settings) -> None:
        client = _FakeClient(dek_wrapped=None)
        await provision_client_cipher(client, settings=settings)
        wrapped_before = client.dek_wrapped
        # 2ª provisão não gera outra DEK (idempotente sobre a mesma linha)
        cipher2 = await provision_client_cipher(client, settings=settings)
        assert client.dek_wrapped == wrapped_before
        loc = field_locator(AAD_CLIENT_APP_KEY, client.id)
        env, iv = cipher2.encrypt("y", loc)
        assert cipher2.decrypt(env, iv, loc) == "y"


class TestLoadClientCipher:
    async def test_no_dek_reads_only_legacy(self, settings: Settings) -> None:
        client = _FakeClient(dek_wrapped=None)
        cipher = await load_client_cipher(client, settings=settings)
        loc = field_locator(AAD_CLIENT_APP_KEY, client.id)
        # Sem DEK, cifrar (v1) deve falhar — não há fallback silencioso.
        from app.core.crypto import CryptoError

        with pytest.raises(CryptoError):
            cipher.encrypt("x", loc)

    async def test_reads_what_provision_wrote(self, settings: Settings) -> None:
        client = _FakeClient(dek_wrapped=None)
        wcipher = await provision_client_cipher(client, settings=settings)
        loc = field_locator(AAD_CLIENT_APP_KEY, client.id)
        env, iv = wcipher.encrypt("segredo", loc)

        # Simula uma nova request: carrega o cipher do dek_wrapped persistido.
        rcipher = await load_client_cipher(client, settings=settings)
        assert rcipher.decrypt(env, iv, loc) == "segredo"
