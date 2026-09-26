"""Rotas de decisão do de-para contra o banco (BACK 12.4 — R2/R4/R6/R7).

O unitário (`test_client_mapping_decisions.py`) cobre as regras sobre dublês; aqui
elas passam pelo HTTP e pelo Postgres: a permissão do PRD (operador 403 com linha na
trilha), os erros tipados (422/409/400), a vigência gravada e a herança lida de
volta. O cross-tenant/cross-org das rotas está na bateria da lista canônica.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.core.authz import CurrentUser
from app.core.exceptions import MappingDecisionDuplicateError
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    Client,
    ClientAssignment,
    ClientChartOfAccount,
    ClientMappingDecision,
    DecisionType,
    MappingDestination,
    MappingTarget,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_mapping.repository import ClientMappingRepository
from app.modules.client_mapping.service import ClientMappingDecisionService, DecisionInput
from app.modules.client_movements.competence import current_competence, format_competence
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.service import MappingCatalogService

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@DePara#1"


async def _user(
    db: AsyncSession, *, role: UserRole, scope: UserScope = UserScope.SYSTEM, client_id: Any = None
) -> User:
    user = User(
        name="DePara",
        email=f"dp-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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
    manager: User
    tenant_manager: User
    operator: User
    client: Client
    demonstrativo: MappingDestination


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.manager = await _user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente decisões", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    w.tenant_manager = await _user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.operator = await _user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.demonstrativo = (
        await db_session.execute(
            select(MappingDestination).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == "demonstrativo_contabil",
            )
        )
    ).scalar_one()
    for code in ("1.01", "1.02"):
        db_session.add(MappingTarget(destination_id=w.demonstrativo.id, code=code, name=code))
    await db_session.flush()
    return w


def _url(w: World, kind: str = "demonstrativo_contabil", suffix: str = "decisions") -> str:
    return f"/api/v1/clients/{w.client.id}/mapping/{kind}/{suffix}"


async def _decisions(db: AsyncSession, w: World) -> list[ClientMappingDecision]:
    stmt = (
        select(ClientMappingDecision)
        .where(ClientMappingDecision.client_id == w.client.id)
        .order_by(ClientMappingDecision.effective_from)
    )
    return list((await db.execute(stmt)).scalars().all())


class TestPermissao:
    @pytest.mark.parametrize("papel", ["admin", "manager", "tenant_manager"])
    async def test_celulas_do_prd_escrevem(
        self, client_with_db: AsyncClient, world: World, papel: str
    ) -> None:
        await _login(client_with_db, getattr(world, papel))
        resp = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "alvo", "targetCode": "1.01"}
        )
        assert resp.status_code == 200, resp.text
        corrente = format_competence(current_competence())
        assert resp.json()["data"] == {
            "effectiveFrom": corrente,
            "created": 1,
            "resolved": 0,
            "unchanged": 0,
        }

    async def test_operador_recebe_403_e_a_negacao_fica_na_trilha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.operator)
        antes = await db_session.scalar(
            select(func.count(AccessAudit.id)).where(
                AccessAudit.action == "denied", AccessAudit.client_id == world.client.id
            )
        )
        resp = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "nao_mapear"}
        )
        historico = await client_with_db.get(
            _url(world, suffix="decisions/history"), params={"categoryCode": "2.04"}
        )
        depois = await db_session.scalar(
            select(func.count(AccessAudit.id)).where(
                AccessAudit.action == "denied", AccessAudit.client_id == world.client.id
            )
        )
        assert resp.status_code == 403, resp.text
        assert historico.status_code == 200, "o operador VÊ; só não edita"
        assert depois == (antes or 0) + 1
        assert await _decisions(db_session, world) == []


class TestErrosTipados:
    async def test_alvo_inexistente_422_nomeando(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "alvo", "targetCode": "7.77"}
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "ALVO_INEXISTENTE"
        assert "7.77" in resp.json()["error"]["userMessage"]

    async def test_destino_nao_configurado_409_nomeando(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await db_session.execute(
            update(MappingDestination)
            .where(MappingDestination.id == world.demonstrativo.id)
            .values(active=False)
        )
        await _login(client_with_db, world.admin)
        desativado = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "nao_mapear"}
        )
        inexistente = await client_with_db.post(
            _url(world, kind="orcamento_anual"),
            json={"categoryCode": "2.04", "decision": "nao_mapear"},
        )
        for resp, kind in (
            (desativado, "demonstrativo_contabil"),
            (inexistente, "orcamento_anual"),
        ):
            assert resp.status_code == 409, resp.text
            assert resp.json()["error"]["code"] == "DESTINO_NAO_CONFIGURADO"
            assert kind in resp.json()["error"]["userMessage"]

    async def test_duplicada_na_mesma_vigencia_409(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        primeira = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "alvo", "targetCode": "1.01"}
        )
        segunda = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "alvo", "targetCode": "1.02"}
        )
        assert primeira.status_code == 200
        assert segunda.status_code == 409, segunda.text
        assert segunda.json()["error"]["code"] == "DECISAO_DUPLICADA"

    async def test_cliente_encerrado_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await db_session.execute(
            update(Client).where(Client.id == world.client.id).values(closed_at=datetime.now(UTC))
        )
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            _url(world), json={"categoryCode": "2.04", "decision": "nao_mapear"}
        )
        assert resp.status_code == 409, resp.text

    @pytest.mark.parametrize(
        "body",
        [
            {"categoryCode": "2.04", "decision": "alvo"},
            {"categoryCode": "2.04", "decision": "nao_mapear", "targetCode": "1.01"},
            {"categoryCode": "2.04", "decision": "talvez"},
            {"categoryCode": "2.04", "decision": "nao_mapear", "effectiveFrom": "2026-13"},
            {"decision": "nao_mapear"},
        ],
    )
    async def test_forma_invalida_e_400_nunca_422(
        self, client_with_db: AsyncClient, world: World, body: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(_url(world), json=body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


class TestVigenciaERetroatividade:
    async def test_retroativa_pede_confirmacao_listando_e_so_grava_com_ela(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        body: dict[str, Any] = {
            "categoryCode": "2.04",
            "decision": "nao_mapear",
            "effectiveFrom": "2020-01",
        }
        primeira = await client_with_db.post(_url(world), json=body)
        assert primeira.status_code == 409, primeira.text
        assert primeira.json()["error"]["code"] == "RETROATIVA_REQUER_CONFIRMACAO"
        assert "2020-01" in primeira.json()["error"]["details"]["competences"]
        assert await _decisions(db_session, world) == []

        confirmada = await client_with_db.post(
            _url(world), json=body | {"confirmRetroactive": True}
        )
        assert confirmada.status_code == 200, confirmada.text
        (decisao,) = await _decisions(db_session, world)
        assert decisao.effective_from == date(2020, 1, 1)


class TestHerancaPelaRota:
    async def test_iniciar_herda_so_no_demonstrativo_e_le_de_volta(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add_all(
            [
                ClientChartOfAccount(
                    client_id=world.client.id, category_code="2.01", dre_code="1.01"
                ),
                ClientChartOfAccount(
                    client_id=world.client.id, category_code="9.01", dre_code=None
                ),
                ClientChartOfAccount(
                    client_id=world.client.id, category_code="3.01", dre_code="7.77"
                ),
            ]
        )
        await db_session.flush()
        await _login(client_with_db, world.admin)

        outro = await client_with_db.post(
            _url(world, kind="fluxo_de_caixa", suffix="inherit"), json={}
        )
        herda = await client_with_db.post(_url(world, suffix="inherit"), json={})

        assert outro.status_code == 200
        assert outro.json()["data"]["state"] == "destino_sem_heranca"
        assert herda.status_code == 200, herda.text
        data = herda.json()["data"]
        assert (data["state"], data["created"], data["withoutDre"]) == ("ok", 1, 1)
        assert data["missingTargetCodes"] == ["7.77"]

        historico = await client_with_db.get(
            _url(world, suffix="decisions/history"), params={"categoryCode": "2.01"}
        )
        (vigencia,) = historico.json()["data"]
        assert (vigencia["origin"], vigencia["targetCode"]) == ("herdada", "1.01")

    async def test_sem_plano_de_contas_responde_estado_sem_erro(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(_url(world, suffix="inherit"), json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["state"] == "sem_plano_de_contas"

    async def test_confirmacao_em_lote_exige_o_parametro(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add(
            ClientChartOfAccount(client_id=world.client.id, category_code="2.01", dre_code="1.01")
        )
        await db_session.flush()
        await _login(client_with_db, world.admin)
        await client_with_db.post(_url(world, suffix="inherit"), json={})

        so_conta = await client_with_db.post(
            _url(world, suffix="decisions/confirm-inherited"), json={}
        )
        confirma = await client_with_db.post(
            _url(world, suffix="decisions/confirm-inherited"), json={"confirm": True}
        )
        assert so_conta.json()["data"] == {"affected": 1, "applied": False, "result": None}
        assert confirma.json()["data"]["applied"] is True
        origens = {d.origin for d in await _decisions(db_session, world)}
        assert origens == {"confirmada"}


class TestCorridaNoBanco:
    """Retrabalho 12.4: a outra requisição grava ENTRE a nossa leitura e o nosso INSERT.

    A leitura do serviço devolve o estado de antes; a linha concorrente entra na MESMA
    chave e vigência logo depois. A UNIQUE real do Postgres decide: escrita → 409
    `DECISAO_DUPLICADA` (SAVEPOINT, a sessão segue viva); herança → no-op pelo
    `ON CONFLICT DO NOTHING`. Nunca `IntegrityError` → 500.
    """

    @staticmethod
    def _service(db: AsyncSession) -> ClientMappingDecisionService:
        catalog = MappingCatalogRepository(db)
        return ClientMappingDecisionService(
            ClientMappingRepository(db),
            catalog=catalog,
            catalog_service=MappingCatalogService(catalog),
        )

    @staticmethod
    def _author(user: User) -> CurrentUser:
        return CurrentUser(
            id=str(user.id),
            email=user.email,
            name=user.name,
            role=user.role,
            scope=user.scope,
            client_id=None,
            organization_id=user.organization_id,
        )

    @staticmethod
    def _concurrent_row(w: World, *, target_code: str, origin: str) -> Any:
        async def write(db: AsyncSession) -> None:
            target = (
                await db.execute(
                    select(MappingTarget).where(
                        MappingTarget.destination_id == w.demonstrativo.id,
                        MappingTarget.code == target_code,
                    )
                )
            ).scalar_one()
            db.add(
                ClientMappingDecision(
                    client_id=w.client.id,
                    source_type="omie",
                    category_code="2.01",
                    destination_id=w.demonstrativo.id,
                    decision_type="alvo",
                    target_id=target.id,
                    origin=origin,
                    effective_from=current_competence(),
                    author_id=w.admin.id,
                )
            )
            await db.flush()

        return write

    @staticmethod
    def _stale_read(service: ClientMappingDecisionService, db: AsyncSession, write: Any) -> None:
        repo = service._repo
        original = repo.list_decisions

        async def stale(*args: Any, **kwargs: Any) -> list[ClientMappingDecision]:
            snapshot = await original(*args, **kwargs)
            await write(db)
            return snapshot

        repo.list_decisions = stale  # type: ignore[method-assign]

    async def test_escrita_concorrente_vira_409_e_a_sessao_segue(
        self, db_session: AsyncSession, world: World
    ) -> None:
        service = self._service(db_session)
        self._stale_read(
            service,
            db_session,
            self._concurrent_row(world, target_code="1.01", origin="confirmada"),
        )
        with pytest.raises(MappingDecisionDuplicateError) as exc:
            await service.write_decisions(
                world.client,
                "demonstrativo_contabil",
                [
                    DecisionInput(
                        category_code="2.01", decision_type=DecisionType.ALVO, target_code="1.02"
                    )
                ],
                author=self._author(world.admin),
            )
        assert exc.value.status_code == 409
        (so_a_outra,) = await _decisions(db_session, world)
        assert so_a_outra.target_id is not None

    async def test_iniciar_de_para_concorrente_e_no_op(
        self, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add(
            ClientChartOfAccount(client_id=world.client.id, category_code="2.01", dre_code="1.01")
        )
        await db_session.flush()
        service = self._service(db_session)
        self._stale_read(
            service,
            db_session,
            self._concurrent_row(world, target_code="1.01", origin="herdada"),
        )
        result = await service.inherit(
            world.client, "demonstrativo_contabil", author=self._author(world.admin)
        )
        assert (result.state, result.created, result.already_decided) == ("ok", 0, 1)
        assert len(await _decisions(db_session, world)) == 1
