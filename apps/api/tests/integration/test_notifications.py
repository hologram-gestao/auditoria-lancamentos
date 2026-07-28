"""Notificações in-app de fim de conciliação (Sprint 4 / BACK 04.4).

Cobre os critérios de aceite:
    - ao entrar em `reviewing` cria 1 notificação para o AUTOR, apontando à
      sessão; ao entrar em `error`, o aviso leva o CÓDIGO genérico (nunca
      "limite de token" nem qualquer linguagem interna);
    - `unread-count` devolve só a contagem do usuário autenticado; RBAC impede
      ver notificação de cliente de outro manager;
    - a lista pagina; `POST /{id}/read` é idempotente, some do contador e não
      reaparece;
    - nenhuma PII do conteúdo do arquivo no payload (só conta/mês/status/código);
    - endpoints negam sem auth.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import ErrorCode
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    FileEntrySituation,
    Notification,
    NotificationType,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)
from app.modules.reconciliations.processing.job import run_reconciliation_processing

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "notif-admin@hologram.com.br"
MANAGER_A_EMAIL = "notif-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "notif-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "notif-app-key"
FAKE_APP_SECRET = "notif-app-secret"

OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"

SECRET_CLIENT_NAME = "Cliente Secretissimo LTDA"
SECRET_DESCRIPTION = "Pagamento fornecedor Fulano de Tal CNPJ 12.345.678/0001-99"


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Notif User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    manager: User | None = None,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    if manager is not None:
        session.add(
            ClientAssignment(client_id=client.id, user_id=manager.id, assigned_by=creator.id)
        )
        await session.flush()
    return client


async def _seed_notification(
    session: AsyncSession,
    *,
    user: User,
    client: Client,
    tipo: NotificationType = NotificationType.PROCESSADA,
    error_code: str | None = None,
) -> Notification:
    notification = Notification(
        user_id=user.id,
        session_id=uuid4(),
        client_id=client.id,
        tipo=tipo.value,
        omie_conta_id=42,
        reference_month=date(2026, 6, 1),
        error_code=error_code,
    )
    session.add(notification)
    await session.flush()
    return notification


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _unread(client: AsyncClient) -> int:
    resp = await client.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["unread"])


# ----------------------------------------------------------------------
# Criação na transição de status (via job real)
# ----------------------------------------------------------------------


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_job_fixtures(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
) -> tuple[UUID, UUID, UUID]:
    """Admin + cliente + sessão `processing` COMMITADOS (o job usa DB real)."""
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    async with factory() as s, s.begin():
        user = User(
            name="Autor",
            email=email.lower(),
            password_hash=hash_password(PLAIN_PASSWORD),
            role=UserRole.ADMIN.value,
            active=True,
        )
        s.add(user)
        await s.flush()
        cli = Client(
            name=SECRET_CLIENT_NAME,
            omie_app_key_encrypted=ct_key,
            omie_app_key_iv=iv_key,
            omie_app_secret_encrypted=ct_secret,
            omie_app_secret_iv=iv_secret,
            active=True,
            created_by=user.id,
        )
        s.add(cli)
        await s.flush()
        sess = ReconciliationSession(
            client_id=cli.id,
            created_by=user.id,
            omie_conta_id=77,
            reference_month=date(2026, 4, 1),
            date_tolerance_days=0,
            file_hash=None,
            status=ReconciliationStatus.PROCESSING.value,
        )
        s.add(sess)
        await s.flush()
        ct, iv = encrypt(SECRET_DESCRIPTION, hex_key)
        s.add(
            ReconciliationFileEntry(
                session_id=sess.id,
                transaction_date=date(2026, 4, 2),
                description_encrypted=ct,
                description_iv=iv,
                amount=Decimal("-500.00"),
                situation=FileEntrySituation.SEM_OMIE.value,
            )
        )
        return sess.id, cli.id, user.id


class TestNotificationOnSettle:
    @respx.mock
    async def test_erro_gera_notificacao_com_codigo_generico(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """O aviso de falha leva CÓDIGO — nunca 'limite de token' nem PII."""
        session_id, client_id, user_id = await _seed_job_fixtures(
            factory, email="notif-job-err@hologram.com.br"
        )
        # Como o Omie REALMENTE sinaliza credencial inválida: HTTP 200 + fault no
        # corpo (client.py `_raise_for_fault`; mesma forma de
        # test_reconciliation_job.py:876). Um 401 cai no ramo "status inesperado"
        # e vira `OMIE_FAULT` — que é outro erro, não o de autenticação.
        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "faultstring": "App Key inválida",
                    "faultcode": "SOAP-ENV:Client-101",
                },
            )
        )

        await run_reconciliation_processing(
            str(session_id), settings=get_settings(), session_factory=factory
        )

        async with factory() as s:
            rows = (
                await s.execute(select(Notification).where(Notification.session_id == session_id))
            ).scalars()
            notifications = list(rows)

        assert len(notifications) == 1
        notification = notifications[0]
        assert notification.tipo == NotificationType.ERRO.value
        assert notification.user_id == user_id  # o AUTOR
        assert notification.client_id == client_id
        assert notification.omie_conta_id == 77
        assert notification.reference_month == date(2026, 4, 1)
        assert notification.read_at is None
        # Código canônico presente e sem linguagem interna.
        assert notification.error_code == ErrorCode.OMIE_AUTH_ERROR.value
        assert "token" not in (notification.error_code or "").lower()

    @respx.mock
    async def test_notificacao_nao_carrega_pii(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Nem razão social, nem descrição de lançamento cabem no aviso."""
        session_id, _, _ = await _seed_job_fixtures(factory, email="notif-job-pii@hologram.com.br")
        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                401, json={"faultstring": "x", "faultcode": "SOAP-ENV:Client"}
            )
        )

        await run_reconciliation_processing(
            str(session_id), settings=get_settings(), session_factory=factory
        )

        async with factory() as s:
            notification = await s.scalar(
                select(Notification).where(Notification.session_id == session_id)
            )
        assert notification is not None
        blob = " ".join(
            str(v) for v in (notification.tipo, notification.error_code, notification.omie_conta_id)
        )
        assert SECRET_CLIENT_NAME not in blob
        assert "Fulano" not in blob
        assert "12.345.678" not in blob

    async def test_sessao_presa_em_processing_nao_notifica(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Sessão inexistente não conclui — e não vira aviso órfão."""
        ghost = uuid4()

        await run_reconciliation_processing(
            str(ghost), settings=get_settings(), session_factory=factory
        )

        async with factory() as s:
            rows = (
                await s.execute(select(Notification).where(Notification.session_id == ghost))
            ).scalars()
        assert list(rows) == []


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


class TestNotificationEndpoints:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/v1/notifications/unread-count"),
            ("GET", "/api/v1/notifications"),
            ("POST", f"/api/v1/notifications/{uuid4()}/read"),
        ],
    )
    async def test_sem_auth_retorna_401(
        self, client_with_db: AsyncClient, method: str, path: str
    ) -> None:
        resp = await client_with_db.request(method, path)
        assert resp.status_code == 401

    async def test_unread_count_conta_so_as_do_usuario(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        outro = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="Austral", creator=admin, manager=outro)
        await _seed_notification(db_session, user=admin, client=cliente)
        await _seed_notification(db_session, user=admin, client=cliente)
        # Do OUTRO usuário — não pode entrar na minha contagem.
        await _seed_notification(db_session, user=outro, client=cliente)
        await _login(client_with_db, ADMIN_EMAIL)

        assert await _unread(client_with_db) == 2

    async def test_manager_nao_ve_notificacao_de_cliente_de_outro_manager(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Carteira reatribuída: o aviso antigo para de aparecer para o antigo dono.

        A notificação é do manager B (`user_id`), mas o cliente pertence à
        carteira do A. Sem o filtro por `client_assignments`, B seguiria vendo
        conta+mês de um cliente que não é mais dele.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        mgr_b = await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_do_a = await _seed_client(db_session, name="Austral", creator=admin, manager=mgr_a)
        orfa = await _seed_notification(db_session, user=mgr_b, client=cliente_do_a)
        await _login(client_with_db, MANAGER_B_EMAIL)

        assert await _unread(client_with_db) == 0

        lista = await client_with_db.get("/api/v1/notifications")
        assert lista.status_code == 200, lista.text
        assert lista.json()["data"] == []

        # E marcar como lida também é negado (404, sem distinguir de inexistente).
        marcar = await client_with_db.post(f"/api/v1/notifications/{orfa.id}/read")
        assert marcar.status_code == 404

    async def test_lista_expoe_session_id_e_tipo_para_o_evento_do_front(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Sem `session_id`+`tipo` na resposta, `notificacao_entregue` ficaria órfão."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        notification = await _seed_notification(
            db_session,
            user=admin,
            client=cliente,
            tipo=NotificationType.ERRO,
            error_code=ErrorCode.PARSE_ERROR.value,
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get("/api/v1/notifications")
        assert resp.status_code == 200, resp.text
        item = resp.json()["data"][0]
        assert item["id"] == str(notification.id)
        assert item["session_id"] == str(notification.session_id)
        assert item["tipo"] == "erro"
        assert item["error_code"] == ErrorCode.PARSE_ERROR.value
        assert item["omie_conta_id"] == 42
        assert item["reference_month"] == "2026-06-01"
        assert item["read_at"] is None

    async def test_marcar_como_lida_some_do_contador_e_e_idempotente(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        notification = await _seed_notification(db_session, user=admin, client=cliente)
        await _login(client_with_db, ADMIN_EMAIL)
        assert await _unread(client_with_db) == 1

        first = await client_with_db.post(f"/api/v1/notifications/{notification.id}/read")
        assert first.status_code == 200, first.text
        assert first.json()["data"]["already_read"] is False
        assert await _unread(client_with_db) == 0

        second = await client_with_db.post(f"/api/v1/notifications/{notification.id}/read")
        assert second.status_code == 200, second.text
        assert second.json()["data"]["already_read"] is True
        # Timestamp da PRIMEIRA leitura é preservado.
        assert second.json()["data"]["read_at"] == first.json()["data"]["read_at"]
        # E não reaparece no contador.
        assert await _unread(client_with_db) == 0

    async def test_filtro_de_nao_lidas_e_paginacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        lida = await _seed_notification(db_session, user=admin, client=cliente)
        await _seed_notification(db_session, user=admin, client=cliente)
        await _seed_notification(db_session, user=admin, client=cliente)
        await _login(client_with_db, ADMIN_EMAIL)
        await client_with_db.post(f"/api/v1/notifications/{lida.id}/read")

        todas = await client_with_db.get("/api/v1/notifications")
        assert todas.json()["pagination"]["total"] == 3

        nao_lidas = await client_with_db.get("/api/v1/notifications?unreadOnly=true")
        assert nao_lidas.json()["pagination"]["total"] == 2
        assert len(nao_lidas.json()["data"]) == 2

        pagina = await client_with_db.get("/api/v1/notifications?page=1&pageSize=2")
        assert len(pagina.json()["data"]) == 2
        assert pagina.json()["pagination"]["totalPages"] == 2

    async def test_marcar_notificacao_de_outro_usuario_retorna_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        outro = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="Austral", creator=admin, manager=outro)
        alheia = await _seed_notification(db_session, user=outro, client=cliente)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(f"/api/v1/notifications/{alheia.id}/read")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_notificacao_inexistente_retorna_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(f"/api/v1/notifications/{uuid4()}/read")
        assert resp.status_code == 404
