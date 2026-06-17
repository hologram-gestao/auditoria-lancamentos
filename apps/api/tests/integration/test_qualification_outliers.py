"""Testes de integração da Camada 3 (outlier de valor) — S19 BACK 12.1."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    FileEntrySituation,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)
from app.integrations.omie.lancamento_cache import (
    OmieLancamentoCache,
    OmieLancamentoData,
)
from app.modules.reconciliations.qualification.outliers import find_value_outliers
from app.modules.reconciliations.qualification.schemas import QualificationPair

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_user(factory: async_sessionmaker[AsyncSession], email: str) -> User:
    async with factory() as s, s.begin():
        u = User(
            name="Test",
            email=email.lower(),
            password_hash=hash_password("Senh@Forte#1"),
            role=UserRole.ADMIN.value,
            active=True,
        )
        s.add(u)
        await s.flush()
        return u


async def _seed_client(
    factory: async_sessionmaker[AsyncSession],
    creator_id: UUID,
    name: str,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("k", hex_key)
    ct_secret, iv_secret = encrypt("s", hex_key)
    async with factory() as s, s.begin():
        c = Client(
            name=name,
            omie_app_key_encrypted=ct_key,
            omie_app_key_iv=iv_key,
            omie_app_secret_encrypted=ct_secret,
            omie_app_secret_iv=iv_secret,
            active=True,
            created_by=creator_id,
        )
        s.add(c)
        await s.flush()
        return c


async def _seed_session_with_amounts(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: UUID,
    created_by: UUID,
    salt: str,
    amounts: list[Decimal],
    omie_id_start: int,
    offset_days: int,
    reference_month: date,
) -> tuple[UUID, list[int]]:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    omie_ids = list(range(omie_id_start, omie_id_start + len(amounts)))
    async with factory() as s, s.begin():
        sess = ReconciliationSession(
            client_id=client_id,
            created_by=created_by,
            omie_conta_id=42,
            reference_month=reference_month,
            date_tolerance_days=3,
            file_hash=_hex64(salt),
            status=ReconciliationStatus.DONE.value,
            processed_at=datetime.now(UTC) - timedelta(days=offset_days),
        )
        s.add(sess)
        await s.flush()
        for oid, amount in zip(omie_ids, amounts, strict=True):
            ct, iv = encrypt("d", hex_key)
            s.add(
                ReconciliationFileEntry(
                    session_id=sess.id,
                    transaction_date=reference_month,
                    description_encrypted=ct,
                    description_iv=iv,
                    amount=amount,
                    situation=FileEntrySituation.CONCILIADO.value,
                    omie_lancamento_id=oid,
                )
            )
        return sess.id, omie_ids


def _cache_for_supplier(
    client_id: UUID,
    omie_ids: list[int],
    supplier: str,
) -> OmieLancamentoCache:
    cache = OmieLancamentoCache()
    for oid in omie_ids:
        cache._l1[(client_id, oid)] = OmieLancamentoData(
            omie_id=oid,
            transaction_date=date(2026, 1, 1),
            description="",
            amount=Decimal("0"),
            supplier=supplier,
            category="Cat",
            status="Conciliado",
        )
    return cache


@pytest.mark.integration
async def test_outlier_flagged_when_value_beyond_3sigma(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Histórico em torno de R$ 50 (com pouca variação) e atual R$ 500 →
    fora de 3*sigma, amostra >= 5 → flag."""
    admin = await _seed_user(factory, "qualif-out-flag@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Out Flag")

    # 5 pagamentos históricos do mesmo fornecedor com valores próximos.
    sess1, ids1 = await _seed_session_with_amounts(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        salt="out-h1",
        amounts=[Decimal(s) for s in ("-30.00", "-32.00", "-31.00", "-29.00", "-30.50")],
        omie_id_start=1100,
        offset_days=30,
        reference_month=date(2026, 1, 1),
    )
    _ = sess1
    cache = _cache_for_supplier(cliente.id, ids1, supplier="Tarifa Mensal")

    # Sessão atual com valor extremo.
    pair_id = uuid4()
    current_pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=9999,
            description="TARIFA MENSAL",
            supplier="Tarifa Mensal",
            category="Cat",
            amount=Decimal("-500.00"),
        )
    ]
    # Sessão atual no DB — só pra ter um session_id válido (a query
    # exclui ela mesma do histórico).
    current_session, _ = await _seed_session_with_amounts(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        salt="out-current",
        amounts=[Decimal("-500.00")],
        omie_id_start=9999,
        offset_days=1,
        reference_month=date(2026, 4, 1),
    )

    async with factory() as db:
        results = await find_value_outliers(
            db,
            client_id=cliente.id,
            current_session_id=current_session,
            current_pairs=current_pairs,
            cache=cache,
        )
    assert len(results) == 1
    assert results[0].pair_id == str(pair_id)
    assert "Tarifa Mensal" in results[0].motivo
    assert "500,00" in results[0].motivo


@pytest.mark.integration
async def test_outlier_not_flagged_when_sample_too_small(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Amostra histórica < 5 → não flaga (variância pouco confiável)."""
    admin = await _seed_user(factory, "qualif-out-small@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Out Small")

    sess1, ids1 = await _seed_session_with_amounts(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        salt="osm-h1",
        amounts=[Decimal("-10.00"), Decimal("-12.00")],
        omie_id_start=2100,
        offset_days=30,
        reference_month=date(2026, 1, 1),
    )
    _ = sess1
    cache = _cache_for_supplier(cliente.id, ids1, supplier="Forn Small")

    current_session, _ = await _seed_session_with_amounts(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        salt="osm-current",
        amounts=[Decimal("-100.00")],
        omie_id_start=2999,
        offset_days=1,
        reference_month=date(2026, 4, 1),
    )

    pair_id = uuid4()
    current_pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=2999,
            description="",
            supplier="Forn Small",
            category="Cat",
            amount=Decimal("-100.00"),
        )
    ]
    async with factory() as db:
        results = await find_value_outliers(
            db,
            client_id=cliente.id,
            current_session_id=current_session,
            current_pairs=current_pairs,
            cache=cache,
        )
    assert results == []


@pytest.mark.integration
async def test_outlier_not_flagged_when_within_3sigma(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Valor atual dentro da média histórica → não flaga."""
    admin = await _seed_user(factory, "qualif-out-within@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Out Within")
    _, ids = await _seed_session_with_amounts(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        salt="ow-h",
        amounts=[
            Decimal("-100.00"),
            Decimal("-110.00"),
            Decimal("-95.00"),
            Decimal("-105.00"),
            Decimal("-102.00"),
        ],
        omie_id_start=3100,
        offset_days=30,
        reference_month=date(2026, 1, 1),
    )
    cache = _cache_for_supplier(cliente.id, ids, supplier="Forn Normal")
    current_session, _ = await _seed_session_with_amounts(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        salt="ow-current",
        amounts=[Decimal("-108.00")],
        omie_id_start=3999,
        offset_days=1,
        reference_month=date(2026, 4, 1),
    )
    pair_id = uuid4()
    current_pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=3999,
            description="",
            supplier="Forn Normal",
            category="Cat",
            amount=Decimal("-108.00"),
        )
    ]
    async with factory() as db:
        results = await find_value_outliers(
            db,
            client_id=cliente.id,
            current_session_id=current_session,
            current_pairs=current_pairs,
            cache=cache,
        )
    assert results == []
