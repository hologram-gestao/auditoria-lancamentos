"""A planilha do plano de contas da Omie e a natureza da conta, pela API e contra Postgres
(86e3qhhrr).

    GET /api/v1/clients/{client_id}/accounting-chart/omie-export          → XLSX
    GET /api/v1/clients/{client_id}/accounting-chart/omie-export/preview  → contagens

O que este módulo afirma (critérios de aceite da subtask 4):
  - o plano importado com as DUAS relações anonimizadas da Hologram sai na planilha, e as
    contas 685 e 918 levam o documento COM máscara na coluna `CNPJ do Estabelecimento`;
  - o download é o modelo da Omie preenchido (as listas de opções intactas), grava 1
    linha `export` na trilha e o evento `planilha_omie_gerada` só com contagens;
  - conta sem classificação (e sem natureza) bloqueia com 409 nomeando o CÓDIGO; nada é
    gerado, e nem a trilha nem o evento ganham linha;
  - `client_manager` e `client_operator` recebem 403 com linha `denied`; cliente
    encerrado 409;
  - a prévia conta vinculadas e quantos documentos não têm cadastro no Omie (o mock);
    cliente sem Omie devolve `checked=false` com o motivo, nunca erro;
  - a natureza: derivada na importação e na conta manual, escolhida à mão na edição e
    respeitada pela reimportação, `null` volta ao automático; o backfill da migration em
    SQL dá o mesmo que a função Python.

O cross-tenant e o cross-org das duas rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import re
import warnings
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import openpyxl
import pytest
from sqlalchemy import select, text
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAccountingAccount,
    ClientAssignment,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie import mock_client
from app.integrations.omie.mock_client import (
    _MOCK_FORNECEDORES_SEED,
    FAKE_DEMO_KEY_PREFIX,
    reset_mock_payables_state,
)
from app.main import app as fastapi_app
from app.modules.client_accounting_chart.nature import derive_nature
from app.modules.client_accounting_chart.omie_sheet import SHEET_PART, TEMPLATE_PATH
from tests.parties_builder import VALID_CNPJS, VALID_CPFS, build_parties_xlsx

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@PlanilhaOmie#1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "accounting_chart_dominio_parties"
_CLIENTS = _FIXTURES / "relacao_clientes.xls"
_SUPPLIERS = _FIXTURES / "relacao_fornecedores.xls"
_EXPECTED = _FIXTURES / "vinculos_esperados.csv"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


@pytest.fixture(autouse=True)
def _fast_mock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(mock_client, "_DELAY_LISTAR_CONTAS_SECONDS", 0.0)
    reset_mock_payables_state()
    yield
    reset_mock_payables_state()


def _expected_links() -> list[dict[str, str]]:
    with _EXPECTED.open(encoding="utf-8", newline="") as fh:
        return [r for r in csv.DictReader(fh, delimiter=";") if r["situacao"] == "vinculavel"]


def _hologram_plan_csv() -> bytes:
    """Um plano com classificação para TODAS as contas que as relações citam.

    Clientes no grupo 1 (devedora), fornecedores no grupo 2 (credora), mais as duas
    sintéticas que os agrupam: é o plano que a importação do Domínio deixaria.
    """
    lines = [
        "codigo_reduzido;nome;tipo;classificacao",
        "1;Ativo;sintetica;1",
        "2;Passivo;sintetica;2",
    ]
    seen: set[str] = set()
    for row in _expected_links():
        code = row["conta"]
        if code in seen:
            continue
        seen.add(code)
        group = "1.1.2" if row["relatorio"] == "cliente" else "2.1.1"
        lines.append(f"{code};Conta de exemplo {code};analitica;{group}.{int(code):04d}")
    return ("\n".join(lines) + "\n").encode("utf-8")


async def _seed_user(
    session: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Planilha Omie",
        email=f"planilha-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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
    manager: User
    tenant_manager: User
    tenant_operator: User
    client: Client
    omie_client: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente da Planilha", active=True, created_by=w.admin.id)
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(f"{FAKE_DEMO_KEY_PREFIX}{uuid4().hex[:8]}", hex_key)
    ct_s, iv_s = encrypt("demo-secret", hex_key)
    w.omie_client = Client(
        name="Cliente com Omie",
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=w.admin.id,
    )
    db_session.add_all([w.client, w.omie_client])
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    w.tenant_manager = await _seed_user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.tenant_operator = await _seed_user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
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


async def _import_plan(http: AsyncClient, client: Client, content: bytes) -> None:
    resp = await http.post(
        f"{_url(client)}/import", files={"file": ("plano.csv", content, "text/csv")}
    )
    assert resp.status_code == 200, resp.text


async def _import_parties(http: AsyncClient, client: Client, content: bytes) -> Response:
    resp = await http.post(
        f"{_url(client)}/parties/import", files={"file": ("relacao.xls", content, _XLSX)}
    )
    assert resp.status_code == 200, resp.text
    return resp


def _rows(content: bytes) -> dict[str, list[str]]:
    """As linhas preenchidas da planilha, por código reduzido (coluna C)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
    out: dict[str, list[str]] = {}
    for row in workbook["Omie_Plano_Contas"].iter_rows(
        min_row=6, min_col=2, max_col=10, values_only=True
    ):
        if all(value is None for value in row):
            break
        values = ["" if value is None else str(value) for value in row]
        out[values[1]] = values
    workbook.close()
    return out


async def _audit(db: AsyncSession, client_id: Any, action: str) -> list[AccessAudit]:
    stmt = select(AccessAudit).where(
        AccessAudit.action == action, AccessAudit.client_id == client_id
    )
    return list((await db.execute(stmt)).scalars().all())


async def _events(db: AsyncSession) -> list[UsageEvent]:
    stmt = select(UsageEvent).where(UsageEvent.event == "planilha_omie_gerada")
    return list((await db.execute(stmt)).scalars().all())


async def _account(db: AsyncSession, client_id: Any, code: str) -> ClientAccountingAccount:
    stmt = (
        select(ClientAccountingAccount)
        .where(ClientAccountingAccount.client_id == client_id, ClientAccountingAccount.code == code)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one()


class TestAPlanilhaDaHologram:
    async def test_685_e_918_saem_com_o_documento_e_o_modelo_fica_intacto(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _hologram_plan_csv())
        await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        await _import_parties(client_with_db, world.client, _SUPPLIERS.read_bytes())

        with capture_logs() as logs:
            resp = await client_with_db.get(f"{_url(world.client)}/omie-export")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == _XLSX
        disposition = resp.headers["content-disposition"]
        assert disposition == (
            f'attachment; filename="Omie_Plano_Contas_{world.client.id.hex[:8]}.xlsx"'
        )
        rows = _rows(resp.content)
        docs = {r["conta"]: r["documento"] for r in _expected_links()}
        for code in ("685", "918"):
            assert re.fullmatch(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", rows[code][6]), code
            assert re.sub(r"\D", "", rows[code][6]) == docs[code]
            assert rows[code][3] == "Devedora"
            assert rows[code][4] == "Analítica"
        assert rows["1"][4] == "Totalizadora (Sintética)"
        assert rows["2"][3] == "Credora"
        linked = sum(1 for row in rows.values() if row[6])
        assert linked > 0

        # As listas de opções do modelo continuam no arquivo, iguais.
        with (
            zipfile.ZipFile(io.BytesIO(resp.content)) as out,
            zipfile.ZipFile(TEMPLATE_PATH) as tpl,
        ):
            sheet = out.read(SHEET_PART).decode()
            model = tpl.read(SHEET_PART).decode()
        block = re.compile(r"<x14:dataValidations.*?</x14:dataValidations>", re.S)
        assert block.search(sheet).group(0) == block.search(model).group(0)  # type: ignore[union-attr]

        # Trilha e evento: só IDs e contagens; o documento não vai para log.
        assert len(await _audit(db_session, world.client.id, "export")) == 1
        events = await _events(db_session)
        assert len(events) == 1
        assert events[0].props == {
            "client_id": str(world.client.id),
            "contas": len(rows),
            "vinculadas": linked,
            "sem_vinculo": len(rows) - linked,
        }
        rendered = repr(logs)
        for document in docs.values():
            assert document not in rendered

    async def test_mesma_entrada_mesmos_bytes(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _hologram_plan_csv())
        await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        first = await client_with_db.get(f"{_url(world.client)}/omie-export")
        second = await client_with_db.get(f"{_url(world.client)}/omie-export")
        assert first.status_code == second.status_code == 200
        assert first.content == second.content


class TestRecusas:
    async def test_conta_sem_classificacao_bloqueia_com_o_codigo(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        plan = b"codigo_reduzido;nome;tipo;classificacao\n10;Caixa;analitica;1.1\n77;Sem grupo;analitica;\n"
        await _import_plan(client_with_db, world.client, plan)
        resp = await client_with_db.get(f"{_url(world.client)}/omie-export")
        assert resp.status_code == 409, resp.text
        error = resp.json()["error"]
        assert error["code"] == "PLANILHA_OMIE_RECUSADA"
        assert error["details"]["problems"] == [
            {"reason": "conta_sem_classificacao", "accountCodes": ["77"], "count": 1},
            {"reason": "conta_sem_natureza", "accountCodes": ["77"], "count": 1},
        ]
        assert "Sem grupo" not in resp.text
        assert await _audit(db_session, world.client.id, "export") == []
        assert await _events(db_session) == []

    async def test_plano_vazio(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(f"{_url(world.client)}/omie-export")
        assert resp.status_code == 409
        assert resp.json()["error"]["details"]["problems"] == [
            {"reason": "plano_vazio", "accountCodes": [], "count": 0}
        ]

    async def test_nome_que_nao_cabe_na_coluna(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        plan = f"codigo_reduzido;nome;tipo;classificacao\n10;{'x' * 61};analitica;1.1\n"
        await _import_plan(client_with_db, world.client, plan.encode())
        resp = await client_with_db.get(f"{_url(world.client)}/omie-export")
        assert resp.status_code == 409
        assert resp.json()["error"]["details"]["problems"][0]["reason"] == "texto_longo"


class TestPermissao:
    @pytest.mark.parametrize("who", ["tenant_manager", "tenant_operator"])
    async def test_usuario_do_cliente_recebe_403_com_denied(
        self,
        client_with_db: AsyncClient,
        world: World,
        db_session: AsyncSession,
        who: str,
    ) -> None:
        await _login(client_with_db, getattr(world, who))
        for path in ("omie-export", "omie-export/preview"):
            resp = await client_with_db.get(f"{_url(world.client)}/{path}")
            assert resp.status_code == 403, resp.text
        assert len(await _audit(db_session, world.client.id, "denied")) == 2

    async def test_gerente_da_carteira_gera(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _hologram_plan_csv())
        await _login(client_with_db, world.manager)
        resp = await client_with_db.get(f"{_url(world.client)}/omie-export")
        assert resp.status_code == 200, resp.text

    async def test_cliente_encerrado_409(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _hologram_plan_csv())
        resp = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert resp.status_code in (200, 204), resp.text
        for path in ("omie-export", "omie-export/preview"):
            resp = await client_with_db.get(f"{_url(world.client)}/{path}")
            assert resp.status_code == 409, resp.text


class TestPrevia:
    async def test_conta_vinculadas_e_documentos_fora_do_cadastro(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        known = next(iter(_MOCK_FORNECEDORES_SEED))
        plan = (
            "codigo_reduzido;nome;tipo;classificacao\n"
            "1;Ativo;sintetica;1\n10;Cliente A;analitica;1.1\n11;Cliente B;analitica;1.2\n"
            "12;Caixa;analitica;1.3\n"
        )
        await _import_plan(client_with_db, world.omie_client, plan.encode())
        report = build_parties_xlsx(
            "cliente",
            [(1, 10, "A", "A LTDA", known), (2, 11, "B", "B LTDA", VALID_CNPJS[0])],
        )
        await _import_parties(client_with_db, world.omie_client, report)

        resp = await client_with_db.get(f"{_url(world.omie_client)}/omie-export/preview")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["contas"] == 4
        assert data["vinculadas"] == 2
        assert data["semVinculo"] == 2
        assert data["problems"] == []
        assert data["omieRegistry"] == {"checked": True, "missing": 1, "reason": None}
        assert known not in resp.text

    async def test_refresh_rele_o_diretorio_da_omie(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Depois de cadastrar o que faltava no Omie, `refresh=true` relê (cache de 6 h)."""
        await _login(client_with_db, world.admin)
        known = next(iter(_MOCK_FORNECEDORES_SEED))
        plan = "codigo_reduzido;nome;tipo;classificacao\n10;Cliente A;analitica;1.1\n"
        await _import_plan(client_with_db, world.omie_client, plan.encode())
        report = build_parties_xlsx("cliente", [(1, 10, "A", "A LTDA", known)])
        await _import_parties(client_with_db, world.omie_client, report)
        # O diretório em cache é de ANTES do cadastro: o documento ainda não estava lá.
        fastapi_app.state.omie_fornecedores_directory_cache.set(world.omie_client.id, [])
        url = f"{_url(world.omie_client)}/omie-export/preview"
        stale = await client_with_db.get(url)
        assert stale.json()["data"]["omieRegistry"]["missing"] == 1
        fresh = await client_with_db.get(url, params={"refresh": "true"})
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["data"]["omieRegistry"] == {
            "checked": True,
            "missing": 0,
            "reason": None,
        }

    async def test_cliente_sem_omie_nao_confere_e_nao_falha(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(
            client_with_db,
            world.client,
            b"codigo_reduzido;nome;tipo;classificacao\n10;Cliente;analitica;1.1\n",
        )
        report = build_parties_xlsx("cliente", [(1, 10, "A", "A LTDA", VALID_CPFS[0])])
        await _import_parties(client_with_db, world.client, report)
        resp = await client_with_db.get(f"{_url(world.client)}/omie-export/preview")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["omieRegistry"] == {
            "checked": False,
            "missing": None,
            "reason": "sem_omie",
        }

    async def test_sem_vinculo_nao_pergunta_ao_omie(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(
            client_with_db,
            world.client,
            b"codigo_reduzido;nome;tipo;classificacao\n10;Caixa;analitica;1.1\n77;X;analitica;\n",
        )
        resp = await client_with_db.get(f"{_url(world.client)}/omie-export/preview")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["omieRegistry"] == {"checked": True, "missing": 0, "reason": None}
        assert [p["reason"] for p in data["problems"]] == [
            "conta_sem_classificacao",
            "conta_sem_natureza",
        ]


class TestNatureza:
    async def test_derivada_na_importacao_e_na_conta_manual(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        plan = (
            "codigo_reduzido;nome;tipo;classificacao\n"
            "1;Caixa;analitica;1.1\n2;Fornecedores;analitica;2.1\n3;Receita;analitica;3.1\n"
            "4;Despesa;analitica;4.2\n5;Sem;analitica;\n"
        )
        await _import_plan(client_with_db, world.client, plan.encode())
        resp = await client_with_db.get(_url(world.client), params={"pageSize": 100})
        natures = {item["code"]: item["nature"] for item in resp.json()["data"]}
        assert natures == {
            "1": "devedora",
            "2": "credora",
            "3": "credora",
            "4": "devedora",
            "5": None,
        }
        resp = await client_with_db.post(
            f"{_url(world.client)}/accounts",
            json={"code": "9", "name": "Manual", "type": "analitica", "classification": "2.9"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["nature"] == "credora"
        assert resp.json()["data"]["natureOverridden"] is False
        resp = await client_with_db.post(
            f"{_url(world.client)}/accounts",
            json={"code": "8", "name": "Manual", "type": "analitica", "nature": "devedora"},
        )
        assert resp.status_code == 201, resp.text
        assert (resp.json()["data"]["nature"], resp.json()["data"]["natureOverridden"]) == (
            "devedora",
            True,
        )

    async def test_escolhida_a_mao_sobrevive_a_reimportacao(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        plan = b"codigo_reduzido;nome;tipo;classificacao\n3;Redutora;analitica;3.1\n4;Outra;analitica;3.2\n"
        await _import_plan(client_with_db, world.client, plan)
        account = await _account(db_session, world.client.id, "3")
        resp = await client_with_db.patch(
            f"{_url(world.client)}/accounts/{account.id}", json={"nature": "devedora"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["nature"] == "devedora"
        assert resp.json()["data"]["natureOverridden"] is True

        # Reimportação que muda a classificação das duas: a manual fica, a outra segue.
        moved = b"codigo_reduzido;nome;tipo;classificacao\n3;Redutora;analitica;3.9\n4;Outra;analitica;4.2\n"
        await _import_plan(client_with_db, world.client, moved)
        assert (await _account(db_session, world.client.id, "3")).nature == "devedora"
        assert (await _account(db_session, world.client.id, "4")).nature == "devedora"

        # `null` volta ao automático.
        resp = await client_with_db.patch(
            f"{_url(world.client)}/accounts/{account.id}", json={"nature": None}
        )
        assert resp.status_code == 200, resp.text
        assert (resp.json()["data"]["nature"], resp.json()["data"]["natureOverridden"]) == (
            "credora",
            False,
        )
        events = (
            (
                await db_session.execute(
                    select(UsageEvent).where(UsageEvent.event == "plano_contabil_conta_editada")
                )
            )
            .scalars()
            .all()
        )
        assert any("natureza" in (e.props or {}).get("campos", []) for e in events)

    async def test_patch_da_classificacao_recalcula_a_automatica(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(
            client_with_db,
            world.client,
            b"codigo_reduzido;nome;tipo;classificacao\n3;X;analitica;1.1\n",
        )
        account = await _account(db_session, world.client.id, "3")
        resp = await client_with_db.patch(
            f"{_url(world.client)}/accounts/{account.id}", json={"classification": "2.1"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["nature"] == "credora"

    async def test_o_backfill_em_sql_da_o_mesmo_que_a_funcao(
        self, world: World, db_session: AsyncSession
    ) -> None:
        path = next(_VERSIONS.glob("5d8e2b7c4a91_*.py"))
        spec = importlib.util.spec_from_file_location("mig_nature_backfill", path)
        assert spec is not None
        assert spec.loader is not None
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        classifications = [
            "1", "1.1.01", "2", "2.1", "3.1", "4.2", "5", "9.9", "01.1", "002.1", "11201",
            " 3.1", "0", "0.1", "A.1", "-1", None, "",
        ]  # fmt: skip
        for position, classification in enumerate(classifications):
            db_session.add(
                ClientAccountingAccount(
                    client_id=world.client.id,
                    code=f"B{position}",
                    classification=classification,
                    name_encrypted="v1:k1:00",
                    name_iv="0" * 24,
                    account_type="analitica",
                    created_by=world.admin.id,
                    updated_by=world.admin.id,
                )
            )
        await db_session.flush()
        await db_session.execute(text(mig._BACKFILL))
        await db_session.execute(text(mig._BACKFILL))  # idempotente
        for position, classification in enumerate(classifications):
            account = await _account(db_session, world.client.id, f"B{position}")
            derived = derive_nature(classification)
            assert account.nature == (derived.value if derived else None), classification
