"""Testes de integração — POST /api/v1/reconciliations + GET /status (S10).

Cobre BACK 8.1 e 8.6:
    - POST cria sessão `processing`, criptografa descrições com IV próprio
      por linha, retorna `session_id`.
    - Idempotência: 2º POST com mesma tupla → 409 DUPLICATE_FILE.
    - RBAC: admin OK; manager-da-carteira OK; manager fora → 404; sem
      auth → 401.
    - Validação: file_hash inválido → 400; reference_month normalizado
      pra dia 1; statement vazio → 400.
    - GET /status: shape correto, RBAC consistente.

NÃO testa o worker real — o ARQ enqueue é sobrescrito por um stub que
apenas registra a chamada. O job é testado em `test_reconciliation_job.py`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.core.search_index import compute_query_hmacs
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    FileEntrySituation,
    ReconciliationFileEntry,
    ReconciliationSession,
    User,
    UserRole,
)
from app.main import app as fastapi_app
from app.modules.reconciliations import routes as reconciliation_routes

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient


# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------

ADMIN_EMAIL = "create-recon-admin@hologram.com.br"
MANAGER_A_EMAIL = "create-recon-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "create-recon-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"


def _hex64(salt: str) -> str:
    """Gera um SHA-256 hex (64 chars) determinístico a partir de um salt."""
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
) -> User:
    user = User(
        name="Test",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
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


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _statement(transactions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Builder padrão de payload (ParsedStatement)."""
    if transactions is None:
        transactions = [
            {
                "date": "2026-04-02",
                "description": "Pagamento fornecedor X",
                "amount": "-500.00",
                "balance": "500.00",
            },
            {
                "date": "2026-04-15",
                "description": "Recebimento cliente Y",
                "amount": "734.56",
                "balance": None,
            },
        ]
    return {
        "bank_name": "Sicredi",
        "account_type": "checking",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "opening_balance": "1000.00",
        "closing_balance": "1234.56",
        "transactions": transactions,
    }


def _create_payload(
    *,
    client_id: UUID,
    omie_conta_id: int = 42,
    file_hash: str | None = None,
    reference_month: str = "2026-04-01",
    transactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "client_id": str(client_id),
        "omie_conta_id": omie_conta_id,
        "reference_month": reference_month,
        "date_tolerance_days": 3,
        "file_hash": file_hash or _hex64("default"),
        "statement": _statement(transactions),
    }


# Stub do agendamento: registra cada `session_id` agendado e NÃO dispara a
# BackgroundTask real (o TestClient do Starlette executaria o processamento de
# verdade após a resposta). Sobrescreve `_schedule_reconciliation_processing` —
# ponto único de override no endpoint.
@pytest.fixture
def stub_enqueue() -> Iterator[list[UUID]]:
    """Substitui o agendamento real por um stub. Devolve a lista de
    `session_id` agendados — testes podem assert nesta lista."""
    scheduled: list[UUID] = []

    def _stub(_background_tasks: object, session_id: UUID) -> None:
        scheduled.append(session_id)

    original = reconciliation_routes._schedule_reconciliation_processing  # type: ignore[attr-defined]
    reconciliation_routes._schedule_reconciliation_processing = _stub  # type: ignore[attr-defined]
    try:
        yield scheduled
    finally:
        reconciliation_routes._schedule_reconciliation_processing = original  # type: ignore[attr-defined]


# ----------------------------------------------------------------------
# POST /reconciliations — RBAC
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestCreateReconciliationRBAC:
    async def test_unauthenticated_returns_401(
        self, client_with_db: AsyncClient, stub_enqueue: list[UUID]
    ) -> None:
        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=uuid4()),
        )
        assert resp.status_code == 401
        assert stub_enqueue == []  # nada enfileirado

    async def test_admin_creates_session(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=cliente.id),
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["data"]["status"] == "processing"
        session_id = UUID(body["data"]["session_id"])
        assert stub_enqueue == [session_id]

    async def test_manager_in_portfolio_creates(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _login(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=cliente.id),
        )

        assert resp.status_code == 201, resp.text
        assert len(stub_enqueue) == 1

    async def test_manager_outside_portfolio_returns_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(db_session, name="X", creator=admin, manager=mgr_a)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=cliente_a.id),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"
        assert stub_enqueue == []

    async def test_inexistent_client_returns_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=uuid4()),
        )
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# POST /reconciliations — Persistência + crypto
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestCreateReconciliationPersistence:
    async def test_persists_session_and_entries_with_unique_iv_per_line(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        # 5 transações para validar IV único por linha mais robustamente.
        transactions = [
            {
                "date": f"2026-04-{day:02d}",
                "description": f"Mov {day}",
                "amount": "100.00",
                "balance": None,
            }
            for day in (2, 5, 10, 15, 20)
        ]
        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=cliente.id, transactions=transactions),
        )
        assert resp.status_code == 201, resp.text
        session_id = UUID(resp.json()["data"]["session_id"])

        # SQL direto: garantir que sessão veio com `processing` + 5 entries.
        sess = (
            await db_session.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        assert sess.status == "processing"
        assert sess.total_file_entries == 0  # ainda 0; worker vai popular depois

        rows = (
            (
                await db_session.execute(
                    select(ReconciliationFileEntry).where(
                        ReconciliationFileEntry.session_id == session_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 5

        # IV único por linha (CLAUDE.md §4.2).
        ivs = {r.description_iv for r in rows}
        assert len(ivs) == 5

        # Descrição persistida criptografada — descriptografando recupera o
        # plaintext exato.
        hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
        decrypted_descriptions = {
            decrypt(r.description_encrypted, r.description_iv, hex_key) for r in rows
        }
        assert decrypted_descriptions == {f"Mov {day}" for day in (2, 5, 10, 15, 20)}

        # Situation default
        assert all(r.situation == FileEntrySituation.SEM_OMIE.value for r in rows)
        # Amount preservado em Decimal
        assert all(r.amount == Decimal("100.00") for r in rows)

        # S16 — blind index gravado em paralelo à description criptografada.
        # Cada linha tem um HMAC do(s) token(s) "mov" + "<day>", então buscar
        # por "mov" deve bater contra todas as 5 linhas via SQL puro.
        assert all(r.description_search_hmac is not None for r in rows)
        hex_blind_key = get_settings().SEARCH_BLIND_INDEX_KEY.get_secret_value()
        mov_hmac = compute_query_hmacs("mov", hex_blind_key)[0]
        assert all(f" {mov_hmac} " in (r.description_search_hmac or "") for r in rows)

    async def test_persists_period_start_and_end_from_statement(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """`period_start/period_end` agora persistem em `reconciliation_sessions`.

        Antes da migration `4a2f9e8b1c3d` o review service usava
        `[reference_month, last_day_of_month]` como aproximação — quebrava
        em extratos com período fora do mês (15/04→14/05), faturas de
        cartão e lançamentos nos primeiros dias do mês seguinte.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        # Statement com período "quebrado" — não alinha com o mês de referência.
        broken_statement = _statement()
        broken_statement["period_start"] = "2026-04-15"
        broken_statement["period_end"] = "2026-05-14"
        payload = {
            "client_id": str(cliente.id),
            "omie_conta_id": 42,
            "reference_month": "2026-04-01",
            "date_tolerance_days": 3,
            "file_hash": _hex64("period-persist"),
            "statement": broken_statement,
        }
        resp = await client_with_db.post("/api/v1/reconciliations", json=payload)
        assert resp.status_code == 201, resp.text
        session_id = UUID(resp.json()["data"]["session_id"])

        sess = (
            await db_session.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        assert sess.period_start == date(2026, 4, 15)
        assert sess.period_end == date(2026, 5, 14)

    async def test_reference_month_normalized_to_first_day(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Front pode mandar `2026-04-15` — temos que normalizar pra dia 1
        antes de armazenar (idempotência depende disso)."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_payload(client_id=cliente.id, reference_month="2026-04-15"),
        )
        assert resp.status_code == 201
        session_id = UUID(resp.json()["data"]["session_id"])

        sess = (
            await db_session.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        assert sess.reference_month == date(2026, 4, 1)


# ----------------------------------------------------------------------
# POST /reconciliations — Idempotência (409 DUPLICATE_FILE)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestCreateReconciliationIdempotency:
    async def test_duplicate_returns_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Mesma tupla (client, conta, mês, hash) → 409 DUPLICATE_FILE."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        file_hash = _hex64("dup")
        payload = _create_payload(client_id=cliente.id, file_hash=file_hash)

        # 1º POST: 201
        resp1 = await client_with_db.post("/api/v1/reconciliations", json=payload)
        assert resp1.status_code == 201, resp1.text

        # 2º POST com mesma tupla: 409
        resp2 = await client_with_db.post("/api/v1/reconciliations", json=payload)
        assert resp2.status_code == 409, resp2.text
        assert resp2.json()["error"]["code"] == "DUPLICATE_FILE"

        # Apenas o 1º foi enfileirado — o 2º falha antes do enqueue.
        assert len(stub_enqueue) == 1


# ----------------------------------------------------------------------
# POST /reconciliations — Validação
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestCreateReconciliationValidation:
    async def test_invalid_file_hash_returns_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        payload = _create_payload(client_id=cliente.id, file_hash="not-a-sha256")
        resp = await client_with_db.post("/api/v1/reconciliations", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert stub_enqueue == []

    async def test_empty_transactions_returns_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        payload = _create_payload(client_id=cliente.id, transactions=[])
        resp = await client_with_db.post("/api/v1/reconciliations", json=payload)
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# GET /reconciliations/{id}/status (BACK 8.6)
# ----------------------------------------------------------------------


async def _seed_session(
    session: AsyncSession,
    *,
    cliente: Client,
    creator: User,
    status_value: str = "processing",
    error_message: str | None = None,
    file_hash: str | None = None,
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=cliente.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=3,
        file_hash=file_hash or _hex64("status-test"),
        status=status_value,
        error_message=error_message,
        balance_start=Decimal("0.00"),
        processed_at=datetime.now(UTC) - timedelta(seconds=1),
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    return sess


@pytest.mark.integration
class TestSessionStatusEndpoint:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get(f"/api/v1/reconciliations/{uuid4()}/status")
        assert resp.status_code == 401

    async def test_inexistent_session_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{uuid4()}/status")
        assert resp.status_code == 404

    async def test_admin_reads_status(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        sess = await _seed_session(db_session, cliente=cliente, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/status")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "processing"
        assert body["session_id"] == str(sess.id)
        assert body["error_message"] is None
        assert body["conciliated_count"] == 0

    async def test_status_error_includes_message(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        sess = await _seed_session(
            db_session,
            cliente=cliente,
            creator=admin,
            status_value="error",
            error_message="Credenciais Omie inválidas. Verifique as configurações do cliente.",
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/status")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["status"] == "error"
        assert "Credenciais" in body["error_message"]

    async def test_manager_outside_portfolio_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(db_session, name="X", creator=admin, manager=mgr_a)
        sess = await _seed_session(db_session, cliente=cliente_a, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}/status")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# S11 — GET /reconciliations/{id}  (header da Tela de Revisão)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestSessionDetailEndpoint:
    """RBAC IDÊNTICO ao /status — manager fora da carteira recebe 404, não 403
    (CLAUDE.md §3.11). Substitui o scan O(N) que o front fazia via histórico
    paginado do cliente.
    """

    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get(f"/api/v1/reconciliations/{uuid4()}")
        assert resp.status_code == 401

    async def test_inexistent_session_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{uuid4()}")
        assert resp.status_code == 404

    async def test_admin_reads_detail(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        sess = await _seed_session(db_session, cliente=cliente, creator=admin)
        # Popula contadores e totais — devem aparecer no payload.
        sess.total_file_entries = 42
        sess.conciliated_count = 30
        sess.sem_omie_count = 8
        sess.omie_sem_arquivo_count = 4
        sess.anomaly_count = 2
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["session_id"] == str(sess.id)
        assert body["client_id"] == str(cliente.id)
        assert body["omie_conta_id"] == 42
        assert body["reference_month"] == "2026-04-01"
        assert body["status"] == "processing"
        assert body["total_file_entries"] == 42
        assert body["conciliated_count"] == 30
        assert body["sem_omie_count"] == 8
        assert body["omie_sem_arquivo_count"] == 4
        assert body["anomaly_count"] == 2

    async def test_manager_in_portfolio_reads_detail(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        sess = await _seed_session(db_session, cliente=cliente, creator=admin)
        await _login(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["session_id"] == str(sess.id)

    async def test_manager_outside_portfolio_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(db_session, name="X", creator=admin, manager=mgr_a)
        sess = await _seed_session(db_session, cliente=cliente_a, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# POST /reconciliations/{id}/reprocess  (S11.fix — retry de sessão em erro)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestReprocessReconciliation:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.post(f"/api/v1/reconciliations/{uuid4()}/reprocess")
        assert resp.status_code == 401

    async def test_inexistent_session_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession, stub_enqueue: list[UUID]
    ) -> None:
        del stub_enqueue
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{uuid4()}/reprocess")
        assert resp.status_code == 404

    async def test_reprocess_resets_error_session_and_enqueues(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Caminho feliz: sessão em error vira processing + job enfileirado."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(
            db_session,
            cliente=cliente,
            creator=admin,
            status_value="error",
            error_message="O Omie não respondeu no tempo esperado. Tente novamente.",
            file_hash=_hex64("reprocess-happy"),
        )
        # Polui contadores pra confirmar que o reset zera.
        sess.conciliated_count = 5
        sess.anomaly_count = 2
        await db_session.flush()

        await _login(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/reprocess")
        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["session_id"] == str(sess.id)
        assert body["status"] == "processing"

        # Estado da sessão após o reset.
        await db_session.refresh(sess)
        assert sess.status == "processing"
        assert sess.error_message is None
        assert sess.processed_at is None
        assert sess.conciliated_count == 0
        assert sess.anomaly_count == 0

        # Job foi enfileirado.
        assert sess.id in stub_enqueue

    async def test_reprocess_non_error_session_returns_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Sessão em status diferente de error não pode ser reprocessada."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Y", creator=admin)
        sess = await _seed_session(
            db_session,
            cliente=cliente,
            creator=admin,
            status_value="reviewing",
            file_hash=_hex64("reprocess-conflict"),
        )
        await _login(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/reprocess")
        assert resp.status_code == 409, resp.text
        assert "estado de erro" in resp.json()["error"]["userMessage"].lower()
        # Não enfileira.
        assert stub_enqueue == []

    async def test_manager_outside_portfolio_returns_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        del stub_enqueue
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(db_session, name="A", creator=admin, manager=mgr_a)
        sess = await _seed_session(
            db_session,
            cliente=cliente_a,
            creator=admin,
            status_value="error",
            error_message="erro",
            file_hash=_hex64("reprocess-rbac"),
        )
        await _login(client_with_db, MANAGER_B_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/reprocess")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# POST /reconciliations/{id}/discard  (S11.fix — soft-delete de erro)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestDiscardReconciliation:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.post(f"/api/v1/reconciliations/{uuid4()}/discard")
        assert resp.status_code == 401

    async def test_inexistent_session_returns_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{uuid4()}/discard")
        assert resp.status_code == 404

    async def test_discard_error_session_marks_deleted_at(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Caminho feliz: sessão em error → 204; deleted_at populado."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        sess = await _seed_session(
            db_session,
            cliente=cliente,
            creator=admin,
            status_value="error",
            error_message="O Omie não respondeu",
            file_hash=_hex64("discard-happy"),
        )
        await _login(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/discard")
        assert resp.status_code == 204
        assert resp.content == b""

        await db_session.refresh(sess)
        assert sess.deleted_at is not None
        # Status segue 'error' — soft-delete não muda o histórico.
        assert sess.status == "error"

    async def test_discard_non_error_session_returns_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Y", creator=admin)
        sess = await _seed_session(
            db_session,
            cliente=cliente,
            creator=admin,
            status_value="reviewing",
            file_hash=_hex64("discard-reviewing"),
        )
        await _login(client_with_db, ADMIN_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/discard")
        assert resp.status_code == 409, resp.text

        # Não toca o deleted_at em sessão não-error.
        await db_session.refresh(sess)
        assert sess.deleted_at is None

    async def test_discard_releases_idempotency_unique(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Depois de descartar, criar nova sessão com mesma tupla idempotente
        NÃO retorna 409 DUPLICATE_FILE — o UNIQUE no banco é parcial
        (WHERE deleted_at IS NULL)."""
        del stub_enqueue
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Z", creator=admin)
        same_hash = _hex64("discard-reuse")
        sess = await _seed_session(
            db_session,
            cliente=cliente,
            creator=admin,
            status_value="error",
            error_message="erro",
            file_hash=same_hash,
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # 1. Descarta a sessão em erro.
        resp_discard = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/discard")
        assert resp_discard.status_code == 204

        # 2. Tenta criar nova sessão com MESMA tupla idempotente. Antes do
        #    soft-delete + índice parcial, isso retornaria 409 DUPLICATE_FILE.
        payload = _create_payload(
            client_id=cliente.id,
            omie_conta_id=42,
            reference_month="2026-04-01",
            file_hash=same_hash,
        )
        resp_create = await client_with_db.post("/api/v1/reconciliations", json=payload)
        assert resp_create.status_code == 201, resp_create.text

    async def test_manager_outside_portfolio_returns_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(db_session, name="A", creator=admin, manager=mgr_a)
        sess = await _seed_session(
            db_session,
            cliente=cliente_a,
            creator=admin,
            status_value="error",
            error_message="erro",
            file_hash=_hex64("discard-rbac"),
        )
        await _login(client_with_db, MANAGER_B_EMAIL)
        resp = await client_with_db.post(f"/api/v1/reconciliations/{sess.id}/discard")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Garantia de fixture do app: as rotas estão registradas no FastAPI app.
# (sanity check para evitar falsos passes se o include_router for esquecido)
# ----------------------------------------------------------------------


def test_routes_are_registered_in_app() -> None:
    paths = {route.path for route in fastapi_app.routes}  # type: ignore[attr-defined]
    assert "/api/v1/reconciliations" in paths
    assert "/api/v1/reconciliations/{session_id}/status" in paths
    assert "/api/v1/reconciliations/{session_id}" in paths
    assert "/api/v1/reconciliations/{session_id}/reprocess" in paths
    assert "/api/v1/reconciliations/{session_id}/discard" in paths
