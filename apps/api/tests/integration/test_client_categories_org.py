"""Catálogo de categorias POR ORGANIZAÇÃO (86e36ecqz, D3 da camada de organizações).

Duas organizações com catálogos próprios. O que se prova contra o banco real:

    - a leitura é o catálogo da organização da LINHA do observador (a
      plataforma vê todos, com `?organizationId=` opcional; o admin só pode
      pedir a própria — outra é 403, nunca ignorado);
    - a categoria nasce na organização do ator (a plataforma escolhe,
      obrigatório; o admin não aponta outra);
    - o nome é único sem caixa DENTRO da organização — a mesma palavra em duas
      organizações são duas categorias;
    - alvo por PK de outra organização é 404 (anti-IDOR), para PATCH e DELETE;
    - a response diz de que organização a categoria é.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import null, select

from app.core.security import hash_password
from app.db.models import ClientCategory, Organization, User, UserRole, UserScope

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PASSWORD = "Senh@Catalogo#1"
BASE = "/api/v1/client-categories"


async def _seed_org(session: AsyncSession, name: str) -> Organization:
    org = Organization(name=name)
    session.add(org)
    await session.flush()
    return org


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    organization: Organization | None,
    scope: UserScope = UserScope.SYSTEM,
) -> User:
    user = User(
        name=email.split("@")[0],
        email=email.lower(),
        password_hash=hash_password(PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        organization_id=null() if organization is None else organization.id,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user, ["organization_id"])
    return user


async def _seed_category(
    session: AsyncSession, *, organization: Organization, name: str
) -> ClientCategory:
    category = ClientCategory(name=name, tone="neutral", organization_id=organization.id)
    session.add(category)
    await session.flush()
    return category


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def catalogs(db_session: AsyncSession) -> dict[str, Any]:
    suffix = uuid4().hex[:6]
    org_a = await _seed_org(db_session, f"Escritorio A {suffix}")
    org_b = await _seed_org(db_session, f"Escritorio B {suffix}")
    platform = await _seed_user(
        db_session,
        email=f"plataforma-cat-{suffix}@hologram.com.br",
        role=UserRole.PLATFORM_ADMIN,
        organization=None,
        scope=UserScope.PLATFORM,
    )
    admin_a = await _seed_user(
        db_session, email=f"admin-a-{suffix}@a.com.br", role=UserRole.ADMIN, organization=org_a
    )
    manager_a = await _seed_user(
        db_session, email=f"gerente-a-{suffix}@a.com.br", role=UserRole.MANAGER, organization=org_a
    )
    admin_b = await _seed_user(
        db_session, email=f"admin-b-{suffix}@b.com.br", role=UserRole.ADMIN, organization=org_b
    )
    cat_a1 = await _seed_category(db_session, organization=org_a, name="Varejo")
    cat_a2 = await _seed_category(db_session, organization=org_a, name="Fintech")
    cat_b = await _seed_category(db_session, organization=org_b, name="Varejo")
    return {
        "org_a": org_a,
        "org_b": org_b,
        "platform": platform,
        "admin_a": admin_a,
        "manager_a": manager_a,
        "admin_b": admin_b,
        "cat_a1": cat_a1,
        "cat_a2": cat_a2,
        "cat_b": cat_b,
    }


class TestLeituraPorOrganizacao:
    async def test_admin_le_so_o_catalogo_da_propria_organizacao(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["admin_a"].email)
        resp = await client_with_db.get(BASE)
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert {r["id"] for r in rows} == {str(catalogs["cat_a1"].id), str(catalogs["cat_a2"].id)}
        assert {r["organization_id"] for r in rows} == {str(catalogs["org_a"].id)}
        assert {r["organization_name"] for r in rows} == {catalogs["org_a"].name}
        assert str(catalogs["cat_b"].id) not in resp.text

    async def test_gerente_tambem_le_a_propria_e_nao_escreve(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["manager_a"].email)
        resp = await client_with_db.get(BASE)
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 2
        assert (await client_with_db.post(BASE, json={"name": "X"})).status_code == 403

    async def test_plataforma_le_todos_e_filtra_por_organizacao(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["platform"].email)
        tudo = await client_with_db.get(BASE)
        assert tudo.status_code == 200, tudo.text
        ids = {r["id"] for r in tudo.json()["data"]}
        assert {
            str(catalogs["cat_a1"].id),
            str(catalogs["cat_a2"].id),
            str(catalogs["cat_b"].id),
        } <= ids

        so_b = await client_with_db.get(BASE, params={"organizationId": str(catalogs["org_b"].id)})
        assert so_b.status_code == 200, so_b.text
        assert [r["id"] for r in so_b.json()["data"]] == [str(catalogs["cat_b"].id)]
        assert so_b.json()["data"][0]["organization_name"] == catalogs["org_b"].name

    async def test_admin_nao_pede_o_catalogo_de_outra_organizacao(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["admin_b"].email)
        alheio = await client_with_db.get(
            BASE, params={"organizationId": str(catalogs["org_a"].id)}
        )
        assert alheio.status_code == 403, alheio.text
        assert "Fintech" not in alheio.text
        proprio = await client_with_db.get(
            BASE, params={"organizationId": str(catalogs["org_b"].id)}
        )
        assert proprio.status_code == 200, proprio.text
        assert [r["id"] for r in proprio.json()["data"]] == [str(catalogs["cat_b"].id)]


class TestEscritaPorOrganizacao:
    async def test_admin_cria_na_propria_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["admin_b"].email)
        resp = await client_with_db.post(BASE, json={"name": "Indústria"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["organization_id"] == str(catalogs["org_b"].id)
        assert body["organization_name"] == catalogs["org_b"].name
        row = await db_session.get(ClientCategory, body["id"])
        assert row is not None
        await db_session.refresh(row, ["organization_id"])
        assert row.organization_id == catalogs["org_b"].id

    async def test_admin_nao_aponta_outra_organizacao_mas_pode_repetir_a_propria(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["admin_b"].email)
        alheia = await client_with_db.post(
            BASE, json={"name": "Indústria", "organization_id": str(catalogs["org_a"].id)}
        )
        assert alheia.status_code == 403, alheia.text
        propria = await client_with_db.post(
            BASE, json={"name": "Indústria", "organization_id": str(catalogs["org_b"].id)}
        )
        assert propria.status_code == 201, propria.text

    async def test_plataforma_escolhe_a_organizacao_e_e_obrigatorio(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["platform"].email)
        sem = await client_with_db.post(BASE, json={"name": "Serviços"})
        assert sem.status_code == 400, sem.text
        inexistente = await client_with_db.post(
            BASE, json={"name": "Serviços", "organization_id": str(uuid4())}
        )
        assert inexistente.status_code == 404, inexistente.text
        ok = await client_with_db.post(
            BASE, json={"name": "Serviços", "organization_id": str(catalogs["org_a"].id)}
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["organization_id"] == str(catalogs["org_a"].id)
        assert ok.json()["organization_name"] == catalogs["org_a"].name

    async def test_nome_e_unico_dentro_da_organizacao_nao_entre_elas(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        """ "Varejo" já existe em A e em B: A não cria outro; B cria "Fintech"
        (que só existe em A) sem colisão."""
        await _login(client_with_db, catalogs["admin_a"].email)
        assert (await client_with_db.post(BASE, json={"name": "varejo"})).status_code == 409
        await _login(client_with_db, catalogs["admin_b"].email)
        assert (await client_with_db.post(BASE, json={"name": "Fintech"})).status_code == 201

    async def test_patch_e_delete_de_outra_organizacao_sao_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["admin_b"].email)
        alvo = catalogs["cat_a1"].id
        patch = await client_with_db.patch(f"{BASE}/{alvo}", json={"name": "Sequestrada"})
        assert patch.status_code == 404, patch.text
        delete = await client_with_db.delete(f"{BASE}/{alvo}")
        assert delete.status_code == 404, delete.text
        row = (
            await db_session.execute(select(ClientCategory).where(ClientCategory.id == alvo))
        ).scalar_one()
        assert row.name == "Varejo"

    async def test_renomear_colide_so_dentro_da_organizacao(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["admin_a"].email)
        # "Fintech" existe em A: colisão. Em B não existe, mas B é outra org —
        # o admin de A nem chega lá.
        colisao = await client_with_db.patch(
            f"{BASE}/{catalogs['cat_a1'].id}", json={"name": "FINTECH"}
        )
        assert colisao.status_code == 409, colisao.text
        await _login(client_with_db, catalogs["admin_b"].email)
        livre = await client_with_db.patch(
            f"{BASE}/{catalogs['cat_b'].id}", json={"name": "Fintech"}
        )
        assert livre.status_code == 200, livre.text
        assert livre.json()["organization_id"] == str(catalogs["org_b"].id)

    async def test_plataforma_edita_e_exclui_em_qualquer_organizacao(
        self, client_with_db: AsyncClient, catalogs: dict[str, Any]
    ) -> None:
        await _login(client_with_db, catalogs["platform"].email)
        ok = await client_with_db.patch(f"{BASE}/{catalogs['cat_b'].id}", json={"tone": "info"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["organization_name"] == catalogs["org_b"].name
        assert (await client_with_db.delete(f"{BASE}/{catalogs['cat_a2'].id}")).status_code == 204
