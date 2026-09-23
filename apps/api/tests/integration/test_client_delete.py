"""Testes de integração da exclusão DEFINITIVA de cliente — 86e34jd1d.

Cobre:
    - Admin exclui: cliente, conciliações (e o que pende delas), notificações,
      usuários DO tenant, favoritos e atribuição vão junto; `access_audit`
      FICA (só IDs, §4.7); evento `cliente_excluido` com as contagens; o
      tenant vizinho não é tocado; o usuário do tenant excluído deixa de logar.
    - 409 com conciliação EM PROCESSAMENTO (o job roda fora do request).
    - Manager da carteira → 403 (matriz: excluir é a célula de editar, só admin);
      sem login → 401; inexistente → 404.

O cross-tenant (operador do tenant A contra cliente B) está no parametrizado de
`test_sensitive_endpoints.py`, que lê a rota da lista canônica.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    ClientChartOfAccount,
    ClientConnection,
    Notification,
    NotificationType,
    ProviderType,
    ReconciliationFile,
    ReconciliationFileStatus,
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

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
ADMIN_EMAIL = "admin-del@hologram.com.br"
MANAGER_EMAIL = "manager-del@hologram.com.br"
TENANT_MANAGER_EMAIL = "gerente@cliente-a.com.br"
TENANT_OPERATOR_EMAIL = "operador@cliente-a.com.br"
OTHER_TENANT_USER_EMAIL = "operador@cliente-b.com.br"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    user = User(
        name="Test User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, name: str, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("del-app-key", hex_key)
    ct_s, iv_s = encrypt("del-app-secret", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession, *, client: Client, created_by: User, status: str = "reviewing"
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=created_by.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=_hex64(f"del-{uuid4().hex}"),
        status=status,
        balance_start=Decimal("0.00"),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    session.add(
        ReconciliationFile(
            session_id=sess.id,
            file_hash=_hex64(f"file-{uuid4().hex}"),
            status=ReconciliationFileStatus.PARSED.value,
        )
    )
    await session.flush()
    return sess


async def _login_as(client: AsyncClient, email: str) -> int:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    return resp.status_code


async def _count(session: AsyncSession, stmt: object) -> int:
    return int((await session.execute(stmt)).scalar_one())  # type: ignore[arg-type]


class _World:
    """Tenant A (alvo, com tudo pendurado) e tenant B (vizinho, intocado)."""

    def __init__(self) -> None:
        self.admin: User
        self.manager: User
        self.cli_a: Client
        self.cli_b: Client
        self.sess_a: ReconciliationSession


async def _seed_world(db: AsyncSession, *, processing: bool = False) -> _World:
    w = _World()
    w.admin = await _seed_user(db, email=ADMIN_EMAIL, role=UserRole.ADMIN)
    w.manager = await _seed_user(db, email=MANAGER_EMAIL, role=UserRole.MANAGER)
    w.cli_a = await _seed_client(db, name="Cliente A", creator=w.admin)
    w.cli_b = await _seed_client(db, name="Cliente B", creator=w.admin)
    db.add(
        ClientAssignment(
            client_id=w.cli_a.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    tenant_manager = await _seed_user(
        db,
        email=TENANT_MANAGER_EMAIL,
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=w.cli_a.id,
    )
    await _seed_user(
        db,
        email=TENANT_OPERATOR_EMAIL,
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.cli_a.id,
    )
    await _seed_user(
        db,
        email=OTHER_TENANT_USER_EMAIL,
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.cli_b.id,
    )
    # A conciliação foi criada por um usuário DO tenant: é o `created_by`
    # RESTRICT que trava a ordem "usuários primeiro".
    w.sess_a = await _seed_session(
        db,
        client=w.cli_a,
        created_by=tenant_manager,
        status="processing" if processing else "reviewing",
    )
    await _seed_session(db, client=w.cli_b, created_by=w.admin)
    db.add(
        Notification(
            user_id=tenant_manager.id,
            client_id=w.cli_a.id,
            session_id=w.sess_a.id,
            tipo=NotificationType.PROCESSADA.value,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
        )
    )
    db.add(UserClientFavorite(user_id=w.admin.id, client_id=w.cli_a.id))
    # S9 (BACK 09.1): a origem do cliente. Pendura uma FK a mais no grafo — e é
    # por isso que ela está no mundo padrão, não num teste à parte: a exclusão
    # inteira tem de continuar passando com ela lá.
    db.add(
        ClientConnection(
            client_id=w.cli_a.id,
            provider_type=ProviderType.OMIE.value,
            label="Omie",
        )
    )
    # S10 (BACK 10.1): o plano de contas do cliente. Mesma razão da conexão
    # acima — é mais uma FK no grafo, e a FK dela é `CASCADE`: a exclusão
    # definitiva tem de continuar passando sem `DELETE` explícito.
    db.add(ClientChartOfAccount(client_id=w.cli_a.id, category_code="1.01.01", dre_code="1.01"))
    db.add(
        AccessAudit(
            user_id=w.admin.id,
            client_id=w.cli_a.id,
            user_scope=UserScope.SYSTEM.value,
            action="view",
            rota=f"/api/v1/clients/{w.cli_a.id}",
        )
    )
    await db.flush()
    return w


class TestDeleteClient:
    async def test_admin_exclui_e_tudo_que_pende_vai_junto(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        # Antes: o usuário do tenant A loga normalmente.
        assert await _login_as(client_with_db, TENANT_OPERATOR_EMAIL) == 200

        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.delete(f"/api/v1/clients/{w.cli_a.id}")
        assert resp.status_code == 204, resp.text

        a = w.cli_a.id
        assert await _count(db_session, select(func.count(Client.id)).where(Client.id == a)) == 0
        assert (
            await _count(
                db_session,
                select(func.count(ReconciliationSession.id)).where(
                    ReconciliationSession.client_id == a
                ),
            )
            == 0
        )
        assert (
            await _count(
                db_session,
                select(func.count(ReconciliationFile.id)).where(
                    ReconciliationFile.session_id == w.sess_a.id
                ),
            )
            == 0
        )
        assert (
            await _count(
                db_session, select(func.count(Notification.id)).where(Notification.client_id == a)
            )
            == 0
        )
        assert await _count(db_session, select(func.count(User.id)).where(User.client_id == a)) == 0
        assert (
            await _count(
                db_session,
                select(func.count(UserClientFavorite.id)).where(UserClientFavorite.client_id == a),
            )
            == 0
        )
        assert (
            await _count(
                db_session,
                select(func.count(ClientAssignment.id)).where(ClientAssignment.client_id == a),
            )
            == 0
        )
        # A origem some junto — e não travou a exclusão (FK com ondelete declarado).
        assert (
            await _count(
                db_session,
                select(func.count(ClientConnection.id)).where(ClientConnection.client_id == a),
            )
            == 0
        )
        # O plano de contas também — pelo CASCADE da FK, sem `DELETE` explícito
        # na lista do repositório. É o teste que prova o `ondelete` declarado.
        assert (
            await _count(
                db_session,
                select(func.count(ClientChartOfAccount.id)).where(
                    ClientChartOfAccount.client_id == a
                ),
            )
            == 0
        )
        # Trilha LGPD sobrevive ao cliente (só IDs).
        assert (
            await _count(
                db_session, select(func.count(AccessAudit.id)).where(AccessAudit.client_id == a)
            )
            == 1
        )
        # O fato é medido, com contagens e sem nome.
        evento = (
            await db_session.execute(
                select(UsageEvent).where(UsageEvent.event == "cliente_excluido")
            )
        ).scalar_one()
        assert evento.props == {"client_id": str(a), "n_conciliacoes": 1, "n_usuarios": 2}
        assert "Cliente A" not in str(evento.props)

        # Vizinho intocado: cliente, conciliação e usuário de B continuam.
        b = w.cli_b.id
        assert await _count(db_session, select(func.count(Client.id)).where(Client.id == b)) == 1
        assert (
            await _count(
                db_session,
                select(func.count(ReconciliationSession.id)).where(
                    ReconciliationSession.client_id == b
                ),
            )
            == 1
        )
        assert await _count(db_session, select(func.count(User.id)).where(User.client_id == b)) == 1

        # Depois: 404 no detalhe e o usuário do tenant excluído não loga mais.
        assert (await client_with_db.get(f"/api/v1/clients/{a}")).status_code == 404
        assert await _login_as(client_with_db, TENANT_OPERATOR_EMAIL) == 401

    async def test_409_com_conciliacao_em_processamento(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session, processing=True)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.delete(f"/api/v1/clients/{w.cli_a.id}")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CONFLICT"
        assert "em processamento" in resp.json()["error"]["userMessage"]
        assert (
            await _count(db_session, select(func.count(Client.id)).where(Client.id == w.cli_a.id))
            == 1
        )

    async def test_manager_da_carteira_recebe_403_e_nada_muda(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, MANAGER_EMAIL) == 200
        resp = await client_with_db.delete(f"/api/v1/clients/{w.cli_a.id}")
        assert resp.status_code == 403, resp.text
        assert (
            await _count(db_session, select(func.count(Client.id)).where(Client.id == w.cli_a.id))
            == 1
        )
        assert (
            await _count(
                db_session, select(func.count(User.id)).where(User.client_id == w.cli_a.id)
            )
            == 2
        )

    async def test_sem_login_401_e_inexistente_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        assert (await client_with_db.delete(f"/api/v1/clients/{uuid4()}")).status_code == 401
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (await client_with_db.delete(f"/api/v1/clients/{uuid4()}")).status_code == 404
