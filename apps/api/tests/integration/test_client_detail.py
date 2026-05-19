"""Testes de integração do detalhe do cliente + cache L1 + histórico (S7).

Cobre:
    - GET /api/v1/clients/{id}              (BACK 4.1)
    - PATCH /api/v1/clients/{id}/sync-accounts
    - GET /api/v1/clients/{id}/reconciliations  (BACK 4.2)

Cenários:
    Cache L1:
        - Miss: chama Omie, popula cache, retorna contas (CC + CA).
        - Hit em < 24h: NÃO chama Omie (assertado via respx call_count == 0).
        - TTL expirado (> 24h): chama Omie de novo.
        - Force sync ignora TTL e atualiza synced_at para `now()`.
        - Idempotência: 2 syncs com as mesmas contas mantêm UNIQUE consistente.
        - Omie retorna AuthError → 502 OMIE_SYNC_FAILED.
        - Tipos `CC` e `CA` ambos retornam.

    RBAC:
        - Manager fora da carteira recebe 403 nos 3 endpoints.
        - Admin recebe 200 sempre.
        - Não autenticado → 401.

    Histórico:
        - Ordenado por created_at DESC.
        - Filtra por omie_conta_id.
        - Filtra por month=YYYY-MM (range half-open do mês).
        - Filtros combinados.
        - Paginação respeita pageSize (default 10, max 50).
        - Sessão com status='error' expõe error_message.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    OmieAccountCache,
    ReconciliationSession,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


# ----------------------------------------------------------------------
# Constantes / helpers (paralelos a tests/integration/test_clients.py —
# prefixos diferentes para evitar colisão de e-mails entre arquivos)
# ----------------------------------------------------------------------

ADMIN_EMAIL = "detail-admin@hologram.com.br"
MANAGER_A_EMAIL = "detail-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "detail-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

OMIE_CONTACORRENTE_URL = "https://app.omie.com.br/api/v1/geral/contacorrente/"

FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"


def _omie_response(items: list[dict[str, Any]]) -> httpx.Response:
    """Wrapper que mimica o payload de `ListarContasCorrentes`.

    Page size do client é 100 — uma página com < 100 itens encerra a paginação.
    A chave do array no envelope é literalmente `ListarContasCorrentes`
    (mesmo nome do método, ver doc oficial Omie).
    """
    return httpx.Response(200, json={"ListarContasCorrentes": items})


def _conta_payload(
    *,
    n_cod_cc: int,
    descricao: str = "Conta Teste",
    codigo_banco: str = "999",
    tipo: str = "CC",
) -> dict[str, Any]:
    return {
        "nCodCC": n_cod_cc,
        "descricao": descricao,
        "codigo_banco": codigo_banco,
        "tipo_conta_corrente": tipo,
    }


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    name: str = "Test User",
    active: bool = True,
) -> User:
    user = User(
        name=name,
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=active,
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


async def _seed_account_cache(
    session: AsyncSession,
    *,
    client: Client,
    omie_conta_id: int,
    name: str = "Conta",
    bank_name: str = "Banco",
    account_type: str = "CC",
    synced_at: datetime | None = None,
) -> OmieAccountCache:
    """Insere uma linha no cache L1 e atualiza o sync timestamp do Client.

    A decisão de TTL passou a ser baseada em `clients.omie_accounts_synced_at`
    (não mais em `MAX(omie_accounts_cache.synced_at)`), então o helper precisa
    atualizar AMBOS para refletir o invariante: se há linhas no cache, há
    timestamp de sync no Client com data ≥ ao das linhas.

    `synced_at=None` deixa cair em `now()` (server_default no row + Python now
    no Client).
    """
    effective_synced_at = synced_at or datetime.now(UTC)
    row = OmieAccountCache(
        client_id=client.id,
        omie_conta_id=omie_conta_id,
        name=name,
        bank_name=bank_name,
        account_type=account_type,
    )
    row.synced_at = effective_synced_at
    session.add(row)
    client.omie_accounts_synced_at = effective_synced_at
    await session.flush()
    return row


async def _seed_reconciliation(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    omie_conta_id: int,
    reference_month: date,
    status: str = "done",
    file_hash: str | None = None,
    error_message: str | None = None,
    counts: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
    created_at_offset: timedelta = timedelta(),
) -> ReconciliationSession:
    """Cria uma sessão de conciliação. `counts` = (file_total, conciliated,
    sem_omie, omie_sem_arquivo, anomaly). `created_at_offset` permite
    espaçar artificialmente as sessões para testar a ordenação."""
    total, conc, sem_omie, omie_sem_arq, anomaly = counts
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=omie_conta_id,
        reference_month=reference_month,
        date_tolerance_days=3,
        file_hash=file_hash or uuid4().hex,
        status=status,
        error_message=error_message,
        balance_start=Decimal("0.00"),
        total_file_entries=total,
        conciliated_count=conc,
        sem_omie_count=sem_omie,
        omie_sem_arquivo_count=omie_sem_arq,
        anomaly_count=anomaly,
    )
    session.add(sess)
    await session.flush()
    if created_at_offset != timedelta():
        # Ajusta o created_at apenas se o teste pediu — server_default já
        # cobre o caso comum.
        sess.created_at = sess.created_at + created_at_offset
        await session.flush()
    return sess


async def _login_as(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# RBAC
# ----------------------------------------------------------------------


class TestDetailRBAC:
    async def test_unauthenticated_returns_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.get(f"/api/v1/clients/{uuid4()}")
        assert resp.status_code == 401

    async def test_manager_other_portfolio_returns_403_on_detail(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_b = await _seed_client(db_session, name="Do B", creator=admin, manager=mgr_b)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{cliente_b.id}")
        assert resp.status_code == 403

    async def test_manager_other_portfolio_returns_403_on_sync_accounts(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_b = await _seed_client(db_session, name="Do B", creator=admin, manager=mgr_b)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.patch(f"/api/v1/clients/{cliente_b.id}/sync-accounts")
        assert resp.status_code == 403

    async def test_manager_other_portfolio_returns_403_on_reconciliations(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_b = await _seed_client(db_session, name="Do B", creator=admin, manager=mgr_b)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{cliente_b.id}/reconciliations")
        assert resp.status_code == 403

    async def test_admin_accesses_any_client(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Qualquer", creator=admin, manager=mgr)
        # Pré-popula cache pra evitar chamada Omie no detalhe (RBAC é o foco aqui)
        await _seed_account_cache(db_session, client=target, omie_conta_id=1)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# GET /clients/{id} — Cache L1
# ----------------------------------------------------------------------


class TestClientDetail:
    @respx.mock
    async def test_cache_miss_fetches_from_omie_and_returns_accounts(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Quial", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        route = respx.post(OMIE_CONTACORRENTE_URL).mock(
            return_value=_omie_response(
                [
                    _conta_payload(
                        n_cod_cc=1001,
                        descricao="Sicredi 91263-1",
                        codigo_banco="748",
                        tipo="CC",
                    ),
                    _conta_payload(
                        n_cod_cc=2002,
                        descricao="Cartão Itaú",
                        codigo_banco="341",
                        tipo="CA",
                    ),
                ]
            )
        )

        resp = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["id"] == str(target.id)
        assert body["name"] == "Quial"
        assert body["accounts_synced_at"] is not None
        assert len(body["accounts"]) == 2
        types = {a["account_type"] for a in body["accounts"]}
        assert types == {"CC", "CA"}  # ambos os tipos retornam (DoD)
        assert route.called
        # Não vaza credenciais
        assert "omie_app_key" not in body
        assert "omie_app_key_encrypted" not in body

        # Cache foi populado
        rows = (
            (
                await db_session.execute(
                    select(OmieAccountCache).where(OmieAccountCache.client_id == target.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2

    @respx.mock
    async def test_cache_hit_within_ttl_does_not_call_omie(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Cache Hit", creator=admin, manager=mgr)

        # Cache fresco — synced_at recente (1h atrás)
        await _seed_account_cache(
            db_session,
            client=target,
            omie_conta_id=42,
            name="Conta Cacheada",
            bank_name="Banco Cache",
            account_type="CC",
            synced_at=datetime.now(UTC) - timedelta(hours=1),
        )

        route = respx.post(OMIE_CONTACORRENTE_URL)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["accounts"]) == 1
        assert body["accounts"][0]["omie_conta_id"] == 42
        assert body["accounts"][0]["name"] == "Conta Cacheada"

        # DoD: cache hit NÃO chama Omie
        assert route.call_count == 0

    @respx.mock
    async def test_cache_expired_triggers_resync(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Stale", creator=admin, manager=mgr)

        # Cache stale (25h atrás → fora do TTL de 24h)
        old_synced = datetime.now(UTC) - timedelta(hours=25)
        await _seed_account_cache(
            db_session,
            client=target,
            omie_conta_id=999,
            name="Antiga",
            synced_at=old_synced,
        )

        route = respx.post(OMIE_CONTACORRENTE_URL).mock(
            return_value=_omie_response(
                [_conta_payload(n_cod_cc=111, descricao="Nova Conta", tipo="CC")]
            )
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # DoD: TTL expirado dispara resync
        assert route.called
        # Cache foi reescrito: a antiga sumiu, só a nova ficou (clean-slate)
        assert len(body["accounts"]) == 1
        assert body["accounts"][0]["omie_conta_id"] == 111

    @respx.mock
    async def test_omie_auth_error_during_sync_returns_502(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="AuthFail", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        respx.post(OMIE_CONTACORRENTE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "faultstring": "App Key não autorizada",
                    "faultcode": "SOAP-ENV:Client-101",
                },
            )
        )

        resp = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp.status_code == 502
        body = resp.json()
        assert body["error"]["code"] == "OMIE_SYNC_FAILED"
        assert "credenciais" in body["error"]["userMessage"].lower()

    @respx.mock
    async def test_empty_omie_response_persists_zero_rows_and_caches_ttl(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Regressão (bug descoberto em 29/04 com Quial):

        Quando o Omie retorna `ListarContasCorrentes: []`, o cache fica sem
        linhas. Antes do fix, `MAX(synced_at)` voltava None na 2ª request e
        o cache miss disparava de novo — toda request batia o Omie.

        Após o fix, o `synced_at` é gravado em `clients.omie_accounts_synced_at`,
        então a 2ª request dentro do TTL é cache hit (não chama Omie).
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Empty", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        route = respx.post(OMIE_CONTACORRENTE_URL).mock(return_value=_omie_response([]))

        # 1ª request — cache miss, chama Omie, retorna lista vazia, persiste
        # synced_at na coluna do Client.
        resp1 = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp1.status_code == 200, resp1.text
        body1 = resp1.json()
        assert body1["accounts"] == []
        assert body1["accounts_synced_at"] is not None
        assert route.call_count == 1

        # 2ª request — DEVE ser cache hit, mesmo com cache vazio.
        resp2 = await client_with_db.get(f"/api/v1/clients/{target.id}")
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["accounts"] == []
        assert body2["accounts_synced_at"] == body1["accounts_synced_at"]
        # DoD: 2ª chamada NÃO bate o Omie. Antes do fix, route.call_count == 2.
        assert route.call_count == 1


# ----------------------------------------------------------------------
# PATCH /clients/{id}/sync-accounts — Force sync
# ----------------------------------------------------------------------


class TestForceSync:
    @respx.mock
    async def test_force_sync_ignores_fresh_cache_and_updates_synced_at(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Force", creator=admin, manager=mgr)

        # Cache fresco (1h atrás) — get_or_sync iria considerar cache hit
        old_synced = datetime.now(UTC) - timedelta(hours=1)
        await _seed_account_cache(
            db_session,
            client=target,
            omie_conta_id=10,
            name="Antiga",
            synced_at=old_synced,
        )

        route = respx.post(OMIE_CONTACORRENTE_URL).mock(
            return_value=_omie_response(
                [_conta_payload(n_cod_cc=20, descricao="Nova após force", tipo="CC")]
            )
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.patch(f"/api/v1/clients/{target.id}/sync-accounts")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Force sync sempre chama Omie
        assert route.called
        # Cache foi reescrito com a nova lista
        assert len(body["accounts"]) == 1
        assert body["accounts"][0]["omie_conta_id"] == 20
        # synced_at avançou
        new_synced = datetime.fromisoformat(body["accounts_synced_at"])
        assert new_synced > old_synced

    @respx.mock
    async def test_two_consecutive_syncs_keep_unique_consistent(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        target = await _seed_client(db_session, name="Dup", creator=admin, manager=mgr)
        await _login_as(client_with_db, ADMIN_EMAIL)

        respx.post(OMIE_CONTACORRENTE_URL).mock(
            return_value=_omie_response(
                [
                    _conta_payload(n_cod_cc=1, descricao="A", tipo="CC"),
                    _conta_payload(n_cod_cc=2, descricao="B", tipo="CC"),
                ]
            )
        )

        # 1ª sincronização (force pra ignorar TTL)
        resp1 = await client_with_db.patch(f"/api/v1/clients/{target.id}/sync-accounts")
        assert resp1.status_code == 200

        # 2ª sincronização — mesmas contas
        resp2 = await client_with_db.patch(f"/api/v1/clients/{target.id}/sync-accounts")
        assert resp2.status_code == 200

        # UNIQUE consistente: exatamente 2 linhas no cache, sem duplicata
        rows = (
            (
                await db_session.execute(
                    select(OmieAccountCache).where(OmieAccountCache.client_id == target.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        omie_ids = {r.omie_conta_id for r in rows}
        assert omie_ids == {1, 2}


# ----------------------------------------------------------------------
# GET /clients/{id}/reconciliations
# ----------------------------------------------------------------------


@pytest.fixture
async def seeded_history(
    db_session: AsyncSession,
) -> tuple[Client, list[ReconciliationSession], User]:
    """Seed reaproveitado pelos cenários de listagem.

    Cria 3 sessões com dados variados — as datas são fixadas para que os
    filtros sejam previsíveis. `created_at` é diferenciado via offset pra
    garantir ordem estável.
    """
    admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
    mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
    target = await _seed_client(db_session, name="History", creator=admin, manager=mgr)

    # Mais antiga (offset -2h)
    s1 = await _seed_reconciliation(
        db_session,
        client=target,
        creator=admin,
        omie_conta_id=100,
        reference_month=date(2026, 3, 1),
        status="done",
        counts=(50, 45, 3, 2, 1),
        created_at_offset=timedelta(hours=-2),
    )
    # Intermediária (offset -1h)
    s2 = await _seed_reconciliation(
        db_session,
        client=target,
        creator=admin,
        omie_conta_id=200,
        reference_month=date(2026, 4, 1),
        status="error",
        error_message="Falha ao processar arquivo: timeout",
        created_at_offset=timedelta(hours=-1),
    )
    # Mais recente (offset 0)
    s3 = await _seed_reconciliation(
        db_session,
        client=target,
        creator=admin,
        omie_conta_id=100,
        reference_month=date(2026, 4, 1),
        status="done",
        counts=(20, 18, 1, 1, 0),
    )
    return target, [s1, s2, s3], admin


class TestReconciliationsHistory:
    async def test_lists_ordered_by_created_at_desc(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, sessions, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{target.id}/reconciliations")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pagination"]["total"] == 3
        assert body["pagination"]["pageSize"] == 10  # default S7

        # s3 (mais recente) primeiro, depois s2, depois s1
        ids_in_order = [item["id"] for item in body["data"]]
        expected = [str(sessions[2].id), str(sessions[1].id), str(sessions[0].id)]
        assert ids_in_order == expected

    async def test_filter_by_omie_conta_id(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, sessions, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{target.id}/reconciliations?omie_conta_id=100"
        )
        assert resp.status_code == 200
        body = resp.json()
        # Apenas s1 e s3 são da conta 100
        assert body["pagination"]["total"] == 2
        ids = {item["id"] for item in body["data"]}
        assert ids == {str(sessions[0].id), str(sessions[2].id)}

    async def test_filter_by_month(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, sessions, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{target.id}/reconciliations?month=2026-04"
        )
        assert resp.status_code == 200
        body = resp.json()
        # s2 e s3 são de 2026-04
        ids = {item["id"] for item in body["data"]}
        assert ids == {str(sessions[1].id), str(sessions[2].id)}

    async def test_filters_combined(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, sessions, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        # conta 100 + abril → só s3
        resp = await client_with_db.get(
            f"/api/v1/clients/{target.id}/reconciliations?omie_conta_id=100&month=2026-04"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["id"] == str(sessions[2].id)

    async def test_pagination_respects_page_size(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, _, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{target.id}/reconciliations?page=1&pageSize=2"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["pageSize"] == 2
        assert body["pagination"]["total"] == 3
        assert body["pagination"]["totalPages"] == 2

    async def test_invalid_month_format_returns_400(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, _, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{target.id}/reconciliations?month=2026-13"
        )
        assert resp.status_code == 400

    async def test_session_with_error_exposes_error_message(
        self,
        client_with_db: AsyncClient,
        seeded_history: tuple[Client, list[ReconciliationSession], User],
    ) -> None:
        target, sessions, _ = seeded_history
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{target.id}/reconciliations?omie_conta_id=200"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        item = body["data"][0]
        assert item["id"] == str(sessions[1].id)
        assert item["status"] == "error"
        assert item["error_message"] == "Falha ao processar arquivo: timeout"

    async def test_manager_only_sees_own_portfolio_history(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Manager A não deve nem conseguir bater o GET (403) num cliente do B."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_b = await _seed_client(db_session, name="Do B", creator=admin, manager=mgr_b)
        await _seed_reconciliation(
            db_session,
            client=cliente_b,
            creator=admin,
            omie_conta_id=1,
            reference_month=date(2026, 4, 1),
        )
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{cliente_b.id}/reconciliations")
        assert resp.status_code == 403
