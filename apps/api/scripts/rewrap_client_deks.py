"""Reembrulho das DEKs dos clientes para a KEK do projeto novo (épico 86e3r2egb, 86e3r2egr).

O caso:
    Toda DEK de cliente está embrulhada pela KEK do projeto `liberdade-assessoria`, e chave
    do Cloud KMS não se move entre projetos. Subir o banco no projeto `hologramos-512320`
    com a KEK de lá deixaria TODO cliente `[indecifrável]`. Este script troca, cliente a
    cliente, o blob `clients.dek_wrapped` da KEK antiga (ORIGEM) para a nova (ALVO).
    Executado no ensaio (22/10/2026) e no corte (23/10/2026): 14 clientes reembrulhados,
    `--verify` com 14 ok e 0 falhas nas duas vezes (runbook, "Migração de projeto: como
    foi executada").

O que ele NÃO toca (CLAUDE.md §3.14):
    - **Nenhum campo cifrado.** O envelope dos campos (`v1:<key_id>:<hex>`) é cifrado com a
      DEK, e a DEK continua a mesma: só o embrulho dela muda.
    - **O `KEK_KEY_ID` (`k1`).** Os dois clientes de KMS usam o mesmo `key_id`; o Cloud KMS
      não o usa no wrap.
    - **O modo do wrapper.** Alvo e origem são os dois Cloud KMS, ou os dois locais (só
      testes). Misturar é recusado: seria a landmine 3 (KMS ⇄ local sobre o mesmo banco).

As chaves:
    - ALVO = `KEK_KMS_KEY_NAME` do `Settings`, a MESMA do serviço no projeto novo.
    - ORIGEM = variável de ambiente `KEK_KMS_KEY_NAME_SOURCE`, lida só aqui e nunca pelo
      `Settings`. Obrigatória; igual à alvo é recusada. Nada é gravado numa recusa.
    - Modo local (sem `KEK_KMS_KEY_NAME`, testes e ensaio na máquina): a origem é
      `OMIE_ENCRYPTION_KEY_SOURCE` (64 hex), e as duas KEKs locais têm o mesmo `key_id`.

Por cliente com `dek_wrapped` não nulo, em ordem de id:
    1. o ALVO abre o blob → já reembrulhado (`already`), nada a fazer;
    2. o ALVO diz "este ciphertext não é desta chave" → a ORIGEM abre; o ALVO embrulha a
       DEK; o ALVO abre o blob novo e ele tem de devolver a MESMA DEK (prova em memória:
       se falhar, aborta);
    3. `UPDATE clients SET dek_wrapped = :novo WHERE id = :id AND dek_wrapped = :antigo`,
       em Core, exigindo 1 linha (0 = alguém mexeu: aborta) e commit por cliente;
    4. nenhuma das duas KEKs abre → `errors` (conta e segue; a saída é não-zero).
    A falha é classificada pelo TIPO da exceção (`WRONG_KEY_EXCEPTIONS`,
    `PERMISSION_EXCEPTIONS`), nunca pelo texto. Permissão negada aborta a execução
    inteira; qualquer outra exceção também. Rodar de novo é idempotente: o que já foi
    commitado conta como `already`.

Modos:
    --dry-run   faz tudo do passo 1 ao 2, inclusive a prova com a KEK nova, e NÃO grava
                (`pending` = quantos seriam reembrulhados). Prova as permissões nas duas
                chaves antes de qualquer escrita.
    (padrão)    execução: grava e emite `deks_reembrulhadas` em `usage_events`.
    --verify    para cada cliente, abre a DEK com o ALVO pelo MESMO `load_client_cipher` do
                serviço e decifra UM campo cifrado real dele (o primeiro par `AAD_*` que
                tiver linha no envelope), sem imprimir o texto. Zero `verify_failed` é a
                condição do corte.

A DEK em claro e os blobs nunca são logados nem persistidos fora de `dek_wrapped`: log e
saída levam só contagens, `client_id` e os dois NOMES de chave (não são segredo).

Uso local (testes e ensaio):
    cd apps/api
    OMIE_ENCRYPTION_KEY_SOURCE=<64 hex> uv run python -m scripts.rewrap_client_deks --dry-run

Execução real (operação, roteiro em scripts/environments-runbook.md, passo 7):
    Cloud Run Job no projeto NOVO, clonado do job `migrate` de lá (como o setup-gcp.sh faz
    com o da carteira), `--command=python --args=-m,scripts.rewrap_client_deks[,--dry-run]`,
    env `KEK_KMS_KEY_NAME_SOURCE=<nome completo da KEK antiga>`; a SA do job com
    `roles/cloudkms.cryptoKeyDecrypter` SÓ na chave antiga (binding no recurso da chave, no
    projeto antigo). Sempre `--dry-run` antes. ⚠️ Roda SOMENTE sobre a cópia importada na
    instância nova, NUNCA sobre o banco antigo: é o que mantém o rollback trivial (o banco
    antigo continua decifrável pela KEK antiga até o fim).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

# Garante que ``apps/api/`` está no sys.path (idem rotate_encryption_key.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from google.api_core.exceptions import Forbidden, InvalidArgument, PermissionDenied  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.engine import CursorResult  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.crypto import CryptoError  # noqa: E402
from app.core.crypto_service import (  # noqa: E402
    AAD_ACCOUNTING_ACCOUNT_NAME,
    AAD_ACCOUNTING_ACCOUNT_PARTY_DOCUMENT,
    AAD_ANOMALY_CONTEXT,
    AAD_ANOMALY_RESOLUTION_NOTE,
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    AAD_CONNECTION_CREDENTIALS,
    AAD_DECISION_HISTORY,
    AAD_FILE_CATEGORY_LABEL,
    AAD_FILE_ENTRY_DESCRIPTION,
    AAD_FILE_ENTRY_USER_NOTE,
    AAD_FILE_NAME,
    AAD_GLOSSARY_CODE,
    AAD_GLOSSARY_DESCRIPTION,
    AAD_GLOSSARY_NAME,
    AAD_MOVEMENT_DESCRIPTION,
    AAD_OMIE_ENTRY_USER_NOTE,
    AAD_TITLE_CONTEXT_TEXT,
    field_locator,
    load_client_cipher,
)
from app.core.kms import LocalKmsClient, build_cloud_kms_client  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import Client  # noqa: E402
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402
from app.modules.usage_events.repository import UsageEventRepository  # noqa: E402
from app.modules.usage_events.service import UsageEventService  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Mapping

    from sqlalchemy import Select, Table
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import Settings
    from app.core.kms import KmsClient

log = get_logger(__name__)

#: Variável com o nome completo da KEK ANTIGA no Cloud KMS. Lida só por este script.
SOURCE_KEK_ENV = "KEK_KMS_KEY_NAME_SOURCE"
#: Equivalente no modo local (testes): a chave mestra de onde a KEK antiga é derivada.
SOURCE_LOCAL_KEY_ENV = "OMIE_ENCRYPTION_KEY_SOURCE"

#: "Este ciphertext não é desta chave": o blob fica PENDENTE de reembrulho, não é erro.
#: - `CryptoError`: o `LocalKmsClient` (tag GCM inválida sob outra KEK).
#: - `InvalidArgument`: o Cloud KMS (400 "Decryption failed: the ciphertext is invalid").
#:   CONFIRMADO contra o KMS real no `--dry-run` do ensaio (22/10/2026: 14 clientes
#:   classificados `pending`, nenhum aborto) e de novo no do corte (23/10/2026). O
#:   `CloudKmsClient.unwrap_dek` não embrulha a exceção do SDK, então é ela que chega aqui.
#:   Se um dia a classe mudar, o dry-run ABORTA (exceção fora desta tupla é fatal) e esta
#:   tupla é corrigida, nunca o texto da mensagem comparado.
WRONG_KEY_EXCEPTIONS: tuple[type[Exception], ...] = (CryptoError, InvalidArgument)
#: A SA não pode usar a chave: aborta a execução inteira (é configuração, não dado).
#: `PermissionDenied` é subclasse de `Forbidden`; as duas listadas para ser explícito.
PERMISSION_EXCEPTIONS: tuple[type[Exception], ...] = (PermissionDenied, Forbidden)

#: Todos os pares cifrados, na ordem em que o `--verify` procura um campo para provar.
#: Fonte: as constantes `AAD_*` de `crypto_service.py`; o teste unitário falha se uma
#: constante nova não entrar aqui. Os pares de CREDENCIAL ficam por último de propósito:
#: o `--verify` só decifra uma credencial (e a descarta) quando o cliente não tem nenhum
#: outro campo cifrado para provar a DEK.
VERIFIABLE_PAIRS: tuple[tuple[str, str], ...] = (
    AAD_FILE_ENTRY_DESCRIPTION,
    AAD_FILE_NAME,
    AAD_FILE_ENTRY_USER_NOTE,
    AAD_OMIE_ENTRY_USER_NOTE,
    AAD_ANOMALY_CONTEXT,
    AAD_ANOMALY_RESOLUTION_NOTE,
    AAD_GLOSSARY_NAME,
    AAD_GLOSSARY_CODE,
    AAD_GLOSSARY_DESCRIPTION,
    AAD_TITLE_CONTEXT_TEXT,
    AAD_FILE_CATEGORY_LABEL,
    AAD_MOVEMENT_DESCRIPTION,
    AAD_ACCOUNTING_ACCOUNT_NAME,
    AAD_ACCOUNTING_ACCOUNT_PARTY_DOCUMENT,
    AAD_DECISION_HISTORY,
    AAD_CONNECTION_CREDENTIALS,
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
)

#: Forma do nome de uma CryptoKey (não de uma versão dela). Um nome fora dela faria o
#: Cloud KMS responder 400 (`InvalidArgument`) a todo cliente, e o script contaria isso
#: como "chave errada" em vez de recusar a configuração.
_CRYPTO_KEY_NAME = re.compile(r"^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+$")

_MASTER_KEY_BYTES = 32

EXIT_OK = 0
EXIT_WITH_FAILURES = 1
EXIT_REFUSED = 2
EXIT_ABORTED = 3


class RewrapMode(StrEnum):
    DRY_RUN = "dry_run"
    EXECUTE = "execute"
    VERIFY = "verify"


class RewrapConfigError(Exception):
    """Configuração recusada ANTES de abrir o banco: nada é lido nem gravado."""


class RewrapAbortedError(Exception):
    """Execução interrompida. O que já foi commitado fica (a próxima rodada conta como
    `already`). `reason` é vocabulário fechado; a mensagem nunca carrega material de chave."""

    def __init__(self, reason: str, message: str, *, client_id: UUID | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.client_id = client_id


@dataclass(frozen=True)
class RewrapKeys:
    """Os dois clientes de KMS e os NOMES das chaves (para log; não são segredo)."""

    source: KmsClient
    target: KmsClient
    source_name: str
    target_name: str


@dataclass
class RewrapStats:
    """Resultado de uma rodada: só contadores, e os ids dos clientes com falha."""

    mode: RewrapMode
    clients: int = 0
    already: int = 0
    pending: int = 0
    rewrapped: int = 0
    errors: int = 0
    verified: int = 0
    verify_failed: int = 0
    no_field: int = 0
    duration_s: int = 0

    @property
    def ok(self) -> bool:
        return self.errors == 0 and self.verify_failed == 0


@dataclass(frozen=True)
class _DekRow:
    """O mínimo que `load_client_cipher` precisa (o `_HasDek` dele)."""

    id: UUID
    dek_wrapped: bytes | None


def resolve_rewrap_keys(settings: Settings, environ: Mapping[str, str]) -> RewrapKeys:
    """Monta origem e alvo, ou recusa com `RewrapConfigError` (mensagem sem segredo)."""
    key_id = settings.KEK_KEY_ID
    source_kms_name = (environ.get(SOURCE_KEK_ENV) or "").strip()
    source_local_hex = (environ.get(SOURCE_LOCAL_KEY_ENV) or "").strip()
    target_kms_name = (settings.KEK_KMS_KEY_NAME or "").strip()

    if target_kms_name:
        if source_local_hex:
            raise RewrapConfigError(
                f"{SOURCE_LOCAL_KEY_ENV} não vale com Cloud KMS: misturar o wrapper local "
                "e o KMS sobre o mesmo banco é proibido (CLAUDE.md §3.14)."
            )
        if not source_kms_name:
            raise RewrapConfigError(
                f"{SOURCE_KEK_ENV} é obrigatória: o nome completo da KEK antiga no Cloud KMS."
            )
        if not _CRYPTO_KEY_NAME.fullmatch(source_kms_name):
            raise RewrapConfigError(
                f"{SOURCE_KEK_ENV} tem de ser o nome completo de uma CryptoKey "
                "(projects/…/locations/…/keyRings/…/cryptoKeys/…), sem /cryptoKeyVersions."
            )
        if source_kms_name == target_kms_name:
            raise RewrapConfigError(
                f"{SOURCE_KEK_ENV} é igual a KEK_KMS_KEY_NAME: origem e alvo têm de ser "
                "chaves diferentes."
            )
        return RewrapKeys(
            source=build_cloud_kms_client(source_kms_name, key_id),
            target=build_cloud_kms_client(target_kms_name, key_id),
            source_name=source_kms_name,
            target_name=target_kms_name,
        )

    if source_kms_name:
        raise RewrapConfigError(
            f"{SOURCE_KEK_ENV} exige KEK_KMS_KEY_NAME: sem ele o alvo é o wrapper local, e "
            "misturar o wrapper local e o KMS sobre o mesmo banco é proibido (CLAUDE.md §3.14)."
        )
    if not source_local_hex:
        raise RewrapConfigError(
            f"{SOURCE_LOCAL_KEY_ENV} é obrigatória no modo local (sem KEK_KMS_KEY_NAME)."
        )
    try:
        source_bytes = bytes.fromhex(source_local_hex)
    except ValueError:
        source_bytes = b""
    if len(source_bytes) != _MASTER_KEY_BYTES:
        raise RewrapConfigError(f"{SOURCE_LOCAL_KEY_ENV} tem de ser 64 caracteres hex.")
    target_hex = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
    if source_bytes == bytes.fromhex(target_hex):
        raise RewrapConfigError(
            f"{SOURCE_LOCAL_KEY_ENV} é igual a OMIE_ENCRYPTION_KEY: origem e alvo têm de ser "
            "chaves diferentes."
        )
    return RewrapKeys(
        source=LocalKmsClient(master_hex_key=source_local_hex, key_id=key_id),
        target=LocalKmsClient(master_hex_key=target_hex, key_id=key_id),
        source_name=f"local ({SOURCE_LOCAL_KEY_ENV})",
        target_name="local (OMIE_ENCRYPTION_KEY)",
    )


async def _guarded[T](call: Awaitable[T], *, key_name: str) -> T:
    """Chamada ao KMS em que permissão negada vira aborto da execução inteira."""
    try:
        return await call
    except PERMISSION_EXCEPTIONS:
        raise RewrapAbortedError(
            "permissao_negada", f"Permissão negada no KMS na chave {key_name}."
        ) from None


async def _try_unwrap(kms: KmsClient, blob: bytes, *, key_name: str) -> bytes | None:
    """A DEK se esta KEK abre o blob; `None` se o blob é de OUTRA chave.

    Permissão negada aborta; qualquer exceção fora de `WRONG_KEY_EXCEPTIONS` sobe.
    """
    try:
        return await _guarded(kms.unwrap_dek(blob), key_name=key_name)
    except WRONG_KEY_EXCEPTIONS:
        return None


def _envelope_field_query(pair: tuple[str, str], client_id: UUID) -> Select[tuple[object, ...]]:
    """A primeira linha do cliente com o campo `pair` no envelope (`v<n>:`), em ordem de pk.

    O escopo por cliente vem da própria tabela: `clients.id`, uma coluna `client_id`, ou
    `session_id` → `reconciliation_sessions.client_id`.
    """
    table_name, column = pair
    table = Base.metadata.tables[table_name]
    ct_col = table.c[column]
    iv_col = table.c[column.removesuffix("_encrypted") + "_iv"]
    stmt = select(table.c.id, ct_col, iv_col).where(
        ct_col.is_not(None), iv_col.is_not(None), ct_col.like("v%")
    )
    if table_name == "clients":
        stmt = stmt.where(table.c.id == client_id)
    elif "client_id" in table.c:
        stmt = stmt.where(table.c.client_id == client_id)
    else:
        sessions = Base.metadata.tables["reconciliation_sessions"]
        stmt = stmt.join(sessions, sessions.c.id == table.c.session_id).where(
            sessions.c.client_id == client_id
        )
    return stmt.order_by(table.c.id).limit(1)


#: Um campo cifrado achado para o `--verify`: o par, a pk da linha, o envelope e o IV.
type EnvelopeField = tuple[tuple[str, str], UUID, str, str]


class DekStore(Protocol):
    """O que o reembrulho precisa do banco. `PostgresDekStore` em produção; os testes
    unitários usam um store em memória."""

    async def load_wrapped(self) -> list[tuple[UUID, bytes]]:
        """`(client_id, dek_wrapped)` de todo cliente com DEK, em ordem de id."""
        ...

    async def swap_blob(self, client_id: UUID, *, old_blob: bytes, new_blob: bytes) -> int:
        """Troca o blob SE ele ainda é `old_blob` e comita. Devolve as linhas atingidas."""
        ...

    async def first_envelope_field(self, client_id: UUID) -> EnvelopeField | None:
        """Um campo do cliente no envelope `v<n>:`, ou `None` se ele não tem nenhum."""
        ...

    async def emit_rewrapped(self, stats: RewrapStats) -> None:
        """Grava `deks_reembrulhadas` em `usage_events` (fail-soft)."""
        ...


class PostgresDekStore:
    """`DekStore` sobre o banco do serviço; uma sessão (e um commit) por operação."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def load_wrapped(self) -> list[tuple[UUID, bytes]]:
        table = cast("Table", Client.__table__)
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    select(table.c.id, table.c.dek_wrapped)
                    .where(table.c.dek_wrapped.is_not(None))
                    .order_by(table.c.id)
                )
            ).all()
        return [(row[0], bytes(row[1])) for row in rows]

    async def swap_blob(self, client_id: UUID, *, old_blob: bytes, new_blob: bytes) -> int:
        """UPDATE condicional em Core (CLAUDE.md §7: UPDATE com WHERE extra é Core)."""
        table = cast("Table", Client.__table__)
        async with self._session_factory() as db:
            result = await db.execute(
                update(table)
                .where(table.c.id == client_id, table.c.dek_wrapped == old_blob)
                .values(dek_wrapped=new_blob)
            )
            # UPDATE devolve CursorResult (com rowcount); o narrow é para o mypy.
            rowcount = int(result.rowcount or 0) if isinstance(result, CursorResult) else 0
            if rowcount != 1:
                await db.rollback()
                return rowcount
            await db.commit()
        return rowcount

    async def first_envelope_field(self, client_id: UUID) -> EnvelopeField | None:
        async with self._session_factory() as db:
            for pair in VERIFIABLE_PAIRS:
                row = (await db.execute(_envelope_field_query(pair, client_id))).first()
                if row is not None:
                    pk, envelope, iv = row
                    return pair, UUID(str(pk)), str(envelope), str(iv)
        return None

    async def emit_rewrapped(self, stats: RewrapStats) -> None:
        async with self._session_factory() as db:
            await UsageEventService(UsageEventRepository(db)).emit_deks_reembrulhadas(
                ja_reembrulhadas=stats.already,
                reembrulhadas=stats.rewrapped,
                falhas=stats.errors,
                duracao_s=stats.duration_s,
            )
            await db.commit()


async def _rewrap_one(
    store: DekStore,
    keys: RewrapKeys,
    stats: RewrapStats,
    *,
    client_id: UUID,
    blob: bytes,
) -> str:
    """Classifica e (fora do dry-run) reembrulha UM cliente. Devolve o desfecho."""
    if await _try_unwrap(keys.target, blob, key_name=keys.target_name) is not None:
        stats.already += 1
        return "already"
    dek = await _try_unwrap(keys.source, blob, key_name=keys.source_name)
    if dek is None:
        stats.errors += 1
        return "undecipherable"
    new_blob = await _guarded(keys.target.wrap_dek(dek), key_name=keys.target_name)
    proof = await _try_unwrap(keys.target, new_blob, key_name=keys.target_name)
    if proof != dek:
        raise RewrapAbortedError(
            "prova_falhou",
            "A KEK nova não devolveu a mesma DEK que acabou de embrulhar. Nada deste "
            "cliente foi gravado.",
            client_id=client_id,
        )
    if stats.mode is RewrapMode.DRY_RUN:
        stats.pending += 1
        return "pending"
    if await store.swap_blob(client_id, old_blob=blob, new_blob=new_blob) != 1:
        raise RewrapAbortedError(
            "linha_alterada",
            "O dek_wrapped do cliente mudou durante o reembrulho (o UPDATE condicional não "
            "atingiu exatamente 1 linha). Nada deste cliente foi gravado; rode de novo.",
            client_id=client_id,
        )
    stats.rewrapped += 1
    return "rewrapped"


async def _verify_one(
    store: DekStore,
    settings: Settings,
    keys: RewrapKeys,
    stats: RewrapStats,
    *,
    client_id: UUID,
    blob: bytes,
) -> str:
    """Abre a DEK com o ALVO pelo caminho do serviço e decifra UM campo real do cliente."""
    try:
        cipher = await _guarded(
            load_client_cipher(_DekRow(client_id, blob), settings=settings, kms=keys.target),
            key_name=keys.target_name,
        )
    except WRONG_KEY_EXCEPTIONS:
        stats.verify_failed += 1
        return "verify_failed"
    found = await store.first_envelope_field(client_id)
    if found is None:
        stats.no_field += 1
        return "no_field"
    pair, pk, envelope, iv = found
    try:
        cipher.decrypt(envelope, iv, field_locator(pair, pk))
    except CryptoError:
        stats.verify_failed += 1
        return "verify_failed"
    stats.verified += 1
    return "verified"


async def run_rewrap(
    *,
    store: DekStore,
    settings: Settings,
    keys: RewrapKeys,
    mode: RewrapMode,
) -> RewrapStats:
    """Uma rodada sobre TODOS os clientes com `dek_wrapped`. Idempotente.

    `RewrapAbortedError` sobe depois de logar o que já foi feito; o que foi commitado fica.
    """
    started = time.perf_counter()
    stats = RewrapStats(mode=mode)
    wrapped = await store.load_wrapped()
    log.info(
        "rewrap_started",
        mode=mode.value,
        clients=len(wrapped),
        source_key=keys.source_name,
        target_key=keys.target_name,
    )
    try:
        for client_id, blob in wrapped:
            stats.clients += 1
            if mode is RewrapMode.VERIFY:
                outcome = await _verify_one(
                    store, settings, keys, stats, client_id=client_id, blob=blob
                )
            else:
                outcome = await _rewrap_one(store, keys, stats, client_id=client_id, blob=blob)
            log.info("rewrap_client", client_id=str(client_id), outcome=outcome)
    except RewrapAbortedError as exc:
        log.error(
            "rewrap_aborted",
            reason=exc.reason,
            client_id=str(exc.client_id) if exc.client_id else None,
            **_counts(stats),
        )
        # A execução interrompida também deixa registro: o que já foi commitado fica.
        stats.duration_s = round(time.perf_counter() - started)
        await _emit_if_executed(store, stats)
        raise
    stats.duration_s = round(time.perf_counter() - started)
    await _emit_if_executed(store, stats)
    log.info(
        "rewrap_done",
        source_key=keys.source_name,
        target_key=keys.target_name,
        duration_s=stats.duration_s,
        **_counts(stats),
    )
    return stats


async def _emit_if_executed(store: DekStore, stats: RewrapStats) -> None:
    """`deks_reembrulhadas` só na execução, e fail-soft: o evento nunca transforma um
    reembrulho já commitado em saída de erro."""
    if stats.mode is not RewrapMode.EXECUTE:
        return
    try:
        await store.emit_rewrapped(stats)
    except Exception as exc:
        log.warning("rewrap_event_emit_failed", error_class=type(exc).__name__)


def _counts(stats: RewrapStats) -> dict[str, int | str]:
    return {
        "mode": stats.mode.value,
        "clients": stats.clients,
        "already": stats.already,
        "pending": stats.pending,
        "rewrapped": stats.rewrapped,
        "errors": stats.errors,
        "verified": stats.verified,
        "verify_failed": stats.verify_failed,
        "no_field": stats.no_field,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true", help="classifica e prova as chaves, sem gravar"
    )
    group.add_argument(
        "--verify", action="store_true", help="decifra um campo real de cada cliente com o alvo"
    )
    return parser.parse_args(argv)


def _report(stats: RewrapStats, keys: RewrapKeys) -> None:
    print(f"origem: {keys.source_name}")
    print(f"alvo:   {keys.target_name}")
    print(" ".join(f"{name}={value}" for name, value in _counts(stats).items()))
    if not stats.ok:
        print("ATENÇÃO: há clientes com falha (ids no log, evento rewrap_client).")


async def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = _parse_args(argv)
    mode = (
        RewrapMode.DRY_RUN
        if args.dry_run
        else RewrapMode.VERIFY
        if args.verify
        else RewrapMode.EXECUTE
    )
    settings = get_settings()
    setup_logging(settings)
    try:
        keys = resolve_rewrap_keys(settings, os.environ if environ is None else environ)
    except RewrapConfigError as exc:
        log.error("rewrap_refused", message=str(exc))
        print(f"recusado: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    init_db(settings)
    try:
        stats = await run_rewrap(
            store=PostgresDekStore(get_session_factory()), settings=settings, keys=keys, mode=mode
        )
    except RewrapAbortedError as exc:
        print(f"abortado: {exc}", file=sys.stderr)
        return EXIT_ABORTED
    except Exception as exc:
        # Sem exc_info: o traceback com variáveis locais poderia carregar a DEK em claro.
        log.error("rewrap_failed", error_class=type(exc).__name__)
        print(f"abortado: erro inesperado ({type(exc).__name__}).", file=sys.stderr)
        return EXIT_ABORTED
    finally:
        await close_db()
    _report(stats, keys)
    return EXIT_OK if stats.ok else EXIT_WITH_FAILURES


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
