"""Conta do banco de cada conta de origem e o bloqueio da materialização (Sprint 16 / BACK 16.3).

    GET /api/v1/clients/{client_id}/source-accounts  → quem alcança o cliente
    PUT /api/v1/clients/{client_id}/source-accounts  → `manage_client_accounting_chart`

O que este módulo afirma, contra Postgres:
  - a lista traz as contas de origem da base + o slot PADRÃO, pendentes ou associadas;
  - PUT cria e TROCA (upsert, `created`), inclusive no slot padrão (índice parcial);
    outro cliente 404, sintética 422, `client_manager` lê 200 e escreve 403 com 1
    `denied`, cliente encerrado 409;
  - materializar `conta_contabil` com linha de alvo de conta de origem sem conta do banco
    → 409 `CONTA_DO_BANCO_PENDENTE` só com identificadores, e NENHUMA materialização;
  - o item guarda o código do banco; trocar a associação depois não muda a
    materialização; trocar entre a prévia e a confirmação → 409 `PREVIA_DESATUALIZADA`;
  - nos outros destinos a associação é ignorada (regressão);
  - o encerramento purga as associações.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, null, select

from app.core.config import get_settings
from app.core.crypto_service import new_client_dek
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    Client,
    ClientAccountingAccount,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    ClientMovement,
    ClientMovementSync,
    ClientSourceAccountBinding,
    MappingDestination,
    MappingTarget,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_movements.competence import current_competence, format_competence
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ContaDoBanco#1"
C = current_competence()
PLANO = (
    b"codigo_reduzido;nome;tipo\n"
    b"649;Banco conta movimento;analitica\n"
    b"700;Banco secundario;analitica\n"
    b"662;Alugueis a receber - Inquilino D;analitica\n"
    b"10;Ativo circulante;sintetica\n"
)


async def _user(
    db: AsyncSession, *, role: UserRole, scope: UserScope = UserScope.SYSTEM, client_id: Any = None
) -> User:
    extra: dict[str, Any] = {"organization_id": null()} if scope is UserScope.PLATFORM else {}
    user = User(
        name="Conta do banco",
        email=f"cb-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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


class World:
    admin: User
    tenant_manager: User
    client: Client
    other: Client
    accounts: dict[str, ClientAccountingAccount]
    other_account: ClientAccountingAccount


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _accounts(db: AsyncSession, client: Client) -> dict[str, ClientAccountingAccount]:
    stmt = select(ClientAccountingAccount).where(ClientAccountingAccount.client_id == client.id)
    return {a.code: a for a in (await db.execute(stmt)).scalars().all()}


@pytest.fixture
async def world(db_session: AsyncSession, client_with_db: AsyncClient) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.client = Client(name="Cliente do banco", active=True, created_by=w.admin.id)
    w.other = Client(name="Outro", active=True, created_by=w.admin.id)
    db_session.add_all([w.client, w.other])
    await db_session.flush()
    for client in (w.client, w.other):
        _cipher, client.dek_wrapped = await new_client_dek(client.id, settings=get_settings())
    w.tenant_manager = await _user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
    )
    db_session.add(
        ClientMovementSync(client_id=w.client.id, competence=C, synced_at=datetime.now(UTC))
    )
    # Duas linhas SEM conta de origem (o arquivo da MSFG) e uma com conta `cc-9`.
    for mid, amount, account in (
        ("1", "5466.87", None),
        ("2", "-1.75", None),
        ("3", "-9.00", "cc-9"),
    ):
        db_session.add(
            ClientMovement(
                client_id=w.client.id,
                source_type="arquivo",
                source_movement_id=mid,
                source_account_id=account,
                competence=C,
                movement_date=C.replace(day=5),
                amount=Decimal(amount),
                category_code="aluguel",
            )
        )
    await db_session.flush()
    await _login(client_with_db, w.admin)
    for client in (w.client, w.other):
        resp = await client_with_db.post(
            f"/api/v1/clients/{client.id}/accounting-chart/import",
            files={"file": ("plano.csv", PLANO, "text/csv")},
        )
        assert resp.status_code == 200, resp.text
    w.accounts = await _accounts(db_session, w.client)
    w.other_account = (await _accounts(db_session, w.other))["649"]
    return w


def _url(w: World) -> str:
    return f"/api/v1/clients/{w.client.id}/source-accounts"


async def _bind(
    http: AsyncClient, w: World, account: ClientAccountingAccount | Any, source: str | None = None
) -> Response:
    account_id = account.id if hasattr(account, "id") else account
    return await http.put(
        _url(w),
        json={
            "sourceType": "arquivo",
            "sourceAccountId": source,
            "accountingAccountId": str(account_id),
        },
    )


async def _bindings(db: AsyncSession, client: Client) -> list[ClientSourceAccountBinding]:
    stmt = (
        select(ClientSourceAccountBinding)
        .where(ClientSourceAccountBinding.client_id == client.id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _decide_conta_contabil(http: AsyncClient, w: World) -> None:
    resp = await http.post(
        f"/api/v1/clients/{w.client.id}/mapping/conta_contabil/decisions",
        json={
            "categoryCode": "aluguel",
            "sourceType": "arquivo",
            "decision": "alvo",
            "accountingAccountId": str(w.accounts["662"].id),
            "history": "RECEBIMENTO REF. ALUGUEL",
            "effectiveFrom": format_competence(C),
        },
    )
    assert resp.status_code == 200, resp.text


async def _preview(http: AsyncClient, w: World, kind: str = "conta_contabil") -> dict[str, Any]:
    resp = await http.get(
        f"/api/v1/clients/{w.client.id}/mapping/{kind}/preview",
        params={"competence": format_competence(C)},
    )
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def _materialize(
    http: AsyncClient, w: World, token: str, kind: str = "conta_contabil"
) -> Response:
    return await http.post(
        f"/api/v1/clients/{w.client.id}/mapping/{kind}/materializations",
        json={
            "competence": format_competence(C),
            "previewToken": token,
            "confirmPartialCoverage": True,
        },
    )


async def _materializations(db: AsyncSession, w: World) -> int:
    stmt = select(func.count(ClientMappingMaterialization.id)).where(
        ClientMappingMaterialization.client_id == w.client.id
    )
    return int((await db.execute(stmt)).scalar_one())


class TestAssociacao:
    async def test_lista_cria_e_troca(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        listing = await client_with_db.get(_url(world))
        assert listing.status_code == 200, listing.text
        entries = {(e["sourceType"], e["sourceAccountId"]): e for e in listing.json()["data"]}
        assert set(entries) == {("arquivo", None), ("arquivo", "cc-9")}
        assert entries[("arquivo", None)]["isDefault"] is True
        assert all(e["pending"] for e in entries.values())

        created = await _bind(client_with_db, world, world.accounts["649"])
        assert created.status_code == 200, created.text
        assert created.json()["data"]["created"] is True
        assert created.json()["data"]["entry"]["bankAccount"]["code"] == "649"
        trocada = await _bind(client_with_db, world, world.accounts["700"])
        assert trocada.json()["data"]["created"] is False
        (binding,) = await _bindings(db_session, world.client)
        assert binding.accounting_account_id == world.accounts["700"].id
        assert binding.source_account_id is None

        listing = await client_with_db.get(_url(world))
        entries = {(e["sourceType"], e["sourceAccountId"]): e for e in listing.json()["data"]}
        assert entries[("arquivo", None)]["bankAccount"]["name"] == "Banco secundario"
        assert entries[("arquivo", "cc-9")]["pending"] is True

    async def test_recusas_sem_gravar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        alheia = await _bind(client_with_db, world, world.other_account)
        assert alheia.status_code == 404
        sintetica = await _bind(client_with_db, world, world.accounts["10"])
        assert sintetica.status_code == 422
        assert sintetica.json()["error"]["code"] == "CONTA_CONTABIL_NAO_LANCAVEL"
        vazia = await _bind(client_with_db, world, world.accounts["649"], source="   ")
        assert vazia.status_code == 400
        assert await _bindings(db_session, world.client) == []

    async def test_gerente_do_cliente_le_e_nao_escreve(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.tenant_manager)
        assert (await client_with_db.get(_url(world))).status_code == 200
        resp = await _bind(client_with_db, world, world.accounts["649"])
        assert resp.status_code == 403
        denied = (
            (
                await db_session.execute(
                    select(AccessAudit).where(
                        AccessAudit.action == "denied", AccessAudit.client_id == world.client.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(denied) == 1
        assert await _bindings(db_session, world.client) == []

    async def test_encerrado_escreve_409_le_200_e_a_purga_leva_as_associacoes(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _bind(client_with_db, world, world.accounts["649"])
        closed = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert closed.status_code == 204, closed.text
        assert await _bindings(db_session, world.client) == []
        assert (await _bind(client_with_db, world, world.accounts["649"])).status_code == 409
        assert (await client_with_db.get(_url(world))).status_code == 200


class TestMaterializacao:
    async def test_conta_de_origem_sem_banco_bloqueia_com_409_e_nada_gravado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _decide_conta_contabil(client_with_db, world)
        await _bind(client_with_db, world, world.accounts["649"])  # só o slot padrão
        data = await _preview(client_with_db, world)
        assert data["pendingSourceAccounts"] == [
            {"sourceType": "arquivo", "sourceAccountId": "cc-9"}
        ]
        resp = await _materialize(client_with_db, world, data["previewToken"])
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["code"] == "CONTA_DO_BANCO_PENDENTE"
        assert body["details"] == {
            "pendingSourceAccounts": [{"sourceType": "arquivo", "sourceAccountId": "cc-9"}]
        }
        assert "Banco" not in resp.text
        assert await _materializations(db_session, world) == 0

    async def test_snapshot_do_banco_nao_muda_quando_a_associacao_muda(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _decide_conta_contabil(client_with_db, world)
        await _bind(client_with_db, world, world.accounts["649"])
        await _bind(client_with_db, world, world.accounts["700"], source="cc-9")
        data = await _preview(client_with_db, world)
        assert data["pendingSourceAccounts"] == []
        (categoria,) = data["accountingCategories"]
        assert categoria["completeCount"] == 3
        resp = await _materialize(client_with_db, world, data["previewToken"])
        assert resp.status_code == 201, resp.text

        await _bind(client_with_db, world, world.accounts["700"])  # troca a padrão depois
        items = (
            (
                await db_session.execute(
                    select(ClientMappingMaterializationItem)
                    .where(ClientMappingMaterializationItem.client_id == world.client.id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        by_movement = {i.source_movement_id: i for i in items}
        assert by_movement["1"].bank_account_code == "649"
        assert by_movement["2"].bank_account_code == "649"
        assert by_movement["3"].bank_account_code == "700"
        assert all(i.history_present is True for i in items)
        assert all(i.accounting_account_code == "662" for i in items)

    async def test_trocar_a_associacao_entre_previa_e_confirmacao_e_409(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _decide_conta_contabil(client_with_db, world)
        await _bind(client_with_db, world, world.accounts["649"])
        await _bind(client_with_db, world, world.accounts["649"], source="cc-9")
        token = (await _preview(client_with_db, world))["previewToken"]
        await _bind(client_with_db, world, world.accounts["700"], source="cc-9")
        resp = await _materialize(client_with_db, world, token)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "PREVIA_DESATUALIZADA"

    async def test_outros_destinos_ignoram_a_associacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        fluxo = (
            await db_session.execute(
                select(MappingDestination).where(
                    MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                    MappingDestination.destination_type == "fluxo_de_caixa",
                )
            )
        ).scalar_one()
        db_session.add(MappingTarget(destination_id=fluxo.id, code="1.01", name="Recebimentos"))
        await db_session.flush()
        resp = await client_with_db.post(
            f"/api/v1/clients/{world.client.id}/mapping/fluxo_de_caixa/decisions",
            json={
                "categoryCode": "aluguel",
                "sourceType": "arquivo",
                "decision": "alvo",
                "targetCode": "1.01",
                "effectiveFrom": format_competence(C),
            },
        )
        assert resp.status_code == 200, resp.text
        data = await _preview(client_with_db, world, "fluxo_de_caixa")
        assert data["pendingSourceAccounts"] is None
        assert data["accountingCategories"] is None
        mat = await _materialize(client_with_db, world, data["previewToken"], "fluxo_de_caixa")
        assert mat.status_code == 201, mat.text
        items = (
            (
                await db_session.execute(
                    select(ClientMappingMaterializationItem).where(
                        ClientMappingMaterializationItem.client_id == world.client.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(i.bank_account_code is None and i.history_present is None for i in items)
        assert all(i.target_code == "1.01" for i in items)


class TestExclusaoDefinitiva:
    async def test_exclusao_com_autor_do_tenant_leva_as_associacoes(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add(
            ClientSourceAccountBinding(
                client_id=world.client.id,
                source_type="arquivo",
                source_account_id=None,
                accounting_account_id=world.accounts["649"].id,
                created_by=world.tenant_manager.id,
                updated_by=world.tenant_manager.id,
            )
        )
        await db_session.flush()
        resp = await client_with_db.delete(f"/api/v1/clients/{world.client.id}")
        assert resp.status_code == 204, resp.text
        assert await _bindings(db_session, world.client) == []
