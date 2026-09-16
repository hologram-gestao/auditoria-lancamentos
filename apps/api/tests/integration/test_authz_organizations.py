"""A decisão de acesso com DUAS organizações (camada de organizações, 86e36ecar).

Dois BPOs (A e B), cada um com admin, gerente (com carteira) e um cliente; um
usuário de cliente em A; e a plataforma. O que se prova contra o banco real:

    - `resolve_client_access`: plataforma alcança os dois; admin e gerente só a
      própria organização (o gerente, ainda pela carteira);
    - a lista de clientes (`GET /clients`) e o detalhe por PK respeitam o alcance,
      e a negação cross-org grava `actor_organization_id` na trilha;
    - organização suspensa derruba os usuários dela no request seguinte e recusa
      login com a mensagem genérica;
    - login/refresh devolvem a organização (id + nome) e o JWT carrega o claim.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import null, select

from app.core.authz import resolve_client_access
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.dependencies import ACCESS_TOKEN_COOKIE
from app.core.security import decode_token, hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    Notification,
    NotificationType,
    Organization,
    User,
    UserRole,
    UserScope,
)
from app.modules.reconciliations.service import HOLOGRAM_TEAM_LABEL, author_for_viewer

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PASSWORD = "Senh@OrgAuthz#1"
SECRET_CLIENT_A = "Fulana Participacoes A LTDA"
SECRET_CLIENT_B = "Beltrano Servicos B LTDA"


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
    client_id: UUID | None = None,
) -> User:
    user = User(
        name=email.split("@")[0],
        email=email.lower(),
        password_hash=hash_password(PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        # Plataforma: `null()` grava NULL de verdade (o `None` seria omitido e o
        # banco preencheria a Hologram).
        organization_id=null() if organization is None else organization.id,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user, ["organization_id"])
    return user


async def _seed_client(
    session: AsyncSession, *, creator: User, organization: Organization, name: str
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("org-app-key", hex_key)
    ct_s, iv_s = encrypt("org-app-secret", hex_key)
    client = Client(
        name=name,
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


async def _assign(
    session: AsyncSession, *, client: Client, manager: User, by: User, primary: bool = True
) -> None:
    session.add(
        ClientAssignment(
            client_id=client.id, user_id=manager.id, assigned_by=by.id, is_primary=primary
        )
    )
    await session.flush()


async def _login(client: AsyncClient, email: str) -> dict[str, Any]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["user"])


@pytest.fixture
async def world(db_session: AsyncSession) -> dict[str, Any]:
    suffix = uuid4().hex[:6]
    org_a = await _seed_org(db_session, f"Hologram Teste {suffix}")
    org_b = await _seed_org(db_session, f"Prospecta Teste {suffix}")

    platform = await _seed_user(
        db_session,
        email=f"plataforma-{suffix}@hologram.com.br",
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
    manager_b = await _seed_user(
        db_session, email=f"gerente-b-{suffix}@b.com.br", role=UserRole.MANAGER, organization=org_b
    )

    client_a = await _seed_client(
        db_session, creator=admin_a, organization=org_a, name=SECRET_CLIENT_A
    )
    client_a2 = await _seed_client(
        db_session, creator=admin_a, organization=org_a, name=f"Outro de A {suffix}"
    )
    client_b = await _seed_client(
        db_session, creator=admin_b, organization=org_b, name=SECRET_CLIENT_B
    )
    await _assign(db_session, client=client_a, manager=manager_a, by=admin_a)
    await _assign(db_session, client=client_b, manager=manager_b, by=admin_b)

    operator_a = await _seed_user(
        db_session,
        email=f"op-a-{suffix}@cliente-a.com.br",
        role=UserRole.CLIENT_OPERATOR,
        organization=org_a,
        scope=UserScope.CLIENT,
        client_id=client_a.id,
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "platform": platform,
        "admin_a": admin_a,
        "manager_a": manager_a,
        "admin_b": admin_b,
        "manager_b": manager_b,
        "client_a": client_a,
        "client_a2": client_a2,
        "client_b": client_b,
        "operator_a": operator_a,
    }


def _as_current(user: User) -> Any:
    from app.core.authz import CurrentUser

    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        scope=user.scope,
        client_id=user.client_id,
        organization_id=user.organization_id,
    )


class TestResolveClientAccess:
    async def test_plataforma_alcanca_as_duas_organizacoes(
        self, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        platform = _as_current(world["platform"])
        assert await resolve_client_access(db_session, platform, world["client_a"].id) is True
        assert await resolve_client_access(db_session, platform, world["client_b"].id) is True

    async def test_admin_alcanca_so_a_propria_organizacao(
        self, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        admin_a = _as_current(world["admin_a"])
        assert await resolve_client_access(db_session, admin_a, world["client_a"].id) is True
        assert await resolve_client_access(db_session, admin_a, world["client_a2"].id) is True
        assert await resolve_client_access(db_session, admin_a, world["client_b"].id) is False
        admin_b = _as_current(world["admin_b"])
        assert await resolve_client_access(db_session, admin_b, world["client_a"].id) is False

    async def test_gerente_alcanca_a_carteira_e_so_dentro_da_organizacao(
        self, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        manager_a = _as_current(world["manager_a"])
        assert await resolve_client_access(db_session, manager_a, world["client_a"].id) is True
        # Mesma org, fora da carteira: a regra da carteira continua valendo.
        assert await resolve_client_access(db_session, manager_a, world["client_a2"].id) is False
        assert await resolve_client_access(db_session, manager_a, world["client_b"].id) is False

    async def test_gerente_com_carteira_em_outra_organizacao_nao_alcanca(
        self, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        """Defense-in-depth: um assignment cross-org (que a task D2 passa a
        recusar) não basta — a organização do cliente decide antes da carteira."""
        await _assign(
            db_session,
            client=world["client_b"],
            manager=world["manager_a"],
            by=world["admin_b"],
            primary=False,
        )
        manager_a = _as_current(world["manager_a"])
        assert await resolve_client_access(db_session, manager_a, world["client_b"].id) is False

    async def test_organizacao_alvo_informada_pelo_caller_e_respeitada(
        self, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        """`require_client_access` passa a org do `Client` já carregado — a decisão
        usa o valor recebido em vez de consultar de novo."""
        admin_a = _as_current(world["admin_a"])
        assert (
            await resolve_client_access(
                db_session,
                admin_a,
                world["client_a"].id,
                target_organization_id=world["org_b"].id,
            )
            is False
        )


class TestListaDeClientes:
    async def test_admin_lista_so_a_propria_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.get("/api/v1/clients", params={"pageSize": 50})
        assert resp.status_code == 200, resp.text
        names = {row["name"] for row in resp.json()["data"]}
        assert names == {SECRET_CLIENT_B}
        assert SECRET_CLIENT_A not in resp.text
        assert resp.json()["pagination"]["total"] == 1

    async def test_gerente_lista_so_a_carteira(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["manager_a"].email)
        resp = await client_with_db.get("/api/v1/clients", params={"pageSize": 50})
        assert resp.status_code == 200, resp.text
        assert {row["name"] for row in resp.json()["data"]} == {SECRET_CLIENT_A}
        assert SECRET_CLIENT_B not in resp.text

    async def test_plataforma_lista_as_duas_organizacoes(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.get("/api/v1/clients", params={"pageSize": 50})
        assert resp.status_code == 200, resp.text
        names = {row["name"] for row in resp.json()["data"]}
        assert {SECRET_CLIENT_A, SECRET_CLIENT_B} <= names


class TestDetalheCrossOrg:
    async def test_admin_de_outra_organizacao_leva_404_e_a_trilha_diz_de_onde_veio(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.get(f"/api/v1/clients/{world['client_a'].id}")

        assert resp.status_code in {403, 404}, resp.text
        assert SECRET_CLIENT_A not in resp.text

        rows = (
            (
                await db_session.execute(
                    select(AccessAudit).where(
                        AccessAudit.action == "denied",
                        AccessAudit.client_id == world["client_a"].id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].user_scope == UserScope.SYSTEM.value
        assert rows[0].actor_client_id is None
        assert rows[0].actor_organization_id == world["org_b"].id

    async def test_plataforma_abre_recursos_de_qualquer_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        """Por PK, nas duas organizações. A lista de conciliações do cliente (e não
        o detalhe do cliente): o detalhe sincroniza contas no Omie a cada cache
        miss, e o Omie não faz parte do que se prova aqui."""
        await _login(client_with_db, world["platform"].email)
        for key in ("client_a", "client_b"):
            resp = await client_with_db.get(f"/api/v1/clients/{world[key].id}/reconciliations")
            assert resp.status_code == 200, resp.text


class TestNotificacoes:
    async def test_admin_so_ve_notificacao_de_cliente_da_propria_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        """A notificação é pessoal E o alcance é da organização: se a pessoa for
        movida de organização, a notificação antiga do cliente para de aparecer."""
        admin_a: User = world["admin_a"]
        db_session.add(
            Notification(
                user_id=admin_a.id,
                client_id=world["client_a"].id,
                session_id=uuid4(),
                tipo=NotificationType.PROCESSADA.value,
                omie_conta_id=42,
                reference_month=date(2026, 8, 1),
            )
        )
        await db_session.flush()
        await _login(client_with_db, admin_a.email)

        resp = await client_with_db.get("/api/v1/notifications/unread-count")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["unread"] == 1

        # Movido para a organização B (UPDATE: `None`/valor direto funciona).
        admin_a.organization_id = world["org_b"].id
        await db_session.flush()

        resp = await client_with_db.get("/api/v1/notifications/unread-count")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["unread"] == 0


class TestOrganizacaoSuspensa:
    async def test_desativar_a_organizacao_derruba_o_usuario_no_request_seguinte(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        assert (await client_with_db.get("/api/v1/clients")).status_code == 200

        world["org_b"].active = False
        await db_session.flush()

        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 401, resp.text

    async def test_login_em_organizacao_suspensa_e_recusado_com_mensagem_generica(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        world["org_b"].active = False
        await db_session.flush()

        resp = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": world["admin_b"].email, "password": PASSWORD},
        )
        assert resp.status_code == 401
        body = resp.json()["error"]
        assert body["userMessage"] == "E-mail ou senha incorretos."
        # O corpo é IDÊNTICO ao de senha errada — nem `message` distingue.
        wrong = await client_with_db.post(
            "/api/v1/auth/login",
            json={"email": world["admin_a"].email, "password": "errada-de-proposito"},
        )
        assert wrong.status_code == 401
        assert wrong.json()["error"] == body
        assert str(world["admin_b"].id) not in resp.text

    async def test_plataforma_nao_depende_de_organizacao_nenhuma(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        world["org_a"].active = False
        world["org_b"].active = False
        await db_session.flush()
        await _login(client_with_db, world["platform"].email)
        assert (await client_with_db.get("/api/v1/clients")).status_code == 200


class TestUsuariosDeStaff:
    """`/api/v1/users` é a organização do observador — e só staff."""

    async def test_admin_lista_so_o_staff_da_propria_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.get("/api/v1/users", params={"pageSize": 100})
        assert resp.status_code == 200, resp.text
        emails = {row["email"] for row in resp.json()["data"]}
        assert emails == {world["admin_b"].email, world["manager_b"].email}
        # Nem a plataforma, nem o staff de A, nem o usuário de cliente.
        assert world["platform"].email not in resp.text
        assert world["admin_a"].email not in resp.text
        assert world["operator_a"].email not in resp.text

    async def test_plataforma_lista_o_staff_de_todas_as_organizacoes(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.get("/api/v1/users", params={"pageSize": 100})
        assert resp.status_code == 200, resp.text
        emails = {row["email"] for row in resp.json()["data"]}
        assert {world["admin_a"].email, world["admin_b"].email} <= emails
        # A própria plataforma não é "staff de organização": fica fora da lista.
        assert world["platform"].email not in emails
        assert world["operator_a"].email not in emails

    @pytest.mark.parametrize("target", ["platform", "admin_a", "operator_a"])
    async def test_admin_nao_alcanca_plataforma_outra_org_nem_usuario_de_cliente(
        self, client_with_db: AsyncClient, world: dict[str, Any], target: str
    ) -> None:
        """Anti-IDOR: o alvo fora do alcance simplesmente não existe (404) — nem
        para desativar, nem para rebaixar (o que, na plataforma, estouraria o
        CHECK e viraria 500)."""
        await _login(client_with_db, world["admin_b"].email)
        user_id = world[target].id
        assert (await client_with_db.get(f"/api/v1/users/{user_id}")).status_code == 404
        assert (await client_with_db.post(f"/api/v1/users/{user_id}/deactivate")).status_code == 404
        assert (
            await client_with_db.patch(f"/api/v1/users/{user_id}", json={"role": "manager"})
        ).status_code == 404
        # Continua ativo e com o papel original.
        assert world[target].active is True

    async def test_staff_criado_nasce_na_organizacao_de_quem_cria(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.post(
            "/api/v1/users",
            json={
                "name": "Novo Gerente B",
                "email": f"novo-b-{uuid4().hex[:6]}@b.com.br",
                "password": "Senh@Nova#123",
                "role": "manager",
            },
        )
        assert resp.status_code == 201, resp.text
        created = await db_session.get(User, UUID(resp.json()["id"]))
        assert created is not None
        await db_session.refresh(created, ["organization_id"])
        assert created.organization_id == world["org_b"].id


class TestClienteCriadoNaOrganizacaoDoAtor:
    async def test_cliente_criado_por_admin_de_b_nasce_em_b_e_continua_ao_alcance(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        """Sem o carimbo, o cliente cairia no default do banco (Hologram) e o
        próprio criador perderia o alcance a ele no request seguinte."""
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.post(
            "/api/v1/clients",
            json={
                "name": f"Cliente novo de B {uuid4().hex[:6]}",
                "omie_app_key": "FAKE_DEMO_OMIE_APP_KEY_DO_NOT_USE",
                "omie_app_secret": "FAKE_DEMO_OMIE_APP_SECRET_DO_NOT_USE",
            },
        )
        assert resp.status_code == 201, resp.text
        created = await db_session.get(Client, UUID(resp.json()["id"]))
        assert created is not None
        await db_session.refresh(created, ["organization_id"])
        assert created.organization_id == world["org_b"].id
        listed = await client_with_db.get("/api/v1/clients", params={"pageSize": 50})
        assert resp.json()["id"] in {row["id"] for row in listed.json()["data"]}


class TestSessao:
    async def test_login_e_refresh_devolvem_a_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        body = await _login(client_with_db, world["admin_a"].email)
        assert body["organization_id"] == str(world["org_a"].id)
        assert body["organization_name"] == world["org_a"].name

        refreshed = await client_with_db.post("/api/v1/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["user"]["organization_id"] == str(world["org_a"].id)

    async def test_plataforma_vem_sem_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        body = await _login(client_with_db, world["platform"].email)
        assert body["scope"] == UserScope.PLATFORM.value
        assert body["role"] == UserRole.PLATFORM_ADMIN.value
        assert body["organization_id"] is None
        assert body["organization_name"] is None

    async def test_jwt_carrega_o_claim_da_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["manager_b"].email)
        token = client_with_db.cookies.get(ACCESS_TOKEN_COOKIE)
        assert token
        payload = decode_token(token, get_settings())
        assert payload.organization_id == str(world["org_b"].id)
        assert payload.scope == UserScope.SYSTEM.value


class TestMascaraDeAutor:
    def test_autor_de_plataforma_e_mascarado_para_o_cliente_como_o_staff(self) -> None:
        author = User(
            name="Pedro",
            email="pedro@x.com",
            password_hash="x",
            role="platform_admin",
            scope="platform",
        )
        masked = author_for_viewer(author, UserScope.CLIENT.value)
        assert masked.name == HOLOGRAM_TEAM_LABEL
        assert masked.email is None
        # Staff vendo staff: nome real.
        assert author_for_viewer(author, UserScope.SYSTEM.value).email == "pedro@x.com"
