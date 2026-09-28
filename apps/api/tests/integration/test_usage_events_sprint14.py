"""Os dois eventos da Sprint 14 contra o banco (BACK 14.2).

O teste que CONTA LINHAS (ADR-066-BE): uma materialização gera exatamente N linhas
`fechamento_produzido` (N = tipos de origem distintos entre os itens) com a competência
em `YYYY-MM`, e a contagem de `depara_aplicado` não muda; a segunda materialização
gera mais N; o 409 (competência nunca sincronizada) gera zero.

E a prova de durabilidade da recusa: `arquivo_processado{rejeitado=true}` sobrevive ao
`rollback()` que o `get_db_session` de produção aplica quando o serviço levanta o
`AppError` logo depois — enquanto a linha do aceito (sem commit próprio) não sobrevive
ao mesmo rollback. A fixture `db_session` roda com `join_transaction_mode=
"create_savepoint"`: o `commit()` da aplicação libera o savepoint e o `rollback()`
seguinte só desfaz o que veio depois (mesma mecânica de `client_with_request_rollback`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientMappingDecision,
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
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Fechamento#1"
JUN = date(2026, 6, 1)


async def _user(db: AsyncSession, *, role: UserRole) -> User:
    user = User(
        name="Fechamento",
        email=f"fp-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=UserScope.SYSTEM.value,
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
    client: Client
    destination: MappingDestination
    target: MappingTarget


def _mov(db: AsyncSession, w: World, source_type: str, mid: str, amount: str, cat: str) -> None:
    db.add(
        ClientMovement(
            client_id=w.client.id,
            source_type=source_type,
            source_movement_id=mid,
            competence=JUN,
            movement_date=date(2026, 6, 10),
            amount=Decimal(amount),
            category_code=cat,
        )
    )


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.client = Client(name="Cliente fechamento", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
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
    # Dois tipos de origem na MESMA competência: o cliente que migrou do Omie para
    # o arquivo (ou o inverso) — é o caso que prova "uma linha por tipo distinto".
    _mov(db_session, w, "omie", "1", "-100.00", "2.01")
    _mov(db_session, w, "omie", "2", "-30.00", "2.01")
    _mov(db_session, w, "arquivo", "deadbeef:1", "-20.00", "arq-1")
    db_session.add_all(
        [
            ClientMappingDecision(
                client_id=w.client.id,
                source_type="omie",
                category_code="2.01",
                destination_id=w.destination.id,
                decision_type="alvo",
                target_id=w.target.id,
                origin=DecisionOrigin.CONFIRMADA.value,
                effective_from=JUN,
                author_id=w.admin.id,
            ),
            ClientMappingDecision(
                client_id=w.client.id,
                source_type="arquivo",
                category_code="arq-1",
                destination_id=w.destination.id,
                decision_type="nao_mapear",
                target_id=None,
                origin=DecisionOrigin.CONFIRMADA.value,
                effective_from=JUN,
                author_id=w.admin.id,
            ),
        ]
    )
    await db_session.flush()
    return w


def _base(w: World) -> str:
    return f"/api/v1/clients/{w.client.id}/mapping/demonstrativo_contabil"


async def _events(db: AsyncSession, event: str, client_id: Any) -> list[UsageEvent]:
    rows = (await db.execute(select(UsageEvent).where(UsageEvent.event == event))).scalars().all()
    return [r for r in rows if r.props.get("client_id") == str(client_id)]


async def _materialize(http: AsyncClient, w: World, competence: str = "2026-06") -> Any:
    preview = await http.get(f"{_base(w)}/preview", params={"competence": competence})
    if preview.status_code != 200:
        return preview
    return await http.post(
        f"{_base(w)}/materializations",
        json={
            "competence": competence,
            "previewToken": preview.json()["data"]["previewToken"],
            "confirmPartialCoverage": True,
        },
    )


class TestFechamentoProduzidoContaLinhas:
    async def test_uma_linha_por_tipo_de_origem_e_depara_inalterado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        v1 = await _materialize(client_with_db, world)
        assert v1.status_code == 201, v1.text

        fechamentos = await _events(db_session, "fechamento_produzido", world.client.id)
        assert sorted(f.props["tipo_origem"] for f in fechamentos) == ["arquivo", "omie"]
        for f in fechamentos:
            assert f.session_id is None
            assert set(f.props) == {"client_id", "tipo_origem", "competencia"}
            assert f.props["competencia"] == "2026-06"
        # O evento da S12 continua sendo UM por materialização.
        assert len(await _events(db_session, "depara_aplicado", world.client.id)) == 1

        v2 = await _materialize(client_with_db, world)
        assert v2.status_code == 201, v2.text
        assert v2.json()["data"]["version"] == 2
        assert len(await _events(db_session, "fechamento_produzido", world.client.id)) == 4
        assert len(await _events(db_session, "depara_aplicado", world.client.id)) == 2

    async def test_materializacao_recusada_nao_emite(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _materialize(client_with_db, world, competence="2026-07")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "BASE_NAO_SINCRONIZADA"
        assert await _events(db_session, "fechamento_produzido", world.client.id) == []
        assert await _events(db_session, "depara_aplicado", world.client.id) == []

    async def test_a_formula_d30_le_o_evento(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """`count(DISTINCT client_id) WHERE tipo_origem = 'arquivo'` = 1 depois de 2 versões."""
        await _login(client_with_db, world.admin)
        assert (await _materialize(client_with_db, world)).status_code == 201
        assert (await _materialize(client_with_db, world)).status_code == 201
        arquivo = [
            f
            for f in await _events(db_session, "fechamento_produzido", world.client.id)
            if f.props["tipo_origem"] == "arquivo"
        ]
        assert len(arquivo) == 2
        assert len({f.props["client_id"] for f in arquivo}) == 1


class TestArquivoProcessadoDuravel:
    async def test_a_recusa_sobrevive_ao_rollback_da_request(
        self, db_session: AsyncSession, world: World
    ) -> None:
        """Política REAL de transação: o serviço levanta `AppError` → `rollback()`."""
        service = UsageEventService(UsageEventRepository(db_session))
        ok = await service.emit_arquivo_processado(
            client_id=world.client.id,
            mapeamento_id=None,
            linhas=0,
            colunas_reconhecidas=4,
            rejeitado=True,
            motivo="cabecalho_divergente",
        )
        assert ok is True
        # O que `get_db_session` faz quando o handler levanta.
        await db_session.rollback()

        db_session.expunge_all()
        rows = await _events(db_session, "arquivo_processado", world.client.id)
        assert len(rows) == 1
        assert rows[0].props == {
            "client_id": str(world.client.id),
            "mapeamento_id": None,
            "linhas": 0,
            "colunas_reconhecidas": 4,
            "rejeitado": True,
            "motivo": "cabecalho_divergente",
        }

    async def test_o_aceito_nao_commita_por_conta_propria(
        self, db_session: AsyncSession, world: World
    ) -> None:
        """Contraste: sem a barreira, o rollback leva a linha — é o commit que salva a recusa."""
        service = UsageEventService(UsageEventRepository(db_session))
        ok = await service.emit_arquivo_processado(
            client_id=world.client.id,
            mapeamento_id=None,
            linhas=240,
            colunas_reconhecidas=6,
            rejeitado=False,
            motivo="nenhum",
        )
        assert ok is True
        await db_session.rollback()
        db_session.expunge_all()
        assert await _events(db_session, "arquivo_processado", world.client.id) == []
