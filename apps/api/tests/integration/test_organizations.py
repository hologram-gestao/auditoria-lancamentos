"""Módulo de organizações — administração da plataforma (86e36ecnp).

Cobre:
    - RBAC: sem login 401; admin, manager e usuário de cliente recebem 403 sem
      corpo que nomeie organização nenhuma; só a plataforma passa.
    - CRUD: criar, nome único sem caixa (409), listar paginado com contagens de
      clientes e staff, `pageSize` com alias, busca, detalhe, 404 em id
      desconhecido, renomear (409 se colide).
    - Suspensão via API: `PATCH {active:false}` derruba o staff da organização
      no request seguinte e recusa login; a plataforma continua; reativar
      desfaz; a plataforma não cria cliente numa organização suspensa.
    - Eventos de uso: `organizacao_criada` e `organizacao_desativada` (uma
      linha por fato, só IDs e contagens).
    - `GET /platform-admins`: quem administra a plataforma, só para a
      plataforma, sem staff de organização na lista e sem ser engolida pela
      rota `/{organization_id}`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import null, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Client, Organization, UsageEvent, User, UserRole, UserScope

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PASSWORD = "Senh@OrgModulo#1"
BASE = "/api/v1/organizations"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    organization: Organization | None = None,
    client_id: UUID | None = None,
) -> User:
    extra: dict[str, object] = {}
    if scope is UserScope.PLATFORM:
        extra["organization_id"] = null()
    elif organization is not None:
        extra["organization_id"] = organization.id
    user = User(
        name=email.split("@")[0],
        email=email.lower(),
        password_hash=hash_password(PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        **extra,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession, *, creator: User, organization: Organization
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("org-mod-key", hex_key)
    ct_s, iv_s = encrypt("org-mod-secret", hex_key)
    client = Client(
        name=f"Cliente {uuid4().hex[:6]}",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
        organization_id=organization.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    suffix = uuid4().hex[:6]
    org = Organization(name=f"Escritorio Alfa {suffix}")
    db_session.add(org)
    await db_session.flush()
    platform = await _seed_user(
        db_session,
        email=f"plataforma-{suffix}@hologram.com.br",
        role=UserRole.PLATFORM_ADMIN,
        scope=UserScope.PLATFORM,
    )
    admin = await _seed_user(
        db_session, email=f"admin-{suffix}@alfa.com.br", role=UserRole.ADMIN, organization=org
    )
    manager = await _seed_user(
        db_session, email=f"gerente-{suffix}@alfa.com.br", role=UserRole.MANAGER, organization=org
    )
    client = await _seed_client(db_session, creator=admin, organization=org)
    operator = await _seed_user(
        db_session,
        email=f"op-{suffix}@cliente-alfa.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        organization=org,
        client_id=client.id,
    )
    return {
        "org": org,
        "platform": platform,
        "admin": admin,
        "manager": manager,
        "client": client,
        "operator": operator,
        "suffix": suffix,
    }


async def _events(db: AsyncSession, name: str) -> list[UsageEvent]:
    rows = (
        await db.execute(
            select(UsageEvent).where(UsageEvent.event == name).order_by(UsageEvent.created_at)
        )
    ).scalars()
    return list(rows)


class TestRBAC:
    async def test_sem_login_401(self, client_with_db: AsyncClient) -> None:
        assert (await client_with_db.get(BASE)).status_code == 401

    @pytest.mark.parametrize("who", ["admin", "manager", "operator"])
    async def test_quem_nao_e_plataforma_recebe_403_sem_nomear_organizacao(
        self, client_with_db: AsyncClient, scene: dict[str, Any], who: str
    ) -> None:
        await _login(client_with_db, scene[who].email)
        org_id = scene["org"].id
        for resp in (
            await client_with_db.get(BASE),
            await client_with_db.post(BASE, json={"name": "Tentativa"}),
            await client_with_db.get(f"{BASE}/{org_id}"),
            await client_with_db.patch(f"{BASE}/{org_id}", json={"active": False}),
            await client_with_db.get(f"{BASE}/platform-admins"),
        ):
            assert resp.status_code == 403, resp.text
            assert scene["org"].name not in resp.text


class TestPlatformAdmins:
    """`GET /platform-admins` — a única tela que mostra quem é plataforma.

    Existe porque `GET /users` filtra `scope='system'` no próprio SELECT
    (anti-IDOR da 86e36ecar) e o `users_count` de cada organização conta só o
    staff dela: sem esta rota, nem a plataforma enxerga os pares dela.
    """

    async def test_sem_login_401(self, client_with_db: AsyncClient) -> None:
        assert (await client_with_db.get(f"{BASE}/platform-admins")).status_code == 401

    async def test_a_rota_literal_nao_e_lida_como_uuid(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        """Regressão de ORDEM de declaração: se `/{organization_id}` viesse
        antes, "platform-admins" seria parseado como UUID e a resposta seria
        422 — verde no lint, quebrado na tela."""
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.get(f"{BASE}/platform-admins")
        assert resp.status_code == 200, resp.text
        assert resp.status_code != 422

    async def test_lista_a_propria_plataforma_e_nenhum_staff_de_organizacao(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.get(f"{BASE}/platform-admins")
        assert resp.status_code == 200, resp.text

        emails = {row["email"] for row in resp.json()["data"]}
        assert scene["platform"].email in emails
        # O complemento é o que prova o filtro: admin e gerente da organização
        # e usuário de cliente NÃO são plataforma.
        assert scene["admin"].email not in emails
        assert scene["manager"].email not in emails
        assert scene["operator"].email not in emails

    async def test_plataforma_desativada_continua_aparecendo_marcada(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Desativar não tira o escopo: quem responde "quem é plataforma?"
        precisa mostrar a conta desativada, senão ela some da vista mantendo o
        alcance no banco."""
        outro = await _seed_user(
            db_session,
            email=f"plataforma-inativa-{scene['suffix']}@hologram.com.br",
            role=UserRole.PLATFORM_ADMIN,
            scope=UserScope.PLATFORM,
        )
        outro.active = False
        await db_session.flush()

        await _login(client_with_db, scene["platform"].email)
        rows = (await client_with_db.get(f"{BASE}/platform-admins")).json()["data"]
        por_email = {row["email"]: row for row in rows}
        assert por_email[outro.email]["active"] is False
        assert por_email[scene["platform"].email]["active"] is True

    async def test_payload_e_enxuto_e_nao_carrega_hash_nem_tenant(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        rows = (await client_with_db.get(f"{BASE}/platform-admins")).json()["data"]
        assert rows, "a própria conta logada tem de estar na lista"
        assert set(rows[0]) == {"id", "name", "email", "active", "created_at"}
        # §3.2: hash de senha nunca sai em response, aqui inclusive.
        assert "password_hash" not in rows[0]


class TestCRUD:
    async def test_cria_lista_com_contagens_e_detalha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        name = f"Prospecta {scene['suffix']}"

        created = await client_with_db.post(BASE, json={"name": f"  {name}  "})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == name
        assert body["active"] is True
        assert body["clients_count"] == 0
        assert body["users_count"] == 0

        listed = await client_with_db.get(BASE, params={"pageSize": 100, "search": scene["suffix"]})
        assert listed.status_code == 200, listed.text
        rows = {row["name"]: row for row in listed.json()["data"]}
        assert name in rows
        alfa = rows[scene["org"].name]
        # Só STAFF conta como usuário da org: o operador do cliente fica de fora.
        assert alfa["clients_count"] == 1
        assert alfa["users_count"] == 2
        assert listed.json()["pagination"]["pageSize"] == 100

        detail = await client_with_db.get(f"{BASE}/{body['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == body["id"]

        events = await _events(db_session, "organizacao_criada")
        assert [e.props["organization_id"] for e in events] == [body["id"]]

    async def test_nome_e_unico_sem_caixa(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(BASE, json={"name": scene["org"].name.upper()})
        assert resp.status_code == 409, resp.text

    async def test_nome_vazio_e_400(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        assert (await client_with_db.post(BASE, json={"name": "   "})).status_code == 400

    async def test_desconhecida_e_404(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        assert (await client_with_db.get(f"{BASE}/{uuid4()}")).status_code == 404
        assert (
            await client_with_db.patch(f"{BASE}/{uuid4()}", json={"name": "X"})
        ).status_code == 404

    async def test_renomeia_e_recusa_nome_de_outra(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        other = await client_with_db.post(BASE, json={"name": f"Beta {scene['suffix']}"})
        assert other.status_code == 201

        renamed = await client_with_db.patch(
            f"{BASE}/{scene['org'].id}", json={"name": f"Alfa Renomeada {scene['suffix']}"}
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == f"Alfa Renomeada {scene['suffix']}"

        collision = await client_with_db.patch(
            f"{BASE}/{scene['org'].id}", json={"name": f"beta {scene['suffix']}"}
        )
        assert collision.status_code == 409, collision.text


class TestSuspensao:
    async def test_suspender_derruba_o_staff_e_reativar_desfaz(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        org_id = scene["org"].id

        suspended = await client_with_db.patch(f"{BASE}/{org_id}", json={"active": False})
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["active"] is False

        events = await _events(db_session, "organizacao_desativada")
        assert len(events) == 1
        assert events[0].props == {
            "organization_id": str(org_id),
            "n_usuarios": 2,
            "n_clientes": 1,
        }

        # Suspender de novo é no-op: nenhum segundo evento.
        again = await client_with_db.patch(f"{BASE}/{org_id}", json={"active": False})
        assert again.status_code == 200
        assert len(await _events(db_session, "organizacao_desativada")) == 1

        # O admin da org não entra mais; a plataforma segue.
        denied = await client_with_db.post(
            "/api/v1/auth/login", json={"email": scene["admin"].email, "password": PASSWORD}
        )
        assert denied.status_code == 401
        assert denied.json()["error"]["userMessage"] == "E-mail ou senha incorretos."

        # Nem recebe cliente novo pela plataforma.
        blocked = await client_with_db.post(
            "/api/v1/clients",
            json={
                "name": "Cliente em org suspensa",
                "omie_app_key": "FAKE_DEMO_OMIE_APP_KEY_DO_NOT_USE",
                "omie_app_secret": "FAKE_DEMO_OMIE_APP_SECRET_DO_NOT_USE",
                "organization_id": str(org_id),
            },
        )
        assert blocked.status_code == 409, blocked.text

        reactivated = await client_with_db.patch(f"{BASE}/{org_id}", json={"active": True})
        assert reactivated.status_code == 200
        assert reactivated.json()["active"] is True
        await _login(client_with_db, scene["admin"].email)
        assert (await client_with_db.get("/api/v1/clients")).status_code == 200

    async def test_staff_logado_cai_no_request_seguinte(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A sessão do staff não sobrevive à suspensão: a organização é lida com a
        linha do usuário a cada request."""
        await _login(client_with_db, scene["admin"].email)
        assert (await client_with_db.get("/api/v1/clients")).status_code == 200

        scene["org"].active = False
        await db_session.flush()

        assert (await client_with_db.get("/api/v1/clients")).status_code == 401
