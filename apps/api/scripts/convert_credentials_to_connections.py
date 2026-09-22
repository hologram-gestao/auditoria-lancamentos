"""Converte a credencial de cada cliente para `client_connections` (S9, BACK 09.5 — R2).

**Por que isto NÃO é um `UPDATE`.** O envelope amarra o AAD a
`client_id ‖ tabela ‖ coluna ‖ pk` (`core/crypto.py`). Copiar o ciphertext de
`clients.omie_app_key_encrypted` para `client_connections.credentials_encrypted`
em SQL puro **quebraria a decifra de todos os clientes existentes** — e a
bateria ficaria VERDE, porque os testes criam clientes pela API já no caminho
novo. Só a base real quebraria. Além disso há linhas **bare** legadas (chave
global, sem AAD) e re-cifrar exige *unwrap* da DEK no KMS, impossível em SQL.

Então é processo de APLICAÇÃO, com as mesmas secrets do serviço, no molde de
`scripts/rotate_encryption_key.py`.

O que faz, por cliente:
    1. decifra a credencial do locator ANTIGO (`AAD_CLIENT_APP_KEY/SECRET`) —
       `ClientCipher` é multi-chave, então bare e `v1:` passam pelo mesmo
       `decrypt`;
    2. re-cifra o JSON `{app_key, app_secret}` com a DEK do cliente e o locator
       de `client_connections` (`AAD_CONNECTION_CREDENTIALS`, 09.1);
    3. insere a conexão `omie` / rótulo `Omie` / `status=ativa`, copiando
       `clients.omie_accounts_synced_at` → `connections.accounts_synced_at`;
    4. aponta as linhas de `omie_accounts_cache` do cliente para a conexão.

**As colunas antigas ficam INTACTAS** — são a salvaguarda de rollback até o
`contract` da sprint seguinte.

Propriedades:
    - **Por lotes, com commit por lote**: progresso parcial persiste.
    - **Idempotente e retomável**: a seleção exclui quem já tem conexão `omie`,
      então o 2º run converte 0 e uma interrupção no meio não duplica nada.
    - **NUNCA toca cliente encerrado** (`closed_at` preenchido): a credencial
      dele é `''` e a DEK foi destruída (§4.12).
    - Falha permanente numa linha **não derruba o lote**: registra o `client_id`
      no relatório e o processo termina com `exit != 0`.
    - Relatório só com IDs e contagens — sem PII, sem credencial.

Runbook (nesta ordem, sem pular):

    1. deploy com a migration `a7f2c1d93e84` aplicada;
    2. Cloud Run Job com as MESMAS secrets do serviço:
       `--command=python --args=-m,scripts.convert_credentials_to_connections`
    3. `--verify` → precisa sair **PASS**;
    4. só então `--update-env-vars LEGACY_CREDENTIALS_FALLBACK_ENABLED=false`.

Uso local:

    cd apps/api
    uv run python -m scripts.convert_credentials_to_connections --dry-run
    uv run python -m scripts.convert_credentials_to_connections
    uv run python -m scripts.convert_credentials_to_connections --verify
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

# Garante que ``apps/api/`` está no sys.path (idem rotate_encryption_key.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import Select, func, select, update  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.crypto_service import (  # noqa: E402
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    provision_client_cipher,
)
from app.core.kms import get_kms_client  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models import Client, ClientConnection, OmieAccountCache  # noqa: E402
from app.db.models.client_connection import ConnectionStatus, ProviderType  # noqa: E402
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402
from app.modules.client_connections.legacy_fallback import (  # noqa: E402
    SYNTHETIC_LABEL,
    count_pending_conversion,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import Settings
    from app.core.kms import KmsClient

log = get_logger(__name__)

DEFAULT_BATCH_SIZE = 100


@dataclass
class ConversionStats:
    """Resultado da conversão — só IDs e contadores. NUNCA credencial."""

    converted: int = 0
    skipped_already_converted: int = 0
    skipped_closed: int = 0
    deks_provisioned: int = 0
    accounts_relinked: int = 0
    failed_client_ids: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed_client_ids

    def as_report(self) -> dict[str, object]:
        return {
            "converted": self.converted,
            "skipped_already_converted": self.skipped_already_converted,
            "skipped_closed": self.skipped_closed,
            "deks_provisioned": self.deks_provisioned,
            "accounts_relinked": self.accounts_relinked,
            "failed": len(self.failed_client_ids),
            "failed_client_ids": self.failed_client_ids,
            "duration_s": self.duration_s,
        }


def _pending_clients_stmt(limit: int) -> Select[tuple[Client]]:
    """Clientes ABERTOS, com credencial não vazia, SEM conexão `omie`.

    É a seleção que torna o script retomável: a conexão criada no lote anterior
    tira o cliente do conjunto, então uma interrupção no meio só deixa trabalho
    para o próximo run — nunca duplicata.
    """
    ja_convertido = (
        select(ClientConnection.id)
        .where(
            ClientConnection.client_id == Client.id,
            ClientConnection.provider_type == ProviderType.OMIE.value,
        )
        .correlate(Client)
        .exists()
    )
    return (
        select(Client)
        .where(
            Client.closed_at.is_(None),
            Client.omie_app_key_encrypted.is_not(None),
            Client.omie_app_key_encrypted != "",
            Client.omie_app_secret_encrypted.is_not(None),
            Client.omie_app_secret_encrypted != "",
            ~ja_convertido,
        )
        .order_by(Client.id)
        .limit(limit)
    )


async def _convert_one(
    db: AsyncSession, client: Client, *, settings: Settings, kms: KmsClient
) -> tuple[bool, int]:
    """Converte UM cliente. Devolve `(provisionou_dek, linhas_de_cache_religadas)`.

    Levanta se a decifragem falhar — o caller registra o `client_id` e segue.
    """
    had_dek = client.dek_wrapped is not None
    cipher = await provision_client_cipher(client, settings=settings, kms=kms)

    # 1. Decifra do locator ANTIGO (bare ou v1: — o cipher é multi-chave).
    assert client.omie_app_key_encrypted is not None
    assert client.omie_app_key_iv is not None
    assert client.omie_app_secret_encrypted is not None
    assert client.omie_app_secret_iv is not None
    app_key = cipher.decrypt(
        client.omie_app_key_encrypted,
        client.omie_app_key_iv,
        field_locator(AAD_CLIENT_APP_KEY, client.id),
    )
    app_secret = cipher.decrypt(
        client.omie_app_secret_encrypted,
        client.omie_app_secret_iv,
        field_locator(AAD_CLIENT_APP_SECRET, client.id),
    )

    # 2. A linha precisa existir ANTES da cifra: a pk entra no AAD (§4.1).
    connection = ClientConnection(
        client_id=client.id,
        provider_type=ProviderType.OMIE.value,
        label=SYNTHETIC_LABEL,
        status=ConnectionStatus.ATIVA.value,
        last_checked_at=datetime.now(UTC),
        accounts_synced_at=client.omie_accounts_synced_at,
    )
    db.add(connection)
    await db.flush()

    # 3. Re-cifra com o locator NOVO. Mesmo `sort_keys` do serviço (09.3): o
    # plaintext é o JSON, e ordem instável mudaria o texto sem nada mudar.
    envelope, iv = cipher.encrypt(
        json.dumps(
            {"app_key": app_key, "app_secret": app_secret}, ensure_ascii=False, sort_keys=True
        ),
        field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
    )
    connection.credentials_encrypted = envelope
    connection.credentials_iv = iv

    # 4. O cache de contas passa a pertencer à conexão (a 09.6 lê por ela).
    result = await db.execute(
        update(OmieAccountCache)
        .where(
            OmieAccountCache.client_id == client.id,
            OmieAccountCache.connection_id.is_(None),
        )
        .values(connection_id=connection.id)
    )
    relinked = int(getattr(result, "rowcount", 0) or 0)

    # ⚠️ As 4 colunas antigas ficam INTACTAS — salvaguarda de rollback.
    return (not had_dek), relinked


async def run_conversion(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    kms: KmsClient | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> ConversionStats:
    """Converte todos os pendentes, por lotes, com commit por lote."""
    kms = kms or get_kms_client(settings)
    started = time.perf_counter()
    stats = ConversionStats()
    seen_failures: set[UUID] = set()

    while True:
        async with session_factory() as db:
            pending = list((await db.execute(_pending_clients_stmt(batch_size))).scalars().all())
            # Um cliente que falhou fica no conjunto para sempre (não ganhou
            # conexão) — sem este filtro, o `while` nunca terminaria.
            pending = [c for c in pending if c.id not in seen_failures]
            if not pending:
                break
            if dry_run:
                stats.converted += len(pending)
                log.info("conversion_dry_run_batch", size=len(pending))
                break

            for client in pending:
                try:
                    provisioned, relinked = await _convert_one(
                        db, client, settings=settings, kms=kms
                    )
                except Exception:
                    # `except Exception` e não um tipo específico: sob anyio o
                    # erro do KMS pode vir dentro de um ExceptionGroup, e o que
                    # importa aqui é não parar o lote por causa de uma linha.
                    await db.rollback()
                    seen_failures.add(client.id)
                    stats.failed_client_ids.append(str(client.id))
                    # Só o ID — a mensagem pode carregar contexto do ciphertext.
                    log.error("conversion_failed", client_id=str(client.id))
                    continue
                stats.converted += 1
                stats.deks_provisioned += int(provisioned)
                stats.accounts_relinked += relinked
            await db.commit()

    stats.duration_s = round(time.perf_counter() - started, 3)
    async with session_factory() as db:
        # Encerrados NUNCA entram: credencial `''` e DEK destruída (§4.12).
        # Contados para o relatório dizer que foram vistos e pulados, não
        # esquecidos.
        stats.skipped_closed = int(
            (
                await db.execute(select(func.count(Client.id)).where(Client.closed_at.is_not(None)))
            ).scalar_one()
        )
        stats.skipped_already_converted = int(
            (
                await db.execute(
                    select(func.count(ClientConnection.id)).where(
                        ClientConnection.provider_type == ProviderType.OMIE.value
                    )
                )
            ).scalar_one()
            - stats.converted
        )
    log.info("conversion_done", **stats.as_report())
    return stats


@dataclass(frozen=True)
class VerifyResult:
    """Veredito do `--verify`. `pending == 0` é o único PASS."""

    pending: int

    @property
    def passed(self) -> bool:
        return self.pending == 0

    def render(self) -> str:
        veredito = "PASS" if self.passed else "FAIL"
        return (
            f"[{veredito}] clientes abertos com credencial antiga e SEM conexão omie: "
            f"{self.pending}"
        )


async def run_verify(*, session_factory: async_sessionmaker[AsyncSession]) -> VerifyResult:
    """A MESMA contagem que o `lifespan` roda — uma fonte só."""
    async with session_factory() as db:
        return VerifyResult(pending=await count_pending_conversion(db))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Conta o que seria convertido e não grava nada.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Só o veredito PASS/FAIL da conversão. Não converte.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    setup_logging(settings)
    init_db(settings)
    try:
        sf = get_session_factory()
        if args.verify:
            result = await run_verify(session_factory=sf)
            print(result.render())
            return 0 if result.passed else 1
        stats = await run_conversion(
            session_factory=sf,
            settings=settings,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
        print(json.dumps(stats.as_report(), ensure_ascii=False, indent=2))
        return 0 if stats.ok else 1
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
