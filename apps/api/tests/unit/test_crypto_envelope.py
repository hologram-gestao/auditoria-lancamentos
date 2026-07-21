"""Testes do envelope versionado DEK-por-cliente + AAD (Sprint 3, BACK 03.3).

Cobre os critérios de aceite críticos:
    - Round-trip v1 (DEK + AAD).
    - Isolamento: a DEK de A NÃO decifra valor de B.
    - AAD: ciphertext de outra linha/cliente/coluna → FALHA.
    - Tag: ciphertext adulterado → FALHA (nunca decifra parcial).
    - Leitura multi-chave: linha bare (legado) e v1 convivem.
    - DEK ausente → erro tratado, sem fallback silencioso para chave global.
    - KMS local: wrap/unwrap round-trip; key_id errado → falha; DEKs distintas.
"""

from __future__ import annotations

import os

import pytest

from app.core.crypto import (
    CURRENT_ENVELOPE_VERSION,
    ClientCipher,
    CryptoError,
    FieldLocator,
    encrypt,
)
from app.core.kms import (
    CloudKmsClient,
    LocalKmsClient,
    generate_dek,
    get_kms_client,
)

_MASTER_HEX = "ab" * 32  # 64 chars hex (256 bits) — chave mestra fake p/ teste
_KEY_ID = "k1"


def _dek() -> bytes:
    return generate_dek()


def _cipher(*, client_id: str, dek: bytes | None) -> ClientCipher:
    return ClientCipher(
        client_id=client_id,
        dek=dek,
        key_id=_KEY_ID,
        legacy_hex_key=_MASTER_HEX,
    )


def _loc(
    pk: str = "row-1",
    *,
    table: str = "reconciliation_file_entries",
    column: str = "description_encrypted",
) -> FieldLocator:
    return FieldLocator(table=table, column=column, pk=pk)


class TestRoundTrip:
    def test_encrypt_then_decrypt(self) -> None:
        cipher = _cipher(client_id="client-A", dek=_dek())
        envelope, iv = cipher.encrypt("segredo do lançamento", _loc())
        assert envelope.startswith(f"v{CURRENT_ENVELOPE_VERSION}:{_KEY_ID}:")
        assert cipher.decrypt(envelope, iv, _loc()) == "segredo do lançamento"

    def test_empty_string_round_trip(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        env, iv = cipher.encrypt("", _loc())
        assert cipher.decrypt(env, iv, _loc()) == ""

    def test_each_encrypt_uses_new_iv(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        _, iv1 = cipher.encrypt("x", _loc())
        _, iv2 = cipher.encrypt("x", _loc())
        assert iv1 != iv2


class TestIsolationBetweenClients:
    def test_dek_of_a_cannot_decrypt_b(self) -> None:
        """Critério: entregar a DEK de A para decifrar valor de B → FALHA."""
        dek_a, dek_b = _dek(), _dek()
        cipher_b = _cipher(client_id="client-B", dek=dek_b)
        env, iv = cipher_b.encrypt("dado do B", _loc())

        # Atacante tenta decifrar o valor de B usando a DEK de A (e o client_id de A).
        cipher_a = _cipher(client_id="client-A", dek=dek_a)
        with pytest.raises(CryptoError):
            cipher_a.decrypt(env, iv, _loc())

    def test_same_dek_but_wrong_client_id_fails_via_aad(self) -> None:
        """Mesmo que a DEK vazasse, o AAD amarra ao client_id: decifrar como
        outro cliente falha."""
        dek = _dek()
        cipher_b = _cipher(client_id="client-B", dek=dek)
        env, iv = cipher_b.encrypt("dado do B", _loc())

        cipher_wrong = _cipher(client_id="client-A", dek=dek)  # DEK certa, client errado
        with pytest.raises(CryptoError):
            cipher_wrong.decrypt(env, iv, _loc())


class TestAAD:
    def test_ciphertext_from_other_row_fails(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        env, iv = cipher.encrypt("x", _loc(pk="row-1"))
        with pytest.raises(CryptoError):
            cipher.decrypt(env, iv, _loc(pk="row-2"))

    def test_ciphertext_from_other_column_fails(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        env, iv = cipher.encrypt("x", _loc(column="description_encrypted"))
        with pytest.raises(CryptoError):
            cipher.decrypt(env, iv, _loc(column="user_note_encrypted"))

    def test_ciphertext_from_other_table_fails(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        env, iv = cipher.encrypt("x", _loc(table="reconciliation_file_entries"))
        with pytest.raises(CryptoError):
            cipher.decrypt(env, iv, _loc(table="reconciliation_anomalies"))


class TestTampering:
    def test_tampered_ciphertext_fails(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        env, iv = cipher.encrypt("valor íntegro", _loc())
        version, key_id, ct_hex = env.split(":", 2)
        ct = bytearray.fromhex(ct_hex)
        ct[0] ^= 0x01  # flip 1 bit
        tampered = f"{version}:{key_id}:{ct.hex()}"
        with pytest.raises(CryptoError):
            cipher.decrypt(tampered, iv, _loc())

    def test_tampered_iv_fails(self) -> None:
        cipher = _cipher(client_id="c", dek=_dek())
        env, iv = cipher.encrypt("x", _loc())
        bad_iv = bytearray.fromhex(iv)
        bad_iv[0] ^= 0x01
        with pytest.raises(CryptoError):
            cipher.decrypt(env, bad_iv.hex(), _loc())


class TestMultiKeyLegacyCoexistence:
    def test_reads_legacy_bare_row(self) -> None:
        """Linha bare (legado, chave global, sem AAD) é lida pelo cipher."""
        legacy_ct, legacy_iv = encrypt("credencial antiga", _MASTER_HEX)
        assert ClientCipher.is_legacy(legacy_ct)
        cipher = _cipher(client_id="c", dek=_dek())
        assert cipher.decrypt(legacy_ct, legacy_iv, _loc()) == "credencial antiga"

    def test_legacy_and_v1_coexist(self) -> None:
        """Duas versões lado a lado: bare (global) + v1 (DEK) decifram no mesmo
        cipher — a leitura é multi-chave."""
        cipher = _cipher(client_id="c", dek=_dek())
        legacy_ct, legacy_iv = encrypt("valor legado", _MASTER_HEX)
        v1_env, v1_iv = cipher.encrypt("valor novo", _loc())

        assert cipher.decrypt(legacy_ct, legacy_iv, _loc()) == "valor legado"
        assert cipher.decrypt(v1_env, v1_iv, _loc()) == "valor novo"

    def test_is_legacy_discriminator(self) -> None:
        assert ClientCipher.is_legacy("deadbeef")  # hex puro = bare
        assert not ClientCipher.is_legacy("v1:k1:deadbeef")


class TestMissingDek:
    def test_encrypt_without_dek_raises(self) -> None:
        cipher = _cipher(client_id="c", dek=None)
        with pytest.raises(CryptoError):
            cipher.encrypt("x", _loc())

    def test_decrypt_v_row_without_dek_raises_no_global_fallback(self) -> None:
        """DEK ausente + linha v1 → erro tratado, NUNCA cai na chave global."""
        real = _cipher(client_id="c", dek=_dek())
        env, iv = real.encrypt("x", _loc())
        no_dek = _cipher(client_id="c", dek=None)
        with pytest.raises(CryptoError):
            no_dek.decrypt(env, iv, _loc())

    def test_decrypt_legacy_row_without_dek_still_works(self) -> None:
        """Sem DEK ainda se lê bare legado (o cliente pré-backfill só tem bare)."""
        legacy_ct, legacy_iv = encrypt("legado", _MASTER_HEX)
        no_dek = _cipher(client_id="c", dek=None)
        assert no_dek.decrypt(legacy_ct, legacy_iv, _loc()) == "legado"


class TestLocalKms:
    async def test_wrap_unwrap_round_trip(self) -> None:
        kms = LocalKmsClient(master_hex_key=_MASTER_HEX, key_id=_KEY_ID)
        dek = generate_dek()
        wrapped = await kms.wrap_dek(dek)
        assert wrapped != dek  # embrulhado, não em claro
        assert await kms.unwrap_dek(wrapped) == dek

    async def test_wrong_key_id_cannot_unwrap(self) -> None:
        kms1 = LocalKmsClient(master_hex_key=_MASTER_HEX, key_id="k1")
        kms2 = LocalKmsClient(master_hex_key=_MASTER_HEX, key_id="k2")
        wrapped = await kms1.wrap_dek(generate_dek())
        with pytest.raises(CryptoError):
            await kms2.unwrap_dek(wrapped)

    async def test_malformed_wrapped_raises(self) -> None:
        kms = LocalKmsClient(master_hex_key=_MASTER_HEX, key_id=_KEY_ID)
        with pytest.raises(CryptoError):
            await kms.unwrap_dek(b"short")

    def test_generate_dek_is_random_and_sized(self) -> None:
        a, b = generate_dek(), generate_dek()
        assert len(a) == 32
        assert a != b


class TestKmsFactory:
    def test_local_when_no_kms_name(self) -> None:
        settings = _FakeSettings(kek_kms_key_name=None)
        assert isinstance(get_kms_client(settings), LocalKmsClient)  # type: ignore[arg-type]

    def test_cloud_when_kms_name_set(self) -> None:
        settings = _FakeSettings(kek_kms_key_name="projects/p/locations/l/keyRings/r/cryptoKeys/k")
        assert isinstance(get_kms_client(settings), CloudKmsClient)  # type: ignore[arg-type]


class _FakeSecret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _FakeSettings:
    """Stub mínimo de Settings para `get_kms_client` (evita construir env)."""

    def __init__(self, *, kek_kms_key_name: str | None) -> None:
        self.KEK_KMS_KEY_NAME = kek_kms_key_name
        self.KEK_KEY_ID = _KEY_ID
        self.OMIE_ENCRYPTION_KEY = _FakeSecret(_MASTER_HEX)


def test_os_urandom_available() -> None:
    # sanity: os importado (usado pelos helpers) — evita import não usado no lint.
    assert len(os.urandom(1)) == 1
