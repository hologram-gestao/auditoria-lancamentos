"""Testes de integração do ENCERRAMENTO com retenção — 86e36pm1z.

O irmão da exclusão (test_client_delete.py): apaga quem o cliente É e mantém o
que ACONTECEU. Cobre:
    - Admin encerra: nome anonimizado, credenciais vazias, `dek_wrapped` NULL
      (crypto-shredding §4.1), usuários do tenant anonimizados + desativados
      (login morre), glossário/cache/notificações/favoritos removidos;
      conciliações, atribuição de carteira, `access_audit` e a trilha FICAM;
      evento `cliente_encerrado` com contagens; vizinho intocado; o histórico
      continua legível (GET detalhe 200).
    - Escrita em cliente encerrado → 409 em TODAS as superfícies com guard
      (`OpenClientDep` + service): editar, sync, usuário novo, glossário,
      encerrar de novo.
    - 409 com conciliação EM PROCESSAMENTO; manager → 403; 401/404 padrão.
    - Exclusão TOTAL continua possível depois do encerramento (LGPD).

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
    ClientGlossaryEntry,
    Notification,
    NotificationType,
    OmieAccountCache,
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
ADMIN_EMAIL = "admin-close@hologram.com.br"
MANAGER_EMAIL = "manager-close@hologram.com.br"
TENANT_MANAGER_EMAIL = "gerente@cliente-close-a.com.br"
TENANT_OPERATOR_EMAIL = "operador@cliente-close-a.com.br"
OTHER_TENANT_USER_EMAIL = "operador@cliente-close-b.com.br"


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
    ct_k, iv_k = encrypt("close-app-key", hex_key)
    ct_s, iv_s = encrypt("close-app-secret", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
        # Crypto-shredding é o coração do encerramento: o cliente PRECISA ter
        # uma DEK embrulhada para o teste provar que ela morre.
        dek_wrapped=b"fake-wrapped-dek-for-close-test",
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
        reference_month=date(2026, 5, 1),
        date_tolerance_days=0,
        file_hash=_hex64(f"close-{uuid4().hex}"),
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
    w.cli_a = await _seed_client(db, name="Cliente Fechável A", creator=w.admin)
    w.cli_b = await _seed_client(db, name="Cliente Aberto B", creator=w.admin)
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
    w.sess_a = await _seed_session(
        db,
        client=w.cli_a,
        created_by=tenant_manager,
        status="processing" if processing else "reviewing",
    )
    db.add(
        Notification(
            user_id=tenant_manager.id,
            client_id=w.cli_a.id,
            session_id=w.sess_a.id,
            tipo=NotificationType.PROCESSADA.value,
            omie_conta_id=42,
            reference_month=date(2026, 5, 1),
        )
    )
    db.add(UserClientFavorite(user_id=w.admin.id, client_id=w.cli_a.id))
    # Satélites que a purga do encerramento deve levar: uma entrada de glossário
    # (ciphertext fake — só a CONTAGEM importa) e uma linha do cache de contas.
    db.add(
        ClientGlossaryEntry(
            client_id=w.cli_a.id,
            kind="categoria",
            name_encrypted="v1:k1:deadbeef",
            name_iv="0" * 24,
        )
    )
    db.add(
        OmieAccountCache(
            client_id=w.cli_a.id,
            omie_conta_id=42,
            name="Conta Teste",
            bank_name="Banco Teste",
            account_type="CC",
        )
    )
    # Plano de contas (S10): nada aqui é cifrado, então a purga precisa ser
    # EXPLÍCITA — o crypto-shredding não o alcança, e sem a linha em
    # `close_client_purge` a configuração do cliente sobreviveria ao
    # encerramento. Duas linhas, uma em CADA cliente: a do vizinho prova que a
    # purga é por tenant.
    db.add(ClientChartOfAccount(client_id=w.cli_a.id, category_code="1.01.01", dre_code="1.01"))
    db.add(ClientChartOfAccount(client_id=w.cli_b.id, category_code="1.01.01", dre_code="1.01"))
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


class TestCloseClient:
    async def test_admin_encerra_scrub_e_retencao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, TENANT_OPERATOR_EMAIL) == 200

        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(f"/api/v1/clients/{w.cli_a.id}/close")
        assert resp.status_code == 204, resp.text

        # IDs capturados ANTES do expire_all: atributo de instância expirada
        # dispara lazy-load síncrono e morre em MissingGreenlet no async.
        a = w.cli_a.id
        sess_id = w.sess_a.id
        b = w.cli_b.id
        # O cliente FICA — anonimizado e terminal.
        db_session.expire_all()
        row = (await db_session.execute(select(Client).where(Client.id == a))).scalar_one()
        assert row.closed_at is not None
        assert row.active is False
        assert row.name == f"Cliente encerrado #{a.hex[:8]}"
        assert "Fechável" not in row.name
        assert row.omie_app_key_encrypted == ""
        assert row.omie_app_secret_encrypted == ""
        assert row.dek_wrapped is None  # crypto-shredding (§4.1)

        # RETENÇÃO: conciliação, arquivo, carteira e trilha ficam.
        assert (
            await _count(
                db_session,
                select(func.count(ReconciliationSession.id)).where(
                    ReconciliationSession.client_id == a
                ),
            )
            == 1
        )
        assert (
            await _count(
                db_session,
                select(func.count(ReconciliationFile.id)).where(
                    ReconciliationFile.session_id == sess_id
                ),
            )
            == 1
        )
        assert (
            await _count(
                db_session,
                select(func.count(ClientAssignment.id)).where(ClientAssignment.client_id == a),
            )
            == 1
        )
        assert (
            await _count(
                db_session, select(func.count(AccessAudit.id)).where(AccessAudit.client_id == a)
            )
            == 1
        )

        # PURGA: glossário, cache de contas, notificações e favoritos somem.
        assert (
            await _count(
                db_session,
                select(func.count(ClientGlossaryEntry.id)).where(
                    ClientGlossaryEntry.client_id == a
                ),
            )
            == 0
        )
        assert (
            await _count(
                db_session,
                select(func.count(OmieAccountCache.id)).where(OmieAccountCache.client_id == a),
            )
            == 0
        )
        assert (
            await _count(
                db_session, select(func.count(Notification.id)).where(Notification.client_id == a)
            )
            == 0
        )
        assert (
            await _count(
                db_session,
                select(func.count(UserClientFavorite.id)).where(UserClientFavorite.client_id == a),
            )
            == 0
        )
        # Plano de contas (S10): some junto — é configuração do cliente final, e
        # nada nele morre com a DEK (não é cifrado).
        assert (
            await _count(
                db_session,
                select(func.count(ClientChartOfAccount.id)).where(
                    ClientChartOfAccount.client_id == a
                ),
            )
            == 0
        )
        assert (
            await _count(
                db_session,
                select(func.count(ClientChartOfAccount.id)).where(
                    ClientChartOfAccount.client_id == b
                ),
            )
            == 1
        ), "a purga é POR TENANT — o plano de contas do vizinho não pode ser tocado"

        # Usuários do tenant: FICAM (FK das sessões), mas anonimizados e mortos.
        users_a = (
            (await db_session.execute(select(User).where(User.client_id == a))).scalars().all()
        )
        assert len(users_a) == 2
        for u in users_a:
            assert u.active is False
            assert u.name == "Usuário removido"
            assert u.email == f"encerrado+{u.id}@anonimizado.invalid"
        assert await _login_as(client_with_db, TENANT_OPERATOR_EMAIL) == 401

        # O fato é medido, com contagens e sem nome.
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        evento = (
            await db_session.execute(
                select(UsageEvent).where(UsageEvent.event == "cliente_encerrado")
            )
        ).scalar_one()
        assert evento.props == {"client_id": str(a), "n_conciliacoes": 1, "n_usuarios": 2}
        assert "Fechável" not in str(evento.props)

        # Vizinho intocado.
        row_b = (await db_session.execute(select(Client).where(Client.id == b))).scalar_one()
        assert row_b.closed_at is None
        assert row_b.name == "Cliente Aberto B"

        # O histórico continua acessível para leitura (admin).
        detail = await client_with_db.get(f"/api/v1/clients/{a}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["closed_at"] is not None
        assert detail.json()["name"] == f"Cliente encerrado #{a.hex[:8]}"

    async def test_escrita_em_encerrado_leva_409_em_todas_as_superficies(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (await client_with_db.post(f"/api/v1/clients/{w.cli_a.id}/close")).status_code == 204

        a = w.cli_a.id
        blocked = [
            client_with_db.patch(f"/api/v1/clients/{a}", json={"name": "Renomeado"}),
            client_with_db.patch(f"/api/v1/clients/{a}/sync-accounts"),
            client_with_db.post(
                f"/api/v1/clients/{a}/users",
                json={
                    "name": "Novo",
                    "email": "novo@encerrado.com.br",
                    "password": PLAIN_PASSWORD,
                    "role": "client_operator",
                },
            ),
            client_with_db.post(
                f"/api/v1/clients/{a}/glossary",
                json={"kind": "categoria", "name": "Termo"},
            ),
            client_with_db.post(f"/api/v1/clients/{a}/close"),
        ]
        for coro in blocked:
            resp = await coro
            assert resp.status_code == 409, resp.text
            assert resp.json()["error"]["code"] == "CONFLICT"
            assert "encerrado" in resp.json()["error"]["userMessage"]

    async def test_409_com_conciliacao_em_processamento(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session, processing=True)
        cid = w.cli_a.id
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        resp = await client_with_db.post(f"/api/v1/clients/{cid}/close")
        assert resp.status_code == 409, resp.text
        assert "em processamento" in resp.json()["error"]["userMessage"]
        db_session.expire_all()
        row = (await db_session.execute(select(Client).where(Client.id == cid))).scalar_one()
        assert row.closed_at is None
        assert row.name == "Cliente Fechável A"

    async def test_manager_da_carteira_recebe_403_e_nada_muda(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        w = await _seed_world(db_session)
        cid = w.cli_a.id
        assert await _login_as(client_with_db, MANAGER_EMAIL) == 200
        resp = await client_with_db.post(f"/api/v1/clients/{cid}/close")
        assert resp.status_code == 403, resp.text
        db_session.expire_all()
        row = (await db_session.execute(select(Client).where(Client.id == cid))).scalar_one()
        assert row.closed_at is None

    async def test_sem_login_401_e_inexistente_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        assert (await client_with_db.post(f"/api/v1/clients/{uuid4()}/close")).status_code == 401
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (await client_with_db.post(f"/api/v1/clients/{uuid4()}/close")).status_code == 404

    async def test_exclusao_total_continua_possivel_apos_encerramento(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """LGPD: encerrado não é imune à exclusão definitiva."""
        w = await _seed_world(db_session)
        assert await _login_as(client_with_db, ADMIN_EMAIL) == 200
        assert (await client_with_db.post(f"/api/v1/clients/{w.cli_a.id}/close")).status_code == 204
        resp = await client_with_db.delete(f"/api/v1/clients/{w.cli_a.id}")
        assert resp.status_code == 204, resp.text
        assert (
            await _count(db_session, select(func.count(Client.id)).where(Client.id == w.cli_a.id))
            == 0
        )
