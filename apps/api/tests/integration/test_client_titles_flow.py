"""O fluxo previsto calculado em SQL, e a rota `GET …/titles/flow` (86e3k1q4g).

Repositório: um título por faixa nos dois lados, com valores de ordens de
grandeza distintas, mais ruído (liquidado, ausente, outro tenant). Rota: o
caminho feliz com o "hoje" real do servidor, nunca sincronizada, operador do
próprio tenant 200 e operador de outro tenant negado sem nome e com trilha.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.authz import CurrentUser
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    TitleStatus,
    TitleType,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_titles.flow import FlowBucket
from app.modules.client_titles.repository import ClientTitlesRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Fluxo#2026"
CLIENT_NAME = "Cliente do fluxo previsto"
NEIGHBOR_NAME = "Vizinho do fluxo previsto"
HOJE = date(2026, 10, 7)

#: Dias ATÉ o vencimento de um título por faixa, contra `HOJE`.
DIAS = {
    FlowBucket.VENCIDOS: -15,
    FlowBucket.ATE_7: 3,
    FlowBucket.D8_30: 20,
    FlowBucket.D31_60: 45,
    FlowBucket.D61_90: 75,
    FlowBucket.D90_MAIS: 200,
}
RECEBER = {
    FlowBucket.VENCIDOS: "100000.00",
    FlowBucket.ATE_7: "1.00",
    FlowBucket.D8_30: "20.00",
    FlowBucket.D31_60: "300.00",
    FlowBucket.D61_90: "4000.00",
    FlowBucket.D90_MAIS: "50000.00",
}
PAGAR = {
    FlowBucket.VENCIDOS: "7.00",
    FlowBucket.ATE_7: "60.00",
    FlowBucket.D8_30: "500.00",
    FlowBucket.D31_60: "8000.00",
    FlowBucket.D61_90: "90000.00",
    FlowBucket.D90_MAIS: "0.50",
}


async def _seed_user(
    db: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Fluxo",
        email=f"fluxo-{uuid4().hex[:10]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        scope=scope.value,
        client_id=client_id,
        active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_client(db: AsyncSession, *, creator: User, name: str) -> Client:
    client = Client(name=name, created_by=creator.id)
    db.add(client)
    await db.flush()
    return client


def _row(
    external_id: str,
    *,
    due_date: date,
    amount: str,
    title_type: TitleType,
    status: TitleStatus = TitleStatus.EM_ABERTO,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "title_type": title_type.value,
        "due_date": due_date,
        "amount": Decimal(amount),
        "status": status.value,
        "category_code": "2.04.94",
        "supplier_code": 2624256082,
        "omie_conta_id": 2617722760,
        "document_number": None,
    }


def _carteira(today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket, days in DIAS.items():
        due = today + timedelta(days=days)
        rows.append(
            _row(
                f"R-{bucket.value}",
                due_date=due,
                amount=RECEBER[bucket],
                title_type=TitleType.A_RECEBER,
            )
        )
        rows.append(
            _row(
                f"P-{bucket.value}",
                due_date=due,
                amount=PAGAR[bucket],
                title_type=TitleType.A_PAGAR,
            )
        )
    # Ruído que NÃO pode entrar em faixa nenhuma.
    rows.append(
        _row(
            "R-LIQ",
            due_date=today,
            amount="999999.99",
            title_type=TitleType.A_RECEBER,
            status=TitleStatus.LIQUIDADO,
        )
    )
    rows.append(
        _row(
            "P-AUS",
            due_date=today,
            amount="888888.88",
            title_type=TitleType.A_PAGAR,
            status=TitleStatus.AUSENTE_NA_ORIGEM,
        )
    )
    return rows


async def _seed_titles(db: AsyncSession, client: Client, rows: list[dict[str, Any]]) -> None:
    repo = ClientTitlesRepository(db)
    now = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)
    await repo.reconcile_cycle(client.id, rows, synced_at=now)
    await repo.mark_sync_succeeded(client.id, at=now)
    await db.flush()


def _staff(user: User) -> CurrentUser:
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        scope=user.scope,
        client_id=user.client_id,
        organization_id=user.organization_id,
    )


class TestFluxoEmSQL:
    async def test_cada_faixa_recebe_o_seu_titulo_nos_dois_lados(
        self, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, role=UserRole.ADMIN)
        client = await _seed_client(db_session, creator=admin, name=CLIENT_NAME)
        await _seed_titles(db_session, client, _carteira(HOJE))

        flow = await ClientTitlesRepository(db_session).flow(
            client.id, today=HOJE, user=_staff(admin)
        )

        for bucket in FlowBucket:
            receber = flow[bucket][TitleType.A_RECEBER]
            pagar = flow[bucket][TitleType.A_PAGAR]
            assert receber.total == Decimal(RECEBER[bucket]), bucket
            assert pagar.total == Decimal(PAGAR[bucket]), bucket
            assert (receber.count, pagar.count) == (1, 1), bucket

    async def test_as_faixas_somam_o_em_aberto_e_vencidos_e_o_vencido_do_aging(
        self, db_session: AsyncSession
    ) -> None:
        """A partição contra o resultado REAL das duas queries."""
        admin = await _seed_user(db_session, role=UserRole.ADMIN)
        client = await _seed_client(db_session, creator=admin, name=CLIENT_NAME)
        await _seed_titles(db_session, client, _carteira(HOJE))
        repo = ClientTitlesRepository(db_session)

        flow = await repo.flow(client.id, today=HOJE, user=_staff(admin))
        aging = await repo.aging(client.id, today=HOJE)

        for title_type in TitleType:
            soma = sum((flow[b][title_type].total for b in FlowBucket), start=Decimal("0.00"))
            qtd = sum(flow[b][title_type].count for b in FlowBucket)
            assert soma == aging[title_type].total_em_aberto, title_type
            assert qtd == aging[title_type].qtd_em_aberto, title_type
            vencidos = flow[FlowBucket.VENCIDOS][title_type]
            assert vencidos.total == aging[title_type].total_vencido, title_type

    async def test_limites_contra_o_banco_hoje_inclusive(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, role=UserRole.ADMIN)
        client = await _seed_client(db_session, creator=admin, name=CLIENT_NAME)
        dias = {
            -1: "1.00",
            0: "10.00",
            7: "100.00",
            8: "1000.00",
            30: "10000.00",
            31: "100000.00",
            90: "0.01",
            91: "0.10",
        }
        await _seed_titles(
            db_session,
            client,
            [
                _row(
                    f"d{d}",
                    due_date=HOJE + timedelta(days=d),
                    amount=v,
                    title_type=TitleType.A_RECEBER,
                )
                for d, v in dias.items()
            ],
        )

        flow = await ClientTitlesRepository(db_session).flow(
            client.id, today=HOJE, user=_staff(admin)
        )
        receber = {b: flow[b][TitleType.A_RECEBER].total for b in FlowBucket}
        assert receber[FlowBucket.VENCIDOS] == Decimal("1.00")
        assert receber[FlowBucket.ATE_7] == Decimal("110.00")
        assert receber[FlowBucket.D8_30] == Decimal("11000.00")
        assert receber[FlowBucket.D31_60] == Decimal("100000.00")
        assert receber[FlowBucket.D61_90] == Decimal("0.01")
        assert receber[FlowBucket.D90_MAIS] == Decimal("0.10")

    async def test_titulo_de_outro_cliente_nunca_entra(self, db_session: AsyncSession) -> None:
        admin = await _seed_user(db_session, role=UserRole.ADMIN)
        client = await _seed_client(db_session, creator=admin, name=CLIENT_NAME)
        neighbor = await _seed_client(db_session, creator=admin, name=NEIGHBOR_NAME)
        await _seed_titles(
            db_session,
            neighbor,
            [_row("X", due_date=HOJE, amount="777.00", title_type=TitleType.A_RECEBER)],
        )
        await _seed_titles(db_session, client, [])

        flow = await ClientTitlesRepository(db_session).flow(
            client.id, today=HOJE, user=_staff(admin)
        )
        assert all(
            side.total == Decimal("0.00") and side.count == 0
            for sides in flow.values()
            for side in sides.values()
        )

    async def test_usuario_de_outro_tenant_nao_soma_nada_mesmo_sem_guard_de_rota(
        self, db_session: AsyncSession
    ) -> None:
        """Defense-in-depth: `scoped_by_tenant` no próprio SELECT. Chamado direto
        (sem a rota), com o `client_id` alheio, o usuário de outro tenant vê
        zeros: a query força o tenant da LINHA dele."""
        admin = await _seed_user(db_session, role=UserRole.ADMIN)
        client = await _seed_client(db_session, creator=admin, name=CLIENT_NAME)
        neighbor = await _seed_client(db_session, creator=admin, name=NEIGHBOR_NAME)
        intruso = await _seed_user(
            db_session,
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=neighbor.id,
        )
        await _seed_titles(db_session, client, _carteira(HOJE))

        flow = await ClientTitlesRepository(db_session).flow(
            client.id, today=HOJE, user=_staff(intruso)
        )
        assert all(side.count == 0 for sides in flow.values() for side in sides.values())


# ----------------------------------------------------------------------
# A rota
# ----------------------------------------------------------------------


class World:
    admin: User
    client: Client
    neighbor: Client
    operator: User
    neighbor_operator: User


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
        db_session,
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.neighbor.id,
    )
    return w


async def _login(client: AsyncClient, user: User) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/titles/flow"


class TestRota:
    async def test_caminho_feliz_seis_faixas_com_liquido(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        # O "hoje" da rota é o do servidor; os vencimentos saem dele.
        today = datetime.now(UTC).date()
        await _seed_titles(db_session, world.client, _carteira(today))
        await _login(client_with_db, world.admin)

        resp = await client_with_db.get(_url(world.client))

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["referenceDate"] == today.isoformat()
        assert data["neverSynced"] is False
        assert data["syncedAt"] is not None
        assert [b["bucket"] for b in data["buckets"]] == [b.value for b in FlowBucket]
        for item in data["buckets"]:
            bucket = FlowBucket(item["bucket"])
            assert Decimal(item["aReceber"]["total"]) == Decimal(RECEBER[bucket])
            assert Decimal(item["aPagar"]["total"]) == Decimal(PAGAR[bucket])
            assert item["aReceber"]["count"] == 1
            assert Decimal(item["net"]) == Decimal(RECEBER[bucket]) - Decimal(PAGAR[bucket])
        # Nenhum nome no payload: só faixas, valores e contagens.
        assert CLIENT_NAME not in resp.text

        summary = (
            await client_with_db.get(f"/api/v1/clients/{world.client.id}/titles/summary")
        ).json()["data"]
        for side in ("aReceber", "aPagar"):
            soma = sum(Decimal(b[side]["total"]) for b in data["buckets"])
            assert soma == Decimal(summary[side]["totalEmAberto"]), side

    async def test_nunca_sincronizada_devolve_as_seis_faixas_zeradas(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)

        data = (await client_with_db.get(_url(world.client))).json()["data"]

        assert data["neverSynced"] is True
        assert data["syncedAt"] is None
        assert len(data["buckets"]) == 6
        for item in data["buckets"]:
            assert Decimal(item["aReceber"]["total"]) == 0
            assert item["aPagar"]["count"] == 0
            assert Decimal(item["net"]) == 0

    async def test_operador_do_proprio_tenant_le(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_titles(db_session, world.client, _carteira(datetime.now(UTC).date()))
        await _login(client_with_db, world.operator)

        resp = await client_with_db.get(_url(world.client))

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]["buckets"]) == 6

    async def test_operador_de_outro_tenant_e_negado_sem_nome_e_com_trilha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_titles(db_session, world.client, _carteira(datetime.now(UTC).date()))
        await _login(client_with_db, world.neighbor_operator)
        denied = select(func.count(AccessAudit.id)).where(
            AccessAudit.client_id == world.client.id, AccessAudit.action == "denied"
        )
        before = await db_session.scalar(denied)

        resp = await client_with_db.get(_url(world.client))

        assert resp.status_code in (403, 404), resp.text
        assert CLIENT_NAME not in resp.text
        assert NEIGHBOR_NAME not in resp.text
        assert "100000" not in resp.text
        after = await db_session.scalar(denied)
        assert (after or 0) == (before or 0) + 1

    async def test_sem_login_401(self, client_with_db: AsyncClient, world: World) -> None:
        assert (await client_with_db.get(_url(world.client))).status_code == 401
