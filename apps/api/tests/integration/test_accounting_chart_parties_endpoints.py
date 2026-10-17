"""Vínculo conta contábil ↔ cliente/fornecedor, pela API e contra Postgres (86e3qhhrn).

    POST /api/v1/clients/{client_id}/accounting-chart/parties/import  → `manage_client_accounting_chart`
    GET  /api/v1/clients/{client_id}/accounting-chart/parties          → `manage_client_accounting_chart`

O que este módulo afirma (critérios de aceite da subtask 3):
  - as DUAS relações anonimizadas da Hologram entram e vinculam, entre outras, as contas
    685 e 918 ao documento de cada uma; o documento fica CIFRADO no banco (envelope `v1`,
    AAD da conta) e a lista o devolve só MASCARADO;
  - tudo ou nada por arquivo: conta que não existe no plano ou é sintética recusa o
    relatório inteiro com 422 `LINHAS_INVALIDAS` e ZERO vínculos gravados;
  - a conta `0` e a conta compartilhada (na relação de fornecedores) não recusam: voltam
    em `naoVinculadas`;
  - reimportar é idempotente (`vinculosSemMudanca` = tudo); relatório de OUTRA empresa é
    422 `RELATORIO_DE_OUTRA_EMPRESA` sem gravar; a mesma conta nos dois relatórios fica
    com o tipo do último;
  - `client_manager` LÊ o plano (200) mas não vê nem importa vínculos (403 com linha
    `denied`); cliente encerrado: importar 409, listar 200 sem documento;
  - nenhum documento, nome ou razão social em log, resposta de lista ou `usage_events`.

O cross-tenant e o cross-org das duas rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import null, select
from structlog.testing import capture_logs

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
from tests.parties_builder import VALID_CNPJS, VALID_CPFS, build_parties_xlsx

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Vinculos#1"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "accounting_chart_dominio_parties"
_CLIENTS = _FIXTURES / "relacao_clientes.xls"
_SUPPLIERS = _FIXTURES / "relacao_fornecedores.xls"
_EXPECTED = _FIXTURES / "vinculos_esperados.csv"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_OTHER_COMPANY = "11.222.333/0001-81"


def _expected() -> list[dict[str, str]]:
    with _EXPECTED.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def _plan_csv(codes: set[str], *, synthetic: set[str] = frozenset()) -> bytes:
    """Um plano com TODAS as contas que as relações citam (o cenário feliz)."""
    lines = ["codigo_reduzido;nome;tipo"]
    lines += [
        f"{code};Conta de exemplo {code};{'sintetica' if code in synthetic else 'analitica'}"
        for code in sorted(codes, key=int)
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _codes_in_fixture() -> set[str]:
    return {r["conta"] for r in _expected() if r["conta"] not in ("", "0")}


async def _seed_user(
    session: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    extra: dict[str, Any] = {}
    if scope is UserScope.PLATFORM:
        extra["organization_id"] = null()
    user = User(
        name="Vinculos",
        email=f"vinculos-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        **extra,
    )
    session.add(user)
    await session.flush()
    return user


class World:
    admin: User
    manager: User
    tenant_manager: User
    client: Client
    other: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente dos Vinculos", active=True, created_by=w.admin.id)
    w.other = Client(name="Outro Cliente", active=True, created_by=w.admin.id)
    db_session.add_all([w.client, w.other])
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=w.manager.id, assigned_by=w.admin.id, is_primary=True
        )
    )
    w.tenant_manager = await _seed_user(
        db_session, role=UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=w.client.id
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


async def _import_parties(
    http: AsyncClient, client: Client, content: bytes, filename: str = "relacao.xls"
) -> Response:
    return await http.post(
        f"{_url(client)}/parties/import", files={"file": (filename, content, _XLSX)}
    )


async def _linked(db: AsyncSession, client_id: Any) -> dict[str, ClientAccountingAccount]:
    stmt = (
        select(ClientAccountingAccount)
        .where(
            ClientAccountingAccount.client_id == client_id,
            ClientAccountingAccount.party_document_encrypted.is_not(None),
        )
        .execution_options(populate_existing=True)
    )
    return {a.code: a for a in (await db.execute(stmt)).scalars().all()}


async def _denied(db: AsyncSession, client_id: Any) -> list[AccessAudit]:
    stmt = select(AccessAudit).where(
        AccessAudit.action == "denied", AccessAudit.client_id == client_id
    )
    return list((await db.execute(stmt)).scalars().all())


class TestAsDuasRelacoesDaHologram:
    async def test_importa_as_duas_e_vincula_685_e_918_cifrado(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture()))
        expected = _expected()

        resp = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["relatorio"] == "cliente"
        assert (data["linhas"], data["contasVinculadas"], data["vinculosNovos"]) == (89, 89, 89)
        assert data["naoVinculadas"] == []
        assert data["naoVinculadasTotal"] == 0

        resp = await _import_parties(client_with_db, world.client, _SUPPLIERS.read_bytes())
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["relatorio"] == "fornecedor"
        assert data["linhas"] == 93
        assert sorted({item["reason"] for item in data["naoVinculadas"]}) == [
            "conta_compartilhada",
            "sem_conta",
        ]

        linked = await _linked(db_session, world.client.id)
        for code in ("685", "918"):
            assert code in linked, f"conta {code} sem vínculo"
            assert linked[code].party_document_encrypted is not None
            assert linked[code].party_document_encrypted.startswith("v1:")
            assert linked[code].party_type == "cliente"
        # O documento NUNCA está em claro no banco.
        docs = {r["conta"]: r["documento"] for r in expected if r["situacao"] == "vinculavel"}
        for code, account in linked.items():
            assert docs[code] not in (account.party_document_encrypted or "")
        # A impressão digital da empresa nasceu; o CNPJ dela não está em claro em lugar nenhum.
        await db_session.refresh(world.client)
        assert world.client.party_reports_company_fingerprint is not None
        assert len(world.client.party_reports_company_fingerprint) == 64
        assert "00000000000191" not in world.client.party_reports_company_fingerprint

    async def test_a_lista_devolve_so_o_documento_mascarado_e_o_resumo(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture()))
        await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        await _import_parties(client_with_db, world.client, _SUPPLIERS.read_bytes())

        resp = await client_with_db.get(f"{_url(world.client)}/parties", params={"pageSize": 100})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["contasVinculadas"] == body["pagination"]["total"]
        assert (
            body["summary"]["clientes"] + body["summary"]["fornecedores"]
            == body["summary"]["contasVinculadas"]
        )
        docs = {r["documento"] for r in _expected() if r["situacao"] == "vinculavel"}
        for item in body["data"]:
            assert item["documentResolved"] is True
            masked = item["documentMasked"]
            assert masked.startswith(("**.***.***/", "***.***.*"))
            assert item["documentKind"] in ("cnpj", "cpf")
            assert item["nameResolved"] is True
        for document in docs:
            assert document not in resp.text, "documento inteiro na lista"
        # O filtro por tipo recorta.
        suppliers = await client_with_db.get(
            f"{_url(world.client)}/parties", params={"type": "fornecedor", "pageSize": 100}
        )
        assert {item["partyType"] for item in suppliers.json()["data"]} == {"fornecedor"}

    async def test_reimportar_e_idempotente_e_a_conta_nos_dois_fica_com_o_ultimo_tipo(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture()))
        first = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        again = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert again.status_code == 200, again.text
        data = again.json()["data"]
        assert data["vinculosNovos"] == 0
        assert data["vinculosAtualizados"] == 0
        assert data["vinculosSemMudanca"] == first.json()["data"]["vinculosNovos"]

        # A mesma conta num relatório de fornecedores: o tipo muda, o documento também.
        account = next(iter(await _linked(db_session, world.client.id)))
        content = build_parties_xlsx(
            "fornecedor",
            [(1, int(account), "Pessoa", "Pessoa", VALID_CNPJS[2])],
            swap_code_and_account=True,
        )
        # ⚠️ Mesma empresa no cabeçalho: a fixture e o builder usam `00.000.000/0001-91`.
        resp = await _import_parties(client_with_db, world.client, content, filename="relacao.xlsx")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["vinculosAtualizados"] == 1
        assert (await _linked(db_session, world.client.id))[account].party_type == "fornecedor"


class TestTudoOuNadaPorArquivo:
    async def test_conta_fora_do_plano_recusa_o_arquivo_inteiro(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        codes = _codes_in_fixture()
        codes.discard("685")
        await _import_plan(client_with_db, world.client, _plan_csv(codes))
        resp = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert resp.status_code == 422, resp.text
        error = resp.json()["error"]
        assert error["code"] == "LINHAS_INVALIDAS"
        assert error["details"]["lines"] == [
            {
                "line": pytest.approx(error["details"]["lines"][0]["line"]),
                "reason": "conta_nao_encontrada",
                "accountCode": "685",
            }
        ]
        assert await _linked(db_session, world.client.id) == {}
        for r in _expected():
            if r["documento"]:
                assert r["documento"] not in resp.text

    async def test_conta_sintetica_recusa_o_arquivo_inteiro(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(
            client_with_db, world.client, _plan_csv(_codes_in_fixture(), synthetic={"918"})
        )
        resp = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert resp.status_code == 422, resp.text
        assert [item["reason"] for item in resp.json()["error"]["details"]["lines"]] == [
            "conta_sintetica"
        ]
        assert await _linked(db_session, world.client.id) == {}

    async def test_relatorio_de_outra_empresa_e_recusado_sem_gravar(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture() | {"777"}))
        await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        before = len(await _linked(db_session, world.client.id))
        content = build_parties_xlsx(
            "fornecedor", [(1, 777, "Pessoa", "Pessoa", VALID_CPFS[1])], company=_OTHER_COMPANY
        )
        resp = await _import_parties(client_with_db, world.client, content, filename="relacao.xlsx")
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "RELATORIO_DE_OUTRA_EMPRESA"
        assert _OTHER_COMPANY not in resp.text
        assert "11222333000181" not in resp.text
        assert len(await _linked(db_session, world.client.id)) == before

    async def test_planilha_que_nao_e_a_relacao_e_cabecalho_divergente(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _import_parties(
            client_with_db, world.client, _plan_csv({"649"}), filename="plano.csv"
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "FORMATO_NAO_SUPORTADO"


class TestPermissaoEEncerramento:
    async def test_client_manager_le_o_plano_mas_nao_ve_nem_importa_vinculos(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.tenant_manager)
        assert (await client_with_db.get(_url(world.client))).status_code == 200
        listing = await client_with_db.get(f"{_url(world.client)}/parties")
        assert listing.status_code == 403, listing.text
        importing = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert importing.status_code == 403, importing.text
        assert len(await _denied(db_session, world.client.id)) == 2

    async def test_gerente_da_carteira_importa(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.manager)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture()))
        resp = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert resp.status_code == 200, resp.text

    async def test_cliente_encerrado_nao_importa_e_lista_sem_documento(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture()))
        await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        closed = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert closed.status_code in (200, 204), closed.text

        resp = await _import_parties(client_with_db, world.client, _SUPPLIERS.read_bytes())
        assert resp.status_code == 409, resp.text
        listing = await client_with_db.get(f"{_url(world.client)}/parties", params={"pageSize": 5})
        assert listing.status_code == 200, listing.text
        for item in listing.json()["data"]:
            assert item["documentResolved"] is False
            assert item["documentMasked"] is None
        await db_session.refresh(world.client)
        assert world.client.party_reports_company_fingerprint is None


class TestNadaDePIIForaDoLugar:
    async def test_log_e_evento_so_tem_ids_e_contagens(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        await _import_plan(client_with_db, world.client, _plan_csv(_codes_in_fixture()))
        with capture_logs() as logs:
            resp = await _import_parties(client_with_db, world.client, _CLIENTS.read_bytes())
        assert resp.status_code == 200, resp.text
        dump = repr(logs)
        for r in _expected():
            if r["documento"]:
                assert r["documento"] not in dump
        assert "de exemplo" not in dump
        assert "685" not in dump

        events = list(
            (
                await db_session.execute(
                    select(UsageEvent).where(
                        UsageEvent.event == "plano_contabil_vinculos_importados"
                    )
                )
            ).scalars()
        )
        assert len(events) == 1
        props = events[0].props
        assert props["relatorio"] == "cliente"
        assert (props["linhas"], props["vinculos_novos"], props["nao_vinculadas"]) == (89, 89, 0)
        assert set(props) == {
            "client_id",
            "relatorio",
            "linhas",
            "vinculos_novos",
            "vinculos_atualizados",
            "vinculos_sem_mudanca",
            "nao_vinculadas",
        }
