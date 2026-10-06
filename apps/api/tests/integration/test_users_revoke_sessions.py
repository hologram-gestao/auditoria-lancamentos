"""Revogação de sessão SEM troca de senha (86e3anx4u, parte 1).

    POST /api/v1/users/{user_id}/sessions/revoke                       (staff)
    POST /api/v1/clients/{client_id}/users/{user_id}/sessions/revoke   (usuário do cliente)

Cobre:
    - Rota de staff: plataforma encerra staff de qualquer organização e um par de
      plataforma; admin só o staff da própria (outra org, plataforma e usuário de
      cliente são 404 sem nome);
      gerente 403; anônimo 401; si mesmo 409; inexistente 404.
    - Rota do cliente: `client_manager` do tenant 204; `client_operator` 403 com
      1 linha `denied` em `access_audit`; gerente com o cliente na carteira 204;
      gerente sem o cliente e admin de OUTRA organização negados sem nome (403 do
      guard de tenant, que também grava a trilha); usuário de outro cliente na
      URL 404; cliente encerrado 409; si mesmo 409.
    - O critério de aceite: token emitido ANTES vira 401 no request seguinte, no
      access E no refresh, com `users.active` ainda `true`, e o login com a MESMA
      senha volta a funcionar. Hash intocado.
    - Trilha: evento `sessoes_encerradas` só com IDs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import null, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    Organization,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PASSWORD = "Senh@Revoke#Mesma1"
# Rota autenticada que TODO papel alcança: a sonda de "sessão viva".
PROBE = "/api/v1/notifications/unread-count"


def _staff_url(user_id: UUID) -> str:
    return f"/api/v1/users/{user_id}/sessions/revoke"


def _client_url(client_id: UUID, user_id: UUID) -> str:
    return f"/api/v1/clients/{client_id}/users/{user_id}/sessions/revoke"


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
        name=f"Pessoa {email.split('@')[0]}",
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
    session: AsyncSession, *, creator: User, organization: Organization, closed: bool = False
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("revoke-key", hex_key)
    ct_s, iv_s = encrypt("revoke-secret", hex_key)
    client = Client(
        name=f"Cliente {uuid4().hex[:6]}",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=not closed,
        closed_at=datetime.now(UTC) if closed else None,
        created_by=creator.id,
        organization_id=organization.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _login(client: AsyncClient, email: str, password: str = PASSWORD) -> Any:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp


async def _logout(client: AsyncClient) -> None:
    await client.post("/api/v1/auth/logout")
    client.cookies.clear()


def _assert_sem_nome(resp: Any, target: User) -> None:
    assert target.name not in resp.text
    assert target.email not in resp.text


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    suffix = uuid4().hex[:6]
    org_a = Organization(name=f"Org A {suffix}")
    org_b = Organization(name=f"Org B {suffix}")
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    platform = await _seed_user(
        db_session,
        email=f"plat-{suffix}@hologram.com.br",
        role=UserRole.PLATFORM_ADMIN,
        scope=UserScope.PLATFORM,
    )
    platform_2 = await _seed_user(
        db_session,
        email=f"plat2-{suffix}@hologram.com.br",
        role=UserRole.PLATFORM_ADMIN,
        scope=UserScope.PLATFORM,
    )
    admin_a = await _seed_user(
        db_session, email=f"admin-a-{suffix}@a.com", role=UserRole.ADMIN, organization=org_a
    )
    manager_a = await _seed_user(
        db_session, email=f"ger-a-{suffix}@a.com", role=UserRole.MANAGER, organization=org_a
    )
    manager_a_fora = await _seed_user(
        db_session, email=f"ger-a2-{suffix}@a.com", role=UserRole.MANAGER, organization=org_a
    )
    admin_b = await _seed_user(
        db_session, email=f"admin-b-{suffix}@b.com", role=UserRole.ADMIN, organization=org_b
    )
    manager_b = await _seed_user(
        db_session, email=f"ger-b-{suffix}@b.com", role=UserRole.MANAGER, organization=org_b
    )
    client_a = await _seed_client(db_session, creator=admin_a, organization=org_a)
    client_a2 = await _seed_client(db_session, creator=admin_a, organization=org_a)
    client_a_closed = await _seed_client(
        db_session, creator=admin_a, organization=org_a, closed=True
    )
    # Só `manager_a` tem `client_a` na carteira (§4.13); `manager_a_fora` não.
    db_session.add(
        ClientAssignment(
            client_id=client_a.id, user_id=manager_a.id, assigned_by=admin_a.id, is_primary=True
        )
    )
    cm_a = await _seed_user(
        db_session,
        email=f"cm-{suffix}@cliente-a.com",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        organization=org_a,
        client_id=client_a.id,
    )
    op_a = await _seed_user(
        db_session,
        email=f"op-{suffix}@cliente-a.com",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        organization=org_a,
        client_id=client_a.id,
    )
    op_a2 = await _seed_user(
        db_session,
        email=f"op2-{suffix}@cliente-a2.com",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        organization=org_a,
        client_id=client_a2.id,
    )
    op_closed = await _seed_user(
        db_session,
        email=f"op-closed-{suffix}@cliente-a.com",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        organization=org_a,
        client_id=client_a_closed.id,
    )
    await db_session.flush()
    return {
        "platform": platform,
        "platform_2": platform_2,
        "admin_a": admin_a,
        "manager_a": manager_a,
        "manager_a_fora": manager_a_fora,
        "admin_b": admin_b,
        "manager_b": manager_b,
        "client_a": client_a,
        "client_a2": client_a2,
        "client_a_closed": client_a_closed,
        "cm_a": cm_a,
        "op_a": op_a,
        "op_a2": op_a2,
        "op_closed": op_closed,
    }


# ----------------------------------------------------------------------
# Rota de STAFF
# ----------------------------------------------------------------------


class TestRotaDeStaff:
    async def test_anonimo_e_401(self, client_with_db: AsyncClient, scene: dict[str, Any]) -> None:
        resp = await client_with_db.post(_staff_url(scene["manager_a"].id))
        assert resp.status_code == 401

    @pytest.mark.parametrize("target", ["manager_a", "admin_b", "platform_2"])
    async def test_plataforma_encerra_staff_de_qualquer_organizacao(
        self, client_with_db: AsyncClient, scene: dict[str, Any], target: str
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_staff_url(scene[target].id))
        assert resp.status_code == 204, resp.text
        assert resp.content == b""

    async def test_admin_encerra_staff_da_propria_organizacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        alvo: User = scene["manager_a"]
        hash_before = alvo.password_hash
        await _login(client_with_db, scene["admin_a"].email)
        resp = await client_with_db.post(_staff_url(alvo.id))
        assert resp.status_code == 204, resp.text

        await db_session.refresh(alvo)
        assert alvo.password_changed_at is not None
        assert alvo.password_hash == hash_before, "o hash NÃO muda"
        assert alvo.active is True, "a conta continua ativa"

    @pytest.mark.parametrize("target", ["admin_b", "platform", "cm_a"])
    async def test_admin_recebe_404_sem_nome_fora_do_alcance(
        self, client_with_db: AsyncClient, scene: dict[str, Any], target: str
    ) -> None:
        """Staff de outra org, administrador da plataforma e usuário de cliente: o
        SELECT escopado não carrega a linha (anti-IDOR), como no `PATCH /users/{id}`."""
        alvo: User = scene[target]
        await _login(client_with_db, scene["admin_a"].email)
        resp = await client_with_db.post(_staff_url(alvo.id))
        assert resp.status_code == 404, resp.text
        _assert_sem_nome(resp, alvo)

    @pytest.mark.parametrize("actor", ["manager_a", "manager_b"])
    async def test_gerente_e_403(
        self, client_with_db: AsyncClient, scene: dict[str, Any], actor: str
    ) -> None:
        alvo: User = scene["admin_a"]
        await _login(client_with_db, scene[actor].email)
        resp = await client_with_db.post(_staff_url(alvo.id))
        assert resp.status_code == 403, resp.text
        _assert_sem_nome(resp, alvo)

    async def test_a_propria_sessao_e_409_tipado(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        admin: User = scene["admin_a"]
        await _login(client_with_db, admin.email)
        resp = await client_with_db.post(_staff_url(admin.id))
        assert resp.status_code == 409, resp.text
        assert "OUTRA pessoa" in resp.json()["error"]["userMessage"]
        # E a sessão de quem pediu continua viva.
        assert (await client_with_db.get(PROBE)).status_code == 200

    async def test_inexistente_e_404(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_staff_url(uuid4()))
        assert resp.status_code == 404, resp.text


# ----------------------------------------------------------------------
# Rota do CLIENTE
# ----------------------------------------------------------------------


class TestRotaDoCliente:
    @pytest.mark.parametrize("actor", ["cm_a", "manager_a", "admin_a", "platform"])
    async def test_quem_gere_os_usuarios_do_cliente_encerra(
        self, client_with_db: AsyncClient, scene: dict[str, Any], actor: str
    ) -> None:
        """Gerente do cliente, gerente da org COM o cliente na carteira, admin da org
        e plataforma: o alcance de `resolve_client_access` + `manage_client_users`."""
        await _login(client_with_db, scene[actor].email)
        resp = await client_with_db.post(_client_url(scene["client_a"].id, scene["op_a"].id))
        assert resp.status_code == 204, resp.text
        assert resp.content == b""

    async def test_operador_e_403_com_uma_linha_denied(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        op: User = scene["op_a"]
        alvo: User = scene["cm_a"]
        await _login(client_with_db, op.email)
        resp = await client_with_db.post(_client_url(scene["client_a"].id, alvo.id))
        assert resp.status_code == 403, resp.text
        _assert_sem_nome(resp, alvo)

        rows = (
            (
                await db_session.execute(
                    select(AccessAudit).where(
                        AccessAudit.user_id == op.id,
                        AccessAudit.client_id == scene["client_a"].id,
                        AccessAudit.action == "denied",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].user_scope == "client"
        assert rows[0].actor_client_id == scene["client_a"].id

    @pytest.mark.parametrize("actor", ["manager_a_fora", "admin_b", "manager_b"])
    async def test_fora_do_alcance_do_tenant_e_negado_sem_nome(
        self, client_with_db: AsyncClient, scene: dict[str, Any], actor: str
    ) -> None:
        """Gerente sem o cliente na carteira e staff de OUTRA organização: a negação
        é a do guard de tenant (`AccessibleClientDep`), decidida ANTES da permissão —
        o mesmo 403 sem nome de toda escrita da família `/clients/{id}/users`."""
        alvo: User = scene["op_a"]
        await _login(client_with_db, scene[actor].email)
        resp = await client_with_db.post(_client_url(scene["client_a"].id, alvo.id))
        assert resp.status_code in (403, 404), resp.text
        _assert_sem_nome(resp, alvo)
        assert scene["client_a"].name not in resp.text

    async def test_usuario_de_outro_cliente_na_url_e_404(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        """`AND client_id = <tenant da rota>` no SELECT: o operador do cliente A2 não
        é alcançado pela URL do cliente A, nem pela plataforma."""
        alvo: User = scene["op_a2"]
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_client_url(scene["client_a"].id, alvo.id))
        assert resp.status_code == 404, resp.text
        _assert_sem_nome(resp, alvo)

    async def test_cliente_encerrado_e_409(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _client_url(scene["client_a_closed"].id, scene["op_closed"].id)
        )
        assert resp.status_code == 409, resp.text
        assert "encerrado" in resp.json()["error"]["userMessage"]

    async def test_a_propria_sessao_e_409_tipado(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        cm: User = scene["cm_a"]
        await _login(client_with_db, cm.email)
        resp = await client_with_db.post(_client_url(scene["client_a"].id, cm.id))
        assert resp.status_code == 409, resp.text
        assert "OUTRA pessoa" in resp.json()["error"]["userMessage"]


# ----------------------------------------------------------------------
# O critério de aceite da task
# ----------------------------------------------------------------------


class TestEfeito:
    @pytest.mark.parametrize(
        ("actor", "target", "url_of"),
        [
            ("admin_a", "manager_a", "staff"),
            ("cm_a", "op_a", "client"),
        ],
        ids=["staff", "usuario-do-cliente"],
    )
    async def test_token_antigo_morre_a_conta_segue_ativa_e_a_mesma_senha_entra(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        scene: dict[str, Any],
        actor: str,
        target: str,
        url_of: str,
    ) -> None:
        alvo: User = scene[target]
        hash_before = alvo.password_hash

        # A sessão do ALVO, antes: access e refresh válidos.
        await _login(client_with_db, alvo.email)
        old_cookies = dict(client_with_db.cookies)
        assert (await client_with_db.get(PROBE)).status_code == 200
        await _logout(client_with_db)

        # `iat` é em segundos inteiros: o encerramento tem de cair num segundo
        # POSTERIOR ao da emissão (janela documentada em
        # `token_predates_password_change`).
        await asyncio.sleep(1.05)

        await _login(client_with_db, scene[actor].email)
        url = (
            _staff_url(alvo.id) if url_of == "staff" else _client_url(scene["client_a"].id, alvo.id)
        )
        resp = await client_with_db.post(url)
        assert resp.status_code == 204, resp.text
        await _logout(client_with_db)

        # Volta com os cookies ANTIGOS do alvo: access E refresh morreram.
        for name, value in old_cookies.items():
            client_with_db.cookies.set(name, value)
        assert (await client_with_db.get(PROBE)).status_code == 401
        assert (await client_with_db.post("/api/v1/auth/refresh")).status_code == 401
        client_with_db.cookies.clear()

        # A conta continua ATIVA e a senha é a MESMA (releitura da linha).
        await db_session.refresh(alvo)
        assert alvo.active is True
        assert alvo.password_hash == hash_before
        assert alvo.password_changed_at is not None

        # E a mesma senha abre uma sessão nova, que vale.
        await _login(client_with_db, alvo.email, PASSWORD)
        assert (await client_with_db.get(PROBE)).status_code == 200

    async def test_evento_sai_so_com_ids(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        alvo: User = scene["op_a"]
        actor: User = scene["cm_a"]
        await _login(client_with_db, actor.email)
        resp = await client_with_db.post(_client_url(scene["client_a"].id, alvo.id))
        assert resp.status_code == 204, resp.text

        rows = (
            await db_session.execute(
                select(UsageEvent).where(UsageEvent.event == "sessoes_encerradas")
            )
        ).scalars()
        eventos = [e for e in rows if e.props.get("target_user_id") == str(alvo.id)]
        assert len(eventos) == 1
        assert eventos[0].props == {
            "actor_user_id": str(actor.id),
            "target_user_id": str(alvo.id),
            "target_scope": "client",
        }
        assert alvo.email not in str(eventos[0].props)
        assert alvo.name not in str(eventos[0].props)
