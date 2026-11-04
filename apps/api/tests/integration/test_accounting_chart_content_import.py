"""Importação do plano contábil por CONTEÚDO, pela API e contra Postgres (épico 86e3ydjy9).

    POST /api/v1/clients/{client_id}/accounting-chart/import/preview
    POST /api/v1/clients/{client_id}/accounting-chart/import

Sub 5 (86e3ydk0c): o plano da Tract exportado do Domínio em `.xls` (regravado pelo Excel)
entra inteiro pelo caminho do Domínio, 150 contas, sem linha com problema; importado DEPOIS
do plano ajustado, não cria conta nova e marca as 1700 que o recorte não traz como ausentes
(sem inativar nada, a regra da reimportação).

Sub 4 (86e3ydjzy): a aba "Fornecedores Ajustados" da Tract (sem título do Domínio) pede
o tipo, entra com `tipoVinculo` sem conferir a empresa, recusa nomeando a conta que não
está no plano; e o plano importado com `vincularDocumentos` vincula as contas pela coluna
de CNPJ/CPF da própria planilha, com a mesma política da relação.

Sub 3 (86e3ydjze): linha com problema não derruba mais o arquivo: a prévia a lista, a
importação só grava sem ela com a MESMA lista confirmada (`linhasExcluidas`), o registro
da importação alimenta `ultimaImportacao` na lista (o selo da tela), some quando o arquivo
correto entra, e sai no encerramento e na exclusão definitiva.

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
from sqlalchemy.exc import IntegrityError
from structlog.testing import capture_logs

from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAccountingAccount,
    ClientAccountingChartImport,
    ClientAssignment,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_accounting_chart.sheet import parse_chart_sheet

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@PlanoConteudo#1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_TRACT = _FIXTURES / "accounting_chart_tract" / "plano.xlsx"
_TRACT_EXPECTED = _FIXTURES / "accounting_chart_tract" / "plano_esperado.csv"
_TRACT_DOMINIO = _FIXTURES / "accounting_chart_dominio_tract" / "plano_dominio.xls"
_TRACT_DOMINIO_EXPECTED = _FIXTURES / "accounting_chart_dominio_tract" / "plano_esperado.csv"
_XLS_TYPE = "application/vnd.ms-excel"
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
    tenant_manager: User


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
    w.tenant_manager = await _seed_user(
        db_session, role=UserRole.CLIENT_MANAGER, client_id=w.client.id
    )
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


async def _post_xls(http: AsyncClient, url: str, content: bytes) -> Response:
    return await http.post(url, files={"file": ("plano.xls", content, _XLS_TYPE)})


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


async def _all_codes(http: AsyncClient, client: Client) -> list[str]:
    codes: list[str] = []
    page = 1
    while True:
        listing = await http.get(_url(client), params={"pageSize": 100, "page": page})
        assert listing.status_code == 200, listing.text
        body = listing.json()
        codes += [row["code"] for row in body["data"]]
        if page >= body["pagination"]["totalPages"]:
            return codes
        page += 1


class TestExportCruDaTract:
    async def test_previa_e_importacao_entram_inteiras_pelo_dominio(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _TRACT_DOMINIO.read_bytes()
        with capture_logs() as logs:
            preview = await _post_xls(
                client_with_db, f"{_url(world.client)}/import/preview", content
            )
            resp = await _post_xls(client_with_db, f"{_url(world.client)}/import", content)
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert (data["layout"], data["contas"], data["contasNovas"]) == ("dominio", 150, 150)
        assert data["linhasComProblema"] == []
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["contasNovas"] == 150
        _no_account_name(logs)

        with _TRACT_DOMINIO_EXPECTED.open(encoding="utf-8", newline="") as fh:
            expected = [r["codigo_reduzido"] for r in csv.DictReader(fh, delimiter=";")]
        assert await _all_codes(client_with_db, world.client) == expected

    async def test_depois_do_ajustado_nada_e_novo_e_o_resto_fica_ausente_sem_inativar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        adjusted = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes()
        )
        assert adjusted.status_code == 200, adjusted.text
        inactive_before = await _inactive_count(db_session, world.client.id)

        resp = await _post_xls(
            client_with_db, f"{_url(world.client)}/import", _TRACT_DOMINIO.read_bytes()
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert (data["contasNovas"], data["contasAusentes"]) == (0, 1700)

        stored = (
            await db_session.execute(
                select(ClientAccountingAccount)
                .where(ClientAccountingAccount.client_id == world.client.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
        accounts = list(stored)
        assert len(accounts) == 1850
        assert sum(1 for a in accounts if a.absent_since is not None) == 1700
        assert await _inactive_count(db_session, world.client.id) == inactive_before


async def _inactive_count(db: AsyncSession, client_id: Any) -> int:
    rows = await db.execute(
        select(ClientAccountingAccount)
        .where(
            ClientAccountingAccount.client_id == client_id,
            ClientAccountingAccount.active.is_(False),
        )
        .execution_options(populate_existing=True)
    )
    return len(list(rows.scalars()))


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


def _tract_with(**cells: Any) -> bytes:
    wb = load_workbook(io.BytesIO(_TRACT.read_bytes()))
    ws = wb.worksheets[0]
    for ref, value in cells.items():
        ws[ref] = value
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _imports(db: AsyncSession, client_id: Any) -> list[ClientAccountingChartImport]:
    stmt = (
        select(ClientAccountingChartImport)
        .where(ClientAccountingChartImport.client_id == client_id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _accounts_count(db: AsyncSession, client_id: Any) -> int:
    stmt = select(ClientAccountingAccount).where(ClientAccountingAccount.client_id == client_id)
    return len((await db.execute(stmt)).scalars().all())


_TWO_BROKEN = {"A10": None, "C12": "X"}


class TestLinhasDeFora:
    async def test_previa_lista_as_linhas_e_a_importacao_exige_a_mesma_lista(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _tract_with(**_TWO_BROKEN)
        preview = await _post_xlsx(client_with_db, f"{_url(world.client)}/import/preview", content)
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["linhasComProblema"] == [
            {"linha": 10, "motivo": "codigo_vazio"},
            {"linha": 12, "motivo": "tipo_invalido"},
        ]
        assert data["contas"] == 1848

        # Sem o campo: a recusa de sempre, nada gravado.
        sem = await _post_xlsx(client_with_db, f"{_url(world.client)}/import", content)
        assert sem.status_code == 422, sem.text
        assert sem.json()["error"]["code"] == "LINHAS_INVALIDAS"
        # Lista diferente da calculada: recusa, nada gravado.
        outra = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import", content, {"linhasExcluidas": "[10]"}
        )
        assert outra.status_code == 422, outra.text
        assert "não são as que você confirmou" in outra.json()["error"]["userMessage"]
        assert await _accounts_count(db_session, world.client.id) == 0
        assert await _imports(db_session, world.client.id) == []

        with capture_logs() as logs:
            ok = await _post_xlsx(
                client_with_db,
                f"{_url(world.client)}/import",
                content,
                {"linhasExcluidas": "[12, 10]"},
            )
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["contasNovas"] == 1848
        assert ok.json()["data"]["contasExcluidas"] == 2
        _no_account_name(logs)
        [record] = await _imports(db_session, world.client.id)
        assert record.excluded_count == 2
        assert record.excluded_lines == [
            {"linha": 10, "motivo": "codigo_vazio"},
            {"linha": 12, "motivo": "tipo_invalido"},
        ]
        assert record.imported_by == world.admin.id
        assert record.layout == "reconhecido"

        listing = await client_with_db.get(_url(world.client), params={"pageSize": 1})
        last = listing.json()["ultimaImportacao"]
        assert last["contas"] == 1848
        assert last["linhasExcluidas"] == [
            {"linha": 10, "motivo": "codigo_vazio"},
            {"linha": 12, "motivo": "tipo_invalido"},
        ]

        # O arquivo correto entra inteiro: a última importação não tem linha de fora.
        again = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes()
        )
        assert again.status_code == 200, again.text
        listing = await client_with_db.get(_url(world.client), params={"pageSize": 1})
        assert listing.json()["ultimaImportacao"]["linhasExcluidas"] == []

    async def test_sem_importacao_registrada_a_lista_nao_tem_selo(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        listing = await client_with_db.get(_url(world.client))
        assert listing.status_code == 200
        assert listing.json()["ultimaImportacao"] is None

    async def test_acima_de_20_por_cento_recusa_inteiro(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _tract_with(**{f"C{line}": "X" for line in range(6, 406)})
        # 400 tipos estranhos: a coluna de tipo nem é reconhecida (pede mapeamento) ...
        first = await _post_xlsx(client_with_db, f"{_url(world.client)}/import/preview", content)
        assert first.json()["error"]["code"] == "MAPEAMENTO_NECESSARIO"
        # ... e, mapeada, as 400 linhas passam do teto: o arquivo inteiro é recusado.
        mapping = json.dumps({"colunas": {"codigo": 0, "classificacao": 1, "tipo": 2, "nome": 5}})
        resp = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import/preview", content, {"mapeamento": mapping}
        )
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "LINHAS_INVALIDAS"
        assert error["details"]["total"] == 400
        assert await _accounts_count(db_session, world.client.id) == 0

    @pytest.mark.parametrize("raw", ["nao", "[0]", '["a"]', '{"x": 1}'])
    async def test_linhas_excluidas_com_forma_invalida_e_400(
        self, client_with_db: AsyncClient, world: World, raw: str
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/import",
            _tract_with(**_TWO_BROKEN),
            {"linhasExcluidas": raw},
        )
        assert resp.status_code == 400, resp.text

    async def test_encerramento_purga_o_registro(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _tract_with(**_TWO_BROKEN)
        ok = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import", content, {"linhasExcluidas": "[10, 12]"}
        )
        assert ok.status_code == 200, ok.text
        closed = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert closed.status_code == 204, closed.text
        assert await _imports(db_session, world.client.id) == []
        listing = await client_with_db.get(_url(world.client))
        assert listing.status_code == 200
        assert listing.json()["ultimaImportacao"] is None

    async def test_exclusao_definitiva_com_autor_do_tenant(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """`imported_by` RESTRICT: o registro sai ANTES dos usuários do tenant."""
        db_session.add(
            ClientAccountingChartImport(
                client_id=world.client.id,
                imported_by=world.tenant_manager.id,
                layout="modelo",
                accounts=1,
                new_accounts=1,
                updated_accounts=0,
                absent_accounts=0,
                excluded_count=1,
                excluded_lines=[{"linha": 3, "motivo": "nome_vazio"}],
            )
        )
        await db_session.flush()
        await _login(client_with_db, world.admin)
        resp = await client_with_db.delete(f"/api/v1/clients/{world.client.id}")
        assert resp.status_code == 204, resp.text
        assert await _imports(db_session, world.client.id) == []

    async def test_o_banco_recusa_contagem_que_nao_bate_com_a_lista(
        self, db_session: AsyncSession, world: World
    ) -> None:
        db_session.add(
            ClientAccountingChartImport(
                client_id=world.client.id,
                imported_by=world.admin.id,
                layout="modelo",
                accounts=1,
                new_accounts=1,
                updated_accounts=0,
                absent_accounts=0,
                excluded_count=2,
                excluded_lines=[{"linha": 3, "motivo": "nome_vazio"}],
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


def _tract_parties() -> bytes:
    wb = load_workbook(io.BytesIO(_TRACT.read_bytes()))
    del wb[wb.sheetnames[0]]
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def _linked(db: AsyncSession, client_id: Any) -> int:
    stmt = select(ClientAccountingAccount).where(
        ClientAccountingAccount.client_id == client_id,
        ClientAccountingAccount.party_document_encrypted.is_not(None),
    )
    return len((await db.execute(stmt)).scalars().all())


class TestRelacaoSemTitulo:
    async def test_pede_o_tipo_e_entra_conferindo_a_empresa(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        plan = await _post_xlsx(client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes())
        assert plan.status_code == 200, plan.text
        url = f"{_url(world.client)}/parties/import"

        sem = await _post_xlsx(client_with_db, url, _tract_parties())
        assert sem.status_code == 422, sem.text
        assert sem.json()["error"]["code"] == "TIPO_DE_VINCULO_NECESSARIO"

        # A conta 1260 da relação não existe no plano: a recusa nomeia o CÓDIGO.
        faltando = await _post_xlsx(
            client_with_db, url, _tract_parties(), {"tipoVinculo": "fornecedor"}
        )
        assert faltando.status_code == 422, faltando.text
        assert faltando.json()["error"]["details"]["lines"] == [
            {"line": 259, "reason": "conta_nao_encontrada", "accountCode": "1260"}
        ]
        assert await _linked(db_session, world.client.id) == 0

        created = await client_with_db.post(
            f"{_url(world.client)}/accounts",
            json={"code": "1260", "name": "Fornecedor incluído à mão", "type": "analitica"},
        )
        assert created.status_code == 201, created.text
        with capture_logs() as logs:
            ok = await _post_xlsx(
                client_with_db, url, _tract_parties(), {"tipoVinculo": "fornecedor"}
            )
        assert ok.status_code == 200, ok.text
        data = ok.json()["data"]
        assert data["relatorio"] == "fornecedor"
        assert data["contasVinculadas"] == 275
        # O banner da fixture traz o CNPJ da empresa na mesma célula do rótulo: conferida.
        assert data["empresaConferida"] is True
        assert await _linked(db_session, world.client.id) == 275
        assert "Fornecedor de exemplo" not in repr(logs)
        assert "Fornecedor de exemplo" not in ok.text

    async def test_tipo_que_contradiz_o_titulo_e_recusado(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        wb = load_workbook(io.BytesIO(_tract_parties()))
        wb.worksheets[0]["A3"] = "RELAÇÃO DE CLIENTES"
        buffer = io.BytesIO()
        wb.save(buffer)
        resp = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/parties/import",
            buffer.getvalue(),
            {"tipoVinculo": "fornecedor"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "TIPO_DE_VINCULO_DIVERGENTE"


class TestVinculoPelaColunaDoPlano:
    async def test_previa_conta_os_documentos_e_a_importacao_vincula_por_escolha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        preview = await _post_xlsx(
            client_with_db, f"{_url(world.client)}/import/preview", _TRACT.read_bytes()
        )
        assert preview.json()["data"]["documentosEncontrados"] == 252

        sem = await _post_xlsx(client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes())
        assert sem.status_code == 200, sem.text
        assert sem.json()["data"].get("vinculos") is None
        assert await _linked(db_session, world.client.id) == 0

        with capture_logs() as logs:
            com = await _post_xlsx(
                client_with_db,
                f"{_url(world.client)}/import",
                _TRACT.read_bytes(),
                {"vincularDocumentos": json.dumps({"tipo": "fornecedor"})},
            )
        assert com.status_code == 200, com.text
        assert "vinculos" in com.json()["data"], com.text
        links = com.json()["data"]["vinculos"]
        assert links["relatorio"] == "fornecedor"
        skipped = {item["reason"] for item in links["naoVinculadas"]}
        assert "documento_ausente" in skipped
        assert links["contasVinculadas"] + links["naoVinculadasTotal"] == 263
        assert await _linked(db_session, world.client.id) == links["contasVinculadas"]
        assert links["contasVinculadas"] > 200
        assert "Conta de exemplo" not in repr(logs)

        # A lista de fornecedores mostra só a máscara.
        parties = await client_with_db.get(
            f"{_url(world.client)}/parties", params={"type": "fornecedor", "pageSize": 5}
        )
        assert parties.status_code == 200, parties.text
        assert all("*" in row["documentMasked"] for row in parties.json()["data"])

    @pytest.mark.parametrize(
        "raw", ["fornecedor", '{"tipo": "socio"}', '{"tipo": "cliente", "x": 1}']
    )
    async def test_forma_invalida_e_400(
        self, client_with_db: AsyncClient, world: World, raw: str
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/import",
            _TRACT.read_bytes(),
            {"vincularDocumentos": raw},
        )
        assert resp.status_code == 400, resp.text


class TestAchadosDaRevisaoNaApi:
    async def test_conta_que_virou_sintetica_na_mesma_importacao_nao_vincula(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O tipo do vínculo pelo plano vem da planilha gravada, nunca do objeto antigo."""
        await _login(client_with_db, world.admin)
        assert (
            await _post_xlsx(client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes())
        ).status_code == 200
        parsed_docs = [
            (line, code)
            for line, code, raw in parse_chart_sheet(_TRACT.read_bytes()).documents
            if str(raw) != "0"
        ]
        line, code = parsed_docs[0]
        # A conta com documento vira sintética na planilha seguinte (coluna C = tipo).
        content = _tract_with(**{f"C{line}": "S"})
        resp = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/import",
            content,
            {"vincularDocumentos": json.dumps({"tipo": "fornecedor"})},
        )
        assert resp.status_code == 200, resp.text
        skipped = resp.json()["data"]["vinculos"]["naoVinculadas"]
        assert {"line": line, "reason": "conta_sintetica", "accountCode": code} in skipped
        stmt = (
            select(ClientAccountingAccount)
            .where(
                ClientAccountingAccount.client_id == world.client.id,
                ClientAccountingAccount.code == code,
            )
            .execution_options(populate_existing=True)
        )
        account = (await db_session.execute(stmt)).scalar_one()
        assert account.account_type == "sintetica"
        assert account.party_document_encrypted is None

    async def test_relacao_sem_o_rotulo_do_cnpj_entra_sem_conferir(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _post_xlsx(client_with_db, f"{_url(world.client)}/import", _TRACT.read_bytes())
        await client_with_db.post(
            f"{_url(world.client)}/accounts",
            json={"code": "1260", "name": "Fornecedor incluído à mão", "type": "analitica"},
        )
        wb = load_workbook(io.BytesIO(_tract_parties()))
        wb.worksheets[0]["A2"] = None
        buffer = io.BytesIO()
        wb.save(buffer)
        resp = await _post_xlsx(
            client_with_db,
            f"{_url(world.client)}/parties/import",
            buffer.getvalue(),
            {"tipoVinculo": "fornecedor"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["empresaConferida"] is False
