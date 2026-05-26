"""Testes de integração da Camada 2 (padrão histórico) — S19 BACK 12.1.

Foco: 3 sessões históricas seedadas no DB + cache L1 populado em memória
com supplier/category, e validamos que `find_pattern_breaks` flaga
corretamente quando a categoria atual diverge da moda.
"""

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
from app.modules.reconciliations.qualification.historical import find_pattern_breaks
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
    ct_key, iv_key = encrypt("fake-key", hex_key)
    ct_secret, iv_secret = encrypt("fake-secret", hex_key)
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


async def _seed_session_with_pairs(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: UUID,
    created_by: UUID,
    file_hash_salt: str,
    pairs: list[tuple[int, Decimal]],
    status: ReconciliationStatus,
    processed_at_offset_days: int,
    reference_month: date,
) -> UUID:
    """Cria 1 sessão `status` com `pairs` já conciliados (file_entries com
    `situation=conciliado` e `omie_lancamento_id`).
    """
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    async with factory() as s, s.begin():
        sess = ReconciliationSession(
            client_id=client_id,
            created_by=created_by,
            omie_conta_id=42,
            reference_month=reference_month,
            date_tolerance_days=3,
            file_hash=_hex64(file_hash_salt),
            status=status.value,
            processed_at=datetime.now(UTC) - timedelta(days=processed_at_offset_days),
        )
        s.add(sess)
        await s.flush()
        for omie_id, amount in pairs:
            ct, iv = encrypt(f"desc-{omie_id}", hex_key)
            s.add(
                ReconciliationFileEntry(
                    session_id=sess.id,
                    transaction_date=reference_month,
                    description_encrypted=ct,
                    description_iv=iv,
                    amount=amount,
                    situation=FileEntrySituation.CONCILIADO.value,
                    omie_lancamento_id=omie_id,
                )
            )
        return sess.id


def _cache_with(
    client_id: UUID,
    entries: list[tuple[int, str, str]],
) -> OmieLancamentoCache:
    """Cria um cache L1-only populado manualmente com `(omie_id, supplier, category)`."""
    cache = OmieLancamentoCache(redis=None)
    for omie_id, supplier, category in entries:
        data = OmieLancamentoData(
            omie_id=omie_id,
            transaction_date=date(2026, 1, 1),
            description="",
            amount=Decimal("0"),
            supplier=supplier,
            category=category,
            status="Conciliado",
        )
        cache._l1[(client_id, omie_id)] = data
    return cache


@pytest.mark.integration
async def test_pattern_break_flags_when_category_changes(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Fornecedor X foi 'Material' em 2 sessões anteriores; agora veio
    'Tarifa' → deve flagar `padrao_quebrado`."""
    admin = await _seed_user(factory, "qualif-hist-flag@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cliente Hist Flag")

    # 3 sessões históricas (processadas há 30, 60, 90 dias).
    h1 = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="hist1",
        pairs=[(101, Decimal("-100.00"))],
        status=ReconciliationStatus.DONE,
        processed_at_offset_days=30,
        reference_month=date(2026, 1, 1),
    )
    h2 = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="hist2",
        pairs=[(102, Decimal("-100.00"))],
        status=ReconciliationStatus.DONE,
        processed_at_offset_days=60,
        reference_month=date(2026, 2, 1),
    )
    h3 = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="hist3",
        pairs=[(103, Decimal("-100.00"))],
        status=ReconciliationStatus.REVIEWING,
        processed_at_offset_days=90,
        reference_month=date(2026, 3, 1),
    )
    # Sessão atual (não vai ser incluída).
    current = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="current-hist",
        pairs=[(200, Decimal("-105.00"))],
        status=ReconciliationStatus.REVIEWING,
        processed_at_offset_days=1,
        reference_month=date(2026, 4, 1),
    )
    _ = (h1, h2, h3)  # session_ids são consultados via SQL

    cache = _cache_with(
        cliente.id,
        [
            (101, "Moinho Prado Ltda", "Material de Construção"),
            (102, "Moinho Prado Ltda", "Material de Construção"),
            (103, "Moinho Prado Ltda", "Insumos"),  # ruído
        ],
    )

    pair_id = uuid4()
    current_pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=200,
            description="PIX MOINHO PRADO",
            supplier="Moinho Prado Ltda",
            category="Tarifa Bancária",
            amount=Decimal("-105.00"),
        )
    ]

    async with factory() as db:
        results = await find_pattern_breaks(
            db,
            client_id=cliente.id,
            current_session_id=current,
            current_pairs=current_pairs,
            cache=cache,
        )
    assert len(results) == 1
    assert results[0].pair_id == str(pair_id)
    assert "Moinho Prado Ltda" in results[0].motivo
    assert "Material de Construção" in results[0].motivo


@pytest.mark.integration
async def test_pattern_break_not_flagged_when_category_matches(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Categoria atual == moda histórica → não flaga."""
    admin = await _seed_user(factory, "qualif-hist-match@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cliente Hist Match")
    for i, salt in enumerate(("hm1", "hm2"), start=1):
        await _seed_session_with_pairs(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            file_hash_salt=salt,
            pairs=[(200 + i, Decimal("-50.00"))],
            status=ReconciliationStatus.DONE,
            processed_at_offset_days=30 * i,
            reference_month=date(2026, i, 1),
        )
    current = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="hm-current",
        pairs=[(300, Decimal("-50.00"))],
        status=ReconciliationStatus.REVIEWING,
        processed_at_offset_days=1,
        reference_month=date(2026, 4, 1),
    )
    cache = _cache_with(
        cliente.id,
        [
            (201, "Padaria Z", "Despesas com Alimentação"),
            (202, "Padaria Z", "Despesas com Alimentação"),
        ],
    )
    pair_id = uuid4()
    current_pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=300,
            description="PADARIA Z",
            supplier="Padaria Z",
            category="Despesas com Alimentação",
            amount=Decimal("-50.00"),
        )
    ]
    async with factory() as db:
        results = await find_pattern_breaks(
            db,
            client_id=cliente.id,
            current_session_id=current,
            current_pairs=current_pairs,
            cache=cache,
        )
    assert results == []


@pytest.mark.integration
async def test_pattern_break_aborts_when_cache_miss_majority(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Cache vazio para a maioria do histórico → degrada (lista vazia)."""
    admin = await _seed_user(factory, "qualif-hist-miss@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cliente Hist Miss")
    for i, salt in enumerate(("hmiss1", "hmiss2", "hmiss3"), start=1):
        await _seed_session_with_pairs(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            file_hash_salt=salt,
            pairs=[(500 + i, Decimal("-10.00"))],
            status=ReconciliationStatus.DONE,
            processed_at_offset_days=30 * i,
            reference_month=date(2026, i, 1),
        )
    current = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="hmiss-current",
        pairs=[(600, Decimal("-10.00"))],
        status=ReconciliationStatus.REVIEWING,
        processed_at_offset_days=1,
        reference_month=date(2026, 4, 1),
    )
    # Cache totalmente vazio → 100% miss.
    cache = _cache_with(cliente.id, [])
    pair_id = uuid4()
    current_pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=600,
            description="",
            supplier="Forn Y",
            category="Cat X",
            amount=Decimal("-10.00"),
        )
    ]
    async with factory() as db:
        results = await find_pattern_breaks(
            db,
            client_id=cliente.id,
            current_session_id=current,
            current_pairs=current_pairs,
            cache=cache,
        )
    assert results == []


@pytest.mark.integration
async def test_pattern_break_skipped_when_no_history(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sem sessões prévias → lista vazia, sem erro."""
    admin = await _seed_user(factory, "qualif-hist-empty@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cliente Sem Hist")
    current = await _seed_session_with_pairs(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        file_hash_salt="empty-current",
        pairs=[(900, Decimal("-1.00"))],
        status=ReconciliationStatus.REVIEWING,
        processed_at_offset_days=1,
        reference_month=date(2026, 4, 1),
    )
    cache = _cache_with(cliente.id, [(900, "Forn", "Cat")])
    pair_id = uuid4()
    pairs = [
        QualificationPair(
            pair_id=str(pair_id),
            file_entry_id=pair_id,
            omie_lancamento_id=900,
            description="",
            supplier="Forn",
            category="Cat",
            amount=Decimal("-1.00"),
        )
    ]
    async with factory() as db:
        results = await find_pattern_breaks(
            db,
            client_id=cliente.id,
            current_session_id=current,
            current_pairs=pairs,
            cache=cache,
        )
    assert results == []
