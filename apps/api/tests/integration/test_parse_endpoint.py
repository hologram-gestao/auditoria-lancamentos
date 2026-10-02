"""Testes de integração de POST /api/v1/reconciliations/parse — BACK 7.1.

Cobertura:
    Validação:
        - Sem auth → 401.
        - Cliente inacessível (manager fora da carteira) → 404.
        - Cliente inexistente → 404.
        - Arquivo > MAX_UPLOAD_SIZE_MB → 400.
        - Extensão fora do allowlist → 400.
        - Magic bytes não bate → 400.
        - .xls (não suportado nesta versão) → 400 com mensagem específica.

    IA:
        - Caminho feliz com PDF mockado: response com ParseResponse válida.
        - Sinais aritméticos preservados (crédito positivo, débito negativo).
        - Tool use ausente → 422 PARSE_ERROR.
        - Timeout do SDK → 504.
        - Auth fault do SDK → 502.
"""

from __future__ import annotations

import hashlib
from datetime import date
from io import BytesIO
from typing import TYPE_CHECKING, Any

import openpyxl
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    ReconciliationFile,
    ReconciliationFileStatus,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.anthropic.tools import (
    EXTRACT_MOVEMENTS_TOOL_NAME,
    IDENTIFY_DOCUMENT_TOOL_NAME,
)
from app.main import app as fastapi_app
from app.modules.reconciliations.routes import _get_anthropic_client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient


# ----------------------------------------------------------------------
# Setup helpers
# ----------------------------------------------------------------------

ADMIN_EMAIL = "parse-admin@hologram.com.br"
MANAGER_A_EMAIL = "parse-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "parse-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"

FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    name: str = "Test User",
) -> User:
    user = User(
        name=name,
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
            ClientAssignment(
                client_id=client.id,
                user_id=manager.id,
                assigned_by=creator.id,
                is_primary=True,
            )
        )
        await session.flush()
    return client


async def _login_as(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PLAIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# Fakes do AsyncAnthropic — mesmo formato do test_anthropic_client.py
# ----------------------------------------------------------------------


class _ToolUseBlock:
    def __init__(self, *, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = EXTRACT_MOVEMENTS_TOOL_NAME
        self.id = "toolu_1"
        self.input = payload


class _Message:
    def __init__(self, blocks: list[Any]) -> None:
        self.content = blocks


class _FakeMessages:
    def __init__(self, *, side_effect: Any) -> None:
        self.side_effect = side_effect
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.side_effect, BaseException):
            raise self.side_effect
        return self.side_effect


class _FakeAnthropic:
    def __init__(self, *, side_effect: Any) -> None:
        self.messages = _FakeMessages(side_effect=side_effect)


class _IdentifyToolUseBlock:
    """`tool_use` da identificação (86e3ff8xd): banco e tipo de conta."""

    def __init__(self) -> None:
        self.type = "tool_use"
        self.name = IDENTIFY_DOCUMENT_TOOL_NAME
        self.id = "toolu_identify"
        self.input = {"bank_name": "Banco do Brasil", "account_type": "checking"}


class _FakeMessagesByTool(_FakeMessages):
    """Responde conforme a tool forçada: identificação ou extração."""

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs["tool_choice"]["name"] == IDENTIFY_DOCUMENT_TOOL_NAME:
            return _Message([_IdentifyToolUseBlock()])
        return self.side_effect


class _FakeAnthropicByTool(_FakeAnthropic):
    def __init__(self, *, side_effect: Any) -> None:
        self.messages = _FakeMessagesByTool(side_effect=side_effect)


def _ok_payload() -> dict[str, Any]:
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
            },
            {
                "date": "2026-04-15",
                "description": "Recebimento cliente Y",
                "amount": "734.56",
                "balance": None,
            },
        ],
    }


def _ok_message() -> _Message:
    return _Message([_ToolUseBlock(payload=_ok_payload())])


@pytest.fixture
def override_anthropic() -> Iterator[dict[str, _FakeAnthropic | None]]:
    """Permite que cada teste configure o `_FakeAnthropic` que será injetado.

    Uso:
        def test_x(override_anthropic):
            override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
            ...
    """
    holder: dict[str, _FakeAnthropic | None] = {"fake": None}

    def _override() -> AnthropicClient:
        fake = holder["fake"]
        assert fake is not None, "Teste esqueceu de setar override_anthropic['fake']"
        return AnthropicClient(
            api_key=SecretStr("sk-ant-fake"),
            model="claude-test",
            timeout=10.0,
            anthropic_client=fake,
        )

    fastapi_app.dependency_overrides[_get_anthropic_client] = _override
    try:
        yield holder
    finally:
        fastapi_app.dependency_overrides.pop(_get_anthropic_client, None)


# ----------------------------------------------------------------------
# Helpers de bytes válidos por formato
# ----------------------------------------------------------------------


def _minimal_pdf_bytes() -> bytes:
    """PDF mínimo que passa magic bytes. Não precisa ser parseável — o teste
    mocka a Anthropic, então o conteúdo nunca é processado de verdade."""
    return b"%PDF-1.7\n%fake-pdf-content-for-tests\n" + b"x" * 100


def _multipage_pdf_bytes(n_pages: int) -> bytes:
    """PDF REAL de `n_pages` páginas em branco (o `pypdf` precisa lê-lo para dividir)."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _csv_bytes() -> bytes:
    return (
        b"data,descricao,valor\n"
        b"2026-04-01,Pagamento fornecedor,-100.00\n"
        b"2026-04-15,Recebimento cliente,200.00\n"
    )


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Extrato"
    ws.append(["Data", "Descricao", "Valor"])
    ws.append(["2026-04-01", "Pagamento", "-100.00"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xls_bytes() -> bytes:
    """Cabeçalho OLE Compound Document — magic bytes do XLS."""
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200


# ----------------------------------------------------------------------
# RBAC + Auth
# ----------------------------------------------------------------------


class TestParseRBAC:
    async def test_unauthenticated_returns_401(
        self,
        client_with_db: AsyncClient,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": "00000000-0000-0000-0000-000000000001"},
            files={"file": ("x.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 401

    async def test_admin_can_parse_any_client(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["bank_name"] == "Sicredi"

    async def test_manager_in_portfolio_can_parse(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="X", creator=admin, manager=mgr)
        await _login_as(client_with_db, MANAGER_A_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 200, resp.text

    async def test_manager_outside_portfolio_returns_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente_a = await _seed_client(
            db_session, name="Da carteira de A", creator=admin, manager=mgr_a
        )
        await _login_as(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente_a.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "NOT_FOUND"

    async def test_inexistent_client_returns_404(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": "00000000-0000-0000-0000-000000000999"},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# Validações de upload
# ----------------------------------------------------------------------


class TestParseValidation:
    async def test_oversized_file_returns_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        """20 MB + 1 byte: limite é exclusivo, deve ser rejeitado."""
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        max_bytes = get_settings().max_upload_bytes
        big = b"%PDF-1.7\n" + b"x" * (max_bytes + 1)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("big.pdf", big, "application/pdf")},
        )
        assert resp.status_code == 400
        assert "limite" in resp.json()["error"]["userMessage"].lower()

    async def test_disallowed_extension_returns_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("malicious.exe", b"%PDF-1.7\n" + b"x" * 50, "application/pdf")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_magic_bytes_mismatch_returns_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        """`.pdf` no nome mas conteúdo aleatório binário → magic bytes barram."""
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        # 200 bytes pseudo-aleatórios, sem nenhuma assinatura conhecida.
        evil = bytes((i * 13 + 7) % 256 for i in range(200))
        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("fake.pdf", evil, "application/pdf")},
        )
        assert resp.status_code == 400
        assert "Magic bytes" in resp.json()["error"]["message"]

    async def test_xls_explicitly_rejected(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        """XLS antigo não é suportado por enquanto (xlrd não está nas deps)."""
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("old.xls", _xls_bytes(), "application/vnd.ms-excel")},
        )
        assert resp.status_code == 400
        assert ".xls" in resp.json()["error"]["userMessage"].lower()

    async def test_empty_file_returns_400(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        assert resp.status_code == 400


# ----------------------------------------------------------------------
# Integração com a IA (mockada)
# ----------------------------------------------------------------------


class TestParseIntegration:
    async def test_pdf_happy_path(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["bank_name"] == "Sicredi"
        assert data["account_type"] == "checking"
        assert data["period_start"] == "2026-04-01"
        assert len(data["transactions"]) == 2

    async def test_signs_preserved(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 200
        txs = resp.json()["data"]["transactions"]
        # Pagamento → débito → negativo. Recebimento → crédito → positivo.
        assert float(txs[0]["amount"]) < 0
        assert float(txs[1]["amount"]) > 0

    async def test_pdf_de_varias_paginas_e_dividido_por_paginas(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        """86e3ff8xd — 6 páginas: 1 identificação + ceil(6/2) = 3 blocos, juntados."""
        fake = _FakeAnthropicByTool(side_effect=_ok_message())
        override_anthropic["fake"] = fake
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _multipage_pdf_bytes(6), "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        tools = [call["tool_choice"]["name"] for call in fake.messages.calls]
        assert tools.count(IDENTIFY_DOCUMENT_TOOL_NAME) == 1
        assert tools.count(EXTRACT_MOVEMENTS_TOOL_NAME) == 3
        assert tools[0] == IDENTIFY_DOCUMENT_TOOL_NAME  # identificação ANTES dos blocos
        extract_prompts = [
            call["messages"][0]["content"][-1]["text"]
            for call in fake.messages.calls
            if call["tool_choice"]["name"] == EXTRACT_MOVEMENTS_TOOL_NAME
        ]
        assert sorted("bloco 1 de 3" in text for text in extract_prompts) == [False, False, True]
        assert all('banco "Banco do Brasil"' in text for text in extract_prompts)
        data = resp.json()["data"]
        assert data["bank_name"] == "Banco do Brasil"  # da identificação, não do bloco
        assert data["account_type"] == "checking"
        assert len(data["transactions"]) == 3 * 2  # 2 por bloco, 3 blocos

    async def test_pdf_acima_do_maximo_de_paginas_e_recusado_sem_chamar_a_ia(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        fake = _FakeAnthropicByTool(side_effect=_ok_message())
        override_anthropic["fake"] = fake
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _multipage_pdf_bytes(31), "application/pdf")},
        )
        assert resp.status_code == 400, resp.text
        error = resp.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "31 páginas" in error["userMessage"]
        assert "mesma conciliação" in error["userMessage"]
        assert error["details"] == {"pages": 31, "maxPages": 30}
        assert fake.messages.calls == []

    async def test_csv_happy_path(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.csv", _csv_bytes(), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        # CSV deve ter virado um text block
        msg_call = override_anthropic["fake"].messages.calls[0]
        blocks = msg_call["messages"][0]["content"]
        assert blocks[0]["type"] == "text"
        assert "Pagamento" in blocks[0]["text"]

    async def test_xlsx_happy_path(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={
                "file": (
                    "extrato.xlsx",
                    _xlsx_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert resp.status_code == 200, resp.text
        # Renderização TSV foi enviada como text block
        msg_call = override_anthropic["fake"].messages.calls[0]
        blocks = msg_call["messages"][0]["content"]
        assert blocks[0]["type"] == "text"
        assert "Pagamento" in blocks[0]["text"]

    async def test_no_tool_use_returns_422(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        """Modelo respondeu free-text — handler global converte em PARSE_ERROR (422)."""

        class _TextOnly:
            def __init__(self) -> None:
                self.type = "text"
                self.text = "não consegui ler"

        override_anthropic["fake"] = _FakeAnthropic(side_effect=_Message([_TextOnly()]))
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PARSE_ERROR"

    async def test_timeout_returns_504(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        import httpx
        from anthropic import APITimeoutError

        override_anthropic["fake"] = _FakeAnthropic(
            side_effect=APITimeoutError(
                httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            )
        )
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 504
        assert resp.json()["error"]["code"] == "ANTHROPIC_TIMEOUT"

    async def test_auth_fault_returns_502_with_generic_message(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        """Auth fault: nunca expor `userMessage` técnico — sempre genérico."""
        import httpx
        from anthropic import AuthenticationError

        override_anthropic["fake"] = _FakeAnthropic(
            side_effect=AuthenticationError(
                message="invalid x-api-key",
                response=httpx.Response(
                    401,
                    request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
                ),
                body={"error": {"type": "authentication_error"}},
            )
        )
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", _minimal_pdf_bytes(), "application/pdf")},
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body["error"]["code"] == "ANTHROPIC_AUTH_ERROR"
        # Mensagem ao usuário não menciona "API key" — defesa em profundidade.
        assert "key" not in body["error"]["userMessage"].lower()


async def _seed_session(
    db: AsyncSession,
    *,
    client_id: object,
    created_by: object,
    file_hash: str,
    status: str,
) -> ReconciliationSession:
    """Seed de uma ReconciliationSession com hash/status dados (BACK 02.6).

    Sprint 4 (BACK 04.2): o hash desceu para `reconciliation_files` — o dedup do
    `/parse` procura lá. Semear só a sessão deixaria de reproduzir o cenário.
    """
    sess = ReconciliationSession(
        client_id=client_id,
        created_by=created_by,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=3,
        file_hash=None,
        status=status,
    )
    db.add(sess)
    await db.flush()
    db.add(
        ReconciliationFile(
            session_id=sess.id,
            file_hash=file_hash,
            status=ReconciliationFileStatus.PARSED.value,
        )
    )
    await db.flush()
    return sess


@pytest.mark.integration
class TestParseDuplicate:
    """BACK 02.6 — dedup DENTRO do /parse, ANTES da IA (zero chamadas)."""

    async def test_duplicate_blocked_before_ai_call(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        fake = _FakeAnthropic(side_effect=_ok_message())
        override_anthropic["fake"] = fake
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        content = _minimal_pdf_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        await _seed_session(
            db_session,
            client_id=cliente.id,
            created_by=admin.id,
            file_hash=file_hash,
            status=ReconciliationStatus.REVIEWING.value,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", content, "application/pdf")},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "DUPLICATE_FILE"
        assert "importado" in resp.json()["error"]["userMessage"].lower()
        # ZERO chamadas à Anthropic — o freio é ANTES da IA.
        assert fake.messages.calls == []

    async def test_error_session_allows_reimport(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        fake = _FakeAnthropic(side_effect=_ok_message())
        override_anthropic["fake"] = fake
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        content = _minimal_pdf_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        # Sessão anterior em ERROR → reimportar é permitido.
        await _seed_session(
            db_session,
            client_id=cliente.id,
            created_by=admin.id,
            file_hash=file_hash,
            status=ReconciliationStatus.ERROR.value,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("extrato.pdf", content, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        # IA FOI chamada (reimport permitido) — 1 chamada.
        assert len(fake.messages.calls) == 1

    async def test_same_content_other_client_is_not_duplicate(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        # Mesmo arquivo em cliente DIFERENTE é legítimo (fora de escopo do dedup).
        fake = _FakeAnthropic(side_effect=_ok_message())
        override_anthropic["fake"] = fake
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente_a = await _seed_client(db_session, name="A", creator=admin)
        cliente_b = await _seed_client(db_session, name="B", creator=admin)
        content = _minimal_pdf_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
        await _seed_session(
            db_session,
            client_id=cliente_a.id,
            created_by=admin.id,
            file_hash=file_hash,
            status=ReconciliationStatus.DONE.value,
        )
        await _login_as(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente_b.id)},
            files={"file": ("extrato.pdf", content, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        assert len(fake.messages.calls) == 1


@pytest.mark.integration
class TestParseSizeNonRegression:
    """BACK 02.8 — o teto PERMANECE 20 MB: um arquivo de 14 MB continua
    conciliando (não pode passar a receber erro)."""

    async def test_14mb_file_still_parses(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        override_anthropic: dict[str, Any],
    ) -> None:
        override_anthropic["fake"] = _FakeAnthropic(side_effect=_ok_message())
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="X", creator=admin)
        await _login_as(client_with_db, ADMIN_EMAIL)

        # 14 MB — bem abaixo do teto de 20 MB.
        big_ok = b"%PDF-1.7\n" + b"x" * (14 * 1024 * 1024)
        assert len(big_ok) < get_settings().max_upload_bytes

        resp = await client_with_db.post(
            "/api/v1/reconciliations/parse",
            data={"client_id": str(cliente.id)},
            files={"file": ("grande.pdf", big_ok, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["bank_name"] == "Sicredi"
