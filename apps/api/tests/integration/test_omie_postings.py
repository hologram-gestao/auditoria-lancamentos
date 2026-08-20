"""Dedup primária do lançamento no Omie (Sprint 7 / BACK 07.2).

O que está sendo provado aqui é a afirmação central da sprint: **o ADL não
depende de o Omie impor unicidade** (S-1) para não lançar duas vezes. Todas as
garantias exercitadas abaixo são do BANCO — `UNIQUE(file_entry_id)` e
`UNIQUE(client_id, cod_int_lanc)` — e não de um "SELECT antes do INSERT", que
tem janela de corrida justamente onde o erro custa dinheiro.

Nenhum teste aqui usa mock da Omie: se o único jeito de provar a dedup fosse um
mock que finge deduplicar, não haveria prova nenhuma.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import (
    OmieLancamentoAlreadyLinkedError,
    OmiePostingKeyCollisionError,
)
from app.core.security import hash_password
from app.db.models import (
    Client,
    FileEntrySituation,
    OmiePostingStatus,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)
from app.modules.reconciliations.omie_posting.keys import derive_cod_int_lanc
from app.modules.reconciliations.omie_posting.repository import OmiePostingRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        name="Posting User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, name: str, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("posting-app-key", hex_key)
    ct_secret, iv_secret = encrypt("posting-app-secret", hex_key)
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
    return client


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    omie_conta_id: int = 777,
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=omie_conta_id,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=None,
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_entry(
    session: AsyncSession,
    *,
    sess: ReconciliationSession,
    description: str = "CAFETERIA DO LARGO",
    amount: Decimal = Decimal("-12.00"),
    day: int = 15,
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description, hex_key)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=date(2026, 4, day),
        description_encrypted=ct,
        description_iv=iv,
        amount=amount,
        situation=FileEntrySituation.SEM_OMIE.value,
    )
    session.add(entry)
    await session.flush()
    return entry


class Scenario(NamedTuple):
    """Cliente + sessão de conciliação prontos, com o admin que os criou."""

    admin: User
    client: Client
    session: ReconciliationSession


@pytest.fixture
async def scenario(db_session: AsyncSession) -> Scenario:
    admin = await _seed_user(db_session, email=f"posting-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db_session, name=f"Cliente {uuid4().hex[:6]}", creator=admin)
    sess = await _seed_session(db_session, client=client, creator=admin)
    return Scenario(admin=admin, client=client, session=sess)


@pytest.mark.integration
class TestRegisterIntentIsIdempotent:
    async def test_second_call_does_not_create_a_new_row(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        client, sess = scenario.client, scenario.session
        entry = await _seed_entry(db_session, sess=sess)
        repo = OmiePostingRepository(db_session)

        first = await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )
        second = await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )

        assert first.id == second.id
        count = await db_session.scalar(
            select(sa.func.count())
            .select_from(ReconciliationOmiePosting)
            .where(ReconciliationOmiePosting.file_entry_id == entry.id)
        )
        assert count == 1

    async def test_intent_is_pending_before_any_post(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        """O estado nasce ANTES do envio — é ele que existe quando o POST some."""
        client, sess = scenario.client, scenario.session
        entry = await _seed_entry(db_session, sess=sess)
        posting = await OmiePostingRepository(db_session).register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )
        assert posting.status == OmiePostingStatus.PENDING.value
        assert posting.omie_lancamento_id is None
        assert posting.cod_int_lanc == derive_cod_int_lanc(entry.id)


@pytest.mark.integration
class TestDatabaseEnforcesUniqueness:
    async def test_file_entry_uniqueness_lives_in_the_database(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        """Um INSERT cru que ignore a aplicação ainda é recusado."""
        client, sess = scenario.client, scenario.session
        entry = await _seed_entry(db_session, sess=sess)
        await OmiePostingRepository(db_session).register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )
        db_session.add(
            ReconciliationOmiePosting(
                session_id=sess.id,
                client_id=client.id,
                file_entry_id=entry.id,
                cod_int_lanc="ADLOUTRACHAVE12345",
                status=OmiePostingStatus.PENDING.value,
                attempts=0,
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            await db_session.flush()

    async def test_cod_int_lanc_uniqueness_lives_in_the_database(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        client, sess = scenario.client, scenario.session
        entry_a = await _seed_entry(db_session, sess=sess, day=15)
        entry_b = await _seed_entry(db_session, sess=sess, day=16)
        repo = OmiePostingRepository(db_session)
        first = await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry_a.id,
        )
        # Força a colisão que o encoding torna improvável, para provar que o
        # banco a recusa — e que a recusa não é silenciosa.
        db_session.add(
            ReconciliationOmiePosting(
                session_id=sess.id,
                client_id=client.id,
                file_entry_id=entry_b.id,
                cod_int_lanc=first.cod_int_lanc,
                status=OmiePostingStatus.PENDING.value,
                attempts=0,
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            await db_session.flush()

    async def test_collision_surfaces_as_a_handled_error(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        """Chave já tomada por OUTRA linha do tenant → erro tratado, não silêncio."""
        client, sess = scenario.client, scenario.session
        entry_a = await _seed_entry(db_session, sess=sess, day=15)
        entry_b = await _seed_entry(db_session, sess=sess, day=16)
        # Ocupa a chave de B com um registro cuja linha é A — o único jeito de
        # simular a colisão do digest sem quebrar o encoding.
        db_session.add(
            ReconciliationOmiePosting(
                session_id=sess.id,
                client_id=client.id,
                file_entry_id=entry_a.id,
                cod_int_lanc=derive_cod_int_lanc(entry_b.id),
                status=OmiePostingStatus.PENDING.value,
                attempts=0,
            )
        )
        await db_session.flush()

        with pytest.raises(OmiePostingKeyCollisionError):
            await OmiePostingRepository(db_session).register_intent(
                client_id=client.id,
                session_id=sess.id,
                file_entry_id=entry_b.id,
            )


@pytest.mark.integration
class TestTwoIdenticalPurchasesGetTwoKeys:
    async def test_same_date_amount_and_description_still_produce_two_postings(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        """O caso do "dinheiro faltando": conteúdo idêntico, DOIS lançamentos."""
        client, sess = scenario.client, scenario.session
        first = await _seed_entry(
            db_session,
            sess=sess,
            description="CAFETERIA DO LARGO",
            amount=Decimal("-12.00"),
            day=15,
        )
        second = await _seed_entry(
            db_session,
            sess=sess,
            description="CAFETERIA DO LARGO",
            amount=Decimal("-12.00"),
            day=15,
        )
        assert (first.transaction_date, first.amount) == (second.transaction_date, second.amount)

        repo = OmiePostingRepository(db_session)
        posting_a = await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=first.id,
        )
        posting_b = await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=second.id,
        )
        assert posting_a.id != posting_b.id
        assert posting_a.cod_int_lanc != posting_b.cod_int_lanc


@pytest.mark.integration
class TestTenantIsolation:
    async def test_lookup_from_another_tenant_returns_nothing(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        """Acesso por PK sem `AND client_id` seria vazamento — não existe aqui."""
        admin, client, sess = scenario.admin, scenario.client, scenario.session
        other = await _seed_client(db_session, name="Outro Tenant", creator=admin)
        entry = await _seed_entry(db_session, sess=sess)
        repo = OmiePostingRepository(db_session)
        await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )

        assert await repo.get_by_file_entry(client_id=other.id, file_entry_id=entry.id) is None
        assert await repo.list_by_file_entries(client_id=other.id, file_entry_ids=[entry.id]) == {}

    async def test_mark_confirmed_from_another_tenant_is_refused(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        admin, client, sess = scenario.admin, scenario.client, scenario.session
        other = await _seed_client(db_session, name="Outro Tenant 2", creator=admin)
        entry = await _seed_entry(db_session, sess=sess)
        repo = OmiePostingRepository(db_session)
        await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )
        with pytest.raises(ValueError, match="register_intent"):
            await repo.mark_confirmed(
                client_id=other.id, file_entry_id=entry.id, omie_lancamento_id=999
            )


@pytest.mark.integration
class TestConfirmationReflectsOnTheReconciliation:
    async def test_confirm_updates_posting_and_file_entry(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        client, sess = scenario.client, scenario.session
        entry = await _seed_entry(db_session, sess=sess)
        repo = OmiePostingRepository(db_session)
        await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )

        posting = await repo.mark_confirmed(
            client_id=client.id,
            file_entry_id=entry.id,
            omie_lancamento_id=555_123,
        )

        assert posting.status == OmiePostingStatus.CONFIRMED.value
        assert posting.omie_lancamento_id == 555_123
        await db_session.refresh(entry)
        assert entry.omie_lancamento_id == 555_123
        assert entry.situation == FileEntrySituation.CONCILIADO.value

    async def test_confirm_coexists_with_the_partial_unique_index(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        """`ix_recon_file_entry_session_omie_unique` não é contornado.

        Um lançamento do Omie fecha UMA linha (CLAUDE.md §5.4). Se o `nCodLanc`
        já estiver vinculado a outra linha da sessão, a confirmação levanta erro
        tratado — em vez de estourar `IntegrityError` cru (500) ou, pior, de
        desviar do índice.
        """
        client, sess = scenario.client, scenario.session
        taken = await _seed_entry(db_session, sess=sess, day=10)
        taken.omie_lancamento_id = 4242
        taken.situation = FileEntrySituation.CONCILIADO.value
        await db_session.flush()

        entry = await _seed_entry(db_session, sess=sess, day=11)
        repo = OmiePostingRepository(db_session)
        await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )

        with pytest.raises(OmieLancamentoAlreadyLinkedError):
            await repo.mark_confirmed(
                client_id=client.id,
                file_entry_id=entry.id,
                omie_lancamento_id=4242,
            )
        await db_session.refresh(entry)
        assert entry.omie_lancamento_id is None
        assert entry.situation == FileEntrySituation.SEM_OMIE.value


@pytest.mark.integration
class TestFailurePathMarksNothingAsPosted:
    async def test_mark_failed_keeps_the_line_pending(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        client, sess = scenario.client, scenario.session
        entry = await _seed_entry(db_session, sess=sess)
        repo = OmiePostingRepository(db_session)
        await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )

        posting = await repo.mark_failed(
            client_id=client.id,
            file_entry_id=entry.id,
            error_code="OMIE_FAULT",
            error_message="Categoria informada não existe.",
        )

        assert posting.status == OmiePostingStatus.FAILED.value
        assert posting.omie_lancamento_id is None
        assert posting.error_message == "Categoria informada não existe."
        await db_session.refresh(entry)
        assert entry.situation == FileEntrySituation.SEM_OMIE.value
        assert entry.omie_lancamento_id is None

    async def test_attempts_are_counted_before_the_post(
        self, db_session: AsyncSession, scenario: Scenario
    ) -> None:
        client, sess = scenario.client, scenario.session
        entry = await _seed_entry(db_session, sess=sess)
        repo = OmiePostingRepository(db_session)
        await repo.register_intent(
            client_id=client.id,
            session_id=sess.id,
            file_entry_id=entry.id,
        )
        assert await repo.increment_attempts(client_id=client.id, file_entry_id=entry.id) == 1
        assert await repo.increment_attempts(client_id=client.id, file_entry_id=entry.id) == 2
