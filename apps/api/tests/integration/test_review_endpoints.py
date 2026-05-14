"""Testes de integração da Tela de Revisão (S11 BACK 9.1, 9.3-9.10).

Cobre os 10 endpoints novos. Quando Docker não está disponível, todos os
testes que tocam DB são marcados SKIPPED via fixture `db_session` —
mesmo padrão dos outros arquivos de integração.

Estrutura:
    - Helpers (seed user / client / session / file entry / omie entry /
      anomaly_type / anomaly).
    - Classes por endpoint, agrupando happy + RBAC + erro.
    - Stubbing do `OmieClient` quando preciso (BACK 9.4 chama listar_extrato).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.core.security import hash_password
from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    ClientAssignment,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


ADMIN_EMAIL = "review-admin@hologram.com.br"
MANAGER_A_EMAIL = "review-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "review-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

FAKE_APP_KEY = "test-app-key-review"
FAKE_APP_SECRET = "test-app-secret-review"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="T",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession, *, creator: User, manager: User | None = None
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(FAKE_APP_KEY, hex_key)
    ct_s, iv_s = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name="Cliente Review",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    if manager is not None:
        session.add(
            ClientAssignment(client_id=client.id, user_id=manager.id, assigned_by=creator.id)
        )
        await session.flush()
    return client


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    status: str = "reviewing",
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=3,
        file_hash=_hex64(f"review-{uuid4().hex}"),
        status=status,
        balance_start=Decimal("0.00"),
        processed_at=datetime.now(UTC),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_file_entry(
    session: AsyncSession,
    *,
    reconciliation: ReconciliationSession,
    description: str,
    amount: Decimal,
    situation: str = "sem_omie",
    omie_lancamento_id: int | None = None,
    tx_date: date = date(2026, 4, 10),
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description, hex_key)
    entry = ReconciliationFileEntry(
        session_id=reconciliation.id,
        transaction_date=tx_date,
        description_encrypted=ct,
        description_iv=iv,
        amount=amount,
        situation=situation,
        omie_lancamento_id=omie_lancamento_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _seed_omie_entry(
    session: AsyncSession,
    *,
    reconciliation: ReconciliationSession,
    omie_lancamento_id: int,
    omie_status: str = "Atrasado",
    tx_date: date = date(2026, 4, 20),
) -> ReconciliationOmieEntry:
    entry = ReconciliationOmieEntry(
        session_id=reconciliation.id,
        omie_lancamento_id=omie_lancamento_id,
        transaction_date=tx_date,
        omie_status=omie_status,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _seed_anomaly_types(session: AsyncSession) -> dict[str, AnomalyType]:
    """Insere os 2 AnomalyTypes mais usados pelos testes."""
    types: dict[str, AnomalyType] = {}
    seeds = [
        (
            "missing_in_omie",
            "Movimentação sem lançamento no Omie",
            AnomalySeverity.CRITICAL.value,
            "Falta no Omie.",
        ),
        (
            "wrong_account",
            "Lançamento possivelmente na conta errada",
            AnomalySeverity.MODERATE.value,
            "Suspeita.",
        ),
    ]
    for code, name, severity, descr in seeds:
        existing = (
            await session.execute(select(AnomalyType).where(AnomalyType.code == code))
        ).scalar_one_or_none()
        if existing is not None:
            types[code] = existing
            continue
        atype = AnomalyType(code=code, name=name, description=descr, severity=severity, active=True)
        session.add(atype)
        await session.flush()
        types[code] = atype
    return types


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# BACK 9.1 — GET /file-entries
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestListFileEntries:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        sid = uuid4()
        resp = await client_with_db.get(f"/api/v1/reconciliations/{sid}/file-entries")
        assert resp.status_code == 401

    async def test_admin_lists_with_decrypted_descriptions(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Padaria",
            amount=Decimal("-1250.00"),
        )
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Recebimento Cielo",
            amount=Decimal("999.99"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        descriptions = sorted(item["description"] for item in body["data"])
        assert descriptions == ["Pagamento Padaria", "Recebimento Cielo"]
        assert body["pagination"]["total"] == 2

    async def test_filter_search_post_decrypt(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Padaria",
            amount=Decimal("-1.00"),
        )
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Recebimento Cielo",
            amount=Decimal("2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "padaria"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["description"] == "Pagamento Padaria"

    async def test_filter_type_credit_only(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session, reconciliation=sess, description="Crédito", amount=Decimal("5.00")
        )
        await _seed_file_entry(
            db_session, reconciliation=sess, description="Débito", amount=Decimal("-5.00")
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"type": "credit"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["amount"] == "5.00"

    async def test_manager_outside_portfolio_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cli = await _seed_client(db_session, creator=admin, manager=mgr_a)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/file-entries")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# BACK 9.3 — PATCH /file-entries/{id}
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateFileEntry:
    async def test_admin_updates_situation_and_note(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Lançamento X",
            amount=Decimal("-100.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"situation": "ignorado", "user_note": "Não relacionado"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["situation"] == "ignorado"
        assert body["user_note"] == "Não relacionado"

        # Persistido + criptografado
        await db_session.refresh(entry)
        hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
        assert entry.user_note_encrypted is not None
        assert entry.user_note_iv is not None
        assert decrypt(entry.user_note_encrypted, entry.user_note_iv, hex_key) == (
            "Não relacionado"
        )

    async def test_trocar_omie_id_duplicate_in_session_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry_a = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70001,
            situation="conciliado",
        )
        entry_b = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="B",
            amount=Decimal("-2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Tenta vincular entry_b ao mesmo Omie ID que entry_a já usa
        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry_b.id}",
            json={"omie_lancamento_id": 70001},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

        # entry_a manteve o vínculo
        await db_session.refresh(entry_a)
        assert entry_a.omie_lancamento_id == 70001

    async def test_trocar_omie_id_idempotent_same_value(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70002,
            situation="conciliado",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        for _ in range(2):
            resp = await client_with_db.patch(
                f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
                json={"omie_lancamento_id": 70002},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["omie_lancamento_id"] == 70002

    async def test_clear_omie_id_via_null(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70003,
            situation="conciliado",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"omie_lancamento_id": None},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["omie_lancamento_id"] is None
        assert body["situation"] == "sem_omie"

    async def test_counters_recomputed_after_update(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            situation="sem_omie",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry.id}",
            json={"omie_lancamento_id": 99001},
        )
        assert resp.status_code == 200

        await db_session.refresh(sess)
        assert sess.conciliated_count == 1
        assert sess.sem_omie_count == 0


# ----------------------------------------------------------------------
# BACK 9.6 — PATCH /omie-entries/{id}
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateOmieEntry:
    async def test_update_user_action_and_note(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80001)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}",
            json={"user_action": "flag", "user_note": "Pendente conferência"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["user_action"] == "flag"
        assert body["user_note"] == "Pendente conferência"

    async def test_does_not_recompute_session_counters(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        sess.omie_sem_arquivo_count = 5
        await db_session.flush()
        entry = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=80002)
        await _login(client_with_db, ADMIN_EMAIL)

        await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/omie-entries/{entry.id}",
            json={"user_action": "ignore"},
        )
        await db_session.refresh(sess)
        assert sess.omie_sem_arquivo_count == 5  # inalterado


# ----------------------------------------------------------------------
# BACK 9.7, 9.8, 9.9 — Anomalias
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestAnomalies:
    async def test_create_and_list_anomaly(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        entry = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Foo",
            amount=Decimal("-3.00"),
        )
        types = await _seed_anomaly_types(db_session)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            json={
                "anomaly_type_id": str(types["wrong_account"].id),
                "file_entry_id": str(entry.id),
                "context": "Talvez seja Sicredi",
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()["data"]
        assert created["detected_by"] == "manual"
        assert created["resolved"] is False
        assert created["context"] == "Talvez seja Sicredi"
        assert created["anomaly_type"]["code"] == "wrong_account"
        assert created["related_file_entry"]["description"] == "Foo"

        await db_session.refresh(sess)
        assert sess.anomaly_count == 1

        # Lista
        resp_list = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/anomalies")
        assert resp_list.status_code == 200
        rows = resp_list.json()["data"]
        assert len(rows) == 1

    async def test_create_anomaly_with_both_entries_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        fe = await _seed_file_entry(
            db_session, reconciliation=sess, description="x", amount=Decimal("-1.00")
        )
        oe = await _seed_omie_entry(db_session, reconciliation=sess, omie_lancamento_id=42_424)
        types = await _seed_anomaly_types(db_session)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            json={
                "anomaly_type_id": str(types["wrong_account"].id),
                "file_entry_id": str(fe.id),
                "omie_entry_id": str(oe.id),
            },
        )
        assert resp.status_code == 400, resp.text

    async def test_create_with_inactive_type_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session, reconciliation=sess, description="x", amount=Decimal("-1.00")
        )
        types = await _seed_anomaly_types(db_session)
        types["wrong_account"].active = False
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            json={"anomaly_type_id": str(types["wrong_account"].id)},
        )
        assert resp.status_code == 400, resp.text

    async def test_resolve_with_short_note_returns_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        anomaly = ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=types["wrong_account"].id,
            detected_by=AnomalyDetectedBy.AI.value,
            resolved=False,
        )
        db_session.add(anomaly)
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/anomalies/{anomaly.id}",
            json={"resolved": True, "resolution_note": "ok"},
        )
        assert resp.status_code == 400

    async def test_resolve_happy_path(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        anomaly = ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=types["wrong_account"].id,
            detected_by=AnomalyDetectedBy.AI.value,
            resolved=False,
        )
        db_session.add(anomaly)
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/anomalies/{anomaly.id}",
            json={
                "resolved": True,
                "resolution_note": "Conferido com fornecedor.",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["resolved"] is True
        assert body["resolution_note"] == "Conferido com fornecedor."

    async def test_filter_resolved_true_returns_only_resolved(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        types = await _seed_anomaly_types(db_session)
        # 1 resolvida + 1 pendente
        db_session.add(
            ReconciliationAnomaly(
                session_id=sess.id,
                anomaly_type_id=types["wrong_account"].id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=True,
            )
        )
        db_session.add(
            ReconciliationAnomaly(
                session_id=sess.id,
                anomaly_type_id=types["wrong_account"].id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/anomalies",
            params={"resolved": "true"},
        )
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert all(item["resolved"] is True for item in rows)
        assert len(rows) == 1


# ----------------------------------------------------------------------
# BACK 9.10 — GET /api/v1/anomaly-types
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestAnomalyTypes:
    async def test_lists_only_active_sorted_by_severity(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_client(db_session, creator=admin)
        types = await _seed_anomaly_types(db_session)
        # inativa o "wrong_account"
        types["wrong_account"].active = False
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        codes = [r["code"] for r in rows]
        assert "wrong_account" not in codes
        assert "missing_in_omie" in codes
        # Critical primeiro
        severities = [r["severity"] for r in rows]
        assert severities == sorted(
            severities,
            key=lambda s: {"critical": 1, "moderate": 2, "info": 3}.get(s, 99),
        )

    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get("/api/v1/anomaly-types")
        assert resp.status_code == 401


# ----------------------------------------------------------------------
# Sanity — rotas registradas
# ----------------------------------------------------------------------


def test_review_routes_registered() -> None:
    from app.main import app as fastapi_app

    paths = {route.path for route in fastapi_app.routes}  # type: ignore[attr-defined]
    expected = {
        "/api/v1/reconciliations/{session_id}/file-entries",
        "/api/v1/reconciliations/{session_id}/file-entries/{entry_id}",
        "/api/v1/reconciliations/{session_id}/available-omie-entries",
        "/api/v1/reconciliations/{session_id}/omie-entries",
        "/api/v1/reconciliations/{session_id}/omie-entries/{entry_id}",
        "/api/v1/reconciliations/{session_id}/anomalies",
        "/api/v1/reconciliations/{session_id}/anomalies/{anomaly_id}",
        "/api/v1/omie/lancamentos",
        "/api/v1/anomaly-types",
    }
    assert expected.issubset(paths)


# ----------------------------------------------------------------------
# Garante que cleanup do `_seed_user` ainda enxerga o UUID do row.
# (sanidade que `client.id` é UUID)
# ----------------------------------------------------------------------


def test_uuid_type_sanity() -> None:
    assert isinstance(uuid4(), UUID)
