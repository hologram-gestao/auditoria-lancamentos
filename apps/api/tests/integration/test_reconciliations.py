"""Testes de integração do módulo de conciliações — BACK 6.2.

Cobre `GET /api/v1/reconciliations/check-duplicate`:

    Validação:
        - Hash com formato inválido → 400 (handler global converte 422→400).
        - Mês com formato inválido → 400.
        - Sem autenticação → 401.

    Domínio:
        - (client, conta, month, hash) já existente → {duplicate: true}.
        - Mesma combinação faltando 1 campo (hash diferente, mês diferente,
          conta diferente) → {duplicate: false}.
        - Hash em maiúsculas no input ainda matcha o lowercase do DB.

    RBAC:
        - Admin consulta qualquer cliente → 200.
        - Manager-da-carteira → 200.
        - Manager fora da carteira → 404 (não 403, evita leak de existência).
        - Cliente inexistente → 404.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


# ----------------------------------------------------------------------
# Constantes / helpers (prefixos próprios para não colidir com outros
# arquivos de teste de integração)
# ----------------------------------------------------------------------

ADMIN_EMAIL = "recon-admin@hologram.com.br"
MANAGER_A_EMAIL = "recon-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "recon-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"

# SHA-256 hex de teste — 64 chars hex lowercase. Não é hash de nada secreto;
# só uma string canônica reproduzível pra comparações.
HASH_A = "a" * 64
HASH_B = "b" * 64


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    name: str = "Test User",
    active: bool = True,
) -> User:
    user = User(
        name=name,
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=active,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    manager: User | None = None,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()

    if manager is not None:
        session.add(
            ClientAssignment(
                client_id=client.id,
                user_id=manager.id,
                assigned_by=creator.id,
            )
        )
        await session.flush()
    return client


async def _seed_reconciliation(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    omie_conta_id: int,
    reference_month: date,
    file_hash: str,
    status: str = "done",
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=omie_conta_id,
        reference_month=reference_month,
        date_tolerance_days=3,
        file_hash=file_hash,
        status=status,
        balance_start=Decimal("0.00"),
        processed_at=datetime.now(UTC) - timedelta(days=1),
    )
    session.add(sess)
    await session.flush()
    return sess


async def _login_as(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _query(
    *,
    client_id: str,
    omie_conta_id: int,
    month: str,
    file_hash: str,
) -> str:
    """Monta a query string do endpoint. `hash` é o nome canônico do param."""
    return (
        "/api/v1/reconciliations/check-duplicate"
        f"?client_id={client_id}"
        f"&omie_conta_id={omie_conta_id}"
        f"&month={month}"
        f"&hash={file_hash}"
    )


# ----------------------------------------------------------------------
# RBAC + erros 4xx
# ----------------------------------------------------------------------


class TestCheckDuplicateRBAC:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get(
            _query(
                client_id=str(uuid4()),
                omie_conta_id=1,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 401

    async def test_admin_can_check_any_client(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="Qualquer", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"data": {"duplicate": False}}

    async def test_manager_in_portfolio_can_check(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(
            db_session, name="Da carteira de A", creator=admin, manager=mgr
        )
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 200, resp.text

    async def test_manager_outside_portfolio_returns_404_not_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Manager B consulta cliente da carteira de A: não pode receber 403,
        senão dá pra inferir a existência do cliente. 404 trata o cenário
        igual a "cliente não existe"."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        # Cliente atribuído a A; B não tem assignment.
        cliente_a = await _seed_client(
            db_session, name="Da carteira de A", creator=admin, manager=mgr_a
        )
        await _login_as(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente_a.id),
                omie_conta_id=1,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 404
        # Resposta no formato padrão de erro
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"

    async def test_inexistent_client_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(uuid4()),
                omie_conta_id=1,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Validação de query params
# ----------------------------------------------------------------------


class TestCheckDuplicateValidation:
    async def test_invalid_hash_format_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=1,
                month="2026-04",
                file_hash="not-a-sha256",
            )
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invalid_month_format_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        # Mês 13 é inválido pela regex (0[1-9]|1[0-2])
        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=1,
                month="2026-13",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_invalid_omie_conta_id_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=0,  # ge=1
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# Domínio — duplicidade
# ----------------------------------------------------------------------


class TestCheckDuplicateDomain:
    async def test_existing_session_returns_duplicate_true(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _seed_reconciliation(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            file_hash=HASH_A,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"data": {"duplicate": True}}

    async def test_no_session_returns_duplicate_false(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"data": {"duplicate": False}}

    async def test_different_hash_does_not_count_as_duplicate(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Mesma conta, mesmo mês, hash diferente → UNIQUE de 4 colunas
        garante que NÃO é duplicata. O upload do extrato corrigido tem que
        ser permitido."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _seed_reconciliation(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            file_hash=HASH_A,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-04",
                file_hash=HASH_B,  # diferente
            )
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"data": {"duplicate": False}}

    async def test_different_month_does_not_count_as_duplicate(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _seed_reconciliation(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            file_hash=HASH_A,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-05",  # diferente
                file_hash=HASH_A,
            )
        )
        assert resp.json() == {"data": {"duplicate": False}}

    async def test_different_omie_conta_does_not_count_as_duplicate(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _seed_reconciliation(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            file_hash=HASH_A,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=99,  # diferente
                month="2026-04",
                file_hash=HASH_A,
            )
        )
        assert resp.json() == {"data": {"duplicate": False}}

    async def test_uppercase_hash_in_query_still_matches(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A normalização para lowercase no service evita falso negativo se
        o caller mandar o hash em maiúsculas. O DB armazena em lowercase."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _seed_reconciliation(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            file_hash=HASH_A,  # lowercase no DB
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            _query(
                client_id=str(cliente.id),
                omie_conta_id=42,
                month="2026-04",
                file_hash=HASH_A.upper(),  # maiúsculas no input
            )
        )
        assert resp.json() == {"data": {"duplicate": True}}


# ----------------------------------------------------------------------
# helpers privados
# ----------------------------------------------------------------------


async def _get_user_id(session: AsyncSession, email: str):
    """Pequeno helper para o teste de RBAC quando a fixture já criou o
    usuário e só queremos o id pra setar como manager do cliente."""
    from sqlalchemy import select

    row = await session.execute(select(User).where(User.email == email.lower()))
    user = row.scalar_one()
    return user.id
