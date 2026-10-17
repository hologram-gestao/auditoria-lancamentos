"""Prévia da reimportação e exportação do plano contábil, pela API e contra Postgres (86e3qhhrm).

    POST /api/v1/clients/{client_id}/accounting-chart/import/preview  → `manage_client_accounting_chart`
    GET  /api/v1/clients/{client_id}/accounting-chart/export          → quem alcança o cliente

Os critérios de aceite da subtask, um por teste:
  - reimportar sem uma conta existente: a PRÉVIA diz antes que ela fica ausente (e
    se o de-para ou a conta do banco a usam), e depois da importação ela segue ativa,
    com o selo;
  - reimportar com conta nova embaixo de "Clientes": só ela entra, as demais ficam
    iguais (nenhum UPDATE: `updated_at` intacto);
  - exportar e reimportar o mesmo arquivo é idempotente: zero mudanças na prévia, e a
    importação depois dela não altera nenhuma linha.

E os contornos: a prévia não grava nada (conta linhas e carimbos), recusa o mesmo
422 da importação, é 403 com linha `denied` para o `client_manager` e 409 para
cliente encerrado; a exportação grava 1 linha `export` na trilha, sai com BOM e no
modelo, e o nome com cara de fórmula volta igual. O cross-tenant e o cross-org das
duas rotas rodam na bateria dos três atacantes (`test_sensitive_endpoints.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, null, select

from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAccountingAccount,
    ClientAssignment,
    ClientSourceAccountBinding,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@PreviaPlano#1"


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
        name="Previa do Plano",
        email=f"previa-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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
    tenant_manager: User
    client: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente da Previa", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    w.tenant_manager = await _seed_user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
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


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


async def _post_file(http: AsyncClient, url: str, content: bytes) -> Response:
    return await http.post(url, files={"file": ("plano.csv", content, "text/csv")})


async def _accounts(db: AsyncSession, client_id: Any) -> dict[str, ClientAccountingAccount]:
    stmt = (
        select(ClientAccountingAccount)
        .where(ClientAccountingAccount.client_id == client_id)
        .execution_options(populate_existing=True)
    )
    return {a.code: a for a in (await db.execute(stmt)).scalars().all()}


def _snapshot(accounts: dict[str, ClientAccountingAccount]) -> dict[str, tuple[Any, ...]]:
    return {
        code: (a.name_encrypted, a.account_type, a.classification, a.active, a.updated_at)
        for code, a in accounts.items()
    }


_PLANO = _csv(
    "codigo_reduzido;nome;tipo;classificacao",
    "1;Ativo;sintetica;1",
    "2;Clientes;sintetica;1.1",
    "649;Banco conta movimento;analitica;1.1.01",
    "662;Aluguel - Inquilino D;analitica;1.1.02",
    "700;- Juros pagos;analitica;1.2",
)


class TestPrevia:
    async def test_conta_que_nao_vem_a_previa_avisa_e_a_importacao_so_poe_o_selo(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _post_file(client_with_db, f"{_url(world.client)}/import", _PLANO)).is_success
        contas = await _accounts(db_session, world.client.id)
        # A 662 é a conta do banco: a prévia tem de dizer que a ausente está em uso.
        db_session.add(
            ClientSourceAccountBinding(
                client_id=world.client.id,
                source_type="arquivo",
                source_account_id=None,
                accounting_account_id=contas["662"].id,
                created_by=world.admin.id,
                updated_by=world.admin.id,
            )
        )
        await db_session.flush()
        sem_662 = _csv(
            "codigo_reduzido;nome;tipo;classificacao",
            "1;Ativo;sintetica;1",
            "2;Clientes;sintetica;1.1",
            "649;Banco conta movimento;analitica;1.1.01",
            "700;- Juros pagos;analitica;1.2",
        )
        antes = _snapshot(await _accounts(db_session, world.client.id))

        resp = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", sem_662)

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["layout"] == "modelo"
        assert (data["contas"], data["contasNovas"], data["contasAlteradas"]) == (4, 0, 0)
        assert (data["contasAusentes"], data["contasAusentesEmUso"]) == (1, 1)
        assert data["temMudancas"] is True
        (ausente,) = data["ausentes"]
        assert ausente["code"] == "662"
        assert ausente["name"] == "Aluguel - Inquilino D"
        assert ausente["inUse"] is True
        assert ausente["active"] is True
        assert ausente["absentSince"] is None
        # A prévia não gravou nada.
        assert _snapshot(await _accounts(db_session, world.client.id)) == antes

        resp = await _post_file(client_with_db, f"{_url(world.client)}/import", sem_662)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["contasAusentes"] == 1
        conta = (await _accounts(db_session, world.client.id))["662"]
        assert conta.active is True
        assert conta.absent_since is not None

        # De novo: a mesma ausente já tem o selo, então a prévia não acusa mudança.
        resp = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", sem_662)
        data = resp.json()["data"]
        assert data["temMudancas"] is False
        assert data["ausentes"][0]["absentSince"] is not None

    async def test_conta_nova_embaixo_de_clientes_so_ela_entra(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _post_file(client_with_db, f"{_url(world.client)}/import", _PLANO)).is_success
        antes = _snapshot(await _accounts(db_session, world.client.id))
        com_nova = _PLANO + b"701;Cliente novo;analitica;1.1.03\n"

        preview = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", com_nova)
        data = preview.json()["data"]
        assert (data["contasNovas"], data["contasAlteradas"], data["contasAusentes"]) == (1, 0, 0)
        assert data["novas"] == [
            {"code": "701", "name": "Cliente novo", "type": "analitica", "classification": "1.1.03"}
        ]

        resp = await _post_file(client_with_db, f"{_url(world.client)}/import", com_nova)
        assert resp.json()["data"] == {
            "contas": 6,
            "contasNovas": 1,
            "contasAlteradas": 0,
            "contasAusentes": 0,
        }
        depois = await _accounts(db_session, world.client.id)
        assert set(depois) == set(antes) | {"701"}
        # As demais nem foram regravadas: nome cifrado e carimbo idênticos.
        assert {c: v for c, v in _snapshot(depois).items() if c != "701"} == antes
        listing = await client_with_db.get(_url(world.client), params={"pageSize": 100})
        assert [a["code"] for a in listing.json()["data"]] == ["1", "2", "649", "662", "701", "700"]

    async def test_mesma_recusa_da_importacao_e_nada_gravado(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        repetido = _csv("codigo_reduzido;nome;tipo", "649;A;analitica", "649;B;analitica")
        resp = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", repetido)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "LINHAS_INVALIDAS"
        assert await _accounts(db_session, world.client.id) == {}

    async def test_client_manager_403_com_trilha_e_encerrado_409(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.tenant_manager)
        resp = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", _PLANO)
        assert resp.status_code == 403, resp.text
        stmt = select(func.count()).where(
            AccessAudit.action == "denied", AccessAudit.client_id == world.client.id
        )
        assert (await db_session.execute(stmt)).scalar_one() == 1

        await _login(client_with_db, world.admin)
        assert (
            await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        ).status_code == 204
        resp = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", _PLANO)
        assert resp.status_code == 409, resp.text


class TestExportacao:
    async def test_exportar_e_reimportar_nao_muda_nada(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _post_file(client_with_db, f"{_url(world.client)}/import", _PLANO)).is_success
        antes = _snapshot(await _accounts(db_session, world.client.id))

        resp = await client_with_db.get(f"{_url(world.client)}/export")

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        disposition = resp.headers["content-disposition"]
        assert disposition == (
            f'attachment; filename="plano-contabil-{world.client.id.hex[:8]}.csv"'
        )
        content = resp.content
        assert content.startswith(b"\xef\xbb\xbf")
        lines = content.decode("utf-8-sig").splitlines()
        assert lines[0] == "codigo_reduzido;nome;tipo;classificacao"
        assert lines[1:] == [
            "1;Ativo;sintetica;1",
            "2;Clientes;sintetica;1.1",
            "649;Banco conta movimento;analitica;1.1.01",
            "662;Aluguel - Inquilino D;analitica;1.1.02",
            "700;'- Juros pagos;analitica;1.2",  # a fórmula neutralizada
        ]
        stmt = select(AccessAudit).where(
            AccessAudit.action == "export", AccessAudit.client_id == world.client.id
        )
        (linha,) = (await db_session.execute(stmt)).scalars().all()
        assert linha.user_id == world.admin.id

        preview = await _post_file(client_with_db, f"{_url(world.client)}/import/preview", content)
        data = preview.json()["data"]
        assert data["temMudancas"] is False
        assert (data["contasNovas"], data["contasAlteradas"], data["contasAusentes"]) == (0, 0, 0)
        assert data["contasSemMudanca"] == 5

        resp = await _post_file(client_with_db, f"{_url(world.client)}/import", content)
        assert resp.json()["data"]["contasAlteradas"] == 0
        depois = await _accounts(db_session, world.client.id)
        assert _snapshot(depois) == antes
        assert depois["700"].absent_since is None
        listing = await client_with_db.get(_url(world.client), params={"code": "700"})
        assert listing.json()["data"][0]["name"] == "- Juros pagos"

    async def test_quem_le_exporta_e_plano_vazio_sai_so_com_o_cabecalho(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.tenant_manager)
        resp = await client_with_db.get(f"{_url(world.client)}/export")
        assert resp.status_code == 200, resp.text
        assert resp.content == "﻿codigo_reduzido;nome;tipo;classificacao\r\n".encode()
