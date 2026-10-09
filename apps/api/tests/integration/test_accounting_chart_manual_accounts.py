"""Conta do plano contábil incluída e editada À MÃO, pela API e contra Postgres (86e3nb816).

    POST  /api/v1/clients/{client_id}/accounting-chart/accounts               → criar
    PATCH /api/v1/clients/{client_id}/accounting-chart/accounts/{account_id}  → editar

O que este módulo afirma:
  - a conta nova entra cifrada, ativa e no LUGAR da classificação na lista (a
    `sort_key` é derivada na gravação, a lista não muda);
  - as regras são as da planilha: código repetido 409 com o código, código com
    separador 422 no campo, texto maior que a coluna 400 de forma;
  - a edição troca nome, tipo, classificação e situação; a ordem acompanha;
  - conta de OUTRO cliente pela rota deste é 404, e a linha alheia não muda;
  - conta usada por decisão do de-para ou pela conta do banco não vira sintética
    nem inativa: 422 com as CONTAGENS; renomear continua valendo;
  - `client_manager` cria e edita 403 com 1 linha `denied`; staff cria;
  - cliente encerrado: as duas 409;
  - o evento `plano_contabil_conta_editada` só leva IDs e campos;
  - nenhum nome nem código em log.

O cross-tenant e o cross-org das duas rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, null, select
from structlog.testing import capture_logs

from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAccountingAccount,
    ClientAssignment,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.db.models.client_mapping import ClientMappingDecision
from app.db.models.client_source_account_binding import ClientSourceAccountBinding
from app.db.models.mapping_catalog import MappingDestination
from app.db.models.organization import HOLOGRAM_ORGANIZATION_ID
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ContaManual#1"
_SECRET_NAME = "Alugueis a receber - Inquilino Sigiloso"
_SHEET = (
    b"codigo_reduzido;nome;tipo;classificacao\n"
    b"10;Ativo;sintetica;1.1\n"
    b"649;Banco conta movimento;analitica;1.1.1\n"
    b"700;Clientes;analitica;1.1.3\n"
)


async def _seed_user(
    session: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    extra: dict[str, Any] = {}
    if scope is UserScope.PLATFORM:
        extra["organization_id"] = null()
    user = User(
        name="Conta Manual",
        email=f"manual-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        **extra,
    )
    session.add(user)
    await session.flush()
    return user


class World:
    admin: User
    manager: User
    platform: User
    tenant_manager: User
    tenant_operator: User
    client: Client
    other: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.platform = await _seed_user(
        db_session, role=UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM
    )
    # Cliente SEM origem (sem DEK): a primeira conta manual provisiona a chave.
    w.client = Client(name="Cliente da Conta Manual", active=True, created_by=w.admin.id)
    w.other = Client(name="Outro Cliente", active=True, created_by=w.admin.id)
    db_session.add_all([w.client, w.other])
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    w.tenant_manager = await _seed_user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.tenant_operator = await _seed_user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    await db_session.flush()
    return w


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/accounting-chart"


async def _import(http: AsyncClient, client: Client) -> None:
    resp = await http.post(
        f"{_url(client)}/import", files={"file": ("plano.csv", _SHEET, "text/csv")}
    )
    assert resp.status_code == 200, resp.text


async def _create(http: AsyncClient, client: Client, **body: Any) -> Response:
    payload = {"code": "663", "name": _SECRET_NAME, "type": "analitica", **body}
    return await http.post(f"{_url(client)}/accounts", json=payload)


async def _patch(http: AsyncClient, client: Client, account_id: Any, **body: Any) -> Response:
    return await http.patch(f"{_url(client)}/accounts/{account_id}", json=body)


async def _codes_in_order(http: AsyncClient, client: Client) -> list[str]:
    resp = await http.get(_url(client), params={"pageSize": 100})
    assert resp.status_code == 200, resp.text
    return [row["code"] for row in resp.json()["data"]]


async def _account(db: AsyncSession, client_id: Any, code: str) -> ClientAccountingAccount:
    stmt = (
        select(ClientAccountingAccount)
        .where(ClientAccountingAccount.client_id == client_id, ClientAccountingAccount.code == code)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one()


async def _count(db: AsyncSession, client_id: Any) -> int:
    stmt = select(func.count(ClientAccountingAccount.id)).where(
        ClientAccountingAccount.client_id == client_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def _denied(db: AsyncSession, client_id: Any) -> list[AccessAudit]:
    stmt = select(AccessAudit).where(
        AccessAudit.action == "denied", AccessAudit.client_id == client_id
    )
    return list((await db.execute(stmt)).scalars().all())


async def _events(db: AsyncSession, client_id: Any) -> list[dict[str, Any]]:
    stmt = (
        select(UsageEvent)
        .where(UsageEvent.event == "plano_contabil_conta_editada")
        .order_by(UsageEvent.created_at)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [e.props for e in rows if e.props.get("client_id") == str(client_id)]


class TestCriar:
    async def test_entra_cifrada_ativa_e_no_lugar_da_classificacao(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import(client_with_db, world.client)

        with capture_logs() as logs:
            resp = await _create(client_with_db, world.client, classification="1.1.2")
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["code"] == "663"
        assert data["name"] == _SECRET_NAME
        assert data["nameResolved"] is True
        assert data["active"] is True
        assert data["postable"] is True

        # No LUGAR da classificação: entre 1.1.1 e 1.1.3, sem reordenar nada.
        assert await _codes_in_order(client_with_db, world.client) == ["10", "649", "663", "700"]
        row = await _account(db_session, world.client.id, "663")
        assert _SECRET_NAME not in row.name_encrypted
        assert row.created_by == world.admin.id

        (props,) = await _events(db_session, world.client.id)
        assert props == {
            "client_id": str(world.client.id),
            "account_id": data["id"],
            "operacao": "criada",
            "campos": [],
        }
        assert _SECRET_NAME not in repr(logs)
        assert "'663'" not in repr(logs)

    async def test_cliente_sem_dek_ganha_a_chave_na_primeira_conta(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _create(client_with_db, world.client)
        assert resp.status_code == 201, resp.text
        listed = (await client_with_db.get(_url(world.client))).json()["data"]
        assert [(r["code"], r["name"]) for r in listed] == [("663", _SECRET_NAME)]

    async def test_codigo_repetido_e_409_com_o_codigo_e_nada_gravado(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import(client_with_db, world.client)
        existing = await _account(db_session, world.client.id, "649")
        resp = await _create(client_with_db, world.client, code="649")
        assert resp.status_code == 409, resp.text
        error = resp.json()["error"]
        assert error["code"] == "CONTA_CONTABIL_CODIGO_EXISTENTE"
        assert error["details"] == {"code": "649", "accountId": str(existing.id)}
        assert await _count(db_session, world.client.id) == 3

    async def test_codigo_com_separador_e_422_no_campo(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _create(client_with_db, world.client, code="66;3")
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "CONTA_CONTABIL_INVALIDA"
        assert error["details"] == {"field": "code", "reason": "codigo_invalido"}
        assert await _count(db_session, world.client.id) == 0

    @pytest.mark.parametrize(
        "body",
        [{"code": "1" * 21}, {"name": "x" * 201}, {"classification": "1" * 41}, {"type": "x"}],
    )
    async def test_texto_maior_que_a_coluna_e_400_de_forma(
        self, client_with_db: AsyncClient, world: World, body: dict[str, str]
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _create(client_with_db, world.client, **body)
        assert resp.status_code == 400, resp.text


class TestEditar:
    async def test_nome_tipo_classificacao_e_situacao(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import(client_with_db, world.client)
        account = await _account(db_session, world.client.id, "700")

        resp = await _patch(
            client_with_db, world.client, account.id, name=_SECRET_NAME, classification="1.1.0"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert (data["name"], data["classification"]) == (_SECRET_NAME, "1.1.0")
        # A ordem acompanha a classificação nova.
        assert await _codes_in_order(client_with_db, world.client) == ["10", "700", "649"]

        resp = await _patch(client_with_db, world.client, account.id, type="sintetica")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["postable"] is False
        resp = await _patch(client_with_db, world.client, account.id, active=False)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["active"] is False

        events = await _events(db_session, world.client.id)
        assert [e["campos"] for e in events] == [["nome", "classificacao"], ["tipo"], ["situacao"]]
        assert all(e["operacao"] == "editada" for e in events)

    async def test_conta_de_outro_cliente_e_404_e_nao_muda(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import(client_with_db, world.other)
        alheia = await _account(db_session, world.other.id, "649")
        before = alheia.name_encrypted

        resp = await _patch(client_with_db, world.client, alheia.id, name="Sequestrada")
        assert resp.status_code == 404, resp.text
        assert "Banco conta movimento" not in resp.text
        assert (await _account(db_session, world.other.id, "649")).name_encrypted == before

    async def test_conta_em_uso_nao_sai_do_lancavel_mas_se_renomeia(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import(client_with_db, world.client)
        account = await _account(db_session, world.client.id, "649")
        await MappingCatalogRepository(db_session).seed_default_destinations(
            HOLOGRAM_ORGANIZATION_ID
        )
        destination_id = (
            await db_session.execute(
                select(MappingDestination.id).where(
                    MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                    MappingDestination.destination_type == "conta_contabil",
                )
            )
        ).scalar_one()
        db_session.add_all(
            [
                ClientMappingDecision(
                    client_id=world.client.id,
                    source_type="omie",
                    category_code=code,
                    destination_id=destination_id,
                    decision_type="alvo",
                    accounting_account_id=account.id,
                    origin="confirmada",
                    effective_from=date(2026, 1, 1),
                    author_id=world.admin.id,
                )
                for code in ("2.04.01", "2.04.02")
            ]
        )
        db_session.add(
            ClientSourceAccountBinding(
                client_id=world.client.id,
                source_type="arquivo",
                source_account_id=None,
                accounting_account_id=account.id,
                created_by=world.admin.id,
                updated_by=world.admin.id,
            )
        )
        await db_session.flush()

        for body, reason in (({"active": False}, "inativa"), ({"type": "sintetica"}, "sintetica")):
            resp = await _patch(client_with_db, world.client, account.id, **body)
            assert resp.status_code == 422, resp.text
            error = resp.json()["error"]
            assert error["code"] == "CONTA_CONTABIL_EM_USO"
            assert error["details"] == {"reason": reason, "decisionCount": 2, "bindingCount": 1}
            assert "2.04.01" not in resp.text

        row = await _account(db_session, world.client.id, "649")
        assert (row.active, row.account_type) == (True, "analitica")
        resp = await _patch(client_with_db, world.client, account.id, name="Banco renomeado")
        assert resp.status_code == 200, resp.text


class TestPermissoes:
    async def test_gerente_do_cliente_cria_e_edita_403_com_trilha(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import(client_with_db, world.client)
        account = await _account(db_session, world.client.id, "649")

        await _login(client_with_db, world.tenant_manager)
        resp = await _create(client_with_db, world.client)
        assert resp.status_code == 403, resp.text
        assert await _count(db_session, world.client.id) == 3
        (linha,) = await _denied(db_session, world.client.id)
        assert linha.user_id == world.tenant_manager.id
        assert linha.user_scope == "client"
        assert linha.actor_client_id == world.client.id

        resp = await _patch(client_with_db, world.client, account.id, name="x")
        assert resp.status_code == 403, resp.text
        assert len(await _denied(db_session, world.client.id)) == 2

    async def test_operador_403(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.tenant_operator)
        assert (await _create(client_with_db, world.client)).status_code == 403

    @pytest.mark.parametrize("who", ["manager", "platform"])
    async def test_staff_cria(self, client_with_db: AsyncClient, world: World, who: str) -> None:
        await _login(client_with_db, getattr(world, who))
        resp = await _create(client_with_db, world.client)
        assert resp.status_code == 201, resp.text


class TestEncerrado:
    async def test_criar_e_editar_409(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        created = await _create(client_with_db, world.client)
        assert created.status_code == 201, created.text
        account_id = created.json()["data"]["id"]
        assert (
            await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        ).status_code == 204

        assert (await _create(client_with_db, world.client, code="664")).status_code == 409
        assert (await _patch(client_with_db, world.client, account_id, name="x")).status_code == 409
        assert await _count(db_session, world.client.id) == 0, "o encerramento purga o plano"
