"""Layouts de exportação contra o banco (BACK 13.2 — R1 e as permissões do R3).

Afirma:
  - criar a partir do modelo Domínio grava a versão 1 com os parâmetros da tabela do PRD;
  - alterar grava a versão N+1 e a anterior segue consultável por `GET /export-layouts/{id}`;
  - campo fora do vocabulário e codificação inexistente → 422 `LAYOUT_INVALIDO` com
    `details.field`, NADA gravado (conta de linhas antes/depois); erro de FORMA → 400
    `VALIDATION_ERROR`, nunca 422;
  - staff cria na própria organização (outra no payload = 403); a plataforma escolhe,
    obrigatório, existente e ativa;
  - layout de uma organização é invisível para outra (lista e PK = 404);
  - `client_manager`/`client_operator` → 403 em TODAS as rotas, com linha `denied` em
    `access_audit`; o gerente LÊ e recebe 403 ao escrever.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, null, select

from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ExportLayout,
    ExportLayoutVersion,
    Organization,
    User,
    UserRole,
    UserScope,
)
from app.modules.export_layouts.definition import DOMINIO_TEMPLATE

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Layouts#1"
BASE = "/api/v1/export-layouts"
TEMPLATE = "dominio_lancamentos_csv"


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
        extra["organization_id"] = null()
    elif organization is not None:
        extra["organization_id"] = organization.id
    user = User(
        name="Layouts",
        email=f"lay-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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
    platform: User
    client: Client
    client_manager: User
    client_operator: User
    org_b: Organization
    admin_b: User
    org_off: Organization


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.manager = await _user(db_session, role=UserRole.MANAGER)
    w.platform = await _user(db_session, role=UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM)
    w.client = Client(name="Cliente Layout", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    w.client_manager = await _user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.client_operator = await _user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.org_b = Organization(name=f"Escritorio B {uuid4().hex[:6]}")
    w.org_off = Organization(name=f"Escritorio Suspenso {uuid4().hex[:6]}", active=False)
    db_session.add_all([w.org_b, w.org_off])
    await db_session.flush()
    w.admin_b = await _user(db_session, role=UserRole.ADMIN, organization=w.org_b)
    return w


def _definition(**over: Any) -> dict[str, Any]:
    raw = copy.deepcopy(DOMINIO_TEMPLATE.definition.to_json())
    raw.update(over)
    return raw


async def _counts(db: AsyncSession) -> tuple[int, int]:
    layouts = (await db.execute(select(func.count(ExportLayout.id)))).scalar_one()
    versions = (await db.execute(select(func.count(ExportLayoutVersion.id)))).scalar_one()
    return int(layouts), int(versions)


async def _from_template(http: AsyncClient, **body: Any) -> Any:
    return await http.post(f"{BASE}/from-template", json={"templateKey": TEMPLATE, **body})


class TestModeloEVersoes:
    async def test_modelo_dominio_cria_v1_com_os_parametros_do_prd(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _from_template(client_with_db)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["name"] == "Domínio: lançamentos contábeis (CSV)"
        assert data["targetSystem"] == "Domínio"
        assert data["latestVersion"] == 1
        (v1,) = data["versions"]
        definition = v1["definition"]
        assert [c["field"] for c in definition["columns"]] == [
            "data",
            "conta_debito",
            "conta_credito",
            "valor",
            "historico",
        ]
        assert definition["separator"] == ";"
        assert definition["hasHeader"] is False
        assert definition["encoding"] == "latin-1"
        assert definition["lineEnding"] == "crlf"
        assert definition["dateFormat"] == "dd/mm/aaaa"
        assert definition["amountFormat"] == {
            "prefix": "R$ ",
            "thousandsSeparator": ".",
            "decimalSeparator": ",",
            "decimalPlaces": 2,
        }
        assert v1["author"]["name"] == "Layouts"

    async def test_alterar_cria_n_mais_1_e_a_anterior_segue_consultavel(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        layout_id = (await _from_template(client_with_db)).json()["data"]["id"]
        resp = await client_with_db.post(
            f"{BASE}/{layout_id}/versions", json={"definition": _definition(hasHeader=True)}
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["latestVersion"] == 2

        got = await client_with_db.get(f"{BASE}/{layout_id}")
        assert got.status_code == 200
        versions = got.json()["data"]["versions"]
        assert [v["version"] for v in versions] == [2, 1]
        assert versions[0]["definition"]["hasHeader"] is True
        assert versions[1]["definition"] == DOMINIO_TEMPLATE.definition.to_json()
        # No banco: duas linhas IMUTÁVEIS, a v1 intacta.
        rows = (
            (
                await db_session.execute(
                    select(ExportLayoutVersion)
                    .where(ExportLayoutVersion.layout_id == layout_id)
                    .order_by(ExportLayoutVersion.version)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        assert [(r.version, r.definition["hasHeader"]) for r in rows] == [(1, False), (2, True)]

    async def test_modelo_duas_vezes_e_409_sem_segundo_layout(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _from_template(client_with_db)).status_code == 201
        before = await _counts(db_session)
        again = await _from_template(client_with_db)
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "LAYOUT_NOME_DUPLICADO"
        assert await _counts(db_session) == before

    async def test_lista_de_modelos(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.manager)
        resp = await client_with_db.get("/api/v1/export-layout-templates")
        assert resp.status_code == 200
        (item,) = resp.json()["data"]
        assert item["key"] == TEMPLATE


class TestRecusas:
    async def test_campo_fora_do_vocabulario_e_422_nomeando_e_nada_gravado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        definition = _definition()
        definition["columns"][3]["field"] = "descricao"
        before = await _counts(db_session)
        resp = await client_with_db.post(
            BASE, json={"name": "Meu", "targetSystem": "Domínio", "definition": definition}
        )
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "LAYOUT_INVALIDO"
        assert error["details"] == {"field": "columns[3].field"}
        assert await _counts(db_session) == before

    async def test_codificacao_inexistente_e_422_e_nada_gravado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        before = await _counts(db_session)
        resp = await client_with_db.post(
            BASE,
            json={
                "name": "Meu",
                "targetSystem": "Domínio",
                "definition": _definition(encoding="latin-42"),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["details"] == {"field": "encoding"}
        assert await _counts(db_session) == before

    async def test_versao_invalida_nao_grava(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        layout_id = (await _from_template(client_with_db)).json()["data"]["id"]
        before = await _counts(db_session)
        bad = _definition()
        bad["amountFormat"]["decimalPlaces"] = 7
        resp = await client_with_db.post(f"{BASE}/{layout_id}/versions", json={"definition": bad})
        assert resp.status_code == 422
        assert resp.json()["error"]["details"] == {"field": "amountFormat.decimalPlaces"}
        assert await _counts(db_session) == before

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "X", "targetSystem": "D", "definition": {**_definition(), "hasHeader": "x"}},
            {"name": "X", "targetSystem": "D"},
            {"name": "X", "targetSystem": "D", "definition": {**_definition(), "extra": 1}},
            {
                "name": "X",
                "targetSystem": "D",
                "definition": {**_definition(), "columns": [{"field": "data"}] * 21},
            },
            {"name": "", "targetSystem": "D", "definition": _definition()},
        ],
        ids=["tipo_errado", "obrigatorio_ausente", "chave_desconhecida", "colunas_demais", "nome"],
    )
    async def test_erro_de_forma_e_400_validation_error(
        self, client_with_db: AsyncClient, world: World, body: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(BASE, json=body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


class TestOrganizacao:
    async def test_staff_com_organizacao_alheia_no_payload_e_403(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _from_template(client_with_db, organizationId=str(world.org_b.id))
        assert resp.status_code == 403

    async def test_plataforma_escolhe_obrigatoria_existente_e_ativa(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.platform)
        assert (await _from_template(client_with_db)).status_code == 400
        assert (
            await _from_template(client_with_db, organizationId=str(uuid4()))
        ).status_code == 404
        suspensa = await _from_template(client_with_db, organizationId=str(world.org_off.id))
        assert suspensa.status_code == 409
        ok = await _from_template(client_with_db, organizationId=str(world.org_b.id))
        assert ok.status_code == 201
        assert ok.json()["data"]["organizationId"] == str(world.org_b.id)

    async def test_layout_de_outra_organizacao_e_invisivel(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        secret = "Layout Sigiloso da Hologram"
        created = await _from_template(client_with_db, name=secret)
        layout_id = created.json()["data"]["id"]
        await client_with_db.post("/api/v1/auth/logout")

        await _login(client_with_db, world.admin_b)
        listed = await client_with_db.get(BASE)
        assert listed.status_code == 200
        assert secret not in listed.text
        by_pk = await client_with_db.get(f"{BASE}/{layout_id}")
        assert by_pk.status_code == 404
        assert secret not in by_pk.text
        version = await client_with_db.post(
            f"{BASE}/{layout_id}/versions", json={"definition": _definition()}
        )
        assert version.status_code == 404
        assert secret not in version.text


class TestPapeis:
    async def test_usuario_de_cliente_e_403_em_todas_com_linha_denied(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        layout_id = (await _from_template(client_with_db)).json()["data"]["id"]
        await client_with_db.post("/api/v1/auth/logout")

        calls = [
            ("GET", "/api/v1/export-layout-templates", None),
            ("GET", BASE, None),
            ("GET", f"{BASE}/{layout_id}", None),
            ("POST", BASE, {"name": "X", "targetSystem": "D", "definition": _definition()}),
            ("POST", f"{BASE}/from-template", {"templateKey": TEMPLATE, "name": "Outro"}),
            ("POST", f"{BASE}/{layout_id}/versions", {"definition": _definition()}),
        ]
        for user in (world.client_manager, world.client_operator):
            await _login(client_with_db, user)
            for method, url, body in calls:
                before = (
                    await db_session.execute(
                        select(func.count(AccessAudit.id)).where(
                            AccessAudit.user_id == user.id, AccessAudit.action == "denied"
                        )
                    )
                ).scalar_one()
                resp = await client_with_db.request(method, url, json=body)
                assert resp.status_code == 403, (user.role, method, url, resp.text)
                assert "Cliente Layout" not in resp.text
                rows = (
                    (
                        await db_session.execute(
                            select(AccessAudit)
                            .where(AccessAudit.user_id == user.id, AccessAudit.action == "denied")
                            .execution_options(populate_existing=True)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(rows) == before + 1, (method, url)
                row = rows[-1]
                assert row.user_scope == "client"
                assert row.actor_client_id == world.client.id
                assert row.actor_organization_id is not None
            await client_with_db.post("/api/v1/auth/logout")

    async def test_gerente_le_e_recebe_403_ao_escrever(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        layout_id = (await _from_template(client_with_db)).json()["data"]["id"]
        await client_with_db.post("/api/v1/auth/logout")

        await _login(client_with_db, world.manager)
        assert (await client_with_db.get(BASE)).status_code == 200
        assert (await client_with_db.get(f"{BASE}/{layout_id}")).status_code == 200
        assert (await _from_template(client_with_db, name="Do gerente")).status_code == 403
        resp = await client_with_db.post(
            f"{BASE}/{layout_id}/versions", json={"definition": _definition()}
        )
        assert resp.status_code == 403
