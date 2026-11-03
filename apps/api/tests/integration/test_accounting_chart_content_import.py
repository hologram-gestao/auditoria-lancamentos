"""Importação do plano contábil por CONTEÚDO, pela API e contra Postgres (épico 86e3ydjy9).

    POST /api/v1/clients/{client_id}/accounting-chart/import/preview
    POST /api/v1/clients/{client_id}/accounting-chart/import

Sub 2 (86e3ydjz4): o bloco que o conteúdo não decide responde 422
`MAPEAMENTO_NECESSARIO` com o perfil das colunas (o cabeçalho sai, valor de célula
nunca), e a prévia e a importação com o campo `mapeamento` leem a planilha pela escolha
da pessoa (layout `mapeado`; o tipo pela hierarquia só por escolha, com a contagem).

Sub 1 (86e3ydjyq): a fixture anonimizada da Tract (fora do modelo e do export do
Domínio) entra pela prévia e pela importação com 1850 contas novas, a lista sai na ordem
da classificação, e o log leva só contagens (papéis reconhecidos, colunas ignoradas),
nunca um nome de conta. O cross-tenant e o cross-org das duas rotas rodam na bateria
dos três atacantes (`test_sensitive_endpoints.py`).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
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

    async def test_so_codigo_e_nome_pede_mapeamento_dizendo_o_que_falta(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _xlsx([["Código", "Nome"], [1, "ATIVO"], [2, "CAIXA"], [3, "BANCO"]])
        with capture_logs() as logs:
            resp = await _post_xlsx(client_with_db, f"{_url(world.client)}/import", content)
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "MAPEAMENTO_NECESSARIO"
        assert error["details"]["recognizedRoles"] == ["codigo", "nome"]
        assert error["details"]["missingRoles"] == ["tipo"]
        assert "não encontramos a coluna de tipo (S ou A)" in error["userMessage"]
        assert "ATIVO" not in resp.text
        assert "CAIXA" not in resp.text
        _no_account_name(logs)


def _tract_without_type() -> bytes:
    """A fixture da Tract sem a coluna de tipo: o conteúdo não decide o tipo."""
    wb = load_workbook(io.BytesIO(_TRACT.read_bytes()))
    wb.worksheets[0].delete_cols(3)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


_HIERARCHY = json.dumps(
    {
        "colunas": {"codigo": 0, "classificacao": 1, "grau": 2, "nome": 4},
        "tipoPelaHierarquia": True,
    }
)


class TestMapeamento:
    async def test_previa_pede_mapeamento_com_o_cabecalho_e_sem_celula(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        with capture_logs() as logs:
            resp = await _post_xlsx(
                client_with_db, f"{_url(world.client)}/import/preview", _tract_without_type()
            )
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "MAPEAMENTO_NECESSARIO"
        columns = error["details"]["colunas"]
        assert [c["cabecalho"] for c in columns[:5]] == [
            "Código",
            "Classificação",
            "Grau",
            "Natureza",
            "Nome da conta",
        ]
        assert columns[4]["papelSugerido"] == "nome"
        assert error["details"]["missingRoles"] == ["tipo"]
        # Nenhum valor de célula do bloco: nem nome de conta, nem documento, nem banner.
        assert "de exemplo" not in resp.text
        assert "EXEMPLO" not in resp.text
        assert "Devedora" not in resp.text
        _no_account_name(logs)

    async def test_previa_e_importacao_pelo_mapeamento_com_tipo_pela_hierarquia(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _tract_without_type()
        preview = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/import/preview",
            content,
            {"mapeamento": _HIERARCHY},
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["layout"] == "mapeado"
        assert data["contasNovas"] == 1850
        assert data["tipoDerivado"] == {"sinteticas": 152, "analiticas": 1698}
        with capture_logs() as logs:
            imported = await _post_xlsx(
                client_with_db, f"{_url(world.client)}/import", content, {"mapeamento": _HIERARCHY}
            )
        assert imported.status_code == 200, imported.text
        assert imported.json()["data"]["contasNovas"] == 1850
        _no_account_name(logs)

    async def test_sem_escolha_o_tipo_nunca_e_derivado(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        preview = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import/preview", _TRACT.read_bytes()
        )
        assert preview.json()["data"]["tipoDerivado"] is None

    @pytest.mark.parametrize(
        "raw",
        [
            "não é json",
            json.dumps({"colunas": {"codigo": 0, "nome": 0, "tipo": 1}}),
            json.dumps({"colunas": {"codigo": 0, "nome": 4}}),
            json.dumps({"colunas": {"codigo": 0, "nome": 40, "tipo": 1}}),
        ],
    )
    async def test_mapeamento_com_forma_invalida_e_400(
        self, client_with_db: AsyncClient, world: World, raw: str
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/import/preview",
            _tract_without_type(),
            {"mapeamento": raw},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
