"""Sincroniza a CARTEIRA de títulos de todos os clientes abertos (BACK 11.2).

O comando de APLICAÇÃO da sincronização diária. **Não há agendador aqui** — a
FASE 0 removeu Redis/ARQ e a aplicação só tem `BackgroundTasks` e Cloud Run Jobs.
O Cloud Run Job + Cloud Scheduler que invocam este módulo são da **INFRA 11.6**,
no molde de `auditoria-api-cleanup-stuck-dev`; este arquivo é o que eles chamam, e
roda sozinho:

    cd apps/api
    uv run python -m scripts.sync_client_titles

No Cloud Run Job, override:
    --command=python --args=-m,scripts.sync_client_titles

**Um cliente de cada vez, sempre.** A origem processa uma requisição por método
por credencial, e o serviço já serializa por cliente; varrer a lista em série
resolve também o caso de dois clientes da mesma organização compartilharem
credencial. `asyncio.gather` sobre clientes seria a forma mais rápida de
descobrir o limite da origem em produção.

**Falha de um cliente é registrada e PULADA.** Um cadastro sem conexão, uma
credencial expirada ou uma instabilidade da origem não podem derrubar a varredura
inteira: o carimbo de falha daquele cliente já foi gravado pelo serviço, o
`titles_synced_at` dele continua intocado, e o próximo cliente é processado. O
ciclo seguinte tenta de novo — é o "nunca ficar preso em sincronizando" do R5.

**Idempotente e retomável.** Duas execuções seguidas deixam o mesmo estado (o
ciclo é um `ON CONFLICT DO UPDATE` mais um `UPDATE` de quem saiu), então uma
execução interrompida no meio pode simplesmente rodar de novo.

⚠️ **Escalares ANTES do bloco transacional.** `client_id` e `client_name` são
capturados como `str`/`UUID` antes de qualquer `try`: ler atributo de instância
ORM depois de um `rollback()` levanta `MissingGreenlet` — e o lugar onde isso
apareceria é exatamente o log de erro, que é o único sinal de que algo deu errado
(aprendizado de 22/09/2026).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Garante que ``apps/api/`` está no sys.path (idem mark_stuck_sessions_as_error.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from uuid import UUID  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.models import Client  # noqa: E402
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402
from app.modules.client_titles.repository import ClientTitlesRepository  # noqa: E402
from app.modules.client_titles.service import ClientTitlesSyncService  # noqa: E402
from app.modules.clients.repository import ClientRepository  # noqa: E402

log = get_logger(__name__)


async def _client_ids_to_sync(db: AsyncSession) -> list[UUID]:
    """Os clientes ABERTOS, em ordem estável.

    `closed_at IS NULL` é o filtro de encerramento (§4.12) — cliente encerrado
    não opera e não tem origem a consultar, então sincronizá-lo seria uma
    chamada à origem garantidamente inútil. `active` NÃO entra: cliente
    desativado segue sendo um cliente aberto cujo aging alguém pode consultar.

    Só os IDs: manter instâncias ORM vivas ao longo de uma varredura que abre
    uma transação por cliente é como se colecionam `DetachedInstanceError`.
    """
    rows = await db.execute(
        select(Client.id).where(Client.closed_at.is_(None)).order_by(Client.created_at.asc())
    )
    return list(rows.scalars().all())


async def _sync_one(client_id: UUID, *, settings: Settings) -> bool:
    """Sincroniza UM cliente na própria transação. `False` se falhou.

    Uma sessão (e uma transação) por cliente, de propósito: uma falha no cliente
    N não pode arrastar o que os clientes 1..N-1 já gravaram, e é isso que torna
    a varredura retomável de verdade em vez de só no nome.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        try:
            client = await db.get(Client, client_id)
            if client is None:  # pragma: no cover - corrida com exclusão
                log.warning("client_titles_batch_client_vanished", client_id=str(client_id))
                return False

            service = ClientTitlesSyncService(
                db,
                repository=ClientTitlesRepository(db),
                clients=ClientRepository(db),
                settings=settings,
            )
            result = await service.sync(client)
            await db.commit()
        except Exception as exc:
            # A mensagem do provedor NUNCA é logada (§3.3 — é texto livre de
            # terceiro, e a Omie ecoa nela conteúdo do cadastro do cliente). O
            # que entra no log é o TIPO da exceção, que já diz se foi
            # configuração, credencial ou instabilidade.
            await db.rollback()
            log.warning(
                "client_titles_batch_client_failed",
                client_id=str(client_id),
                error_type=type(exc).__name__,
            )
            return False

        log.info(
            "client_titles_batch_client_synced",
            client_id=str(client_id),
            total=result.total,
            vencidos=result.vencidos,
        )
        return True


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)

    init_db(settings)
    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            client_ids = await _client_ids_to_sync(db)

        log.info("client_titles_batch_started", clients=len(client_ids))

        succeeded = 0
        failed = 0
        for client_id in client_ids:
            if await _sync_one(client_id, settings=settings):
                succeeded += 1
            else:
                failed += 1

        log.info(
            "client_titles_batch_finished",
            clients=len(client_ids),
            succeeded=succeeded,
            failed=failed,
        )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
