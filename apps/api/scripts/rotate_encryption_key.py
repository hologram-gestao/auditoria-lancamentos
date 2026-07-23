"""Rotação/backfill do envelope cripto para DEK-por-cliente (Sprint 3, BACK 03.4).

O que faz (o furo real que fecha):
    Re-cifra TODAS as linhas legadas em formato **bare** (`ciphertext_hex` /
    `iv_hex`, sob a chave global `OMIE_ENCRYPTION_KEY`, sem AAD) para o envelope
    corrente `v<n>:<key_id>:` + AAD (client_id‖tabela‖coluna‖pk) + **DEK do
    cliente**. Cobre todos os campos cifrados do CLAUDE.md §4:
        - clients.omie_app_key_encrypted / omie_app_secret_encrypted
        - reconciliation_file_entries.description_encrypted / user_note_encrypted
        - reconciliation_omie_entries.user_note_encrypted
        - reconciliation_anomalies.context_encrypted / resolution_note_encrypted

Propriedades:
    - **Por lotes, online**: commita a cada lote — progresso parcial persiste.
    - **Interrompível e retomável**: uma linha já convertida vira `v<n>:...`
      (não-bare), então o filtro de bare não a re-seleciona. Rodar 2x é
      idempotente (o 2º run converte 0).
    - **Provisiona a DEK** de cada cliente (gera+embrulha via KMS se legado) e
      persiste `clients.dek_wrapped` — após o backfill nenhuma linha fica nula.
    - **Paridade de segredos**: usa as MESMAS chaves de crypto / `KEK_KMS_KEY_NAME`
      do serviço (via `Settings`). Sem isso, o dado cifrado corromperia.
    - Emite `chave_rotacionada {clientes_afetados, duracao_s}` (sem PII).

Uso:
    cd apps/api
    uv run python -m scripts.rotate_encryption_key

No Cloud Run Job, com as MESMAS secrets do serviço:
    --command=python --args=-m,scripts.rotate_encryption_key
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

# Garante que ``apps/api/`` está no sys.path (idem seed_dev.py / mark_stuck).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.crypto import ClientCipher  # noqa: E402
from app.core.crypto_service import (  # noqa: E402
    AAD_ANOMALY_CONTEXT,
    AAD_ANOMALY_RESOLUTION_NOTE,
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    AAD_FILE_ENTRY_DESCRIPTION,
    AAD_FILE_ENTRY_USER_NOTE,
    AAD_OMIE_ENTRY_USER_NOTE,
    field_locator,
    provision_client_cipher,
)
from app.core.kms import get_kms_client  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.core.telemetry import emit_chave_rotacionada  # noqa: E402
from app.db.models import (  # noqa: E402
    Client,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
)
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import Settings
    from app.core.kms import KmsClient

log = get_logger(__name__)

DEFAULT_BATCH_SIZE = 500


@dataclass
class RotationStats:
    """Resultado da rotação — sem PII, só contadores."""

    clients_afetados: int = 0
    fields_converted: int = 0
    deks_provisioned: int = 0
    duration_s: float = 0.0
    per_table: dict[str, int] = field(default_factory=dict)


def _convert_pair(
    cipher: ClientCipher,
    row: object,
    ct_attr: str,
    iv_attr: str,
    aad_pair: tuple[str, str],
    pk: UUID,
) -> bool:
    """Converte UM par (`_encrypted`/`_iv`) bare→v1 in-place. Retorna True se
    converteu, False se o valor era nulo ou já estava no envelope corrente."""
    ct = getattr(row, ct_attr)
    iv = getattr(row, iv_attr)
    if ct is None or iv is None:
        return False
    if not ClientCipher.is_legacy(ct):
        return False  # já convertido — idempotente
    loc = field_locator(aad_pair, pk)
    # decrypt() com valor bare cai no caminho legado (chave global, sem AAD);
    # encrypt() grava v1 + AAD + DEK do cliente.
    plaintext = cipher.decrypt(ct, iv, loc)
    new_ct, new_iv = cipher.encrypt(plaintext, loc)
    setattr(row, ct_attr, new_ct)
    setattr(row, iv_attr, new_iv)
    return True


def _rotate_client_credentials(client: Client, cipher: ClientCipher) -> int:
    """Converte os 2 campos de credencial do próprio cliente. Retorna nº convertido."""
    converted = 0
    converted += _convert_pair(
        cipher,
        client,
        "omie_app_key_encrypted",
        "omie_app_key_iv",
        AAD_CLIENT_APP_KEY,
        client.id,
    )
    converted += _convert_pair(
        cipher,
        client,
        "omie_app_secret_encrypted",
        "omie_app_secret_iv",
        AAD_CLIENT_APP_SECRET,
        client.id,
    )
    return converted


async def _rotate_file_entries(
    db: AsyncSession, cipher: ClientCipher, client_id: UUID, batch_size: int
) -> int:
    """Converte description + user_note dos file_entries do cliente, por lotes."""
    total = 0
    while True:
        stmt = (
            select(ReconciliationFileEntry)
            .join(
                ReconciliationSession,
                ReconciliationFileEntry.session_id == ReconciliationSession.id,
            )
            .where(
                ReconciliationSession.client_id == client_id,
                (
                    ReconciliationFileEntry.description_encrypted.not_like("v%")
                    | (
                        ReconciliationFileEntry.user_note_encrypted.is_not(None)
                        & ReconciliationFileEntry.user_note_encrypted.not_like("v%")
                    )
                ),
            )
            .order_by(ReconciliationFileEntry.id)
            .limit(batch_size)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            break
        for row in rows:
            total += _convert_pair(
                cipher,
                row,
                "description_encrypted",
                "description_iv",
                AAD_FILE_ENTRY_DESCRIPTION,
                row.id,
            )
            total += _convert_pair(
                cipher,
                row,
                "user_note_encrypted",
                "user_note_iv",
                AAD_FILE_ENTRY_USER_NOTE,
                row.id,
            )
        await db.commit()
    return total


async def _rotate_omie_entries(
    db: AsyncSession, cipher: ClientCipher, client_id: UUID, batch_size: int
) -> int:
    """Converte user_note dos omie_entries do cliente, por lotes."""
    total = 0
    while True:
        stmt = (
            select(ReconciliationOmieEntry)
            .join(
                ReconciliationSession,
                ReconciliationOmieEntry.session_id == ReconciliationSession.id,
            )
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationOmieEntry.user_note_encrypted.is_not(None),
                ReconciliationOmieEntry.user_note_encrypted.not_like("v%"),
            )
            .order_by(ReconciliationOmieEntry.id)
            .limit(batch_size)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            break
        for row in rows:
            total += _convert_pair(
                cipher,
                row,
                "user_note_encrypted",
                "user_note_iv",
                AAD_OMIE_ENTRY_USER_NOTE,
                row.id,
            )
        await db.commit()
    return total


async def _rotate_anomalies(
    db: AsyncSession, cipher: ClientCipher, client_id: UUID, batch_size: int
) -> int:
    """Converte context + resolution_note das anomalias do cliente, por lotes."""
    total = 0
    while True:
        stmt = (
            select(ReconciliationAnomaly)
            .join(
                ReconciliationSession,
                ReconciliationAnomaly.session_id == ReconciliationSession.id,
            )
            .where(
                ReconciliationSession.client_id == client_id,
                (
                    (
                        ReconciliationAnomaly.context_encrypted.is_not(None)
                        & ReconciliationAnomaly.context_encrypted.not_like("v%")
                    )
                    | (
                        ReconciliationAnomaly.resolution_note_encrypted.is_not(None)
                        & ReconciliationAnomaly.resolution_note_encrypted.not_like("v%")
                    )
                ),
            )
            .order_by(ReconciliationAnomaly.id)
            .limit(batch_size)
        )
        rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            break
        for row in rows:
            total += _convert_pair(
                cipher,
                row,
                "context_encrypted",
                "context_iv",
                AAD_ANOMALY_CONTEXT,
                row.id,
            )
            total += _convert_pair(
                cipher,
                row,
                "resolution_note_encrypted",
                "resolution_note_iv",
                AAD_ANOMALY_RESOLUTION_NOTE,
                row.id,
            )
        await db.commit()
    return total


async def run_rotation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    kms: KmsClient | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> RotationStats:
    """Executa a rotação/backfill sobre TODOS os clientes. Emite
    `chave_rotacionada` ao final. Idempotente e retomável."""
    kms = kms or get_kms_client(settings)
    started = time.perf_counter()
    stats = RotationStats(
        per_table={"clients": 0, "file_entries": 0, "omie_entries": 0, "anomalies": 0}
    )

    async with session_factory() as db:
        client_ids = list((await db.execute(select(Client.id))).scalars().all())

    for client_id in client_ids:
        affected = False
        # 1) Credenciais do cliente + provisão da DEK, na MESMA transação.
        async with session_factory() as db:
            client = await db.get(Client, client_id)
            if client is None:  # pragma: no cover - corrida improvável
                continue
            had_dek = client.dek_wrapped is not None
            cipher = await provision_client_cipher(client, settings=settings, kms=kms)
            if not had_dek:
                stats.deks_provisioned += 1
                affected = True
            converted = _rotate_client_credentials(client, cipher)
            await db.commit()
        stats.per_table["clients"] += converted
        stats.fields_converted += converted
        if converted:
            affected = True

        # 2) Linhas-filhas por lotes (novas sessões por lote — commit por lote).
        async with session_factory() as db:
            fe = await _rotate_file_entries(db, cipher, client_id, batch_size)
            oe = await _rotate_omie_entries(db, cipher, client_id, batch_size)
            an = await _rotate_anomalies(db, cipher, client_id, batch_size)
        stats.per_table["file_entries"] += fe
        stats.per_table["omie_entries"] += oe
        stats.per_table["anomalies"] += an
        stats.fields_converted += fe + oe + an
        if fe or oe or an:
            affected = True

        if affected:
            stats.clients_afetados += 1

    stats.duration_s = round(time.perf_counter() - started, 3)
    emit_chave_rotacionada(clientes_afetados=stats.clients_afetados, duracao_s=stats.duration_s)
    log.info(
        "rotation_done",
        clients_afetados=stats.clients_afetados,
        deks_provisioned=stats.deks_provisioned,
        fields_converted=stats.fields_converted,
        duration_s=stats.duration_s,
        per_table=stats.per_table,
    )
    return stats


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    init_db(settings)
    try:
        sf = get_session_factory()
        await run_rotation(session_factory=sf, settings=settings)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
