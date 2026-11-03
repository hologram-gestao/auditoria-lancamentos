"""Importação do plano contábil por CONTEÚDO, pela API e contra Postgres (épico 86e3ydjy9).

    POST /api/v1/clients/{client_id}/accounting-chart/import/preview
    POST /api/v1/clients/{client_id}/accounting-chart/import

Sub 1 (86e3ydjyq): a fixture anonimizada da Tract (fora do modelo e do export do
Domínio) entra pela prévia e pela importação com 1850 contas novas, a lista sai na ordem
da classificação, e o log leva só contagens (papéis reconhecidos, colunas ignoradas),
nunca um nome de conta. O cross-tenant e o cross-org das duas rotas rodam na bateria
dos três atacantes (`test_sensitive_endpoints.py`).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from structlog.testing import capture_logs

from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAccountingAccount,
    ClientAssignment,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@PlanoConteudo#1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_TRACT = _FIXTURES / "accounting_chart_tract" / "plano.xlsx"
_TRACT_EXPECTED = _FIXTURES / "accounting_chart_tract" / "plano_esperado.csv"
_XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _seed_user(session: AsyncSession, *, role: UserRole, client_id: Any = None) -> User:
    scope = UserScope.CLIENT if client_id is not None else UserScope.SYSTEM
    user = User(
        name="Plano por Conteudo",
        email=f"conteudo-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


class World:
    admin: User
    client: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente do Plano por Conteudo", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    await db_session.flush()
    return w


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/accounting-chart"


async def _post_xlsx(
    http: AsyncClient, url: str, content: bytes, data: dict[str, str] | None = None
) -> Response:
    return await http.post(
        url, files={"file": ("plano.xlsx", content, _XLSX_TYPE)}, data=data or {}
    )


def _xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _expected_codes() -> list[str]:
    with _TRACT_EXPECTED.open(encoding="utf-8", newline="") as fh:
        return [r["codigo_reduzido"] for r in csv.DictReader(fh, delimiter=";")]


def _no_account_name(logs: list[dict[str, Any]]) -> None:
    rendered = repr(logs)
    assert "de exemplo" not in rendered
    assert "ATIVO" not in rendered


class TestTractPeloConteudo:
    async def test_previa_mostra_1850_novas_sem_gravar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        with capture_logs() as logs:
            resp = await _post_xlsx(
                client_with_db, f"{_url(world.client)}/import/preview", _TRACT.read_bytes()
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["layout"] == "reconhecido"
        assert data["contas"] == 1850
        assert data["contasNovas"] == 1850
        previewed = [e for e in logs if e.get("event") == "accounting_chart_import_previewed"]
        assert previewed[0]["recognized_roles"] == [
            "codigo",
            "classificacao",
            "tipo",
            "grau",
            "nome",
        ]
        assert previewed[0]["ignored_columns"] == 2
        _no_account_name(logs)
        stored = await db_session.execute(
            select(ClientAccountingAccount).where(
                ClientAccountingAccount.client_id == world.client.id
            )
        )
        assert stored.scalars().first() is None

    async def test_importa_1850_e_a_lista_sai_na_ordem_da_classificacao(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        with capture_logs() as logs:
            resp = await _post_xlsx(
                client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes()
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["contasNovas"] == 1850
        _no_account_name(logs)

        codes: list[str] = []
        page = 1
        while True:
            listing = await client_with_db.get(
                _url(world.client), params={"pageSize": 100, "page": page}
            )
            assert listing.status_code == 200, listing.text
            body = listing.json()
            codes += [row["code"] for row in body["data"]]
            if page >= body["pagination"]["totalPages"]:
                break
            page += 1
        assert codes == _expected_codes()
        first = (await client_with_db.get(_url(world.client), params={"pageSize": 1})).json()
        assert first["data"][0]["classification"] == "1"

    async def test_so_codigo_e_nome_recusa_dizendo_o_que_falta(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _xlsx([["Código", "Nome"], [1, "ATIVO"], [2, "CAIXA"], [3, "BANCO"]])
        with capture_logs() as logs:
            resp = await _post_xlsx(client_with_db, f"{_url(world.client)}/import", content)
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "CABECALHO_DIVERGENTE"
        assert error["details"]["recognizedRoles"] == ["codigo", "nome"]
        assert error["details"]["missingRoles"] == ["tipo"]
        assert "não encontramos a coluna de tipo (S ou A)" in error["userMessage"]
        assert "ATIVO" not in resp.text
        assert "CAIXA" not in resp.text
        _no_account_name(logs)
