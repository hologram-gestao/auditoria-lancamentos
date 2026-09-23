"""Testes dos helpers de `crypto_service` (Sprint 3, BACK 03.3).

Cobre: geração/wrap/unwrap da DEK-por-cliente e a construção do `ClientCipher`
nos três modos (novo cliente, provisão de legado, leitura). Usa o KMS local
(derivado de `OMIE_ENCRYPTION_KEY`) — mesmo caminho de dev/test.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core import crypto_service
from app.core.config import Settings
from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    field_locator,
    load_client_cipher,
    new_client_dek,
    provision_client_cipher,
)
from app.core.kms import get_kms_client

#: Inventário CONGELADO dos pares `(tabela, coluna)` que compõem o AAD — a
#: lista canônica do CLAUDE.md §4.1, transcrita. Renomear tabela ou coluna de um
#: campo cifrado invalida a decifragem de TUDO que já foi gravado com ele, então
#: a lista não se corrige "sem querer": campo novo entra aqui, no
#: `crypto_service` e na §4.1 na MESMA entrega.
EXPECTED_AAD_PAIRS = {
    "AAD_CLIENT_APP_KEY": ("clients", "omie_app_key_encrypted"),
    "AAD_CLIENT_APP_SECRET": ("clients", "omie_app_secret_encrypted"),
    "AAD_FILE_NAME": ("reconciliation_files", "filename_encrypted"),
    "AAD_FILE_ENTRY_DESCRIPTION": ("reconciliation_file_entries", "description_encrypted"),
    "AAD_FILE_ENTRY_USER_NOTE": ("reconciliation_file_entries", "user_note_encrypted"),
    "AAD_OMIE_ENTRY_USER_NOTE": ("reconciliation_omie_entries", "user_note_encrypted"),
    "AAD_ANOMALY_CONTEXT": ("reconciliation_anomalies", "context_encrypted"),
    "AAD_ANOMALY_RESOLUTION_NOTE": ("reconciliation_anomalies", "resolution_note_encrypted"),
    "AAD_GLOSSARY_CODE": ("client_glossary_entries", "code_encrypted"),
    "AAD_GLOSSARY_NAME": ("client_glossary_entries", "name_encrypted"),
    "AAD_GLOSSARY_DESCRIPTION": ("client_glossary_entries", "description_encrypted"),
    # Sprint 9 (BACK 09.1) — a credencial da origem. 11 → 12.
    "AAD_CONNECTION_CREDENTIALS": ("client_connections", "credentials_encrypted"),
}


class TestInventarioDeCamposCifrados:
    """Campo cifrado novo não entra em silêncio, e par existente não é renomeado."""

    def test_as_constantes_declaradas_sao_exatamente_as_esperadas(self) -> None:
        declared = {
            name: value for name, value in vars(crypto_service).items() if name.startswith("AAD_")
        }
        assert declared == EXPECTED_AAD_PAIRS

    def test_a_contagem_bate_com_a_lista_canonica_do_claude_md(self) -> None:
        assert len(EXPECTED_AAD_PAIRS) == 12

    def test_nenhum_par_tabela_coluna_se_repete(self) -> None:
        """Dois campos com o MESMO AAD tornam o ciphertext de um legível no outro."""
        pares = list(EXPECTED_AAD_PAIRS.values())
        assert len(set(pares)) == len(pares)


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
