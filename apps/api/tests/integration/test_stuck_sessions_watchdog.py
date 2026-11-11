"""Watchdog das conciliações paradas contra Postgres real (86e3mpmcw).

A CTE trava e lê as linhas antes do UPDATE para devolver o `updated_at` ANTERIOR
(o UPDATE o sobrescreve). Só um banco de verdade prova isso, junto com o critério
(só `processing` sem sinal há mais de 25 minutos) e o `SKIP LOCKED`.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ErrorCode
from app.core.security import hash_password
from app.db.models import (
    Client,
    ProcessingStage,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)
from app.modules.reconciliations.stuck_sessions import (
    STUCK_ERROR_MESSAGE,
    mark_stuck_sessions_as_error,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_owner(factory: async_sessionmaker[AsyncSession]) -> tuple[UUID, UUID]:
    async with factory() as s, s.begin():
        user = User(
            name="Watchdog",
            email=f"watchdog-{uuid4().hex[:8]}@hologram.com.br",
            password_hash=hash_password("Senh@Watchdog#1"),
            role=UserRole.ADMIN.value,
            active=True,
        )
        s.add(user)
        await s.flush()
        client = Client(name=f"Cliente Watchdog {uuid4().hex[:6]}", created_by=user.id)
        s.add(client)
        await s.flush()
        return user.id, client.id


async def _seed_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    client_id: UUID,
    conta: int,
    status: str,
    minutes_ago: int,
    stage: ProcessingStage | None,
) -> UUID:
    async with factory() as s, s.begin():
        sess = ReconciliationSession(
            client_id=client_id,
            created_by=user_id,
            omie_conta_id=conta,
            reference_month=date(2026, 10, 1),
            date_tolerance_days=0,
            status=status,
            processing_stage=stage.value if stage is not None else None,
        )
        s.add(sess)
        await s.flush()
        # O `onupdate` do ORM não alcança um UPDATE em Core: o `updated_at` fica
        # exatamente onde o teste o põe.
        await s.execute(
            text(
                "UPDATE reconciliation_sessions "
                "SET updated_at = NOW() - make_interval(mins => :m) WHERE id = :id"
            ).bindparams(m=minutes_ago, id=sess.id)
        )
        return sess.id


async def test_marks_only_stale_processing_and_returns_the_previous_signal(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id, client_id = await _seed_owner(factory)
    stale_gravacao = await _seed_session(
        factory,
        user_id=user_id,
        client_id=client_id,
        conta=101,
        status=ReconciliationStatus.PROCESSING.value,
        minutes_ago=40,
        stage=ProcessingStage.GRAVACAO,
    )
    stale_no_stage = await _seed_session(
        factory,
        user_id=user_id,
        client_id=client_id,
        conta=102,
        status=ReconciliationStatus.PROCESSING.value,
        minutes_ago=90,
        stage=None,
    )
    fresh = await _seed_session(
        factory,
        user_id=user_id,
        client_id=client_id,
        conta=103,
        status=ReconciliationStatus.PROCESSING.value,
        minutes_ago=5,
        stage=ProcessingStage.OMIE,
    )
    old_reviewing = await _seed_session(
        factory,
        user_id=user_id,
        client_id=client_id,
        conta=104,
        status=ReconciliationStatus.REVIEWING.value,
        minutes_ago=600,
        stage=ProcessingStage.GRAVACAO,
    )

    mine = {stale_gravacao, stale_no_stage, fresh, old_reviewing}
    async with factory() as s, s.begin():
        # O banco é compartilhado pela suíte (create_all por sessão de pytest):
        # o watchdog pode achar sessões paradas de outros testes. Só as nossas contam.
        stuck = [x for x in await mark_stuck_sessions_as_error(s) if x.session_id in mine]

    # Só as duas paradas, a de mais tempo sem sinal primeiro.
    assert [x.session_id for x in stuck] == [stale_no_stage, stale_gravacao]
    first, second = stuck
    assert first.client_id == client_id
    assert first.omie_conta_id == 102
    assert first.reference_month == date(2026, 10, 1)
    assert first.processing_stage is None
    # Minutos desde o sinal ANTERIOR (90), não desde o UPDATE do watchdog (0).
    assert first.idle_minutes in (90, 91)
    assert second.processing_stage == "gravacao"
    assert second.idle_minutes in (40, 41)

    async with factory() as s:
        rows = {
            r.id: r
            for r in (
                await s.execute(
                    select(ReconciliationSession).where(
                        ReconciliationSession.id.in_(
                            [stale_gravacao, stale_no_stage, fresh, old_reviewing]
                        )
                    )
                )
            ).scalars()
        }
    for sid in (stale_gravacao, stale_no_stage):
        assert rows[sid].status == ReconciliationStatus.ERROR.value
        assert rows[sid].error_code == ErrorCode.RECONCILIATION_TIMEOUT.value
        assert rows[sid].error_message == STUCK_ERROR_MESSAGE
    # A etapa fica: é o diagnóstico de onde parou.
    assert rows[stale_gravacao].processing_stage == "gravacao"
    assert rows[fresh].status == ReconciliationStatus.PROCESSING.value
    assert rows[old_reviewing].status == ReconciliationStatus.REVIEWING.value

    # Segunda passada: nada mais a marcar entre as nossas.
    async with factory() as s, s.begin():
        again = await mark_stuck_sessions_as_error(s)
    assert not {x.session_id for x in again} & mine


async def test_skips_the_row_another_transaction_holds(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """`SKIP LOCKED`: a sessão que o job está gravando agora fica de fora."""
    user_id, client_id = await _seed_owner(factory)
    held = await _seed_session(
        factory,
        user_id=user_id,
        client_id=client_id,
        conta=201,
        status=ReconciliationStatus.PROCESSING.value,
        minutes_ago=60,
        stage=ProcessingStage.GRAVACAO,
    )
    async with factory() as holder, holder.begin():
        await holder.execute(
            select(ReconciliationSession.id)
            .where(ReconciliationSession.id == held)
            .with_for_update()
        )
        async with factory() as s, s.begin():
            stuck = await mark_stuck_sessions_as_error(s)
        assert held not in [x.session_id for x in stuck]
