"""Cria/atualiza um cliente fictício "Padaria Pão Quente Ltda" para testar a UI
sem precisar de credenciais Omie reais.

Idempotente: pode rodar várias vezes. Não duplica cliente nem contas (chaves
únicas: `clients.name` por enquanto + `UNIQUE(client_id, omie_conta_id)` no cache).

Uso:
    cd apps/api
    uv run python -m scripts.seed_demo_client

O script:
  1. Localiza um admin (cria um se não houver — reusa lógica do seed_dev).
  2. Cria o cliente "Padaria Pão Quente Ltda" com credenciais Omie FAKE
     criptografadas (AES-256-GCM real, mas o conteúdo é placeholder e qualquer
     chamada ao Omie vai falhar com 401 — OK, não é o foco).
  3. Insere 3 contas mockadas no `omie_accounts_cache` (2 CC + 1 CA).
  4. Marca `omie_accounts_synced_at = NOW()` no cliente para a UI tratar como
     "já sincronizado".
  5. Se existir algum manager ativo, cria/atualiza `ClientAssignment` para que
     ele veja o cliente. Admin sempre vê (RBAC).

Limitações conhecidas:
  - "Test Connection" no UI de edição vai FALHAR (credenciais são fake).
  - Sincronizar contas no UI também vai falhar — ESTE script é a forma de
    popular o cache em ambiente sem Omie.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Garante que `apps/api/` está no sys.path (idem seed_dev.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402  (sys.path setado acima é pré-requisito)
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import get_settings  # noqa: E402
from app.core.crypto import encrypt  # noqa: E402
from app.db.models import (  # noqa: E402
    Client,
    ClientAssignment,
    OmieAccountCache,
    User,
    UserRole,
)
from app.db.session import close_db, get_session_factory, init_db  # noqa: E402

DEMO_CLIENT_NAME = "Padaria Pão Quente Ltda"
DEMO_FAKE_KEY = "FAKE_DEMO_OMIE_APP_KEY_DO_NOT_USE"
DEMO_FAKE_SECRET = "FAKE_DEMO_OMIE_APP_SECRET_DO_NOT_USE"  # noqa: S105

DEMO_ACCOUNTS: list[dict[str, object]] = [
    {
        "omie_conta_id": 900_000_001,
        "name": "Itaú 12345-6 (Principal)",
        "bank_name": "Itaú Unibanco",
        "account_type": "CC",
    },
    {
        "omie_conta_id": 900_000_002,
        "name": "Sicredi 91263-1",
        "bank_name": "Sicredi",
        "account_type": "CC",
    },
    {
        "omie_conta_id": 900_000_003,
        "name": "Cartão Visa Empresarial 4521",
        "bank_name": "Itaú Unibanco",
        "account_type": "CA",
    },
]


async def get_admin(session: AsyncSession) -> User:
    """Localiza um admin para usar como `created_by` do cliente fictício."""
    admin = await session.scalar(
        select(User).where(User.role == UserRole.ADMIN.value, User.active.is_(True)).limit(1)
    )
    if admin is None:
        raise RuntimeError(
            "Nenhum admin encontrado. Rode antes: uv run python -m scripts.seed_dev",
        )
    return admin


async def upsert_demo_client(session: AsyncSession, admin: User) -> tuple[Client, bool]:
    """Cria o cliente fictício se não existir. Retorna (client, created)."""
    settings = get_settings()
    existing = await session.scalar(select(Client).where(Client.name == DEMO_CLIENT_NAME))
    if existing is not None:
        return existing, False

    hex_key = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
    key_ct, key_iv = encrypt(DEMO_FAKE_KEY, hex_key)
    secret_ct, secret_iv = encrypt(DEMO_FAKE_SECRET, hex_key)

    client = Client(
        name=DEMO_CLIENT_NAME,
        omie_app_key_encrypted=key_ct,
        omie_app_key_iv=key_iv,
        omie_app_secret_encrypted=secret_ct,
        omie_app_secret_iv=secret_iv,
        active=True,
        created_by=admin.id,
    )
    session.add(client)
    await session.flush()
    return client, True


async def upsert_demo_accounts(session: AsyncSession, client: Client) -> tuple[int, int]:
    """Insere as contas que ainda não existem no cache. Retorna (inseridas, já_existentes)."""
    inserted = 0
    skipped = 0
    for spec in DEMO_ACCOUNTS:
        existing = await session.scalar(
            select(OmieAccountCache).where(
                OmieAccountCache.client_id == client.id,
                OmieAccountCache.omie_conta_id == spec["omie_conta_id"],
            )
        )
        if existing is not None:
            skipped += 1
            continue
        session.add(
            OmieAccountCache(
                client_id=client.id,
                omie_conta_id=spec["omie_conta_id"],
                name=spec["name"],
                bank_name=spec["bank_name"],
                account_type=spec["account_type"],
            )
        )
        inserted += 1
    await session.flush()
    return inserted, skipped


async def mark_synced(session: AsyncSession, client: Client) -> None:
    """Marca `omie_accounts_synced_at` para a UI considerar o cache fresco."""
    client.omie_accounts_synced_at = func.now()  # type: ignore[assignment]
    await session.flush()


async def assign_to_first_manager(session: AsyncSession, client: Client, admin: User) -> str | None:
    """Cria assignment com o primeiro manager ativo, se houver. Retorna o nome (ou None)."""
    manager = await session.scalar(
        select(User).where(User.role == UserRole.MANAGER.value, User.active.is_(True)).limit(1)
    )
    if manager is None:
        return None

    existing = await session.scalar(
        select(ClientAssignment).where(ClientAssignment.client_id == client.id)
    )
    if existing is not None:
        return manager.name  # já tem assignment; mantém

    session.add(
        ClientAssignment(
            client_id=client.id,
            user_id=manager.id,
            assigned_by=admin.id,
        )
    )
    await session.flush()
    return manager.name


async def main() -> None:
    settings = get_settings()
    init_db(settings)
    try:
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            admin = await get_admin(session)
            client, created = await upsert_demo_client(session, admin)
            inserted, skipped = await upsert_demo_accounts(session, client)
            await mark_synced(session, client)
            assigned_to = await assign_to_first_manager(session, client, admin)

        verb = "criado" if created else "já existia"
        print(f"[seed-demo] cliente {DEMO_CLIENT_NAME!r} {verb} (id={client.id})")
        print(f"[seed-demo] contas: {inserted} inseridas, {skipped} já existiam")
        if assigned_to:
            print(f"[seed-demo] atribuído ao manager: {assigned_to}")
        else:
            print("[seed-demo] nenhum manager ativo encontrado — só admin verá o cliente")
    finally:
        await close_db()
    print("[seed-demo] concluído.")


if __name__ == "__main__":
    asyncio.run(main())
