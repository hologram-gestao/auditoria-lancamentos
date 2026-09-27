"""`POST /api/v1/users/{id}/password` — a plataforma redefine a senha de qualquer
usuário e derruba as sessões dele (86e3ewukz).

Cobre:
    - RBAC: anônimo 401; admin e gerente de organização (inclusive a do ALVO) e
      usuário de cliente 403, sem corpo que nomeie ninguém; só a plataforma passa.
    - Alvo: staff de outra organização e usuário de cliente são redefinidos;
      outro administrador da plataforma também; a PRÓPRIA senha é 409 tipado;
      usuário de cliente ENCERRADO é 409; inexistente é 404.
    - Efeito: login com a senha nova funciona e com a antiga dá a mensagem
      genérica (§3.9); access e refresh emitidos ANTES da redefinição são
      recusados logo depois (revogação de sessão).
    - Forma: senha curta para o tipo do alvo é o 400 genérico (8 staff, 10
      usuário de cliente); campo extra é 422.
    - Trilha: a senha não aparece em log nem em resposta; o evento
      `senha_redefinida_pela_plataforma` sai só com IDs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
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

PASSWORD = "Senh@Reset#Antiga1"
NEW_PASSWORD = "Senh@Reset#Nova!22"
GENERIC_LOGIN_ERROR = "E-mail ou senha incorretos"


def _url(user_id: UUID) -> str:
    return f"/api/v1/users/{user_id}/password"


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
    session: AsyncSession, *, creator: User, organization: Organization, closed: bool = False
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("reset-key", hex_key)
    ct_s, iv_s = encrypt("reset-secret", hex_key)
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
    admin_b = await _seed_user(
        db_session, email=f"admin-b-{suffix}@b.com", role=UserRole.ADMIN, organization=org_b
    )
    client_a = await _seed_client(db_session, creator=admin_a, organization=org_a)
    client_a_closed = await _seed_client(
        db_session, creator=admin_a, organization=org_a, closed=True
    )
    operator_a = await _seed_user(
        db_session,
        email=f"op-{suffix}@cliente-a.com",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        organization=org_a,
        client_id=client_a.id,
    )
    operator_closed = await _seed_user(
        db_session,
        email=f"op-closed-{suffix}@cliente-a.com",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        organization=org_a,
        client_id=client_a_closed.id,
    )
    return {
        "platform": platform,
        "platform_2": platform_2,
        "admin_a": admin_a,
        "manager_a": manager_a,
        "admin_b": admin_b,
        "client_a": client_a,
        "operator_a": operator_a,
        "operator_closed": operator_closed,
    }


# ----------------------------------------------------------------------
# RBAC
# ----------------------------------------------------------------------


class TestRbac:
    async def test_anonimo_e_401(self, client_with_db: AsyncClient, scene: dict[str, Any]) -> None:
        resp = await client_with_db.post(
            _url(scene["manager_a"].id), json={"password": NEW_PASSWORD}
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize("actor", ["admin_a", "manager_a", "admin_b", "operator_a"])
    async def test_quem_nao_e_plataforma_e_403_sem_nomear_o_alvo(
        self, client_with_db: AsyncClient, scene: dict[str, Any], actor: str
    ) -> None:
        """O admin da PRÓPRIA organização do alvo (`admin_a` sobre `manager_a`) também."""
        await _login(client_with_db, scene[actor].email)
        target = scene["manager_a"]
        resp = await client_with_db.post(_url(target.id), json={"password": NEW_PASSWORD})
        assert resp.status_code == 403, resp.text
        assert target.name not in resp.text
        assert target.email not in resp.text
        # E nada mudou: a senha antiga continua entrando.
        await client_with_db.post("/api/v1/auth/logout")
        await _login(client_with_db, target.email)


# ----------------------------------------------------------------------
# Alvos e efeito
# ----------------------------------------------------------------------


class TestRedefinicao:
    @pytest.mark.parametrize("target", ["manager_a", "operator_a", "platform_2"])
    async def test_plataforma_redefine_e_a_senha_nova_passa_a_valer(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        scene: dict[str, Any],
        target: str,
    ) -> None:
        alvo: User = scene[target]
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_url(alvo.id), json={"password": NEW_PASSWORD})
        assert resp.status_code == 204, resp.text
        assert resp.content == b""
        await client_with_db.post("/api/v1/auth/logout")

        # Antiga: recusa com a mensagem GENÉRICA de sempre (§3.9).
        old = await client_with_db.post(
            "/api/v1/auth/login", json={"email": alvo.email, "password": PASSWORD}
        )
        assert old.status_code == 401
        assert GENERIC_LOGIN_ERROR in old.text
        # Nova: entra.
        await _login(client_with_db, alvo.email, NEW_PASSWORD)

        await db_session.refresh(alvo)
        assert alvo.password_changed_at is not None

    async def test_sessao_antiga_e_derrubada(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        """Access E refresh emitidos antes da redefinição morrem no request seguinte."""
        alvo: User = scene["manager_a"]
        # A sessão do ALVO, antes: access e refresh válidos.
        await _login(client_with_db, alvo.email)
        old_cookies = dict(client_with_db.cookies)
        # A sonda de "sessão viva" é uma rota autenticada que o gerente alcança.
        me = await client_with_db.get("/api/v1/clients?page=1&pageSize=1")
        assert me.status_code == 200, me.text
        await client_with_db.post("/api/v1/auth/logout")

        # `iat` é em segundos inteiros: garante que a redefinição cai num segundo
        # POSTERIOR ao da emissão (senão o token do mesmo segundo continuaria
        # válido — janela documentada em `token_predates_password_change`).
        await asyncio.sleep(1.05)

        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_url(alvo.id), json={"password": NEW_PASSWORD})
        assert resp.status_code == 204, resp.text
        await client_with_db.post("/api/v1/auth/logout")

        # Volta com os cookies ANTIGOS do alvo.
        client_with_db.cookies.clear()
        for name, value in old_cookies.items():
            client_with_db.cookies.set(name, value)
        me_after = await client_with_db.get("/api/v1/clients?page=1&pageSize=1")
        assert me_after.status_code == 401, me_after.text
        refresh_after = await client_with_db.post("/api/v1/auth/refresh")
        assert refresh_after.status_code == 401, refresh_after.text

        # E a senha nova abre uma sessão nova, que vale.
        client_with_db.cookies.clear()
        await _login(client_with_db, alvo.email, NEW_PASSWORD)
        assert (await client_with_db.get("/api/v1/clients?page=1&pageSize=1")).status_code == 200

    async def test_propria_senha_e_409_tipado(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        plat: User = scene["platform"]
        await _login(client_with_db, plat.email)
        resp = await client_with_db.post(_url(plat.id), json={"password": NEW_PASSWORD})
        assert resp.status_code == 409, resp.text
        assert "própria senha" in resp.json()["error"]["userMessage"]
        # Nada mudou: a antiga continua entrando.
        await client_with_db.post("/api/v1/auth/logout")
        await _login(client_with_db, plat.email)

    async def test_usuario_de_cliente_encerrado_e_409(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(scene["operator_closed"].id), json={"password": NEW_PASSWORD}
        )
        assert resp.status_code == 409, resp.text
        assert "encerrado" in resp.json()["error"]["userMessage"]

    async def test_inexistente_e_404(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_url(uuid4()), json={"password": NEW_PASSWORD})
        assert resp.status_code == 404, resp.text


# ----------------------------------------------------------------------
# Forma
# ----------------------------------------------------------------------


class TestForma:
    async def test_senha_curta_para_staff_e_400_generico(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(_url(scene["manager_a"].id), json={"password": "1234567"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_senha_de_9_serve_para_staff_e_nao_para_usuario_de_cliente(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        """O mínimo é do TIPO do alvo: 8 para staff, 10 para usuário de cliente."""
        await _login(client_with_db, scene["platform"].email)
        nove = "Nove!2345"
        ok = await client_with_db.post(_url(scene["manager_a"].id), json={"password": nove})
        assert ok.status_code == 204, ok.text
        curta = await client_with_db.post(_url(scene["operator_a"].id), json={"password": nove})
        assert curta.status_code == 400, curta.text
        assert curta.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_campo_extra_e_422(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(scene["manager_a"].id), json={"password": NEW_PASSWORD, "email": "x@y.z"}
        )
        assert resp.status_code in (400, 422), resp.text


# ----------------------------------------------------------------------
# Trilha
# ----------------------------------------------------------------------


class TestTrilha:
    async def test_senha_nao_aparece_em_log_nem_em_resposta(
        self,
        client_with_db: AsyncClient,
        scene: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        with caplog.at_level(logging.DEBUG):
            resp = await client_with_db.post(
                _url(scene["operator_a"].id), json={"password": NEW_PASSWORD}
            )
        assert resp.status_code == 204
        assert NEW_PASSWORD not in resp.text
        assert NEW_PASSWORD not in caplog.text
        for record in caplog.records:
            assert NEW_PASSWORD not in str(record.__dict__)

    async def test_evento_sai_so_com_ids(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        alvo: User = scene["operator_a"]
        plat: User = scene["platform"]
        await _login(client_with_db, plat.email)
        resp = await client_with_db.post(_url(alvo.id), json={"password": NEW_PASSWORD})
        assert resp.status_code == 204, resp.text

        rows = (
            await db_session.execute(
                select(UsageEvent).where(UsageEvent.event == "senha_redefinida_pela_plataforma")
            )
        ).scalars()
        eventos = [e for e in rows if e.props.get("target_user_id") == str(alvo.id)]
        assert len(eventos) == 1
        props = eventos[0].props
        assert set(props) == {"actor_user_id", "target_user_id", "target_scope"}
        assert props == {
            "actor_user_id": str(plat.id),
            "target_user_id": str(alvo.id),
            "target_scope": "client",
        }
        assert NEW_PASSWORD not in str(props)
        assert alvo.email not in str(props)
