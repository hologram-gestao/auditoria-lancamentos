"""Prévia e materialização do de-para contra o banco (BACK 12.6 — R3/R5).

O teste que CONTA LINHAS de `depara_aplicado` (ADR-066-BE): duas materializações = duas
linhas com os centavos certos; prévia sozinha e materialização recusada = zero. E a
imutabilidade afirmada no banco: reaplicar cria v2 e a v1 (com os itens) fica igual,
campo a campo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientMappingDecision,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    ClientMovement,
    ClientMovementSync,
    DecisionOrigin,
    MappingDestination,
    MappingTarget,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Materializa#1"
JUN = date(2026, 6, 1)
SET = date(2026, 9, 1)


async def _user(
    db: AsyncSession, *, role: UserRole, scope: UserScope = UserScope.SYSTEM, client_id: Any = None
) -> User:
    user = User(
        name="Materializa",
        email=f"mt-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


class World:
    admin: User
    operator: User
    client: Client
    destination: MappingDestination
    target: MappingTarget


def _mov(db: AsyncSession, w: World, mid: str, amount: str, category: str | None) -> None:
    db.add(
        ClientMovement(
            client_id=w.client.id,
            source_type="omie",
            source_movement_id=mid,
            competence=JUN,
            movement_date=date(2026, 6, 10),
            amount=Decimal(amount),
            category_code=category,
        )
    )


def _decision(w: World, category: str, start: date, *, target: MappingTarget | None) -> Any:
    return ClientMappingDecision(
        client_id=w.client.id,
        source_type="omie",
        category_code=category,
        destination_id=w.destination.id,
        decision_type="alvo" if target else "nao_mapear",
        target_id=target.id if target else None,
        origin=DecisionOrigin.CONFIRMADA.value,
        effective_from=start,
        author_id=w.admin.id,
    )


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.client = Client(name="Cliente materializa", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    w.operator = await _user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.destination = (
        await db_session.execute(
            select(MappingDestination).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == "demonstrativo_contabil",
            )
        )
    ).scalar_one()
    w.target = MappingTarget(destination_id=w.destination.id, code="1.01", name="Receita")
    db_session.add(w.target)
    await db_session.flush()
    db_session.add(
        ClientMovementSync(client_id=w.client.id, competence=JUN, synced_at=datetime.now(UTC))
    )
    _mov(db_session, w, "1", "-100.00", "2.01")
    _mov(db_session, w, "2", "-30.00", "3.01")
    _mov(db_session, w, "3", "-20.00", "4.01")
    _mov(db_session, w, "4", "-7.00", None)
    db_session.add_all(
        [_decision(w, "2.01", JUN, target=w.target), _decision(w, "3.01", JUN, target=None)]
    )
    await db_session.flush()
    return w


def _base(w: World) -> str:
    return f"/api/v1/clients/{w.client.id}/mapping/demonstrativo_contabil"


async def _events(db: AsyncSession, w: World) -> list[UsageEvent]:
    rows = (
        (await db.execute(select(UsageEvent).where(UsageEvent.event == "depara_aplicado")))
        .scalars()
        .all()
    )
    return [r for r in rows if r.props.get("client_id") == str(w.client.id)]


async def _preview(http: AsyncClient, w: World) -> dict[str, Any]:
    resp = await http.get(f"{_base(w)}/preview", params={"competence": "2026-06"})
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def _materialize(http: AsyncClient, w: World, token: str, *, confirm: bool = True) -> Any:
    return await http.post(
        f"{_base(w)}/materializations",
        json={"competence": "2026-06", "previewToken": token, "confirmPartialCoverage": confirm},
    )


class TestPrevia:
    async def test_quatro_situacoes_e_cobertura(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.operator)
        data = await _preview(client_with_db, world)
        s = data["situations"]
        assert (Decimal(s["alvo"]["amount"]), s["alvo"]["count"]) == (Decimal("100.00"), 1)
        assert Decimal(s["naoMapear"]["amount"]) == Decimal("30.00")
        assert Decimal(s["semDecisao"]["amount"]) == Decimal("20.00")
        assert Decimal(s["semCategoria"]["amount"]) == Decimal("7.00")
        assert Decimal(data["coveragePct"]) == Decimal("86.67")  # 130 / 150
        assert Decimal(data["naoMapearPct"]) == Decimal("20.00")
        assert [c["categoryCode"] for c in data["undecidedCategories"]] == ["4.01"]
        assert data["baseState"]["syncedAt"] is not None
        assert await _events(db_session, world) == [], "prévia sozinha não emite"

    async def test_nunca_sincronizada_409_e_so_sem_categoria_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        nunca = await client_with_db.get(
            f"{_base(world)}/preview", params={"competence": "2026-07"}
        )
        assert nunca.status_code == 409
        assert nunca.json()["error"]["code"] == "BASE_NAO_SINCRONIZADA"

        await db_session.execute(
            update(ClientMovement)
            .where(ClientMovement.client_id == world.client.id)
            .values(category_code=None)
        )
        so_sem_categoria = await client_with_db.get(
            f"{_base(world)}/preview", params={"competence": "2026-06"}
        )
        assert so_sem_categoria.status_code == 200, so_sem_categoria.text
        data = so_sem_categoria.json()["data"]
        assert Decimal(data["coverageDenominator"]) == 0
        assert data["coveragePct"] is None
        assert data["situations"]["semCategoria"]["count"] == 4

    async def test_anterior_a_primeira_vigencia_409_com_a_mais_antiga(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await db_session.execute(
            update(ClientMappingDecision)
            .where(ClientMappingDecision.client_id == world.client.id)
            .values(effective_from=SET)
        )
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(f"{_base(world)}/preview", params={"competence": "2026-06"})
        assert resp.status_code == 409
        assert resp.json()["error"]["details"]["earliestCompetence"] == "2026-09"


class TestMaterializacao:
    async def test_duas_materializacoes_duas_linhas_e_v1_intacta(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        p1 = await _preview(client_with_db, world)
        v1 = await _materialize(client_with_db, world, p1["previewToken"])
        assert v1.status_code == 201, v1.text
        assert v1.json()["data"]["version"] == 1
        assert v1.json()["data"]["partialCoverageConfirmed"] is True

        mat1 = (
            await db_session.execute(
                select(ClientMappingMaterialization).where(
                    ClientMappingMaterialization.client_id == world.client.id
                )
            )
        ).scalar_one()
        snapshot = {c.name: getattr(mat1, c.name) for c in ClientMappingMaterialization.__table__.c}
        itens_v1 = sorted(
            (i.source_movement_id, i.situation, i.target_code, i.amount)
            for i in (
                await db_session.execute(
                    select(ClientMappingMaterializationItem).where(
                        ClientMappingMaterializationItem.materialization_id == mat1.id
                    )
                )
            ).scalars()
        )

        p2 = await _preview(client_with_db, world)
        v2 = await _materialize(client_with_db, world, p2["previewToken"])
        assert v2.status_code == 201, v2.text
        assert v2.json()["data"]["version"] == 2

        db_session.expire_all()
        mat1_again = await db_session.get(ClientMappingMaterialization, snapshot["id"])
        assert mat1_again is not None
        assert {
            c.name: getattr(mat1_again, c.name) for c in ClientMappingMaterialization.__table__.c
        } == snapshot
        assert itens_v1 == [
            ("1", "alvo", "1.01", Decimal("-100.00")),
            ("2", "nao_mapear", None, Decimal("-30.00")),
            ("3", "sem_decisao", None, Decimal("-20.00")),
            ("4", "sem_categoria", None, Decimal("-7.00")),
        ]

        eventos = await _events(db_session, world)
        assert len(eventos) == 2
        for evento in eventos:
            assert evento.props == {
                "client_id": str(world.client.id),
                "destino": "demonstrativo_contabil",
                "valor_com_decisao_centavos": 13000,
                "valor_nao_mapear_centavos": 3000,
                "valor_sem_decisao_centavos": 2000,
                "categorias_sem_decisao": 1,
            }

    async def test_token_desatualizado_e_parcial_sem_confirmacao_nao_emitem(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        p1 = await _preview(client_with_db, world)
        sem_confirmar = await _materialize(client_with_db, world, p1["previewToken"], confirm=False)
        assert sem_confirmar.status_code == 409
        assert sem_confirmar.json()["error"]["code"] == "COBERTURA_PARCIAL_REQUER_CONFIRMACAO"

        _mov(db_session, world, "5", "-1.00", "2.01")  # a base mudou depois da prévia
        await db_session.flush()
        velho = await _materialize(client_with_db, world, p1["previewToken"])
        assert velho.status_code == 409
        assert velho.json()["error"]["code"] == "PREVIA_DESATUALIZADA"

        assert await _events(db_session, world) == []
        count = (
            await db_session.execute(
                select(ClientMappingMaterialization).where(
                    ClientMappingMaterialization.client_id == world.client.id
                )
            )
        ).all()
        assert count == []

    async def test_vigencia_de_junho_mesmo_depois_de_uma_de_setembro(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add(_decision(world, "2.01", SET, target=None))  # setembro: nao_mapear
        await db_session.flush()
        await _login(client_with_db, world.admin)
        p = await _preview(client_with_db, world)
        resp = await _materialize(client_with_db, world, p["previewToken"])
        assert resp.status_code == 201, resp.text
        item = (
            await db_session.execute(
                select(ClientMappingMaterializationItem).where(
                    ClientMappingMaterializationItem.client_id == world.client.id,
                    ClientMappingMaterializationItem.source_movement_id == "1",
                )
            )
        ).scalar_one()
        assert (item.situation, item.target_code, item.decision_effective_from) == (
            "alvo",
            "1.01",
            JUN,
        )

    async def test_operador_403_e_encerrado_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.operator)
        p = await _preview(client_with_db, world)
        negado = await _materialize(client_with_db, world, p["previewToken"])
        assert negado.status_code == 403
        await db_session.execute(
            update(Client).where(Client.id == world.client.id).values(closed_at=datetime.now(UTC))
        )
        await _login(client_with_db, world.admin)
        encerrado = await _materialize(client_with_db, world, p["previewToken"])
        assert encerrado.status_code == 409
        assert await _events(db_session, world) == []
