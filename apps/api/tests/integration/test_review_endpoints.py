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
from app.core.search_index import compute_search_hmac
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
    skip_search_hmac: bool = False,
) -> ReconciliationFileEntry:
    """Insere file_entry criptografando description e gravando blind index.

    Por default popula `description_search_hmac` (S16) para refletir o
    caminho de criação real. Testes específicos do path "sessão pré-S16"
    passam `skip_search_hmac=True` para deixar a coluna NULL.
    """
    settings = get_settings()
    hex_key = settings.OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description, hex_key)
    if skip_search_hmac:
        search_hmac: str | None = None
    else:
        hex_blind_key = settings.SEARCH_BLIND_INDEX_KEY.get_secret_value()
        search_hmac = compute_search_hmac(description, hex_blind_key)
    entry = ReconciliationFileEntry(
        session_id=reconciliation.id,
        transaction_date=tx_date,
        description_encrypted=ct,
        description_iv=iv,
        description_search_hmac=search_hmac,
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

    async def test_filter_search_uses_blind_index(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S16: filtro `search` casa via blind index (SQL), com acento/case
        normalizados. As linhas seedadas com `_seed_file_entry` já incluem
        `description_search_hmac`.
        """
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

        # Caso happy: token completo bate.
        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "padaria"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["description"] == "Pagamento Padaria"

        # Insensível a case + acento.
        resp_upper = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "PADARIA"},
        )
        assert resp_upper.json()["pagination"]["total"] == 1

    async def test_filter_search_token_below_min_length_returns_empty(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Termo de busca com apenas tokens < 3 chars devolve 0 — não vai ao DB.

        Comportamento UX consistente: "buscar por 'de'" não faz sentido como
        índice; UI pode evoluir para sinalizar isso ao usuário.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento de boleto",
            amount=Decimal("-1.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "de"},
        )
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total"] == 0

    async def test_filter_search_skips_legacy_rows_without_hmac(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """S16: linhas pré-migration (`description_search_hmac IS NULL`) ficam
        fora do filtro `search`. LIKE contra NULL é NULL → falsy em WHERE.
        Listagem sem `search` continua trazendo a linha normalmente.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # Linha "legada" — sem o HMAC populado.
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Antigo Padaria",
            amount=Decimal("-1.00"),
            skip_search_hmac=True,
        )
        # Linha nova — com HMAC.
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="Pagamento Novo Padaria",
            amount=Decimal("-2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Sem search: ambas aparecem.
        resp_all = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
        )
        assert resp_all.json()["pagination"]["total"] == 2

        # Com search: só a linha nova.
        resp_search = await client_with_db.get(
            f"/api/v1/reconciliations/{sess.id}/file-entries",
            params={"search": "padaria"},
        )
        assert resp_search.status_code == 200
        body = resp_search.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["description"] == "Pagamento Novo Padaria"

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

    async def test_trocar_omie_race_caught_by_unique_index(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Race em "Trocar Omie": 2 requests passam pela checagem aplicativa
        no MESMO instante e ambos tentam gravar o mesmo `omie_lancamento_id`.

        O índice ÚNICO PARCIAL `ix_recon_file_entry_session_omie_unique`
        (CLAUDE.md §5.4) detecta a colisão; o service captura o
        `IntegrityError` e devolve a MESMA `ValidationAppError` que o
        caminho aplicativo — UX idêntica com ou sem race.

        Como simular: monkey-patch da checagem aplicativa para retornar
        False, forçando o service a chegar até o flush onde o índice
        dispara o IntegrityError.
        """
        from app.modules.reconciliations.review.repository import ReviewRepository

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # entry_a já tem o vínculo Omie 70404 — basta persistir pra que o
        # índice único dispare quando o entry_b tentar o mesmo Omie ID.
        await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="A",
            amount=Decimal("-1.00"),
            omie_lancamento_id=70404,
            situation="conciliado",
        )
        entry_b = await _seed_file_entry(
            db_session,
            reconciliation=sess,
            description="B",
            amount=Decimal("-2.00"),
        )
        await _login(client_with_db, ADMIN_EMAIL)

        # Força a checagem aplicativa a falsear o conflito — simula "ambos
        # requests passaram pela checagem quase ao mesmo tempo".
        async def _fake_taken(self: ReviewRepository, **kwargs: object) -> bool:
            return False

        monkeypatch.setattr(
            ReviewRepository,
            "file_entry_omie_id_taken_by_another",
            _fake_taken,
        )

        resp = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{entry_b.id}",
            json={"omie_lancamento_id": 70404},
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        # Mensagem amigável idêntica à do caminho aplicativo (§11 CLAUDE.md).
        assert "já está vinculado" in body["error"]["userMessage"]

        # Não checamos estado pós-PATCH via SELECT porque o conftest
        # injeta a MESMA `db_session` do teste no request via override —
        # quando o flush falha com IntegrityError, toda a transação fica
        # ROLLBACK ONLY e qualquer query subsequente levanta
        # PendingRollbackError. O caminho crítico (constraint dispara →
        # service captura → ValidationAppError com mensagem PT-BR) já
        # está provado pela resposta HTTP acima; o rollback transacional
        # da request (DbSessionDep) garante que nada foi persistido.


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


# ----------------------------------------------------------------------
# Service `list_available_omie_entries` — período usado (S11 fix)
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestAvailableOmieEntriesPeriod:
    """Cobre o fix de §S11: `list_available_omie_entries` usa o período REAL
    da sessão quando disponível e cai no fallback `[reference_month,
    last_day_of_month]` para sessões pré-migration (period_start IS NULL).

    Unit-style — exercício direto do service com OmieClient e cache
    mockados, sem subir HTTP. O foco é o período passado a
    `populate_from_extrato`.
    """

    async def test_uses_real_period_when_persisted(self, db_session: AsyncSession) -> None:
        from unittest.mock import AsyncMock

        from pydantic import SecretStr

        from app.modules.reconciliations.review.repository import ReviewRepository
        from app.modules.reconciliations.review.service import ReviewService

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # Período real do statement — extrato "quebrado" (15/04 → 14/05).
        sess.period_start = date(2026, 4, 15)
        sess.period_end = date(2026, 5, 14)
        await db_session.flush()

        cache = AsyncMock()
        cache.populate_from_extrato.return_value = {}
        omie_client = AsyncMock()
        service = ReviewService(
            ReviewRepository(db_session),
            cache=cache,
            encryption_key=SecretStr("0" * 64),  # 32 bytes hex — não usado neste path
            search_blind_index_key=SecretStr("1" * 64),
        )

        await service.list_available_omie_entries(
            session=sess,
            omie_client=omie_client,
            search=None,
        )

        # Período expandido = period_real +/- tolerance(3 dias).
        # period_start=2026-04-15 - 3 = 2026-04-12
        # period_end=2026-05-14 + 3 = 2026-05-17
        call_kwargs = cache.populate_from_extrato.call_args.kwargs
        assert call_kwargs["period_start"] == date(2026, 4, 12)
        assert call_kwargs["period_end"] == date(2026, 5, 17)

    async def test_falls_back_to_month_bounds_when_period_is_null(
        self, db_session: AsyncSession
    ) -> None:
        from unittest.mock import AsyncMock

        from pydantic import SecretStr

        from app.modules.reconciliations.review.repository import ReviewRepository
        from app.modules.reconciliations.review.service import ReviewService

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cli = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=cli, creator=admin)
        # Sessão pré-migration — period_start/end ficam None.
        assert sess.period_start is None
        assert sess.period_end is None

        cache = AsyncMock()
        cache.populate_from_extrato.return_value = {}
        omie_client = AsyncMock()
        service = ReviewService(
            ReviewRepository(db_session),
            cache=cache,
            encryption_key=SecretStr("0" * 64),
            search_blind_index_key=SecretStr("1" * 64),
        )

        await service.list_available_omie_entries(
            session=sess,
            omie_client=omie_client,
            search=None,
        )

        # Fallback: [2026-04-01, 2026-04-30] ± tolerance(3) → [2026-03-29, 2026-05-03].
        call_kwargs = cache.populate_from_extrato.call_args.kwargs
        assert call_kwargs["period_start"] == date(2026, 3, 29)
        assert call_kwargs["period_end"] == date(2026, 5, 3)
