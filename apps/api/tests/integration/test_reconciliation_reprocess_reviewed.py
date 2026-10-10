"""Reprocessar uma conciliação CONCLUÍDA — `POST /reconciliations/{id}/reprocess` (86e3n70q9).

Origem: reunião de 08/10/2026. Depois de lançar no Omie as compras que faltavam,
a conciliação não se atualizava ("tem que excluir e fazer de novo"). Agora a
sessão em `reviewing`/`done` volta a `processing` e o cruzamento roda de novo.

O que estes testes travam é o que NÃO pode acontecer:

- a intenção de lançamento (`reconciliation_omie_postings`, §4.11) não é tocada:
  a linha lançada continua lançada, e reenviá-la é bloqueado (`ja_lancada`),
  nunca um segundo POST no Omie;
- a revisão recomeça de verdade: notas, ações, anomalias resolvidas e vereditos
  saem; as linhas do arquivo (o parse pago) ficam;
- `processing` continua 409 (não se agenda processamento duplicado), e cliente
  encerrado também (§4.12).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    FileEntryUserAction,
    OmiePostingStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationOmiePosting,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX
from app.modules.reconciliations import routes as reconciliation_routes
from app.modules.reconciliations.processing.anomalies import ANOMALY_CODE_MISSING_IN_OMIE

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Reprocessa#1"
REPROCESS_URL = "/api/v1/reconciliations/{session_id}/reprocess"
POSTING_URL = "/api/v1/reconciliations/{session_id}/omie-postings"


# ----------------------------------------------------------------------
# Seeds
# ----------------------------------------------------------------------


async def _seed_user(session: AsyncSession) -> User:
    user = User(
        name="Reprocessa",
        email=f"reproc-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(f"{FAKE_DEMO_KEY_PREFIX}{uuid4().hex[:8]}", hex_key)
    ct_s, iv_s = encrypt("demo-secret", hex_key)
    client = Client(
        name=f"Cli {uuid4().hex[:6]}",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    status: str = ReconciliationStatus.REVIEWING.value,
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=900_000_003,
        account_type=SessionAccountType.CREDIT_CARD.value,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=None,
        status=status,
        conciliated_count=1,
        sem_omie_count=1,
        anomaly_count=1,
        qualification_used_glossary=True,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_entry(
    session: AsyncSession,
    *,
    sess: ReconciliationSession,
    amount: str,
    situation: str = FileEntrySituation.SEM_OMIE.value,
    omie_lancamento_id: int | None = None,
    with_note: bool = False,
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt("CAFETERIA DO LARGO", hex_key)
    note_ct, note_iv = encrypt("conferido com o cliente", hex_key) if with_note else (None, None)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=date(2026, 4, 15),
        description_encrypted=ct,
        description_iv=iv,
        amount=Decimal(amount),
        situation=situation,
        omie_lancamento_id=omie_lancamento_id,
        user_action=FileEntryUserAction.CONFIRM.value if with_note else None,
        user_note_encrypted=note_ct,
        user_note_iv=note_iv,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _missing_in_omie_type(session: AsyncSession) -> AnomalyType:
    anomaly_type = (
        await session.execute(
            select(AnomalyType).where(AnomalyType.code == ANOMALY_CODE_MISSING_IN_OMIE)
        )
    ).scalar_one_or_none()
    if anomaly_type is None:
        anomaly_type = AnomalyType(
            code=ANOMALY_CODE_MISSING_IN_OMIE,
            name="Sem lançamento no Omie",
            description="Linha existe no arquivo e não existe no Omie.",
            severity=AnomalySeverity.CRITICAL.value,
            active=True,
        )
        session.add(anomaly_type)
        await session.flush()
    return anomaly_type


@pytest.fixture
def stub_enqueue() -> Iterator[list[UUID]]:
    """Não dispara a BackgroundTask real — só registra o agendamento."""
    scheduled: list[UUID] = []

    def _stub(_background_tasks: object, session_id: UUID) -> None:
        scheduled.append(session_id)

    original = reconciliation_routes._schedule_reconciliation_processing  # type: ignore[attr-defined]
    reconciliation_routes._schedule_reconciliation_processing = _stub  # type: ignore[attr-defined]
    try:
        yield scheduled
    finally:
        reconciliation_routes._schedule_reconciliation_processing = original  # type: ignore[attr-defined]


@pytest.fixture
def posting_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Liga o kill-switch do lançamento (default `False`, ADR-027-BE)."""
    monkeypatch.setenv("OMIE_POSTING_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _reload_entry(db: AsyncSession, entry_id: UUID) -> ReconciliationFileEntry:
    return (
        await db.execute(
            select(ReconciliationFileEntry)
            .where(ReconciliationFileEntry.id == entry_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _reload_session(db: AsyncSession, session_id: UUID) -> ReconciliationSession:
    return (
        await db.execute(
            select(ReconciliationSession)
            .where(ReconciliationSession.id == session_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _count(db: AsyncSession, model: type, session_id: UUID) -> int:
    return (
        await db.scalar(
            select(func.count()).select_from(model).where(model.session_id == session_id)  # type: ignore[attr-defined]
        )
    ) or 0


# ----------------------------------------------------------------------
# Sessão concluída
# ----------------------------------------------------------------------


class TestReprocessReviewedSession:
    async def test_revisao_recomeca_e_a_linha_lancada_continua_lancada(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
        posting_enabled: None,
    ) -> None:
        """O caso da reunião: lançou no Omie, reprocessou, nada se perde do Omie."""
        del posting_enabled
        admin = await _seed_user(db_session)
        client = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=client, creator=admin)
        posted_line = await _seed_entry(db_session, sess=sess, amount="-12.00")
        noted_line = await _seed_entry(
            db_session,
            sess=sess,
            amount="-30.00",
            situation=FileEntrySituation.CONCILIADO.value,
            omie_lancamento_id=777_001,
            with_note=True,
        )
        anomaly_type = await _missing_in_omie_type(db_session)
        db_session.add(
            ReconciliationAnomaly(
                session_id=sess.id,
                anomaly_type_id=anomaly_type.id,
                file_entry_id=posted_line.id,
                detected_by=AnomalyDetectedBy.AI.value,
                resolved=False,
            )
        )
        hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
        omie_note_ct, omie_note_iv = encrypt("nota do lado Omie", hex_key)
        db_session.add(
            ReconciliationOmieEntry(
                session_id=sess.id,
                omie_lancamento_id=777_001,
                transaction_date=date(2026, 4, 15),
                omie_status="Conciliado",
                user_note_encrypted=omie_note_ct,
                user_note_iv=omie_note_iv,
            )
        )
        await db_session.flush()

        # 1) Lança a linha pela rota real (MockOmieClient): a anomalia
        #    `missing_in_omie` sai RESOLVIDA, com a nota do lançamento.
        await _login(client_with_db, admin.email)
        posted = await client_with_db.post(
            POSTING_URL.format(session_id=sess.id),
            json={"lines": [{"file_entry_id": str(posted_line.id), "cod_categoria": "2.01.03"}]},
        )
        assert posted.status_code == 200, posted.text
        assert posted.json()["data"]["lancadas"] == 1
        posting = (
            await db_session.execute(
                select(ReconciliationOmiePosting).where(
                    ReconciliationOmiePosting.file_entry_id == posted_line.id
                )
            )
        ).scalar_one()
        posting_snapshot = (
            posting.id,
            posting.status,
            posting.omie_lancamento_id,
            posting.cod_int_lanc,
            posting.attempts,
        )
        assert posting.status == OmiePostingStatus.CONFIRMED.value
        resolved = (
            await db_session.execute(
                select(ReconciliationAnomaly).where(
                    ReconciliationAnomaly.file_entry_id == posted_line.id
                )
            )
        ).scalar_one()
        # A nota de resolução depende da DEK do cliente; o semeado é legado (sem
        # DEK) e cai no fail-soft do serviço. O que importa aqui é "resolvida".
        assert resolved.resolved is True

        # 2) Reprocessa a sessão concluída.
        resp = await client_with_db.post(REPROCESS_URL.format(session_id=sess.id))
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "processing"
        assert stub_enqueue == [sess.id]

        # 3) A revisão recomeçou: anomalia resolvida, nota e lançamentos Omie saem.
        assert await _count(db_session, ReconciliationAnomaly, sess.id) == 0
        assert await _count(db_session, ReconciliationOmieEntry, sess.id) == 0
        noted = await _reload_entry(db_session, noted_line.id)
        assert noted.user_note_encrypted is None
        assert noted.user_note_iv is None
        assert noted.user_action is None
        assert noted.situation == FileEntrySituation.SEM_OMIE.value
        assert noted.omie_lancamento_id is None
        # O parse pago fica: as duas linhas continuam lá.
        assert await _count(db_session, ReconciliationFileEntry, sess.id) == 2

        reloaded = await _reload_session(db_session, sess.id)
        assert reloaded.status == ReconciliationStatus.PROCESSING.value
        assert reloaded.conciliated_count == 0
        assert reloaded.anomaly_count == 0
        assert reloaded.qualification_used_glossary is False

        # 4) A intenção de lançamento NÃO foi tocada (§4.11).
        after = (
            await db_session.execute(
                select(ReconciliationOmiePosting)
                .where(ReconciliationOmiePosting.file_entry_id == posted_line.id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert (
            after.id,
            after.status,
            after.omie_lancamento_id,
            after.cod_int_lanc,
            after.attempts,
        ) == posting_snapshot

        # 5) E a linha lançada continua lançada: reenviá-la é bloqueado pela
        #    intenção `confirmed`, nunca um segundo POST no Omie.
        await db_session.execute(
            ReconciliationSession.__table__.update()
            .where(ReconciliationSession.id == sess.id)
            .values(status=ReconciliationStatus.REVIEWING.value)
        )
        again = await client_with_db.post(
            POSTING_URL.format(session_id=sess.id),
            json={"lines": [{"file_entry_id": str(posted_line.id), "cod_categoria": "2.01.03"}]},
        )
        assert again.status_code == 200, again.text
        line = again.json()["data"]["lines"][0]
        assert line["status"] == "bloqueada"
        assert line["reason"] == "ja_lancada"
        assert line["omie_lancamento_id"] == posting_snapshot[2]
        postings = (
            await db_session.scalar(
                select(func.count())
                .select_from(ReconciliationOmiePosting)
                .where(ReconciliationOmiePosting.file_entry_id == posted_line.id)
            )
        ) or 0
        assert postings == 1

    async def test_status_done_tambem_reprocessa(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(
            db_session, client=client, creator=admin, status=ReconciliationStatus.DONE.value
        )
        await _login(client_with_db, admin.email)

        resp = await client_with_db.post(REPROCESS_URL.format(session_id=sess.id))

        assert resp.status_code == 200, resp.text
        assert stub_enqueue == [sess.id]

    async def test_emite_conciliacao_reprocessada_so_com_ids_e_status_de_origem(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        del stub_enqueue
        admin = await _seed_user(db_session)
        client = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=client, creator=admin)
        await _login(client_with_db, admin.email)

        resp = await client_with_db.post(REPROCESS_URL.format(session_id=sess.id))
        assert resp.status_code == 200, resp.text

        events = list(
            (
                await db_session.execute(
                    select(UsageEvent).where(UsageEvent.event == "conciliacao_reprocessada")
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].session_id == sess.id
        assert events[0].props == {
            "client_id": str(client.id),
            "reprocessado_por": str(admin.id),
            "status_origem": "reviewing",
        }


# ----------------------------------------------------------------------
# O que continua recusado
# ----------------------------------------------------------------------


class TestReprocessRefusals:
    async def test_processing_continua_409_e_nao_agenda(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session)
        client = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(
            db_session,
            client=client,
            creator=admin,
            status=ReconciliationStatus.PROCESSING.value,
        )
        entry = await _seed_entry(db_session, sess=sess, amount="-5.00", with_note=True)
        await _login(client_with_db, admin.email)

        resp = await client_with_db.post(REPROCESS_URL.format(session_id=sess.id))

        assert resp.status_code == 409, resp.text
        assert "em processamento" in resp.json()["error"]["userMessage"].lower()
        assert stub_enqueue == []
        # Nada foi limpo: a recusa não escreve.
        assert (await _reload_entry(db_session, entry.id)).user_note_encrypted is not None

    async def test_cliente_encerrado_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        from datetime import UTC, datetime

        admin = await _seed_user(db_session)
        client = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(db_session, client=client, creator=admin)
        client.closed_at = datetime.now(UTC)
        await db_session.flush()
        await _login(client_with_db, admin.email)

        resp = await client_with_db.post(REPROCESS_URL.format(session_id=sess.id))

        assert resp.status_code == 409, resp.text
        assert stub_enqueue == []
        assert (await _reload_session(db_session, sess.id)).status == (
            ReconciliationStatus.REVIEWING.value
        )

    async def test_sessao_em_erro_preserva_o_trabalho_do_analista(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """O "tentar de novo" de sempre não ganha a limpeza da sessão concluída.

        Sessão em erro pode carregar trabalho do analista quando veio de um
        anexo de parte numa sessão em revisão que falhou no cruzamento — o
        caminho do anexo preserva esse trabalho de propósito.
        """
        admin = await _seed_user(db_session)
        client = await _seed_client(db_session, creator=admin)
        sess = await _seed_session(
            db_session, client=client, creator=admin, status=ReconciliationStatus.ERROR.value
        )
        entry = await _seed_entry(db_session, sess=sess, amount="-5.00", with_note=True)
        await _login(client_with_db, admin.email)

        resp = await client_with_db.post(REPROCESS_URL.format(session_id=sess.id))

        assert resp.status_code == 200, resp.text
        assert stub_enqueue == [sess.id]
        reloaded = await _reload_entry(db_session, entry.id)
        assert reloaded.user_note_encrypted is not None
        assert reloaded.user_action == FileEntryUserAction.CONFIRM.value
