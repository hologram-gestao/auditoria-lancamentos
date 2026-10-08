"""`GET /api/v1/clients/{client_id}/summary` contra Postgres (86e3k1q3j).

O que só o banco prova: as contagens saem dos `WHERE` certos. Sessão apagada fica
fora, sessão de outro mês fica fora das contagens do mês (mas pode ser a mais
recente), compra com lançamento CONFIRMADO no Omie fica fora, estorno fica fora,
linha de conta corrente fica fora das compras de cartão, e a cobertura do de-para
é a da `apply_mapping` sobre os movimentos PRESENTES.

O cross-tenant e o cross-org rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica. Aqui fica o
cross-tenant DENTRO da mesma organização, com a linha na trilha.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    AnomalyType,
    Client,
    ClientMappingDecision,
    ClientMovement,
    DecisionOrigin,
    DecisionType,
    FileEntrySituation,
    MovementStatus,
    OmieAccountCache,
    OmiePostingStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_movements.competence import current_competence, format_competence
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.reconciliations.omie_posting.keys import derive_cod_int_lanc

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ResumoCliente#1"
CLIENT_NAME = "Resumo Sigiloso Participacoes LTDA"
NEIGHBOR_NAME = "Vizinho Sigiloso Comercio LTDA"
MONTH = date(2026, 9, 1)


async def _seed_user(
    db: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Resumo",
        email=f"resumo-{role.value}-{uuid4().hex[:8]}@exemplo.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_client(db: AsyncSession, *, creator: User, name: str) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(f"resumo-key-{uuid4().hex[:6]}", hex_key)
    ct_secret, iv_secret = encrypt(f"resumo-secret-{uuid4().hex[:6]}", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    db.add(client)
    await db.flush()
    return client


async def _seed_session(
    db: AsyncSession,
    *,
    client: Client,
    creator: User,
    status: ReconciliationStatus,
    account_type: SessionAccountType = SessionAccountType.CHECKING,
    omie_conta_id: int = 1,
    month: date = MONTH,
    created_at: datetime | None = None,
    deleted: bool = False,
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=omie_conta_id,
        account_type=account_type.value,
        reference_month=month,
        date_tolerance_days=0,
        file_hash=None,
        status=status.value,
        deleted_at=datetime(2026, 10, 2, tzinfo=UTC) if deleted else None,
    )
    if created_at is not None:
        sess.created_at = created_at
    db.add(sess)
    await db.flush()
    return sess


async def _seed_entry(
    db: AsyncSession,
    sess: ReconciliationSession,
    amount: str,
    situation: FileEntrySituation = FileEntrySituation.SEM_OMIE,
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt("COMPRA SIGILOSA DO EXTRATO", hex_key)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=date(2026, 9, 10),
        description_encrypted=ct,
        description_iv=iv,
        amount=Decimal(amount),
        situation=situation.value,
    )
    db.add(entry)
    await db.flush()
    return entry


async def _seed_anomaly(
    db: AsyncSession, sess: ReconciliationSession, anomaly_type: AnomalyType, *, resolved: bool
) -> None:
    db.add(
        ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=anomaly_type.id,
            resolved=resolved,
        )
    )


class World:
    admin: User
    operator: User
    neighbor_operator: User
    client: Client
    neighbor: Client
    code_a: str
    code_b: str
    latest_session_id: Any


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.client = await _seed_client(db_session, creator=w.admin, name=CLIENT_NAME)
    w.neighbor = await _seed_client(db_session, creator=w.admin, name=NEIGHBOR_NAME)
    w.operator = await _seed_user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.neighbor_operator = await _seed_user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.neighbor.id
    )

    # Contas no cache: três, das quais duas têm conciliação no mês.
    for conta in (1, 2, 3):
        db_session.add(
            OmieAccountCache(
                client_id=w.client.id,
                omie_conta_id=conta,
                name=f"Conta {conta}",
                bank_name="Banco",
                account_type="CC",
            )
        )

    card = await _seed_session(
        db_session,
        client=w.client,
        creator=w.admin,
        status=ReconciliationStatus.REVIEWING,
        account_type=SessionAccountType.CREDIT_CARD,
        omie_conta_id=1,
        created_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    )
    checking = await _seed_session(
        db_session,
        client=w.client,
        creator=w.admin,
        status=ReconciliationStatus.DONE,
        omie_conta_id=2,
        created_at=datetime(2026, 10, 1, 11, 0, tzinfo=UTC),
    )
    # Apagada: fora de tudo, mesmo sendo do mês e mesmo sendo a mais nova.
    deleted = await _seed_session(
        db_session,
        client=w.client,
        creator=w.admin,
        status=ReconciliationStatus.ERROR,
        account_type=SessionAccountType.CREDIT_CARD,
        omie_conta_id=3,
        created_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        deleted=True,
    )
    # De outro mês: fora das contagens do mês, mas é a mais recente ativa.
    other_month = await _seed_session(
        db_session,
        client=w.client,
        creator=w.admin,
        status=ReconciliationStatus.PROCESSING,
        account_type=SessionAccountType.CREDIT_CARD,
        omie_conta_id=3,
        month=date(2026, 10, 1),
        created_at=datetime(2026, 10, 4, 9, 0, tzinfo=UTC),
    )
    w.latest_session_id = other_month.id
    # O vizinho tem conciliação no mesmo mês: nada dele pode vazar para cá.
    neighbor_session = await _seed_session(
        db_session,
        client=w.neighbor,
        creator=w.admin,
        status=ReconciliationStatus.ERROR,
        account_type=SessionAccountType.CREDIT_CARD,
    )

    # Compras da fatura: só as duas primeiras são elegíveis.
    await _seed_entry(db_session, card, "-100.00")
    await _seed_entry(db_session, card, "-10.10")
    posted = await _seed_entry(db_session, card, "-50.00")  # posting confirmado
    await _seed_entry(db_session, card, "30.00")  # estorno: bloqueado (§3.16)
    await _seed_entry(db_session, card, "-20.00", FileEntrySituation.CONCILIADO)
    await _seed_entry(db_session, card, "-5.00", FileEntrySituation.IGNORADO)
    await _seed_entry(db_session, checking, "-999.00")  # conta corrente, não cartão
    await _seed_entry(db_session, deleted, "-777.00")  # sessão apagada
    await _seed_entry(db_session, other_month, "-555.00")  # outro mês
    await _seed_entry(db_session, neighbor_session, "-444.00")  # outro tenant
    db_session.add(
        ReconciliationOmiePosting(
            session_id=card.id,
            client_id=w.client.id,
            file_entry_id=posted.id,
            cod_int_lanc=derive_cod_int_lanc(posted.id),
            status=OmiePostingStatus.CONFIRMED.value,
            attempts=1,
        )
    )

    # Anomalias: códigos únicos por teste (a coluna `code` é UNIQUE global).
    suffix = uuid4().hex[:6]
    w.code_a = f"aa_resumo_{suffix}"
    w.code_b = f"bb_resumo_{suffix}"
    type_a = AnomalyType(code=w.code_a, name="Tipo A", description="", severity="moderate")
    type_b = AnomalyType(code=w.code_b, name="Tipo B", description="", severity="info")
    db_session.add_all([type_a, type_b])
    await db_session.flush()
    await _seed_anomaly(db_session, card, type_a, resolved=False)
    await _seed_anomaly(db_session, card, type_a, resolved=False)
    await _seed_anomaly(db_session, card, type_a, resolved=True)
    await _seed_anomaly(db_session, checking, type_b, resolved=False)
    await _seed_anomaly(db_session, deleted, type_b, resolved=False)
    await _seed_anomaly(db_session, other_month, type_b, resolved=False)
    await _seed_anomaly(db_session, neighbor_session, type_b, resolved=False)

    # De-para: base do mês com duas categorias presentes e uma ausente na origem.
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    for idx, (code, amount, status) in enumerate(
        (
            ("1.01", "-100.00", MovementStatus.PRESENTE),
            ("2.02", "300.00", MovementStatus.PRESENTE),
            ("3.03", "50.00", MovementStatus.AUSENTE_NA_ORIGEM),
        )
    ):
        db_session.add(
            ClientMovement(
                client_id=w.client.id,
                source_type="omie",
                source_movement_id=f"mv-{idx}",
                competence=MONTH,
                movement_date=date(2026, 9, 5),
                amount=Decimal(amount),
                category_code=code,
                status=status.value,
            )
        )
    await db_session.flush()
    demonstrativo = await MappingCatalogRepository(db_session).get_destination_by_type(
        HOLOGRAM_ORGANIZATION_ID, "demonstrativo_contabil"
    )
    assert demonstrativo is not None
    db_session.add(
        ClientMappingDecision(
            client_id=w.client.id,
            source_type="omie",
            category_code="1.01",
            destination_id=demonstrativo.id,
            decision_type=DecisionType.NAO_MAPEAR.value,
            origin=DecisionOrigin.CONFIRMADA.value,
            effective_from=date(2026, 1, 1),
            author_id=w.admin.id,
        )
    )
    await db_session.flush()
    return w


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _url(client: Client, query: str = "?month=2026-09") -> str:
    return f"/api/v1/clients/{client.id}/summary{query}"


async def test_caminho_feliz_conta_so_o_que_e_do_mes_e_do_tenant(
    client_with_db: AsyncClient, world: World
) -> None:
    await _login(client_with_db, world.admin)
    with capture_logs() as logs:
        resp = await client_with_db.get(_url(world.client))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["referenceMonth"] == "2026-09"
    assert data["reconciliations"] == {
        "accountsTotal": 3,
        "accountsWithSession": 2,
        "byStatus": {"processing": 0, "reviewing": 1, "done": 1, "error": 0},
        # Conta 3: só a sessão APAGADA no mês e uma de mês POSTERIOR. Nenhuma das
        # duas a faz habitual.
        "habitualAccountIds": [1, 2],
    }
    assert data["anomalies"] == {
        "openTotal": 3,
        "byType": [{"code": world.code_a, "count": 2}, {"code": world.code_b, "count": 1}],
        "resolvedInMonth": 1,
    }
    assert data["cardPurchasesToPost"] == {"count": 2, "totalAmount": "-110.10"}

    by_destination = {item["destinationCode"]: item for item in data["mapping"]}
    # "1.01" decidido (não mapear); "2.02" e "3.03" (vista na base) sem decisão.
    # Cobertura só sobre o PRESENTE: 100 / (100 + 300) = 25%.
    assert by_destination["demonstrativo_contabil"] == {
        "destinationCode": "demonstrativo_contabil",
        "withoutDecision": 2,
        "coveragePct": "25.0",
        "materialized": False,
    }
    for code, item in by_destination.items():
        if code != "demonstrativo_contabil":
            assert item["withoutDecision"] == 3
            assert item["coveragePct"] == "0.0"

    assert data["titles"]["neverSynced"] is True
    assert data["titles"]["overdueCount"] == 0
    assert data["titles"]["overdueTotal"] == "0.00"
    assert data["latestSession"]["id"] == str(world.latest_session_id)
    assert data["latestSession"]["referenceMonth"] == "2026-10"
    assert data["latestSession"]["status"] == "processing"
    assert data["latestSession"]["accountType"] == "credit_card"

    # Só IDs, códigos e números: nem o nome do cliente, nem a descrição da compra.
    assert CLIENT_NAME not in resp.text
    assert "COMPRA SIGILOSA" not in resp.text
    assert CLIENT_NAME not in repr(logs)


async def test_contas_habituais_sao_as_do_mes_e_dos_tres_anteriores(
    client_with_db: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    """A janela é o mês de referência e os três anteriores, só sessões ativas."""
    seeds = (
        (10, date(2026, 7, 1), False),  # dois meses antes: entra
        (11, date(2026, 6, 1), False),  # três meses antes, o limite: entra
        (20, date(2026, 5, 1), False),  # quatro meses antes: fora
        (30, MONTH, False),  # o próprio mês: entra
        (40, date(2026, 11, 1), False),  # mês posterior: fora
        (50, date(2026, 8, 1), True),  # apagada: fora
    )
    for conta, month, deleted in seeds:
        await _seed_session(
            db_session,
            client=world.client,
            creator=world.admin,
            status=ReconciliationStatus.DONE,
            omie_conta_id=conta,
            month=month,
            deleted=deleted,
        )
    # O vizinho conciliou outra conta dentro da janela: nada dele entra aqui.
    await _seed_session(
        db_session,
        client=world.neighbor,
        creator=world.admin,
        status=ReconciliationStatus.DONE,
        omie_conta_id=60,
        month=date(2026, 8, 1),
    )
    await db_session.flush()

    await _login(client_with_db, world.admin)
    resp = await client_with_db.get(_url(world.client))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["reconciliations"]["habitualAccountIds"] == [1, 2, 10, 11, 30]


async def test_mes_padrao_e_o_corrente_do_servidor(
    client_with_db: AsyncClient, world: World
) -> None:
    await _login(client_with_db, world.admin)
    resp = await client_with_db.get(_url(world.client, query=""))
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["referenceMonth"] == format_competence(current_competence())


@pytest.mark.parametrize("month", ["2026-13", "0000-01", "2026-9", "2026-09-01", "abc"])
async def test_mes_mal_formado_e_400_nunca_500(
    client_with_db: AsyncClient, world: World, month: str
) -> None:
    await _login(client_with_db, world.admin)
    resp = await client_with_db.get(_url(world.client, query=f"?month={month}"))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_operador_do_proprio_tenant_le_com_a_carteira(
    client_with_db: AsyncClient, world: World
) -> None:
    await _login(client_with_db, world.operator)
    resp = await client_with_db.get(_url(world.client))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["cardPurchasesToPost"]["count"] == 2
    assert data["titles"] is not None


async def test_operador_de_outro_tenant_da_mesma_org_e_negado_sem_nome_e_com_trilha(
    client_with_db: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    await _login(client_with_db, world.neighbor_operator)
    denied = select(func.count(AccessAudit.id)).where(
        AccessAudit.client_id == world.client.id, AccessAudit.action == "denied"
    )
    before = await db_session.scalar(denied)
    resp = await client_with_db.get(_url(world.client))
    assert resp.status_code in (403, 404), resp.text
    assert CLIENT_NAME not in resp.text
    assert NEIGHBOR_NAME not in resp.text
    after = await db_session.scalar(denied)
    assert (after or 0) == (before or 0) + 1
