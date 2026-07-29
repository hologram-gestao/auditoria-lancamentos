"""Testes de integração do multi-arquivo por conciliação (Sprint 4 / BACK 04.2).

Cobre os critérios de aceite:
    - `reconciliation_files` 1—N por sessão, `file_hash` POR ARQUIVO;
      sessão passa a `UNIQUE(client, conta, mês)` e duplicata de arquivo a
      `UNIQUE(session_id, file_hash)`.
    - Criar com N arquivos consolida as entradas na MESMA sessão, agenda UM
      processamento (cruzamento Omie roda uma vez) e reporta o nº de arquivos.
    - Anexar arquivo a conciliação existente não fechada re-consolida (S-3).
    - Reenvio do mesmo hash é duplicata; parte nova é aceita.
    - Parte que falhou na extração é registrada com o CÓDIGO do erro, aparece
      identificada na listagem e pode ser removida sem corromper a sessão.
    - Nome do arquivo persiste CIFRADO (nunca em claro no banco).

Os testes de unicidade no nível do banco vivem em `test_db_models.py`.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    ReconciliationFile,
    ReconciliationFileEntry,
    ReconciliationFileStatus,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)
from app.modules.reconciliations import routes as reconciliation_routes

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "files-admin@hologram.com.br"
MANAGER_A_EMAIL = "files-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "files-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "files-app-key"
FAKE_APP_SECRET = "files-app-secret"

SECRET_FILENAME = "Extrato Cliente Secretissimo Junho.pdf"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Files User",
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


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _statement(
    *,
    period_start: str,
    period_end: str,
    transactions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "bank_name": "Itau",
        "account_type": "credit_card",
        "period_start": period_start,
        "period_end": period_end,
        "opening_balance": "0.00",
        "closing_balance": "100.00",
        "transactions": transactions,
    }


def _part(
    *,
    salt: str,
    filename: str | None = None,
    day_from: int,
    day_to: int,
    n_tx: int = 2,
) -> dict[str, Any]:
    """Uma parte extraída com sucesso, cobrindo os dias `day_from..day_to`."""
    txs = [
        {
            "date": f"2026-06-{day_from + i:02d}",
            "description": f"Compra {salt} {i}",
            "amount": "-50.00",
            "balance": None,
        }
        for i in range(n_tx)
    ]
    part: dict[str, Any] = {
        "file_hash": _hex64(salt),
        "statement": _statement(
            period_start=f"2026-06-{day_from:02d}",
            period_end=f"2026-06-{day_to:02d}",
            transactions=txs,
        ),
    }
    if filename is not None:
        part["filename"] = filename
    return part


def _failed_part(*, salt: str, filename: str, code: str = "PARSE_ERROR") -> dict[str, Any]:
    return {"file_hash": _hex64(salt), "filename": filename, "error_code": code}


def _create_body(*, client_id: UUID, files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "client_id": str(client_id),
        "omie_conta_id": 42,
        "reference_month": "2026-06-01",
        "files": files,
    }


@pytest.fixture
def stub_enqueue() -> Iterator[list[UUID]]:
    """Não dispara o processamento real — registra os agendamentos.

    A lista é o instrumento do guardrail "o cruzamento Omie roda UMA vez sobre
    o conjunto": 3 arquivos numa criação têm de produzir UM agendamento.
    """
    scheduled: list[UUID] = []

    def _stub(_background_tasks: object, session_id: UUID) -> None:
        scheduled.append(session_id)

    original = reconciliation_routes._schedule_reconciliation_processing  # type: ignore[attr-defined]
    reconciliation_routes._schedule_reconciliation_processing = _stub  # type: ignore[attr-defined]
    try:
        yield scheduled
    finally:
        reconciliation_routes._schedule_reconciliation_processing = original  # type: ignore[attr-defined]


async def _files_of(session: AsyncSession, session_id: UUID) -> list[ReconciliationFile]:
    rows = await session.execute(
        select(ReconciliationFile)
        .where(ReconciliationFile.session_id == session_id)
        .order_by(ReconciliationFile.created_at, ReconciliationFile.id)
    )
    return list(rows.scalars().all())


async def _entries_of(session: AsyncSession, session_id: UUID) -> list[ReconciliationFileEntry]:
    rows = await session.execute(
        select(ReconciliationFileEntry).where(ReconciliationFileEntry.session_id == session_id)
    )
    return list(rows.scalars().all())


# ----------------------------------------------------------------------
# Criação com N arquivos
# ----------------------------------------------------------------------


class TestCreateWithMultipleFiles:
    async def test_tres_partes_consolidam_numa_sessao_com_um_processamento(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Fatura de 12 páginas em 3 PDFs → 1 sessão, 6 linhas, 1 cruzamento."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_body(
                client_id=cliente.id,
                files=[
                    _part(salt="p1", day_from=1, day_to=5),
                    _part(salt="p2", day_from=6, day_to=9),
                    _part(salt="p3", day_from=10, day_to=12),
                ],
            ),
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()["data"]
        assert body["total_files"] == 3
        session_id = UUID(body["session_id"])

        # UM agendamento — o cruzamento Omie não roda por arquivo.
        assert stub_enqueue == [session_id]

        files = await _files_of(db_session, session_id)
        assert len(files) == 3
        assert {f.status for f in files} == {ReconciliationFileStatus.PARSED.value}

        entries = await _entries_of(db_session, session_id)
        assert len(entries) == 6
        # Toda linha aponta para a parte de onde veio (é o que torna a remoção
        # de uma parte cirúrgica).
        assert {e.file_id for e in entries} == {f.id for f in files}

    async def test_periodo_da_sessao_cobre_todas_as_partes(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Janela Omie = da 1ª à última parte, não só a do 1º arquivo.

        Se o período viesse só da 1ª parte, as linhas das partes seguintes
        cairiam fora da janela consultada e virariam `sem_omie` — o pior tipo
        de defeito: silencioso e plausível.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_body(
                client_id=cliente.id,
                files=[
                    _part(salt="a", day_from=1, day_to=5),
                    _part(salt="b", day_from=20, day_to=25),
                ],
            ),
        )
        assert resp.status_code == 201, resp.text
        session_id = UUID(resp.json()["data"]["session_id"])

        sess = await db_session.scalar(
            select(ReconciliationSession).where(ReconciliationSession.id == session_id)
        )
        assert sess is not None
        assert sess.period_start == date(2026, 6, 1)
        assert sess.period_end == date(2026, 6, 25)
        # O hash saiu da sessão — mora nas partes.
        assert sess.file_hash is None

    async def test_forma_legada_de_um_arquivo_continua_aceita(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """`file_hash` + `statement` soltos → normalizados para 1 parte."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        part = _part(salt="legacy", day_from=1, day_to=3)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json={
                "client_id": str(cliente.id),
                "omie_conta_id": 42,
                "reference_month": "2026-06-01",
                "file_hash": part["file_hash"],
                "statement": part["statement"],
            },
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["total_files"] == 1
        session_id = UUID(resp.json()["data"]["session_id"])
        assert len(await _files_of(db_session, session_id)) == 1

    async def test_partes_repetidas_na_mesma_request_sao_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_body(
                client_id=cliente.id,
                files=[
                    _part(salt="mesmo", day_from=1, day_to=3),
                    _part(salt="mesmo", day_from=1, day_to=3),
                ],
            ),
        )

        assert resp.status_code == 400, resp.text
        assert stub_enqueue == []

    async def test_todas_as_partes_com_erro_e_recusado(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Sem nenhuma parte extraída não há o que conciliar."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_body(
                client_id=cliente.id,
                files=[_failed_part(salt="x", filename="parte1.pdf")],
            ),
        )

        assert resp.status_code == 400, resp.text
        assert stub_enqueue == []

    async def test_parte_com_statement_e_error_code_juntos_e_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        part = _part(salt="ambos", day_from=1, day_to=3)
        part["error_code"] = "PARSE_ERROR"

        resp = await client_with_db.post(
            "/api/v1/reconciliations", json=_create_body(client_id=cliente.id, files=[part])
        )
        assert resp.status_code == 400, resp.text

    async def test_error_code_desconhecido_e_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Código fora do enum canônico não entra — nem como texto livre."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_body(
                client_id=cliente.id,
                files=[
                    _part(salt="ok", day_from=1, day_to=3),
                    _failed_part(salt="ruim", filename="p.pdf", code="Erro do CNPJ 12.345/0001-99"),
                ],
            ),
        )
        assert resp.status_code == 400, resp.text


# ----------------------------------------------------------------------
# Nome do arquivo cifrado
# ----------------------------------------------------------------------


class TestFilenameEncryption:
    async def test_nome_do_arquivo_nunca_fica_em_claro_no_banco(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Nome de arquivo é texto livre e costuma trazer razão social."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations",
            json=_create_body(
                client_id=cliente.id,
                files=[_part(salt="cifrado", filename=SECRET_FILENAME, day_from=1, day_to=3)],
            ),
        )
        assert resp.status_code == 201, resp.text
        session_id = UUID(resp.json()["data"]["session_id"])

        files = await _files_of(db_session, session_id)
        assert files[0].filename_encrypted is not None
        assert files[0].filename_iv is not None
        assert "Secretissimo" not in files[0].filename_encrypted

        # …e volta DECIFRADO na listagem.
        listed = await client_with_db.get(f"/api/v1/reconciliations/{session_id}/files")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["files"][0]["filename"] == SECRET_FILENAME


# ----------------------------------------------------------------------
# Anexar parte a conciliação existente (S-3)
# ----------------------------------------------------------------------


async def _create_session(
    client_with_db: AsyncClient,
    cliente: Client,
    *,
    files: list[dict[str, Any]],
) -> UUID:
    resp = await client_with_db.post(
        "/api/v1/reconciliations", json=_create_body(client_id=cliente.id, files=files)
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["data"]["session_id"])


async def _set_status(session: AsyncSession, session_id: UUID, status_value: str) -> None:
    sess = await session.scalar(
        select(ReconciliationSession).where(ReconciliationSession.id == session_id)
    )
    assert sess is not None
    sess.status = status_value
    await session.flush()


class TestAttachFiles:
    async def test_anexar_parte_nova_reconsolida(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """S-3: 'criei com a parte 1, a parte 2 veio no dia seguinte'."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="parte1", day_from=1, day_to=5)]
        )
        # Conciliação já processada, aguardando revisão.
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)
        stub_enqueue.clear()

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{session_id}/files",
            json={"files": [_part(salt="parte2", day_from=6, day_to=9)]},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()["data"]
        assert body["total_files"] == 2
        assert body["reprocessing"] is True
        # Re-consolidação: UM cruzamento sobre o conjunto inteiro.
        assert stub_enqueue == [session_id]

        assert len(await _entries_of(db_session, session_id)) == 4
        sess = await db_session.scalar(
            select(ReconciliationSession).where(ReconciliationSession.id == session_id)
        )
        assert sess is not None
        assert sess.status == ReconciliationStatus.PROCESSING.value

    async def test_anexar_parte_repetida_e_409_duplicate_file(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="parte1", day_from=1, day_to=5)]
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)
        stub_enqueue.clear()

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{session_id}/files",
            json={"files": [_part(salt="parte1", day_from=1, day_to=5)]},
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "DUPLICATE_FILE"
        assert stub_enqueue == []
        # Nada foi gravado: a sessão continua com uma parte só.
        assert len(await _files_of(db_session, session_id)) == 1

    async def test_parte_nova_nao_e_bloqueada_pelas_anteriores(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Caso negativo do critério: repetida barra, nova passa."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="parte1", day_from=1, day_to=5)]
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)

        ok = await client_with_db.post(
            f"/api/v1/reconciliations/{session_id}/files",
            json={"files": [_part(salt="parte4", day_from=13, day_to=15)]},
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["data"]["total_files"] == 2

    async def test_anexar_em_sessao_em_processamento_e_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="parte1", day_from=1, day_to=5)]
        )  # nasce em `processing`

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{session_id}/files",
            json={"files": [_part(salt="parte2", day_from=6, day_to=9)]},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CONFLICT"

    async def test_anexar_em_sessao_concluida_e_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="parte1", day_from=1, day_to=5)]
        )
        await _set_status(db_session, session_id, ReconciliationStatus.DONE.value)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{session_id}/files",
            json={"files": [_part(salt="parte2", day_from=6, day_to=9)]},
        )
        assert resp.status_code == 409, resp.text

    async def test_manager_de_outra_carteira_recebe_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="Austral", creator=admin, manager=mgr_a)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="parte1", day_from=1, day_to=5)]
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{session_id}/files",
            json={"files": [_part(salt="parte2", day_from=6, day_to=9)]},
        )
        assert resp.status_code == 404, resp.text

    async def test_sessao_inexistente_retorna_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            f"/api/v1/reconciliations/{uuid4()}/files",
            json={"files": [_part(salt="parte2", day_from=6, day_to=9)]},
        )
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Parte que falhou: identificada e removível sem corromper a sessão
# ----------------------------------------------------------------------


class TestFailedPartAndRemoval:
    async def test_parte_com_falha_e_identificada_na_listagem(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db,
            cliente,
            files=[
                _part(salt="boa1", filename="parte1.pdf", day_from=1, day_to=5),
                _failed_part(salt="ruim", filename="parte2.pdf"),
                _part(salt="boa3", filename="parte3.pdf", day_from=10, day_to=12),
            ],
        )

        resp = await client_with_db.get(f"/api/v1/reconciliations/{session_id}/files")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["total_files"] == 3
        by_name = {f["filename"]: f for f in data["files"]}
        assert by_name["parte2.pdf"]["status"] == "error"
        assert by_name["parte2.pdf"]["error_code"] == "PARSE_ERROR"
        assert by_name["parte2.pdf"]["entry_count"] == 0
        # As partes boas seguem íntegras — a sessão não foi corrompida.
        assert by_name["parte1.pdf"]["entry_count"] == 2
        assert by_name["parte3.pdf"]["entry_count"] == 2

    async def test_remover_parte_leva_so_as_linhas_dela(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db,
            cliente,
            files=[
                _part(salt="fica", filename="fica.pdf", day_from=1, day_to=5),
                _part(salt="sai", filename="sai.pdf", day_from=6, day_to=9),
            ],
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)
        stub_enqueue.clear()
        files = await _files_of(db_session, session_id)
        alvo = next(f for f in files if f.file_hash == _hex64("sai"))

        resp = await client_with_db.delete(f"/api/v1/reconciliations/{session_id}/files/{alvo.id}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["total_files"] == 1
        assert resp.json()["data"]["reprocessing"] is True
        assert stub_enqueue == [session_id]

        restantes = await _entries_of(db_session, session_id)
        assert len(restantes) == 2  # só as da parte que ficou
        assert all(e.file_id != alvo.id for e in restantes)

    async def test_remover_parte_que_falhou_nao_reprocessa(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Remover um registro sem linhas não muda o cruzamento."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db,
            cliente,
            files=[
                _part(salt="boa", filename="boa.pdf", day_from=1, day_to=5),
                _failed_part(salt="ruim", filename="ruim.pdf"),
            ],
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)
        stub_enqueue.clear()
        files = await _files_of(db_session, session_id)
        alvo = next(f for f in files if f.status == ReconciliationFileStatus.ERROR.value)

        resp = await client_with_db.delete(f"/api/v1/reconciliations/{session_id}/files/{alvo.id}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["reprocessing"] is False
        assert stub_enqueue == []

    async def test_remover_ultima_parte_com_linhas_e_409(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """Conciliação sem nenhuma linha não tem o que conciliar."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="unica", day_from=1, day_to=5)]
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)
        files = await _files_of(db_session, session_id)

        resp = await client_with_db.delete(
            f"/api/v1/reconciliations/{session_id}/files/{files[0].id}"
        )

        assert resp.status_code == 409, resp.text
        assert len(await _entries_of(db_session, session_id)) == 2

    async def test_remover_parte_de_outra_sessao_retorna_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        """O id da parte vem da URL — não pode alcançar outra conciliação."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db, cliente, files=[_part(salt="p", day_from=1, day_to=5)]
        )
        await _set_status(db_session, session_id, ReconciliationStatus.REVIEWING.value)

        resp = await client_with_db.delete(f"/api/v1/reconciliations/{session_id}/files/{uuid4()}")
        assert resp.status_code == 404, resp.text


# ----------------------------------------------------------------------
# Detalhe reporta o nº de arquivos
# ----------------------------------------------------------------------


class TestDetailReportsFileCount:
    async def test_detalhe_traz_total_files(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        stub_enqueue: list[UUID],
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        session_id = await _create_session(
            client_with_db,
            cliente,
            files=[
                _part(salt="d1", day_from=1, day_to=5),
                _part(salt="d2", day_from=6, day_to=9),
            ],
        )

        resp = await client_with_db.get(f"/api/v1/reconciliations/{session_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["total_files"] == 2
