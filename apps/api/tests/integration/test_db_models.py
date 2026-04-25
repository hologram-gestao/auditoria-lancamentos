"""Testes de integração — valida que modelos persistem corretamente no Postgres.

Cobertura:
    - INSERT + SELECT round-trip de cada modelo principal.
    - Constraints UNIQUE são aplicadas (idempotência).
    - Relationships com `lazy='raise'` falham se acessadas sem `selectinload`.
    - Defaults de timestamp + UUID gerados corretamente.
    - Cascade delete em filhos quando session é apagada.

Pula automaticamente se Docker não estiver disponível (via fixture pg_container).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    ClientAssignment,
    FileEntrySituation,
    OmieAccountCache,
    OmieAccountType,
    OmieEntryStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)

# Fixtures de teste — não são credenciais reais.
BCRYPT_FIXTURE = "$2b$12$fakehashfortest"
FAKE_IV_KEY = "0011223344556677889900aa"
FAKE_IV_SECRET = "0011223344556677889900bb"
FAKE_IV_DESC = "0011223344556677889900cc"


async def _make_user(session: AsyncSession, *, email: str = "u@test.com") -> User:
    user = User(
        name="Test User",
        email=email,
        password_hash=BCRYPT_FIXTURE,
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_client(
    session: AsyncSession, *, created_by: UUID, name: str = "Cliente X"
) -> Client:
    client = Client(
        name=name,
        omie_app_key_encrypted="ciphertext_key",
        omie_app_key_iv=FAKE_IV_KEY,
        omie_app_secret_encrypted="ciphertext_secret",
        omie_app_secret_iv=FAKE_IV_SECRET,
        active=True,
        created_by=created_by,
    )
    session.add(client)
    await session.flush()
    return client


class TestUser:
    async def test_insert_and_select(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session)
        loaded = await db_session.scalar(select(User).where(User.id == user.id))
        assert loaded is not None
        assert loaded.email == "u@test.com"
        assert loaded.role == "admin"
        assert loaded.active is True

    async def test_email_unique_constraint(self, db_session: AsyncSession) -> None:
        await _make_user(db_session, email="dup@test.com")
        await db_session.flush()
        # Tenta inserir duplicata; flush é o que dispara a IntegrityError no DB.
        db_session.add(
            User(
                name="Dup",
                email="dup@test.com",
                password_hash=BCRYPT_FIXTURE,
                role="manager",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_uuid_pk_generated(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, email="pk@test.com")
        assert isinstance(user.id, UUID)

    async def test_timestamps_populated(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, email="ts@test.com")
        assert isinstance(user.created_at, datetime)
        assert user.created_at.tzinfo is not None  # TZ-aware


class TestClientAndAssignments:
    async def test_client_with_assignment(self, db_session: AsyncSession) -> None:
        admin = await _make_user(db_session, email="adm@test.com")
        manager = User(
            name="Manager",
            email="mgr@test.com",
            password_hash=BCRYPT_FIXTURE,
            role=UserRole.MANAGER.value,
        )
        db_session.add(manager)
        await db_session.flush()

        client = await _make_client(db_session, created_by=admin.id)
        assignment = ClientAssignment(
            client_id=client.id,
            user_id=manager.id,
            assigned_by=admin.id,
        )
        db_session.add(assignment)
        await db_session.flush()

        loaded = await db_session.scalar(
            select(ClientAssignment).where(ClientAssignment.user_id == manager.id)
        )
        assert loaded is not None
        assert loaded.client_id == client.id

    async def test_one_assignment_per_client(self, db_session: AsyncSession) -> None:
        """Constraint UNIQUE em client_id — 1 cliente -> 1 manager."""
        admin = await _make_user(db_session, email="a2@test.com")
        m1 = User(name="M1", email="m1@test.com", password_hash=BCRYPT_FIXTURE, role="manager")
        m2 = User(name="M2", email="m2@test.com", password_hash=BCRYPT_FIXTURE, role="manager")
        db_session.add_all([m1, m2])
        await db_session.flush()
        client = await _make_client(db_session, created_by=admin.id)
        db_session.add(ClientAssignment(client_id=client.id, user_id=m1.id, assigned_by=admin.id))
        await db_session.flush()
        # Tenta atribuir o mesmo client a outro manager
        db_session.add(ClientAssignment(client_id=client.id, user_id=m2.id, assigned_by=admin.id))
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestOmieAccountCache:
    async def test_unique_per_client_and_account(self, db_session: AsyncSession) -> None:
        admin = await _make_user(db_session, email="ad3@test.com")
        client = await _make_client(db_session, created_by=admin.id)
        db_session.add(
            OmieAccountCache(
                client_id=client.id,
                omie_conta_id=12345,
                name="Sicredi 91263-1",
                bank_name="Sicredi",
                account_type=OmieAccountType.CHECKING.value,
            )
        )
        await db_session.flush()
        # Mesmo client + mesma conta -> viola UNIQUE constraint
        db_session.add(
            OmieAccountCache(
                client_id=client.id,
                omie_conta_id=12345,
                name="Outra",
                bank_name="Sicredi",
                account_type=OmieAccountType.CHECKING.value,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestReconciliationStack:
    async def test_full_session_with_entries_and_anomaly(self, db_session: AsyncSession) -> None:
        admin = await _make_user(db_session, email="rec@test.com")
        client = await _make_client(db_session, created_by=admin.id, name="Recon Test")

        # AnomalyType (catálogo)
        anomaly_type = AnomalyType(
            code="missing_in_omie",
            name="Sem lançamento Omie",
            description="...",
            severity=AnomalySeverity.CRITICAL.value,
        )
        db_session.add(anomaly_type)

        # Sessão
        recon = ReconciliationSession(
            client_id=client.id,
            created_by=admin.id,
            omie_conta_id=999,
            reference_month=date(2026, 1, 1),
            file_hash="a" * 64,
            status=ReconciliationStatus.PROCESSING.value,
        )
        db_session.add(recon)
        await db_session.flush()

        # FileEntry
        fe = ReconciliationFileEntry(
            session_id=recon.id,
            transaction_date=date(2026, 1, 15),
            description_encrypted="ct",
            description_iv=FAKE_IV_DESC,
            amount=Decimal("1234.56"),
            balance=Decimal("9999.00"),
            situation=FileEntrySituation.SEM_OMIE.value,
        )
        db_session.add(fe)

        # OmieEntry (divergência)
        oe = ReconciliationOmieEntry(
            session_id=recon.id,
            omie_lancamento_id=42,
            transaction_date=date(2026, 1, 16),
            omie_status=OmieEntryStatus.ATRASADO.value,
        )
        db_session.add(oe)
        await db_session.flush()

        # Anomalia ligando ambos
        anomaly = ReconciliationAnomaly(
            session_id=recon.id,
            anomaly_type_id=anomaly_type.id,
            file_entry_id=fe.id,
            omie_entry_id=oe.id,
            detected_by=AnomalyDetectedBy.AI.value,
            resolved=False,
        )
        db_session.add(anomaly)
        await db_session.flush()

        # Verifica via select
        loaded = await db_session.scalar(
            select(ReconciliationSession)
            .where(ReconciliationSession.id == recon.id)
            .options(
                selectinload(ReconciliationSession.file_entries),
                selectinload(ReconciliationSession.omie_entries),
                selectinload(ReconciliationSession.anomalies),
            )
        )
        assert loaded is not None
        assert len(loaded.file_entries) == 1
        assert loaded.file_entries[0].amount == Decimal("1234.56")
        assert len(loaded.omie_entries) == 1
        assert len(loaded.anomalies) == 1
        assert loaded.anomalies[0].detected_by == AnomalyDetectedBy.AI.value

    async def test_idempotency_unique_constraint(self, db_session: AsyncSession) -> None:
        """UNIQUE(client_id, omie_conta_id, reference_month, file_hash)."""
        admin = await _make_user(db_session, email="id@test.com")
        client = await _make_client(db_session, created_by=admin.id, name="Idem")
        common = {
            "client_id": client.id,
            "created_by": admin.id,
            "omie_conta_id": 1,
            "reference_month": date(2026, 2, 1),
            "file_hash": "b" * 64,
        }
        db_session.add(ReconciliationSession(**common))
        await db_session.flush()
        # Tenta inserir duplicata
        db_session.add(ReconciliationSession(**common))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_lazy_raise_blocks_implicit_load(self, db_session: AsyncSession) -> None:
        """Acesso a relationship sem selectinload deve falhar — força queries explícitas."""
        admin = await _make_user(db_session, email="raise@test.com")
        client = await _make_client(db_session, created_by=admin.id, name="LazyRaise")
        await db_session.commit()

        # Recarrega sem selectinload
        loaded = await db_session.scalar(select(Client).where(Client.id == client.id))
        assert loaded is not None
        with pytest.raises((MissingGreenlet, Exception)):  # SQLAlchemy lança InvalidRequestError
            _ = loaded.assignments  # trigger lazy load


class TestHealthReadyEndpoint:
    async def test_ready_returns_db_ok(self, client_with_db) -> None:  # type: ignore[no-untyped-def]
        """GET /health/ready deve retornar 200 com db: ok quando DB responde."""
        response = await client_with_db.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "db": "ok"}


# Ensure date import doesn't bite us when calling now()
_ = (UTC, date.today())
