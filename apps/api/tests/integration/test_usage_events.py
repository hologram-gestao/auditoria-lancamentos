"""Testes de integração da instrumentação de outcome (Sprint 4 / BACK 04.1).

Cobre os critérios de aceite da task:
    - `conciliacao_criada` é gravado ao criar a sessão, com
      {session_id, client_id, n_arquivos, criado_por}.
    - `conciliacao_concluida` é gravado ao fim do processamento, com
      {session_id, duracao_s:int, status} — inclusive no desfecho `error`.
    - POST /api/v1/usage-events: exige auth, valida `event` contra enum fechado,
      valida ownership do `session_id` via `client_assignments` (403 de outro
      manager, 404 se a sessão não existe) e whitelista as chaves de `props`.
    - Nenhum evento grava PII: só IDs, enums e inteiros.
    - Falha ao gravar o evento NÃO interrompe a criação da conciliação
      (fail-soft + SAVEPOINT — a transação de negócio sobrevive).
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    FileEntrySituation,
    ReconciliationFileEntry,
    ReconciliationSession,
    ReconciliationStatus,
    UsageEvent,
    User,
    UserRole,
)
from app.modules.reconciliations import routes as reconciliation_routes
from app.modules.reconciliations.processing.job import run_reconciliation_processing
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import UsageEventName

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "usage-admin@hologram.com.br"
MANAGER_A_EMAIL = "usage-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "usage-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "usage-app-key"
FAKE_APP_SECRET = "usage-app-secret"

OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"

#: Chaves permitidas por evento — espelha a whitelist do servidor. Se alguém
#: adicionar um campo novo, este dict tem de mudar junto (e a revisão pergunta
#: "isso é PII?"). É o teste que impede o sink de virar depósito de texto livre.
_EXPECTED_PROP_KEYS: dict[str, set[str]] = {
    UsageEventName.CONCILIACAO_CRIADA.value: {"client_id", "n_arquivos", "criado_por"},
    UsageEventName.CONCILIACAO_CONCLUIDA.value: {"duracao_s", "status"},
    UsageEventName.AUTOR_NAVEGOU_FORA.value: {"segundos_apos_criar"},
    UsageEventName.NOTIFICACAO_ENTREGUE.value: {"via", "latencia_s"},
}


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


# ----------------------------------------------------------------------
# Seeds via db_session (testes HTTP)
# ----------------------------------------------------------------------


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Usage User",
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


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    salt: str = "usage",
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 6, 1),
        date_tolerance_days=0,
        file_hash=_hex64(salt),
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _statement() -> dict[str, Any]:
    return {
        "bank_name": "Sicredi",
        "account_type": "checking",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "opening_balance": "1000.00",
        "closing_balance": "1234.56",
        "transactions": [
            {
                "date": "2026-04-02",
                "description": "Pagamento fornecedor X",
                "amount": "-500.00",
                "balance": "500.00",
            }
        ],
    }


def _create_payload(*, client_id: UUID, file_hash: str | None = None) -> dict[str, Any]:
    return {
        "client_id": str(client_id),
        "omie_conta_id": 42,
        "reference_month": "2026-04-01",
        "file_hash": file_hash or _hex64("usage-create"),
        "statement": _statement(),
    }


@pytest.fixture
def stub_enqueue() -> Iterator[list[UUID]]:
    """Não dispara a BackgroundTask real — só registra o agendamento."""
    scheduled: list[UUID] = []

    def _stub(_background_tasks: object, session_id: UUID) -> None:
        scheduled.append(session_id)

    original = reconciliation_routes._schedule_reconciliation_processing  # type: ignore[attr-defined]
    reconciliation_routes._schedule_reconciliation_processing = _stub  # type: ignore[attr-defined]
    try:
        yield scheduled
    finally:
        reconciliation_routes._schedule_reconciliation_processing = original  # type: ignore[attr-defined]


async def _events(session: AsyncSession, event: str) -> list[UsageEvent]:
    rows = await session.execute(
        select(UsageEvent).where(UsageEvent.event == event).order_by(UsageEvent.created_at)
    )
    return list(rows.scalars().all())


# ----------------------------------------------------------------------
# Emissor de backend — conciliacao_criada
# ----------------------------------------------------------------------


class TestConciliacaoCriadaEmitter:
    async def test_criar_conciliacao_grava_evento_com_props_esperadas(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations", json=_create_payload(client_id=cliente.id)
        )
        assert resp.status_code == 201, resp.text
        session_id = UUID(resp.json()["data"]["session_id"])
        assert stub_enqueue == [session_id]

        events = await _events(db_session, UsageEventName.CONCILIACAO_CRIADA.value)
        assert len(events) == 1
        evt = events[0]
        assert evt.session_id == session_id
        assert set(evt.props) == _EXPECTED_PROP_KEYS[UsageEventName.CONCILIACAO_CRIADA.value]
        assert evt.props["client_id"] == str(cliente.id)
        assert evt.props["criado_por"] == str(admin.id)
        # `n_arquivos` é int (não string) — a leitura D+30 agrega sem cast.
        assert evt.props["n_arquivos"] == 1
        assert isinstance(evt.props["n_arquivos"], int)

    async def test_evento_nao_grava_pii_do_cliente(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Nome do cliente e descrição do lançamento NÃO podem estar no props."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Cliente Secretíssimo LTDA", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations", json=_create_payload(client_id=cliente.id)
        )
        assert resp.status_code == 201, resp.text

        events = await _events(db_session, UsageEventName.CONCILIACAO_CRIADA.value)
        blob = str(events[0].props)
        assert "Secretíssimo" not in blob
        assert "Pagamento fornecedor X" not in blob
        assert ADMIN_EMAIL not in blob

    async def test_falha_ao_gravar_evento_nao_derruba_criacao(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fail-soft REAL: erro de banco dentro do SAVEPOINT do emissor.

        Não é um `raise` sintético antes do SQL — é um `SELECT 1/0` executado
        dentro do savepoint, exatamente o cenário que envenenaria a transação
        da request se o SAVEPOINT não existisse. A conciliação tem de nascer.
        """

        async def _boom(
            self: UsageEventRepository,
            *,
            event: str,
            session_id: UUID | None,
            props: dict[str, Any],
        ) -> bool:
            async with self._session.begin_nested():
                await self._session.execute(text("SELECT 1 / 0"))
            return True

        monkeypatch.setattr(UsageEventRepository, "insert_ignore_duplicate", _boom)

        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations", json=_create_payload(client_id=cliente.id)
        )

        assert resp.status_code == 201, resp.text
        session_id = UUID(resp.json()["data"]["session_id"])
        persisted = await db_session.scalar(
            select(ReconciliationSession).where(ReconciliationSession.id == session_id)
        )
        assert persisted is not None
        assert await _events(db_session, UsageEventName.CONCILIACAO_CRIADA.value) == []


# ----------------------------------------------------------------------
# Emissor de backend — conciliacao_concluida (fim do processamento)
# ----------------------------------------------------------------------


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """sessionmaker apontando pro DB do testcontainers — passado ao job.

    Diferente da fixture `db_session`, o que passa por aqui é **commitado de
    verdade** (o job roda na sua própria transação, como em produção) e por isso
    NÃO é desfeito pelo rollback de fim de teste. Sem a limpeza abaixo, as linhas
    do job vazam para os testes seguintes — foi o que quebrou
    `test_payload_invalido_retorna_400`, que lia a tabela inteira.
    `usage_events` precisa ser citada explicitamente: é log append-only, sem FK,
    então o `CASCADE` de `users`/`clients` não a alcança.
    """
    yield async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE usage_events, users, clients RESTART IDENTITY CASCADE"))


async def _seed_job_fixtures(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    client_name: str,
) -> tuple[UUID, UUID]:
    """Semeia admin + cliente + sessão `processing` COMMITADOS (o job usa DB real)."""
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    async with factory() as s, s.begin():
        user = User(
            name="Admin",
            email=email.lower(),
            password_hash=hash_password(PLAIN_PASSWORD),
            role=UserRole.ADMIN.value,
            active=True,
        )
        s.add(user)
        await s.flush()
        cli = Client(
            name=client_name,
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
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            date_tolerance_days=0,
            file_hash=_hex64(f"job-{email}"),
            status=ReconciliationStatus.PROCESSING.value,
        )
        s.add(sess)
        await s.flush()
        ct, iv = encrypt("Pagamento fornecedor X", hex_key)
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
        return sess.id, cli.id


class TestConciliacaoConcluidaEmitter:
    @respx.mock
    async def test_emite_evento_no_desfecho_error(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Falha do Omie → sessão `error` → evento com o status REAL do banco."""
        session_id, _ = await _seed_job_fixtures(
            factory, email="usage-job-err@hologram.com.br", client_name="JobErr"
        )
        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                401,
                json={"faultstring": "Client Id/Secret inválidos", "faultcode": "SOAP-ENV:Client"},
            )
        )

        await run_reconciliation_processing(
            str(session_id), settings=get_settings(), session_factory=factory
        )

        async with factory() as s:
            status = await s.scalar(
                select(ReconciliationSession.status).where(ReconciliationSession.id == session_id)
            )
            events = await _events(s, UsageEventName.CONCILIACAO_CONCLUIDA.value)
            events = [e for e in events if e.session_id == session_id]

        assert status == ReconciliationStatus.ERROR.value
        assert len(events) == 1
        props = events[0].props
        assert set(props) == _EXPECTED_PROP_KEYS[UsageEventName.CONCILIACAO_CONCLUIDA.value]
        assert props["status"] == ReconciliationStatus.ERROR.value
        assert isinstance(props["duracao_s"], int)
        assert props["duracao_s"] >= 0

    async def test_nao_emite_para_sessao_inexistente(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Sessão que não existe não "conclui" — o sink não ganha linha órfã."""
        ghost = uuid4()

        await run_reconciliation_processing(
            str(ghost), settings=get_settings(), session_factory=factory
        )

        async with factory() as s:
            events = await _events(s, UsageEventName.CONCILIACAO_CONCLUIDA.value)
        assert [e for e in events if e.session_id == ghost] == []


# ----------------------------------------------------------------------
# POST /api/v1/usage-events
# ----------------------------------------------------------------------


class TestUsageEventsEndpoint:
    async def test_sem_auth_retorna_401(self, client_with_db: AsyncClient) -> None:
        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": "autor_navegou_fora",
                "session_id": str(uuid4()),
                "props": {"segundos_apos_criar": 5},
            },
        )
        assert resp.status_code == 401

    async def test_autor_navegou_fora_gravado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": "autor_navegou_fora",
                "session_id": str(sess.id),
                "props": {"segundos_apos_criar": 12},
            },
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["recorded"] is True
        events = await _events(db_session, UsageEventName.AUTOR_NAVEGOU_FORA.value)
        assert len(events) == 1
        assert events[0].session_id == sess.id
        assert events[0].props == {"segundos_apos_criar": 12}

    async def test_notificacao_entregue_gravado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": "notificacao_entregue",
                "session_id": str(sess.id),
                "props": {"via": "sino", "latencia_s": 34},
            },
        )

        assert resp.status_code == 201, resp.text
        events = await _events(db_session, UsageEventName.NOTIFICACAO_ENTREGUE.value)
        assert events[0].props == {"via": "sino", "latencia_s": 34}

    async def test_reenvio_e_idempotente(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        body = {
            "event": "autor_navegou_fora",
            "session_id": str(sess.id),
            "props": {"segundos_apos_criar": 3},
        }

        first = await client_with_db.post("/api/v1/usage-events", json=body)
        second = await client_with_db.post("/api/v1/usage-events", json=body)

        assert first.json()["data"]["recorded"] is True
        assert second.status_code == 201
        assert second.json()["data"]["recorded"] is False
        assert len(await _events(db_session, UsageEventName.AUTOR_NAVEGOU_FORA.value)) == 1

    @pytest.mark.parametrize(
        "body_patch",
        [
            pytest.param({"event": "evento_inventado"}, id="event-fora-do-enum"),
            pytest.param(
                {"event": "conciliacao_criada"},
                id="evento-de-backend-nao-e-aceito-do-cliente",
            ),
            pytest.param(
                {"props": {"segundos_apos_criar": 5, "email": "vitima@x.com"}},
                id="chave-desconhecida-em-props",
            ),
            pytest.param({"props": {"segundos_apos_criar": -1}}, id="duracao-negativa"),
            pytest.param({"props": {}}, id="props-sem-chave-obrigatoria"),
        ],
    )
    async def test_payload_invalido_retorna_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        body_patch: dict[str, Any],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        body: dict[str, Any] = {
            "event": "autor_navegou_fora",
            "session_id": str(sess.id),
            "props": {"segundos_apos_criar": 5},
        }
        body.update(body_patch)

        resp = await client_with_db.post("/api/v1/usage-events", json=body)

        assert resp.status_code == 400, resp.text
        # Escopado à sessão do teste (convenção do arquivo): a tabela é um log
        # global e outros testes commitam nela de verdade — assertar a tabela
        # inteira vazia mediria a ordem de execução, não o endpoint.
        rows = await db_session.execute(select(UsageEvent).where(UsageEvent.session_id == sess.id))
        assert list(rows.scalars().all()) == []

    async def test_via_fora_do_enum_retorna_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": "notificacao_entregue",
                "session_id": str(sess.id),
                "props": {"via": "email", "latencia_s": 1},
            },
        )
        assert resp.status_code == 400

    async def test_sessao_inexistente_retorna_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": "autor_navegou_fora",
                "session_id": str(uuid4()),
                "props": {"segundos_apos_criar": 5},
            },
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_manager_de_outra_carteira_retorna_403(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Isolamento: manager B não emite evento para sessão do cliente do A."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(db_session, name="Austral", creator=admin, manager=mgr_a)
        sess = await _seed_session(db_session, client=cliente_a, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": "autor_navegou_fora",
                "session_id": str(sess.id),
                "props": {"segundos_apos_criar": 5},
            },
        )

        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"
        assert await _events(db_session, UsageEventName.AUTOR_NAVEGOU_FORA.value) == []
