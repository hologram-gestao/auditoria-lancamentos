"""`POST /api/v1/users/{id}/transfer` — staff muda de organização (86e3bvbfx).

Existe porque "apagar e recriar" não é alternativa: `users.email` é UNIQUE no
sistema inteiro e `created_by` de clientes e conciliações é `ondelete=RESTRICT`.
E NÃO é um PATCH de `organization_id`: `client_assignments` e
`user_client_favorites` são pares usuárioxcliente, e trocar só a organização
deixaria lixo cross-org (plano §8.8).

Cobre:
    - RBAC: sem login 401; admin e gerente (de qualquer organização) 403 sem
      corpo que nomeie ninguém; só a plataforma passa.
    - Alvo: usuário de cliente e plataforma são 404 (alvo por PK só alcança
      staff); mesma organização 409; destino inexistente 404; suspensa 409.
    - Responsável de cliente ABERTO → 409 e NADA muda (cliente nunca fica
      órfão); responsável só de cliente ENCERRADO passa.
    - Efeitos: colaborador em cliente aberto e favoritos fora da organização
      nova somem NA MESMA transação; linha em cliente encerrado fica; papel
      não muda; evento de uso com só IDs e contagens.
    - Efeito imediato: com a sessão ANTIGA (JWT emitido antes), o usuário já
      é da organização nova no request seguinte.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import null, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    Notification,
    NotificationType,
    Organization,
    ReconciliationSession,
    UsageEvent,
    User,
    UserClientFavorite,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PASSWORD = "Senh@Transfer#1"


def _url(user_id: UUID) -> str:
    return f"/api/v1/users/{user_id}/transfer"


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
    session: AsyncSession,
    *,
    creator: User,
    organization: Organization,
    closed: bool = False,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("transfer-key", hex_key)
    ct_s, iv_s = encrypt("transfer-secret", hex_key)
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


async def _assign(
    session: AsyncSession, *, client: Client, user: User, primary: bool
) -> ClientAssignment:
    row = ClientAssignment(
        client_id=client.id, user_id=user.id, assigned_by=user.id, is_primary=primary
    )
    session.add(row)
    await session.flush()
    return row


async def _notify(session: AsyncSession, *, client: Client, user: User) -> Notification:
    """Uma notificação do usuário sobre o cliente (exige sessão: FK NOT NULL)."""
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=user.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=uuid4().hex * 2,
        status="reviewing",
        balance_start=Decimal("0.00"),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    notif = Notification(
        user_id=user.id,
        session_id=sess.id,
        client_id=client.id,
        tipo=NotificationType.PROCESSADA.value,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
    )
    session.add(notif)
    await session.flush()
    return notif


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    suffix = uuid4().hex[:6]
    org_a = Organization(name=f"Org A {suffix}")
    org_b = Organization(name=f"Org B {suffix}")
    org_off = Organization(name=f"Org Suspensa {suffix}", active=False)
    db_session.add_all([org_a, org_b, org_off])
    await db_session.flush()

    platform = await _seed_user(
        db_session,
        email=f"plat-{suffix}@hologram.com.br",
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
    return {
        "suffix": suffix,
        "org_a": org_a,
        "org_b": org_b,
        "org_off": org_off,
        "platform": platform,
        "admin_a": admin_a,
        "manager_a": manager_a,
        "admin_b": admin_b,
        "client_a": client_a,
        "client_a_closed": client_a_closed,
        "operator_a": operator_a,
    }


async def _reload_user(db: AsyncSession, user: User) -> User:
    """Relê a linha que a API alterou por outra sessão. `refresh` de UM objeto,
    e não `expire_all()`: expirar tudo faria o próximo `.name` da fixture
    disparar lazy-load fora do greenlet (`MissingGreenlet`)."""
    await db.refresh(user)
    return user


async def _assignments_of(db: AsyncSession, user_id: UUID) -> list[ClientAssignment]:
    rows = await db.execute(select(ClientAssignment).where(ClientAssignment.user_id == user_id))
    return list(rows.scalars())


async def _favorites_of(db: AsyncSession, user_id: UUID) -> list[UserClientFavorite]:
    rows = await db.execute(select(UserClientFavorite).where(UserClientFavorite.user_id == user_id))
    return list(rows.scalars())


class TestRBAC:
    async def test_sem_login_401(self, client_with_db: AsyncClient, scene: dict[str, Any]) -> None:
        resp = await client_with_db.post(
            _url(scene["manager_a"].id), json={"organization_id": str(scene["org_b"].id)}
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize("who", ["admin_a", "admin_b", "manager_a", "operator_a"])
    async def test_quem_nao_e_plataforma_recebe_403_sem_nomear_ninguem(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any], who: str
    ) -> None:
        """Admin da PRÓPRIA organização também é 403: mover gente é escrita
        cross-org por definição, e é a plataforma quem enxerga as duas pontas."""
        await _login(client_with_db, scene[who].email)
        target = scene["manager_a"]
        resp = await client_with_db.post(
            _url(target.id), json={"organization_id": str(scene["org_b"].id)}
        )
        assert resp.status_code == 403, resp.text
        assert target.name not in resp.text
        assert scene["org_b"].name not in resp.text
        row = await _reload_user(db_session, target)
        assert row.organization_id == scene["org_a"].id


class TestAlvoEDestino:
    async def test_usuario_de_cliente_e_plataforma_sao_404(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        """Alvo por PK só alcança staff (`get_staff_by_id`): quem não é staff
        não existe para esta rota — nem para dizer que não pode."""
        await _login(client_with_db, scene["platform"].email)
        body = {"organization_id": str(scene["org_b"].id)}
        assert (
            await client_with_db.post(_url(scene["operator_a"].id), json=body)
        ).status_code == 404
        assert (await client_with_db.post(_url(scene["platform"].id), json=body)).status_code == 404

    async def test_mesma_organizacao_409(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(scene["manager_a"].id), json={"organization_id": str(scene["org_a"].id)}
        )
        assert resp.status_code == 409, resp.text
        assert "já pertence" in resp.json()["error"]["userMessage"]

    async def test_destino_inexistente_404_e_suspensa_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        await _login(client_with_db, scene["platform"].email)
        target = scene["manager_a"]
        assert (
            await client_with_db.post(_url(target.id), json={"organization_id": str(uuid4())})
        ).status_code == 404
        resp = await client_with_db.post(
            _url(target.id), json={"organization_id": str(scene["org_off"].id)}
        )
        assert resp.status_code == 409, resp.text
        assert "suspensa" in resp.json()["error"]["userMessage"]
        row = await _reload_user(db_session, target)
        assert row.organization_id == scene["org_a"].id


class TestCarteira:
    async def test_responsavel_de_cliente_aberto_e_409_e_nada_muda(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        manager = scene["manager_a"]
        await _assign(db_session, client=scene["client_a"], user=manager, primary=True)
        db_session.add(UserClientFavorite(user_id=manager.id, client_id=scene["client_a"].id))
        await db_session.commit()

        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(manager.id), json={"organization_id": str(scene["org_b"].id)}
        )
        assert resp.status_code == 409, resp.text
        msg = resp.json()["error"]["userMessage"]
        assert "responsável" in msg
        assert "1 cliente aberto" in msg
        # Nome do cliente NUNCA sai numa negação (§3.15).
        assert scene["client_a"].name not in resp.text

        row = await _reload_user(db_session, manager)
        assert row.organization_id == scene["org_a"].id
        assert len(await _assignments_of(db_session, manager.id)) == 1
        assert len(await _favorites_of(db_session, manager.id)) == 1

    async def test_responsavel_so_de_cliente_encerrado_passa_e_a_linha_fica(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Cliente encerrado retém a carteira (§4.12) e não aceita escrita:
        não é obstáculo nem é apagado — é histórico de quem respondia."""
        manager = scene["manager_a"]
        await _assign(db_session, client=scene["client_a_closed"], user=manager, primary=True)
        await db_session.commit()

        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(manager.id), json={"organization_id": str(scene["org_b"].id)}
        )
        assert resp.status_code == 200, resp.text
        left = await _assignments_of(db_session, manager.id)
        assert [a.client_id for a in left] == [scene["client_a_closed"].id]


class TestEfeitos:
    async def test_transfere_gerente_limpa_colaboracao_e_favoritos_e_mantem_o_papel(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        manager = scene["manager_a"]
        # Colaborador (não responsável) num cliente aberto de A + favorito nele.
        await _assign(db_session, client=scene["client_a"], user=manager, primary=False)
        db_session.add(UserClientFavorite(user_id=manager.id, client_id=scene["client_a"].id))
        await _notify(db_session, client=scene["client_a"], user=manager)
        await db_session.commit()

        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(manager.id), json={"organization_id": str(scene["org_b"].id)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["organization_id"] == str(scene["org_b"].id)
        assert body["organization_name"] == scene["org_b"].name
        assert body["role"] == "manager"  # o papel não muda
        assert body["scope"] == "system"

        row = await _reload_user(db_session, manager)
        assert row.organization_id == scene["org_b"].id
        assert row.role == UserRole.MANAGER.value
        assert await _assignments_of(db_session, manager.id) == []
        assert await _favorites_of(db_session, manager.id) == []
        # Notificação sobre cliente da organização antiga também sai (mesmo
        # tratamento do encerramento de cliente): sem isto viraria dado morto.
        left_notifs = (
            (
                await db_session.execute(
                    select(Notification).where(Notification.user_id == manager.id)
                )
            )
            .scalars()
            .all()
        )
        assert left_notifs == []

        # Evento de uso: só IDs e contagens, nunca nome.
        events = list(
            (
                await db_session.execute(
                    select(UsageEvent).where(
                        UsageEvent.event == "usuario_transferido_de_organizacao"
                    )
                )
            ).scalars()
        )
        mine = [e for e in events if e.props.get("user_id") == str(manager.id)]
        assert len(mine) == 1
        props = mine[0].props
        assert props["from_organization_id"] == str(scene["org_a"].id)
        assert props["to_organization_id"] == str(scene["org_b"].id)
        assert props["n_carteira_removida"] == 1
        assert props["n_favoritos_removidos"] == 1
        assert props["n_notificacoes_removidas"] == 1
        assert manager.name not in str(props)

    async def test_admin_transferido_continua_admin(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        admin = scene["admin_a"]
        await _login(client_with_db, scene["platform"].email)
        resp = await client_with_db.post(
            _url(admin.id), json={"organization_id": str(scene["org_b"].id)}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["role"] == "admin"
        row = await _reload_user(db_session, admin)
        assert row.organization_id == scene["org_b"].id

    async def test_efeito_e_imediato_com_a_sessao_antiga(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """A autoridade é a LINHA lida a cada request (§3.15): o JWT antigo do
        gerente já responde pela organização nova, sem relogar."""
        manager = scene["manager_a"]
        # 1) o gerente loga ANTES (cookie no jar deste client)
        await _login(client_with_db, manager.email)
        # Como staff de A, filtrar por A é permitido (no-op) e por B é 403.
        ok = await client_with_db.get(
            "/api/v1/clients", params={"organizationId": str(scene["org_a"].id)}
        )
        assert ok.status_code == 200, ok.text
        assert (
            await client_with_db.get(
                "/api/v1/clients", params={"organizationId": str(scene["org_b"].id)}
            )
        ).status_code == 403

        # 2) a plataforma transfere por FORA da sessão do gerente (escrita direta,
        #    o que interessa aqui é o request seguinte do gerente)
        row = await _reload_user(db_session, manager)
        row.organization_id = scene["org_b"].id
        await db_session.commit()

        # 3) mesma sessão, mesmo JWT: agora A é 403 e B é permitido.
        assert (
            await client_with_db.get(
                "/api/v1/clients", params={"organizationId": str(scene["org_a"].id)}
            )
        ).status_code == 403
        assert (
            await client_with_db.get(
                "/api/v1/clients", params={"organizationId": str(scene["org_b"].id)}
            )
        ).status_code == 200
