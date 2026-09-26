"""Catálogo do de-para + schema das decisões e materializações (BACK 12.3 — R1/R2).

Afirma contra o banco:
  - os cinco destinos nascem com a organização (seed idempotente e `POST /organizations`);
  - escrita só com `manage_mapping_catalog` (plataforma/admin); leitura de quem
    pertence à organização, inclusive usuário de cliente;
  - destino/alvo de outra organização = 404 sem vazar nome;
  - lote atômico com 409 listando códigos; apagar alvo referenciado = 409, desativar 200;
  - organização suspensa = 409 na escrita; forma inválida = 400 `VALIDATION_ERROR`;
  - alvo inexistente numa decisão = 422 tipado com o código (o validador do serviço);
  - remover e recriar a CONEXÃO não leva as decisões (source_type ≠ FK de conexão);
  - encerramento purga as decisões e RETÉM as materializações; a exclusão definitiva
    leva tudo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, null, select, update

from app.core.exceptions import MappingTargetNotFoundError
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientConnection,
    ClientMappingDecision,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    DecisionOrigin,
    DecisionType,
    MappingDestination,
    MappingTarget,
    Organization,
    ProviderType,
    User,
    UserRole,
    UserScope,
)
from app.modules.clients.repository import ClientRepository
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.service import MappingCatalogService

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Catalogo#1"
SECRET_TARGET = "Receita Bruta Sigilosa da Hologram"
BASE = "/api/v1/mapping-destinations"


async def _user(
    db: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    organization: Organization | None = None,
    client_id: Any = None,
) -> User:
    extra: dict[str, Any] = {}
    if scope is UserScope.PLATFORM:
        # `null()`, não `None`: com `server_default` o ORM omitiria o campo e o
        # banco preencheria a Hologram — e a plataforma não tem organização.
        extra["organization_id"] = null()
    elif organization is not None:
        extra["organization_id"] = organization.id
    user = User(
        name="Catalogo",
        email=f"cat-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        **extra,
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
    client: Client
    client_manager: User
    admin_b: User
    org_b: Organization
    demonstrativo: MappingDestination
    fluxo: MappingDestination


async def _destination(db: AsyncSession, organization_id: Any, kind: str) -> MappingDestination:
    stmt = select(MappingDestination).where(
        MappingDestination.organization_id == organization_id,
        MappingDestination.destination_type == kind,
    )
    return (await db.execute(stmt)).scalar_one()


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    # O banco de teste nasce do metadata (sem a migration): o seed dos cinco
    # destinos da Hologram é o MESMO método que a criação de organização usa.
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.manager = await _user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente de-para", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    w.client_manager = await _user(
        db_session,
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.org_b = Organization(name=f"Escritorio B {uuid4().hex[:6]}")
    db_session.add(w.org_b)
    await db_session.flush()
    w.admin_b = await _user(db_session, role=UserRole.ADMIN, organization=w.org_b)
    w.demonstrativo = await _destination(
        db_session, HOLOGRAM_ORGANIZATION_ID, "demonstrativo_contabil"
    )
    w.fluxo = await _destination(db_session, HOLOGRAM_ORGANIZATION_ID, "fluxo_de_caixa")
    return w


def _targets_url(destination: MappingDestination) -> str:
    return f"{BASE}/{destination.id}/targets"


async def _plant_target(
    db: AsyncSession, destination: MappingDestination, code: str
) -> MappingTarget:
    target = MappingTarget(destination_id=destination.id, code=code, name=f"Alvo {code}")
    db.add(target)
    await db.flush()
    return target


async def _plant_decision(
    db: AsyncSession, w: World, *, target: MappingTarget | None, category: str = "2.04.94"
) -> ClientMappingDecision:
    decision = ClientMappingDecision(
        client_id=w.client.id,
        source_type=ProviderType.OMIE.value,
        category_code=category,
        destination_id=target.destination_id if target else w.demonstrativo.id,
        decision_type=(DecisionType.ALVO if target else DecisionType.NAO_MAPEAR).value,
        target_id=target.id if target else None,
        origin=DecisionOrigin.CONFIRMADA.value,
        effective_from=date(2026, 6, 1),
        author_id=w.admin.id,
    )
    db.add(decision)
    await db.flush()
    return decision


class TestSeed:
    async def test_cinco_destinos_na_organizacao_e_seed_idempotente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await MappingCatalogRepository(db_session).seed_default_destinations(
            HOLOGRAM_ORGANIZATION_ID
        )
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(BASE)
        assert resp.status_code == 200, resp.text
        tipos = sorted(d["type"] for d in resp.json()["data"])
        assert tipos == sorted(
            [
                "conta_contabil",
                "demonstrativo_contabil",
                "demonstrativo_gerencial",
                "fluxo_de_caixa",
                "natureza_fiscal",
            ]
        ), "rodar o seed duas vezes não duplica"

    async def test_organizacao_criada_pela_plataforma_nasce_com_os_cinco(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        platform = await _user(db_session, role=UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM)
        await _login(client_with_db, platform)
        resp = await client_with_db.post("/api/v1/organizations", json={"name": "Escritorio Novo"})
        assert resp.status_code == 201, resp.text
        org_id = resp.json()["id"]
        count = await db_session.scalar(
            select(func.count(MappingDestination.id)).where(
                MappingDestination.organization_id == org_id
            )
        )
        assert count == 5


class TestPermissaoEAlcance:
    async def test_admin_cria_lote_e_manager_nao(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        body = {"targets": [{"code": f"1.{i:02d}", "name": f"Conta {i}"} for i in range(1, 15)]}
        await _login(client_with_db, world.manager)
        negado = await client_with_db.post(_targets_url(world.demonstrativo), json=body)
        assert negado.status_code == 403, negado.text

        await _login(client_with_db, world.admin)
        criado = await client_with_db.post(_targets_url(world.demonstrativo), json=body)
        assert criado.status_code == 201, criado.text
        assert len(criado.json()["data"]) == 14

    async def test_usuario_de_cliente_le_o_catalogo_da_propria_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _plant_target(db_session, world.demonstrativo, "1.01")
        await _login(client_with_db, world.client_manager)
        destinos = await client_with_db.get(BASE)
        alvos = await client_with_db.get(_targets_url(world.demonstrativo))
        assert destinos.status_code == 200
        assert alvos.status_code == 200
        assert [a["code"] for a in alvos.json()["data"]] == ["1.01"]
        escrita = await client_with_db.post(
            _targets_url(world.demonstrativo), json={"targets": [{"code": "1.02", "name": "x"}]}
        )
        assert escrita.status_code == 403

    async def test_admin_de_outra_organizacao_recebe_404_sem_vazar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        target = MappingTarget(
            destination_id=world.demonstrativo.id, code="1.01", name=SECRET_TARGET
        )
        db_session.add(target)
        await db_session.flush()
        await _login(client_with_db, world.admin_b)

        lista = await client_with_db.get(BASE)
        alvos = await client_with_db.get(_targets_url(world.demonstrativo))
        edita = await client_with_db.patch(
            f"{_targets_url(world.demonstrativo)}/{target.id}", json={"name": "x"}
        )
        apaga = await client_with_db.delete(f"{_targets_url(world.demonstrativo)}/{target.id}")

        assert lista.status_code == 200
        assert all(d["organizationId"] == str(world.org_b.id) for d in lista.json()["data"])
        for resp in (alvos, edita, apaga):
            assert resp.status_code == 404, resp.text
            assert SECRET_TARGET not in resp.text

    async def test_paginacao_pelo_alias_e_teto(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        for i in range(5):
            await _plant_target(db_session, world.demonstrativo, f"3.{i}")
        await _login(client_with_db, world.admin)
        page = await client_with_db.get(
            _targets_url(world.demonstrativo), params={"pageSize": 2, "page": 2}
        )
        assert page.status_code == 200
        assert [a["code"] for a in page.json()["data"]] == ["3.2", "3.3"]
        assert page.json()["pagination"]["total"] == 5
        teto = await client_with_db.get(_targets_url(world.demonstrativo), params={"pageSize": 101})
        assert teto.status_code == 400
        assert teto.json()["error"]["code"] == "VALIDATION_ERROR"


class TestRegras:
    async def test_lote_com_codigo_repetido_e_409_e_atomico(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _plant_target(db_session, world.demonstrativo, "1.01")
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            _targets_url(world.demonstrativo),
            json={"targets": [{"code": "1.02", "name": "Nova"}, {"code": "1.01", "name": "Dup"}]},
        )
        assert resp.status_code == 409, resp.text
        assert "1.01" in resp.json()["error"]["userMessage"]
        codes = (
            (
                await db_session.execute(
                    select(MappingTarget.code).where(
                        MappingTarget.destination_id == world.demonstrativo.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(codes) == ["1.01"], "nada do lote foi gravado"

    async def test_alvo_referenciado_409_desativar_200_livre_204(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        usado = await _plant_target(db_session, world.demonstrativo, "1.01")
        livre = await _plant_target(db_session, world.demonstrativo, "1.02")
        await _plant_decision(db_session, world, target=usado)
        await _login(client_with_db, world.admin)
        url = _targets_url(world.demonstrativo)

        apaga_usado = await client_with_db.delete(f"{url}/{usado.id}")
        desativa = await client_with_db.patch(f"{url}/{usado.id}", json={"active": False})
        apaga_livre = await client_with_db.delete(f"{url}/{livre.id}")

        assert apaga_usado.status_code == 409, apaga_usado.text
        assert desativa.status_code == 200, desativa.text
        assert desativa.json()["data"]["active"] is False
        assert apaga_livre.status_code == 204

    async def test_organizacao_suspensa_recusa_escrita_com_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        platform = await _user(db_session, role=UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM)
        await MappingCatalogRepository(db_session).seed_default_destinations(world.org_b.id)
        destino_b = await _destination(db_session, world.org_b.id, "fluxo_de_caixa")
        await db_session.execute(
            update(Organization).where(Organization.id == world.org_b.id).values(active=False)
        )
        await _login(client_with_db, platform)
        resp = await client_with_db.post(
            _targets_url(destino_b), json={"targets": [{"code": "1", "name": "x"}]}
        )
        assert resp.status_code == 409, resp.text

    @pytest.mark.parametrize("tipo", ["Fluxo De Caixa", "fluxo-de-caixa", "1fluxo", ""])
    async def test_tipo_fora_do_formato_de_slug_e_400(
        self, client_with_db: AsyncClient, world: World, tipo: str
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(BASE, json={"type": tipo, "name": "X"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_sexto_tipo_e_cadastro_e_tipo_repetido_e_409(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        novo = await client_with_db.post(BASE, json={"type": "orcamento", "name": "Orçamento"})
        repetido = await client_with_db.post(
            BASE, json={"type": "fluxo_de_caixa", "name": "Outro fluxo"}
        )
        assert novo.status_code == 201, novo.text
        assert repetido.status_code == 409, repetido.text

    async def test_alvo_inexistente_e_422_tipado_nomeando_o_codigo(
        self, db_session: AsyncSession, world: World
    ) -> None:
        """O validador que 12.4 e 12.5 reusam, contra o banco."""
        await _plant_target(db_session, world.demonstrativo, "1.01")
        service = MappingCatalogService(MappingCatalogRepository(db_session))
        with pytest.raises(MappingTargetNotFoundError) as exc:
            await service.require_targets(world.demonstrativo, ["1.01", "7.77"])
        assert exc.value.status_code == 422
        assert "7.77" in exc.value.user_message


class TestDecisoesSobrevivemATrocaDeConexao:
    async def test_remover_e_recriar_a_conexao_preserva_as_decisoes(
        self, db_session: AsyncSession, world: World
    ) -> None:
        """`source_type` é o TIPO do provedor, não FK: a conexão vai, o de-para fica."""
        conexao = ClientConnection(
            client_id=world.client.id, provider_type=ProviderType.OMIE.value, label="Omie"
        )
        db_session.add(conexao)
        await db_session.flush()
        target = await _plant_target(db_session, world.demonstrativo, "1.01")
        decisao = await _plant_decision(db_session, world, target=target)
        nao_mapear = await _plant_decision(db_session, world, target=None, category="3.01")

        await db_session.execute(delete(ClientConnection).where(ClientConnection.id == conexao.id))
        db_session.add(
            ClientConnection(
                client_id=world.client.id, provider_type=ProviderType.OMIE.value, label="Omie"
            )
        )
        await db_session.flush()

        ids = set(
            (
                await db_session.execute(
                    select(ClientMappingDecision.id).where(
                        ClientMappingDecision.client_id == world.client.id
                    )
                )
            ).scalars()
        )
        assert ids == {decisao.id, nao_mapear.id}


class TestEncerramentoERetencao:
    async def _materialize(self, db: AsyncSession, w: World) -> ClientMappingMaterialization:
        mat = ClientMappingMaterialization(
            client_id=w.client.id,
            destination_id=w.demonstrativo.id,
            destination_type="demonstrativo_contabil",
            competence=date(2026, 6, 1),
            version=1,
            input_hash="0" * 64,
            decisions_used=[],
            mapped_amount=Decimal("100.00"),
            mapped_count=1,
            not_mapped_amount=Decimal("0.00"),
            not_mapped_count=0,
            undecided_amount=Decimal("0.00"),
            undecided_count=0,
            uncategorized_amount=Decimal("0.00"),
            uncategorized_count=0,
            undecided_categories=0,
            author_id=w.client_manager.id,
        )
        db.add(mat)
        await db.flush()
        db.add(
            ClientMappingMaterializationItem(
                materialization_id=mat.id,
                client_id=w.client.id,
                source_type="omie",
                source_movement_id="1",
                movement_date=date(2026, 6, 10),
                amount=Decimal("-100.00"),
                category_code="2.04.94",
                situation="alvo",
                target_code="1.01",
                decision_effective_from=date(2026, 6, 1),
            )
        )
        await db.flush()
        return mat

    async def test_encerrar_purga_decisoes_e_retem_materializacoes(
        self, db_session: AsyncSession, world: World
    ) -> None:
        target = await _plant_target(db_session, world.demonstrativo, "1.01")
        await _plant_decision(db_session, world, target=target)
        await self._materialize(db_session, world)
        client_id = world.client.id

        await ClientRepository(db_session).close_client_purge(client_id)
        await db_session.execute(
            update(Client).where(Client.id == client_id).values(closed_at=datetime.now(UTC))
        )

        decisoes = await db_session.scalar(
            select(func.count(ClientMappingDecision.id)).where(
                ClientMappingDecision.client_id == client_id
            )
        )
        materializacoes = await db_session.scalar(
            select(func.count(ClientMappingMaterialization.id)).where(
                ClientMappingMaterialization.client_id == client_id
            )
        )
        itens = await db_session.scalar(
            select(func.count(ClientMappingMaterializationItem.id)).where(
                ClientMappingMaterializationItem.client_id == client_id
            )
        )
        assert decisoes == 0, "decisão é configuração — sai no encerramento"
        assert materializacoes == 1, "materialização é o que aconteceu — FICA"
        assert itens == 1

    async def test_exclusao_definitiva_leva_tudo_mesmo_com_autor_do_tenant(
        self, db_session: AsyncSession, world: World
    ) -> None:
        """O autor da materialização é usuário DO tenant (RESTRICT) — e a exclusão passa."""
        target = await _plant_target(db_session, world.demonstrativo, "1.01")
        await _plant_decision(db_session, world, target=target)
        await self._materialize(db_session, world)
        client_id = world.client.id

        await ClientRepository(db_session).delete_client_cascade(world.client)

        for model in (
            ClientMappingDecision,
            ClientMappingMaterialization,
            ClientMappingMaterializationItem,
        ):
            count = await db_session.scalar(
                select(func.count(model.id)).where(model.client_id == client_id)
            )
            assert count == 0, model.__tablename__
