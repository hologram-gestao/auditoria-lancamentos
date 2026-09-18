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

Task 86e36ecqz (rotas existentes org-aware) acrescentou: o rótulo de autoria
"Equipe {org do cliente}", os filtros `?organizationId=`/`?role=` e a criação
por organização em `/users`, `organization` na response de cliente, a categoria
validada na organização do cliente, e a trilha da negação cross-org por PK de
sessão (o admin de B nem carrega a sessão de A, e a tentativa fica gravada).
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, null, select

from app.core.authz import CurrentUser, resolve_client_access
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.dependencies import ACCESS_TOKEN_COOKIE
from app.core.security import decode_token, hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    ClientCategory,
    Notification,
    NotificationType,
    Organization,
    ReconciliationFile,
    ReconciliationSession,
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


def _as_current(user: User, *, organization_name: str | None = None) -> CurrentUser:
    return CurrentUser(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        scope=user.scope,
        client_id=user.client_id,
        organization_id=user.organization_id,
        organization_name=organization_name,
    )


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User, conta: int = 77
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=conta,
        reference_month=date(2026, 5, 1),
        date_tolerance_days=0,
        status="reviewing",
    )
    session.add(sess)
    await session.flush()
    session.add(
        ReconciliationFile(
            session_id=sess.id,
            file_hash=hashlib.sha256(str(sess.id).encode()).hexdigest(),
            status="parsed",
        )
    )
    await session.flush()
    return sess


async def _seed_category(
    session: AsyncSession, *, organization: Organization, name: str
) -> ClientCategory:
    category = ClientCategory(name=name, tone="neutral", organization_id=organization.id)
    session.add(category)
    await session.flush()
    return category


async def _denied_for(db_session: AsyncSession, client_id: UUID) -> list[AccessAudit]:
    rows = await db_session.execute(
        select(AccessAudit).where(
            AccessAudit.action == "denied", AccessAudit.client_id == client_id
        )
    )
    return list(rows.scalars().all())


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


def _new_client_body(**extra: object) -> dict[str, object]:
    return {
        "name": f"Cliente novo {uuid4().hex[:6]}",
        "omie_app_key": "FAKE_DEMO_OMIE_APP_KEY_DO_NOT_USE",
        "omie_app_secret": "FAKE_DEMO_OMIE_APP_SECRET_DO_NOT_USE",
        **extra,
    }


async def _created_client(db_session: AsyncSession, resp: Any) -> Client:
    created = await db_session.get(Client, UUID(resp.json()["id"]))
    assert created is not None
    await db_session.refresh(created, ["organization_id"])
    return created


class TestCriacaoDeClientePorOrganizacao:
    """Onde o cliente nasce vem da LINHA do ator; só a plataforma escolhe (86e36ecjp)."""

    async def test_admin_cria_na_propria_organizacao_e_continua_ao_alcance(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        """Sem o carimbo, o cliente cairia no default do banco (Hologram) e o
        próprio criador perderia o alcance a ele no request seguinte."""
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.post("/api/v1/clients", json=_new_client_body())
        assert resp.status_code == 201, resp.text
        assert (await _created_client(db_session, resp)).organization_id == world["org_b"].id
        # Admin não entra na carteira: nasce sem responsável.
        assert resp.json()["responsible_manager"] is None
        listed = await client_with_db.get("/api/v1/clients", params={"pageSize": 50})
        assert resp.json()["id"] in {row["id"] for row in listed.json()["data"]}

    async def test_admin_pode_repetir_a_propria_organizacao_no_payload(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_a"].email)
        resp = await client_with_db.post(
            "/api/v1/clients", json=_new_client_body(organization_id=str(world["org_a"].id))
        )
        assert resp.status_code == 201, resp.text
        assert (await _created_client(db_session, resp)).organization_id == world["org_a"].id

    async def test_admin_nao_cria_em_outra_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        """`organization_id` alheio no payload é 403 — nunca ignorado em silêncio."""
        await _login(client_with_db, world["admin_a"].email)
        before = await db_session.scalar(
            select(func.count(Client.id)).where(Client.organization_id == world["org_b"].id)
        )
        resp = await client_with_db.post(
            "/api/v1/clients", json=_new_client_body(organization_id=str(world["org_b"].id))
        )
        assert resp.status_code == 403, resp.text
        assert SECRET_CLIENT_B not in resp.text
        after = await db_session.scalar(
            select(func.count(Client.id)).where(Client.organization_id == world["org_b"].id)
        )
        assert after == before

    async def test_gerente_cria_na_propria_organizacao_e_vira_o_responsavel(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["manager_a"].email)
        resp = await client_with_db.post("/api/v1/clients", json=_new_client_body())
        assert resp.status_code == 201, resp.text
        assert (await _created_client(db_session, resp)).organization_id == world["org_a"].id
        assert resp.json()["responsible_manager"]["email"] == world["manager_a"].email

    async def test_plataforma_escolhe_a_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/clients", json=_new_client_body(organization_id=str(world["org_b"].id))
        )
        assert resp.status_code == 201, resp.text
        assert (await _created_client(db_session, resp)).organization_id == world["org_b"].id
        # A plataforma não entra na carteira.
        assert resp.json()["responsible_manager"] is None

    async def test_plataforma_sem_organizacao_e_400(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post("/api/v1/clients", json=_new_client_body())
        assert resp.status_code == 400, resp.text

    async def test_plataforma_com_organizacao_inexistente_e_404(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/clients", json=_new_client_body(organization_id=str(uuid4()))
        )
        assert resp.status_code == 404, resp.text

    async def test_plataforma_nao_cria_em_organizacao_suspensa(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        world["org_b"].active = False
        await db_session.flush()
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/clients", json=_new_client_body(organization_id=str(world["org_b"].id))
        )
        assert resp.status_code == 409, resp.text


class TestCarteiraIntraOrg:
    """Gerente e cliente na MESMA organização — a validação mora num lugar só
    (`is_active_manager`), consumido por adicionar gerente, definir responsável
    e criar cliente."""

    async def test_admin_nao_adiciona_gerente_de_outra_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        """400, o MESMO código de "não é gerente": distinguir diria que o id existe
        e é gerente em algum lugar (anti-enumeração)."""
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.post(
            f"/api/v1/clients/{world['client_b'].id}/managers",
            json={"user_id": str(world["manager_a"].id)},
        )
        assert resp.status_code == 400, resp.text
        assert world["manager_a"].email not in resp.text

    async def test_admin_nao_define_responsavel_de_outra_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.patch(
            f"/api/v1/clients/{world['client_b'].id}/assign",
            json={"user_id": str(world["manager_a"].id)},
        )
        assert resp.status_code == 400, resp.text

    async def test_gerente_da_mesma_organizacao_entra_normalmente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        extra = await _seed_user(
            db_session,
            email=f"gerente-b2-{uuid4().hex[:6]}@b.com.br",
            role=UserRole.MANAGER,
            organization=world["org_b"],
        )
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.post(
            f"/api/v1/clients/{world['client_b'].id}/managers",
            json={"user_id": str(extra.id)},
        )
        assert resp.status_code in {200, 201}, resp.text
        assert extra.email in {row["email"] for row in resp.json()["data"]}


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
        cliente = User(
            name="Op",
            email="op@cliente.com",
            password_hash="x",
            role="client_operator",
            scope="client",
        )
        masked = author_for_viewer(author, _as_current(cliente, organization_name="Hologram"))
        assert masked.name == HOLOGRAM_TEAM_LABEL
        assert masked.email is None
        # Staff vendo staff: nome real.
        staff = User(name="A", email="a@x.com", password_hash="x", role="admin", scope="system")
        assert author_for_viewer(author, _as_current(staff)).email == "pedro@x.com"

    def test_rotulo_e_o_da_organizacao_do_observador(self) -> None:
        """Cliente da Prospecta lê "Equipe Prospecta" — nunca "Equipe Hologram"."""
        author = User(
            name="Ana", email="ana@b.com", password_hash="x", role="admin", scope="system"
        )
        cliente = User(
            name="Op",
            email="op@cliente.com",
            password_hash="x",
            role="client_operator",
            scope="client",
        )
        masked = author_for_viewer(author, _as_current(cliente, organization_name="Prospecta"))
        assert masked.name == "Equipe Prospecta"
        assert masked.email is None


class TestRotuloDeEquipePorOrganizacao:
    """O usuário de cliente lê "Equipe {org do cliente}" no detalhe e na lista
    (86e36ecqz) — e nunca o nome ou o e-mail de quem é da equipe."""

    async def test_cliente_da_organizacao_b_le_equipe_b(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        sess = await _seed_session(db_session, client=world["client_b"], creator=world["admin_b"])
        cliente_b = await _seed_user(
            db_session,
            email=f"op-b-{uuid4().hex[:6]}@cliente-b.com.br",
            role=UserRole.CLIENT_OPERATOR,
            organization=world["org_b"],
            scope=UserScope.CLIENT,
            client_id=world["client_b"].id,
        )
        await _login(client_with_db, cliente_b.email)

        detalhe = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detalhe.status_code == 200, detalhe.text
        assert detalhe.json()["data"]["created_by"] == {
            "name": f"Equipe {world['org_b'].name}",
            "email": None,
        }
        assert world["admin_b"].email not in detalhe.text
        assert world["admin_b"].name not in detalhe.text

        lista = await client_with_db.get(f"/api/v1/clients/{world['client_b'].id}/reconciliations")
        assert lista.status_code == 200, lista.text
        assert lista.json()["data"][0]["created_by"] == {
            "name": f"Equipe {world['org_b'].name}",
            "email": None,
        }
        assert world["admin_b"].email not in lista.text

    async def test_cliente_da_organizacao_a_le_equipe_a_e_o_staff_le_o_nome(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        sess = await _seed_session(db_session, client=world["client_a"], creator=world["admin_a"])

        await _login(client_with_db, world["operator_a"].email)
        detalhe = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detalhe.status_code == 200, detalhe.text
        assert detalhe.json()["data"]["created_by"]["name"] == f"Equipe {world['org_a'].name}"

        await _login(client_with_db, world["admin_a"].email)
        detalhe = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detalhe.json()["data"]["created_by"] == {
            "name": world["admin_a"].name,
            "email": world["admin_a"].email,
        }


class TestSessaoCrossOrgPorPk:
    """O admin/gerente da organização B nem carrega a sessão de um cliente de A
    (o SELECT sai restrito ao alcance) — e a tentativa fica na trilha."""

    @pytest.mark.parametrize("attacker", ["admin_b", "manager_b"])
    async def test_staff_de_outra_organizacao_leva_404_e_grava_a_negacao(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        world: dict[str, Any],
        attacker: str,
    ) -> None:
        sess = await _seed_session(db_session, client=world["client_a"], creator=world["admin_a"])
        await _login(client_with_db, world[attacker].email)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 404, resp.text
        assert SECRET_CLIENT_A not in resp.text

        rows = await _denied_for(db_session, world["client_a"].id)
        assert len(rows) == 1
        assert rows[0].user_scope == UserScope.SYSTEM.value
        assert rows[0].actor_client_id is None
        assert rows[0].actor_organization_id == world["org_b"].id
        # A linha aponta o CLIENTE alvo (como no miss por tenant da S5); o
        # `session_id` não entra em `record_cross_tenant_denied` — é o caminho
        # único de gravação, e mudá-lo é outra task.

    async def test_sessao_inexistente_nao_gera_linha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_b"].email)
        resp = await client_with_db.get(f"/api/v1/reconciliations/{uuid4()}")
        assert resp.status_code == 404
        assert await _denied_for(db_session, world["client_a"].id) == []

    async def test_plataforma_e_o_admin_da_propria_organizacao_abrem(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        sess = await _seed_session(db_session, client=world["client_a"], creator=world["admin_a"])
        for who in ("platform", "admin_a", "manager_a"):
            await _login(client_with_db, world[who].email)
            resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
            assert resp.status_code == 200, (who, resp.text)
        assert await _denied_for(db_session, world["client_a"].id) == []


class TestUsuariosDeStaffFiltros:
    """`?organizationId=` e `?role=` em `/users` (86e36ecqz) — e a response diz
    de que organização cada staff é."""

    async def test_plataforma_filtra_por_organizacao_e_ve_a_organizacao_de_cada_um(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.get(
            "/api/v1/users", params={"pageSize": 100, "organizationId": str(world["org_b"].id)}
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert {r["email"] for r in rows} == {world["admin_b"].email, world["manager_b"].email}
        assert resp.json()["pagination"]["total"] == 2
        for row in rows:
            assert row["scope"] == UserScope.SYSTEM.value
            assert row["organization_id"] == str(world["org_b"].id)
            assert row["organization_name"] == world["org_b"].name

    async def test_plataforma_filtra_por_papel(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.get(
            "/api/v1/users", params={"pageSize": 100, "role": "manager"}
        )
        assert resp.status_code == 200, resp.text
        emails = {r["email"] for r in resp.json()["data"]}
        assert {world["manager_a"].email, world["manager_b"].email} <= emails
        assert world["admin_a"].email not in emails
        assert {r["role"] for r in resp.json()["data"]} == {"manager"}

        invalido = await client_with_db.get("/api/v1/users", params={"role": "platform_admin"})
        assert invalido.status_code == 400

    async def test_admin_so_pode_pedir_a_propria_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_a"].email)
        alheia = await client_with_db.get(
            "/api/v1/users", params={"organizationId": str(world["org_b"].id)}
        )
        assert alheia.status_code == 403, alheia.text
        assert world["admin_b"].email not in alheia.text
        assert world["org_b"].name not in alheia.text

        propria = await client_with_db.get(
            "/api/v1/users", params={"pageSize": 100, "organizationId": str(world["org_a"].id)}
        )
        assert propria.status_code == 200, propria.text
        assert {r["email"] for r in propria.json()["data"]} == {
            world["admin_a"].email,
            world["manager_a"].email,
        }
        assert {r["organization_name"] for r in propria.json()["data"]} == {world["org_a"].name}


class TestCriacaoDeStaffPorOrganizacao:
    """`POST /users`: a plataforma escolhe (obrigatório, validado); o admin cria
    na própria e não pode apontar outra; `platform_admin` não é papel de API."""

    def _body(self, **extra: object) -> dict[str, object]:
        return {
            "name": "Staff Novo",
            "email": f"novo-{uuid4().hex[:8]}@staff.com.br",
            "password": "Senh@Nova#123",
            "role": "manager",
            **extra,
        }

    async def test_plataforma_sem_organizacao_e_400(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post("/api/v1/users", json=self._body())
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_plataforma_com_organizacao_inexistente_e_404(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/users", json=self._body(organization_id=str(uuid4()))
        )
        assert resp.status_code == 404, resp.text

    async def test_plataforma_nao_cria_em_organizacao_suspensa(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        world["org_b"].active = False
        await db_session.flush()
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/users", json=self._body(organization_id=str(world["org_b"].id))
        )
        assert resp.status_code == 409, resp.text

    async def test_plataforma_escolhe_a_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/users", json=self._body(organization_id=str(world["org_b"].id))
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["scope"] == UserScope.SYSTEM.value
        assert body["organization_id"] == str(world["org_b"].id)
        assert body["organization_name"] == world["org_b"].name
        created = await db_session.get(User, UUID(body["id"]))
        assert created is not None
        await db_session.refresh(created, ["organization_id"])
        assert created.organization_id == world["org_b"].id
        # E o admin de B passa a alcançá-lo; o de A, não.
        await _login(client_with_db, world["admin_b"].email)
        assert (await client_with_db.get(f"/api/v1/users/{body['id']}")).status_code == 200
        await _login(client_with_db, world["admin_a"].email)
        assert (await client_with_db.get(f"/api/v1/users/{body['id']}")).status_code == 404

    async def test_admin_nao_cria_em_outra_organizacao_mas_pode_repetir_a_propria(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_a"].email)
        alheia = await client_with_db.post(
            "/api/v1/users", json=self._body(organization_id=str(world["org_b"].id))
        )
        assert alheia.status_code == 403, alheia.text
        assert world["org_b"].name not in alheia.text

        propria = await client_with_db.post(
            "/api/v1/users", json=self._body(organization_id=str(world["org_a"].id))
        )
        assert propria.status_code == 201, propria.text
        assert propria.json()["organization_name"] == world["org_a"].name

    @pytest.mark.parametrize("who", ["platform", "admin_a"])
    async def test_platform_admin_nao_e_papel_de_api(
        self, client_with_db: AsyncClient, world: dict[str, Any], who: str
    ) -> None:
        """`SystemUserRole` é a whitelist: forjar o papel de plataforma é erro de
        validação (o handler global converte o 422 do Pydantic em 400)."""
        await _login(client_with_db, world[who].email)
        resp = await client_with_db.post(
            "/api/v1/users",
            json=self._body(role="platform_admin", organization_id=str(world["org_a"].id)),
        )
        assert resp.status_code == 400, resp.text


class TestClientesPorOrganizacao:
    """`GET /clients` diz a organização de cada cliente e aceita `?organizationId=`
    da plataforma; a categoria de um cliente é sempre do catálogo da org dele."""

    async def test_plataforma_ve_a_organizacao_de_cada_cliente_e_filtra(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["platform"].email)
        tudo = await client_with_db.get("/api/v1/clients", params={"pageSize": 100})
        assert tudo.status_code == 200, tudo.text
        por_nome = {c["name"]: c["organization"] for c in tudo.json()["data"]}
        assert por_nome[SECRET_CLIENT_A] == {
            "id": str(world["org_a"].id),
            "name": world["org_a"].name,
        }
        assert por_nome[SECRET_CLIENT_B] == {
            "id": str(world["org_b"].id),
            "name": world["org_b"].name,
        }

        so_b = await client_with_db.get(
            "/api/v1/clients", params={"pageSize": 100, "organizationId": str(world["org_b"].id)}
        )
        assert so_b.status_code == 200, so_b.text
        assert {c["name"] for c in so_b.json()["data"]} == {SECRET_CLIENT_B}
        assert so_b.json()["pagination"]["total"] == 1

    async def test_admin_so_pode_pedir_a_propria_organizacao(
        self, client_with_db: AsyncClient, world: dict[str, Any]
    ) -> None:
        await _login(client_with_db, world["admin_a"].email)
        alheia = await client_with_db.get(
            "/api/v1/clients", params={"organizationId": str(world["org_b"].id)}
        )
        assert alheia.status_code == 403, alheia.text
        assert SECRET_CLIENT_B not in alheia.text

        propria = await client_with_db.get(
            "/api/v1/clients", params={"pageSize": 100, "organizationId": str(world["org_a"].id)}
        )
        assert propria.status_code == 200, propria.text
        assert propria.json()["pagination"]["total"] == 2

        # O gerente pedindo a própria org continua limitado à CARTEIRA: o filtro
        # de org é no-op para o staff, o alcance (`reach_filter`) segue valendo.
        await _login(client_with_db, world["manager_a"].email)
        carteira = await client_with_db.get(
            "/api/v1/clients", params={"pageSize": 100, "organizationId": str(world["org_a"].id)}
        )
        assert carteira.status_code == 200, carteira.text
        assert {c["name"] for c in carteira.json()["data"]} == {SECRET_CLIENT_A}

    async def test_categoria_de_outra_organizacao_nao_entra_no_cliente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        cat_a = await _seed_category(db_session, organization=world["org_a"], name="Varejo A")
        cat_b = await _seed_category(db_session, organization=world["org_b"], name="Varejo B")
        await _login(client_with_db, world["admin_a"].email)
        client_id = world["client_a"].id

        alheia = await client_with_db.patch(
            f"/api/v1/clients/{client_id}", json={"category_id": str(cat_b.id)}
        )
        assert alheia.status_code == 400, alheia.text
        assert "Varejo B" not in alheia.text
        await db_session.refresh(world["client_a"], ["category_id"])
        assert world["client_a"].category_id is None

        propria = await client_with_db.patch(
            f"/api/v1/clients/{client_id}", json={"category_id": str(cat_a.id)}
        )
        assert propria.status_code == 200, propria.text
        assert propria.json()["category"]["id"] == str(cat_a.id)
        assert propria.json()["organization"]["id"] == str(world["org_a"].id)

    async def test_plataforma_criando_cliente_valida_a_categoria_na_organizacao_escolhida(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: dict[str, Any]
    ) -> None:
        cat_a = await _seed_category(db_session, organization=world["org_a"], name="Fintech A")
        await _login(client_with_db, world["platform"].email)
        resp = await client_with_db.post(
            "/api/v1/clients",
            json=_new_client_body(
                organization_id=str(world["org_b"].id), category_id=str(cat_a.id)
            ),
        )
        assert resp.status_code == 400, resp.text
