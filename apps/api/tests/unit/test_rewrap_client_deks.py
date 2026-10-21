"""Reembrulho das DEKs para a KEK do projeto novo (86e3r2egr), sem banco.

Duas KEKs simuladas por dois `LocalKmsClient` com chaves mestras diferentes e o MESMO
`key_id` (é o que acontece no Cloud KMS: duas chaves, um `k1`). O banco é um store em
memória que implementa o `DekStore` do script; a prova contra Postgres está em
`tests/integration/test_rewrap_client_deks.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
import structlog
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from scripts import rewrap_client_deks as rewrap
from scripts.rewrap_client_deks import (
    EXIT_REFUSED,
    VERIFIABLE_PAIRS,
    RewrapAbortedError,
    RewrapConfigError,
    RewrapKeys,
    RewrapMode,
    RewrapStats,
    resolve_rewrap_keys,
    run_rewrap,
)
from sqlalchemy.dialects import postgresql

from app.core import crypto_service
from app.core.config import get_settings
from app.core.crypto import ClientCipher
from app.core.crypto_service import (
    AAD_FILE_ENTRY_DESCRIPTION,
    field_locator,
    load_client_cipher,
)
from app.core.kms import CloudKmsClient, LocalKmsClient, build_cloud_kms_client, generate_dek

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.kms import KmsClient

_KEY_ID = "k1"
_HEX_A = "a1" * 32  # KEK antiga (origem)
_HEX_B = "b2" * 32  # KEK nova (alvo)
_HEX_C = "c3" * 32  # uma terceira chave, que nenhuma das duas abre
_KMS_OLD = "projects/old/locations/southamerica-east1/keyRings/r/cryptoKeys/kek"
_KMS_NEW = "projects/new/locations/southamerica-east1/keyRings/r/cryptoKeys/kek"


def _local(hex_key: str) -> LocalKmsClient:
    return LocalKmsClient(master_hex_key=hex_key, key_id=_KEY_ID)


def _keys(source: KmsClient | None = None, target: KmsClient | None = None) -> RewrapKeys:
    return RewrapKeys(
        source=source or _local(_HEX_A),
        target=target or _local(_HEX_B),
        source_name="local (origem)",
        target_name="local (alvo)",
    )


@dataclass
class _Field:
    pk: UUID
    envelope: str
    iv: str
    plaintext: str


@dataclass
class _MemoryStore:
    """`DekStore` em memória: `blobs` é a coluna `clients.dek_wrapped`."""

    blobs: dict[UUID, bytes]
    fields: dict[UUID, _Field] = field(default_factory=dict)
    swaps: int = 0
    events: list[RewrapStats] = field(default_factory=list)
    force_rowcount: int | None = None
    fail_emit: bool = False

    async def load_wrapped(self) -> list[tuple[UUID, bytes]]:
        return sorted(self.blobs.items())

    async def swap_blob(self, client_id: UUID, *, old_blob: bytes, new_blob: bytes) -> int:
        self.swaps += 1
        if self.force_rowcount is not None:
            return self.force_rowcount
        if self.blobs.get(client_id) != old_blob:
            return 0
        self.blobs[client_id] = new_blob
        return 1

    async def first_envelope_field(
        self, client_id: UUID
    ) -> tuple[tuple[str, str], UUID, str, str] | None:
        found = self.fields.get(client_id)
        if found is None:
            return None
        return AAD_FILE_ENTRY_DESCRIPTION, found.pk, found.envelope, found.iv

    async def emit_rewrapped(self, stats: RewrapStats) -> None:
        if self.fail_emit:
            raise ConnectionError("banco fora do ar")
        self.events.append(stats)


@dataclass
class _Seed:
    store: _MemoryStore
    deks: dict[UUID, bytes]


async def _seed(n: int, *, kek: KmsClient | None = None, with_fields: bool = True) -> _Seed:
    """N clientes com DEK embrulhada pela KEK antiga e um campo cifrado cada."""
    kek = kek or _local(_HEX_A)
    settings = get_settings()
    store = _MemoryStore(blobs={})
    deks: dict[UUID, bytes] = {}
    for i in range(n):
        client_id = uuid4()
        dek = generate_dek()
        deks[client_id] = dek
        store.blobs[client_id] = await kek.wrap_dek(dek)
        if with_fields:
            cipher = ClientCipher(
                client_id=str(client_id),
                dek=dek,
                key_id=_KEY_ID,
                legacy_hex_key=settings.OMIE_ENCRYPTION_KEY.get_secret_value(),
            )
            pk = uuid4()
            text = f"descrição secreta {i}"
            envelope, iv = cipher.encrypt(text, field_locator(AAD_FILE_ENTRY_DESCRIPTION, pk))
            store.fields[client_id] = _Field(pk=pk, envelope=envelope, iv=iv, plaintext=text)
    return _Seed(store=store, deks=deks)


async def _run(
    store: _MemoryStore, mode: RewrapMode, keys: RewrapKeys | None = None
) -> RewrapStats:
    return await run_rewrap(store=store, settings=get_settings(), keys=keys or _keys(), mode=mode)


@dataclass
class _Row:
    id: UUID
    dek_wrapped: bytes | None


class TestReembrulho:
    async def test_reembrulha_n_clientes_e_a_segunda_rodada_nao_tem_pendentes(self) -> None:
        seed = await _seed(3)
        before = dict(seed.store.blobs)

        first = await _run(seed.store, RewrapMode.EXECUTE)

        assert (first.rewrapped, first.already, first.pending, first.errors) == (3, 0, 0, 0)
        assert first.ok
        target = _local(_HEX_B)
        for client_id, dek in seed.deks.items():
            assert seed.store.blobs[client_id] != before[client_id]
            assert await target.unwrap_dek(seed.store.blobs[client_id]) == dek

        second = await _run(seed.store, RewrapMode.EXECUTE)
        assert (second.already, second.pending, second.rewrapped, second.errors) == (3, 0, 0, 0)
        # Uma linha de `usage_events` por execução, com as contagens da execução.
        assert [(e.already, e.rewrapped, e.errors) for e in seed.store.events] == [
            (0, 3, 0),
            (3, 0, 0),
        ]

    async def test_dek_reembrulhada_decifra_o_mesmo_campo_de_antes(self) -> None:
        seed = await _seed(2)
        await _run(seed.store, RewrapMode.EXECUTE)

        settings = get_settings()
        for client_id, blob in seed.store.blobs.items():
            cipher = await load_client_cipher(
                _Row(client_id, blob), settings=settings, kms=_local(_HEX_B)
            )
            f = seed.store.fields[client_id]
            loc = field_locator(AAD_FILE_ENTRY_DESCRIPTION, f.pk)
            assert cipher.decrypt(f.envelope, f.iv, loc) == f.plaintext

    async def test_dry_run_nao_grava_e_conta_pendentes(self) -> None:
        seed = await _seed(3)
        before = dict(seed.store.blobs)

        stats = await _run(seed.store, RewrapMode.DRY_RUN)

        assert (stats.pending, stats.rewrapped, stats.already) == (3, 0, 0)
        assert seed.store.blobs == before  # byte a byte
        assert seed.store.swaps == 0
        assert seed.store.events == []

    async def test_rowcount_diferente_de_um_aborta(self) -> None:
        seed = await _seed(2)
        seed.store.force_rowcount = 0

        with pytest.raises(RewrapAbortedError) as exc_info:
            await _run(seed.store, RewrapMode.EXECUTE)

        assert exc_info.value.reason == "linha_alterada"
        assert exc_info.value.client_id == min(seed.deks)
        assert seed.store.swaps == 1  # parou no primeiro cliente
        # A execução interrompida também deixa o evento, com o que chegou a fazer.
        assert [(e.rewrapped, e.errors) for e in seed.store.events] == [(0, 0)]

    async def test_evento_que_falha_nao_muda_o_resultado(self) -> None:
        seed = await _seed(2)
        seed.store.fail_emit = True

        stats = await _run(seed.store, RewrapMode.EXECUTE)

        assert (stats.rewrapped, stats.errors) == (2, 0)
        assert stats.ok

    async def test_blob_que_nenhuma_kek_abre_conta_falha_e_segue(self) -> None:
        seed = await _seed(2)
        stranger = uuid4()
        seed.store.blobs[stranger] = await _local(_HEX_C).wrap_dek(generate_dek())
        before = seed.store.blobs[stranger]

        stats = await _run(seed.store, RewrapMode.EXECUTE)

        assert (stats.rewrapped, stats.errors) == (2, 1)
        assert not stats.ok
        assert seed.store.blobs[stranger] == before

    async def test_prova_que_falha_aborta_sem_gravar(self) -> None:
        seed = await _seed(1)
        before = dict(seed.store.blobs)

        with pytest.raises(RewrapAbortedError) as exc_info:
            await _run(seed.store, RewrapMode.EXECUTE, _keys(target=_LyingTarget()))

        assert exc_info.value.reason == "prova_falhou"
        assert seed.store.blobs == before


class TestClassificacaoPeloTipo:
    async def test_permissao_negada_na_origem_aborta_a_execucao(self) -> None:
        seed = await _seed(2)
        keys = _keys(source=_RaisingKms(PermissionDenied("sem acesso")))

        with pytest.raises(RewrapAbortedError) as exc_info:
            await _run(seed.store, RewrapMode.EXECUTE, keys)

        assert exc_info.value.reason == "permissao_negada"
        assert seed.store.swaps == 0

    async def test_permissao_negada_no_alvo_aborta_ate_no_dry_run(self) -> None:
        seed = await _seed(1)
        keys = _keys(target=_RaisingKms(PermissionDenied("sem acesso")))

        with pytest.raises(RewrapAbortedError) as exc_info:
            await _run(seed.store, RewrapMode.DRY_RUN, keys)

        assert exc_info.value.reason == "permissao_negada"

    async def test_excecao_fora_da_classe_de_chave_errada_e_fatal(self) -> None:
        seed = await _seed(1)
        keys = _keys(target=_RaisingKms(ServiceUnavailable("fora do ar")))

        with pytest.raises(ServiceUnavailable):
            await _run(seed.store, RewrapMode.DRY_RUN, keys)

    def test_classes_declaradas(self) -> None:
        from google.api_core.exceptions import Forbidden, InvalidArgument

        from app.core.crypto import CryptoError

        assert set(rewrap.WRONG_KEY_EXCEPTIONS) == {CryptoError, InvalidArgument}
        assert set(rewrap.PERMISSION_EXCEPTIONS) == {PermissionDenied, Forbidden}
        assert not set(rewrap.WRONG_KEY_EXCEPTIONS) & set(rewrap.PERMISSION_EXCEPTIONS)


class TestVerify:
    async def test_depois_do_reembrulho_verifica_sem_falha(self) -> None:
        seed = await _seed(3)
        no_field = uuid4()
        seed.store.blobs[no_field] = await _local(_HEX_A).wrap_dek(generate_dek())
        await _run(seed.store, RewrapMode.EXECUTE)
        before = dict(seed.store.blobs)

        stats = await _run(seed.store, RewrapMode.VERIFY)

        assert (stats.verified, stats.verify_failed, stats.no_field) == (3, 0, 1)
        assert stats.ok
        assert seed.store.blobs == before
        assert seed.store.swaps == 4  # só os da execução
        assert len(seed.store.events) == 1  # só a execução emite

    async def test_antes_do_reembrulho_todos_falham(self) -> None:
        seed = await _seed(2)

        stats = await _run(seed.store, RewrapMode.VERIFY)

        assert (stats.verified, stats.verify_failed) == (0, 2)
        assert not stats.ok

    async def test_campo_adulterado_falha(self) -> None:
        seed = await _seed(1)
        await _run(seed.store, RewrapMode.EXECUTE)
        (client_id,) = seed.deks
        f = seed.store.fields[client_id]
        f.envelope = f.envelope[:-2] + ("00" if not f.envelope.endswith("00") else "11")

        stats = await _run(seed.store, RewrapMode.VERIFY)

        assert stats.verify_failed == 1

    @pytest.mark.parametrize("pair", VERIFIABLE_PAIRS, ids=lambda p: f"{p[0]}.{p[1]}")
    def test_a_query_de_cada_par_monta_e_filtra_pelo_cliente(self, pair: tuple[str, str]) -> None:
        client_id = uuid4()
        compiled = rewrap._envelope_field_query(pair, client_id).compile(
            dialect=postgresql.dialect()
        )
        sql = str(compiled)
        assert client_id in compiled.params.values()
        assert "LIKE" in sql
        assert f"{pair[1].removesuffix('_encrypted')}_iv" in sql

    def test_credenciais_sao_os_ultimos_pares(self) -> None:
        credentials = {
            crypto_service.AAD_CLIENT_APP_KEY,
            crypto_service.AAD_CLIENT_APP_SECRET,
            crypto_service.AAD_CONNECTION_CREDENTIALS,
        }
        assert set(VERIFIABLE_PAIRS[-len(credentials) :]) == credentials

    def test_lista_de_pares_cobre_toda_constante_aad(self) -> None:
        declared = {
            getattr(crypto_service, name) for name in dir(crypto_service) if name.startswith("AAD_")
        }
        assert set(VERIFIABLE_PAIRS) == declared
        assert len(VERIFIABLE_PAIRS) == len(declared)


class TestNadaDeMaterialDeChaveNoLog:
    async def test_log_so_tem_contagens_ids_e_nomes(self) -> None:
        seed = await _seed(2)
        stranger = uuid4()
        seed.store.blobs[stranger] = await _local(_HEX_C).wrap_dek(generate_dek())
        old_blobs = list(seed.store.blobs.values())

        with structlog.testing.capture_logs() as logs:
            await _run(seed.store, RewrapMode.EXECUTE)
            await _run(seed.store, RewrapMode.VERIFY)

        secrets = [b.hex() for b in old_blobs]
        secrets += [b.hex() for b in seed.store.blobs.values()]
        secrets += [d.hex() for d in seed.deks.values()]
        secrets += [_HEX_A, _HEX_B, _HEX_C]
        secrets += [f.plaintext for f in seed.store.fields.values()]
        assert logs
        for entry in logs:
            for value in entry.values():
                assert not isinstance(value, bytes | bytearray)
            rendered = repr(entry)
            for secret in secrets:
                assert secret not in rendered
        outcomes = {e["outcome"] for e in logs if e["event"] == "rewrap_client"}
        assert outcomes == {"rewrapped", "undecipherable", "verified", "verify_failed"}


class TestConfiguracao:
    def test_modo_local_sem_origem_recusa(self) -> None:
        with pytest.raises(RewrapConfigError, match="OMIE_ENCRYPTION_KEY_SOURCE"):
            resolve_rewrap_keys(_settings(), {})

    def test_modo_local_origem_igual_ao_alvo_recusa(self) -> None:
        with pytest.raises(RewrapConfigError, match="igual"):
            resolve_rewrap_keys(_settings(), {"OMIE_ENCRYPTION_KEY_SOURCE": _HEX_B.upper()})

    @pytest.mark.parametrize("value", ["zz" * 32, "ab" * 16, "ab" * 33])
    def test_modo_local_origem_invalida_recusa_sem_ecoar(self, value: str) -> None:
        with pytest.raises(RewrapConfigError) as exc_info:
            resolve_rewrap_keys(_settings(), {"OMIE_ENCRYPTION_KEY_SOURCE": value})
        assert value not in str(exc_info.value)

    def test_modo_local_com_origem_no_kms_recusa(self) -> None:
        env = {"KEK_KMS_KEY_NAME_SOURCE": _KMS_OLD, "OMIE_ENCRYPTION_KEY_SOURCE": _HEX_A}
        with pytest.raises(RewrapConfigError, match="misturar"):
            resolve_rewrap_keys(_settings(), env)

    def test_modo_local_monta_duas_kek_com_o_mesmo_key_id(self) -> None:
        keys = resolve_rewrap_keys(_settings(), {"OMIE_ENCRYPTION_KEY_SOURCE": _HEX_A})
        assert isinstance(keys.source, LocalKmsClient)
        assert isinstance(keys.target, LocalKmsClient)
        assert keys.source.key_id == keys.target.key_id == _KEY_ID
        assert _HEX_A not in keys.source_name + keys.target_name

    def test_kms_sem_origem_recusa(self) -> None:
        with pytest.raises(RewrapConfigError, match="KEK_KMS_KEY_NAME_SOURCE"):
            resolve_rewrap_keys(_settings(kms=_KMS_NEW), {})

    @pytest.mark.parametrize(
        "name",
        [
            f"{_KMS_OLD}/cryptoKeyVersions/1",
            "omie-encryption-kek",
            "projects/old/keyRings/r/cryptoKeys/kek",
        ],
    )
    def test_kms_origem_fora_do_formato_de_cryptokey_recusa(self, name: str) -> None:
        with pytest.raises(RewrapConfigError, match="CryptoKey"):
            resolve_rewrap_keys(_settings(kms=_KMS_NEW), {"KEK_KMS_KEY_NAME_SOURCE": name})

    def test_kms_origem_igual_ao_alvo_recusa(self) -> None:
        env = {"KEK_KMS_KEY_NAME_SOURCE": f"  {_KMS_NEW} "}
        with pytest.raises(RewrapConfigError, match="igual"):
            resolve_rewrap_keys(_settings(kms=_KMS_NEW), env)

    def test_kms_com_origem_local_recusa(self) -> None:
        env = {"KEK_KMS_KEY_NAME_SOURCE": _KMS_OLD, "OMIE_ENCRYPTION_KEY_SOURCE": _HEX_A}
        with pytest.raises(RewrapConfigError, match="misturar"):
            resolve_rewrap_keys(_settings(kms=_KMS_NEW), env)

    def test_kms_monta_dois_clientes_com_o_key_id_do_servico(self) -> None:
        keys = resolve_rewrap_keys(_settings(kms=_KMS_NEW), {"KEK_KMS_KEY_NAME_SOURCE": _KMS_OLD})
        assert isinstance(keys.source, CloudKmsClient)
        assert isinstance(keys.target, CloudKmsClient)
        assert keys.source.key_id == keys.target.key_id == _KEY_ID
        assert (keys.source_name, keys.target_name) == (_KMS_OLD, _KMS_NEW)

    def test_build_cloud_kms_client(self) -> None:
        client = build_cloud_kms_client(_KMS_OLD, _KEY_ID)
        assert isinstance(client, CloudKmsClient)
        assert client.key_id == _KEY_ID

    async def test_main_recusa_com_saida_nao_zero_sem_abrir_o_banco(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _no_db(*_: Any, **__: Any) -> None:
            raise AssertionError("o banco não pode ser aberto numa recusa")

        monkeypatch.setattr(rewrap, "init_db", _no_db)
        monkeypatch.setattr(rewrap, "setup_logging", lambda *_: None)

        code = await rewrap.main(["--dry-run"], environ={})

        assert code == EXIT_REFUSED
        assert "recusado:" in capsys.readouterr().err

    def test_dry_run_e_verify_sao_exclusivos(self) -> None:
        with pytest.raises(SystemExit):
            rewrap._parse_args(["--dry-run", "--verify"])


class _RaisingKms:
    """KMS que levanta a exceção dada em toda chamada."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    @property
    def key_id(self) -> str:
        return _KEY_ID

    async def wrap_dek(self, dek: bytes) -> bytes:
        raise self._exc

    async def unwrap_dek(self, wrapped: bytes) -> bytes:
        raise self._exc


class _LyingTarget:
    """Alvo que não abre o blob antigo e devolve OUTRA DEK para o blob novo."""

    def __init__(self) -> None:
        self._real = _local(_HEX_B)
        self._mine: set[bytes] = set()

    @property
    def key_id(self) -> str:
        return _KEY_ID

    async def wrap_dek(self, dek: bytes) -> bytes:
        blob = await self._real.wrap_dek(dek)
        self._mine.add(blob)
        return blob

    async def unwrap_dek(self, wrapped: bytes) -> bytes:
        if wrapped in self._mine:
            return generate_dek()
        return await self._real.unwrap_dek(wrapped)


class _FakeSecret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _FakeSettings:
    """Stub mínimo de Settings para `resolve_rewrap_keys`."""

    def __init__(self, *, kms: str | None = None) -> None:
        self.KEK_KMS_KEY_NAME = kms
        self.KEK_KEY_ID = _KEY_ID
        self.OMIE_ENCRYPTION_KEY = _FakeSecret(_HEX_B)


def _settings(*, kms: str | None = None) -> Settings:
    return _FakeSettings(kms=kms)  # type: ignore[return-value]
