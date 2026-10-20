"""Vínculo conta contábil ↔ cliente/fornecedor À MÃO, pela API e contra Postgres (86e3tttaf).

    PUT    /api/v1/clients/{client_id}/accounting-chart/accounts/{account_id}/party
    DELETE /api/v1/clients/{client_id}/accounting-chart/accounts/{account_id}/party

O que este módulo afirma (critérios de aceite da subtask 9):
  - o PUT vincula uma conta analítica ativa a um CNPJ ou CPF com ou sem máscara: o
    documento fica CIFRADO (envelope `v1`), a resposta e a lista o devolvem MASCARADO, a
    conta aparece na lista do tipo dela, e a impressão digital da empresa dos relatórios
    não muda;
  - o PUT de novo troca o vínculo (documento e tipo); o mesmo documento e tipo não
    regrava nem emite;
  - recusas: dígito errado, conta sintética e conta inativa são 422
    `CONTA_CONTABIL_INVALIDA` com `details.reason`; o MESMO documento em outra conta do
    cliente é 409 `DOCUMENTO_JA_VINCULADO` com o código da outra conta e sem documento;
    conta de outro cliente é 404;
  - o DELETE zera documento, IV e tipo juntos (CHECK `party_pair`), vale para vínculo
    IMPORTADO, e é idempotente (204 de novo, sem evento);
  - `client_manager` recebe 403 com linha `denied`; cliente encerrado é 409 nas duas;
  - o evento `plano_contabil_vinculo_editado` só leva IDs, o tipo e a operação; o
    documento nunca vai para o log.

O cross-tenant e o cross-org das duas rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from app.db.models import (
    AccessAudit,
    Client,
    ClientAccountingAccount,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from tests.integration.test_accounting_chart_parties_endpoints import (
    PLAIN_PASSWORD,
    _codes_in_fixture,
    _plan_csv,
    _seed_user,
)

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "accounting_chart_dominio_parties"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_CNPJ = "45723174000110"
_CNPJ_MASKED_INPUT = "45.723.174/0001-10"
_CPF = "52998224725"
_CPF_MASKED_INPUT = "529.982.247-25"
_CNPJ_WRONG_DIGIT = "45.723.174/0001-19"


class World:
    admin: User
    tenant_manager: User
    client: Client
    other: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.client = Client(name="Cliente do Vinculo Manual", active=True, created_by=w.admin.id)
    w.other = Client(name="Outro Cliente Manual", active=True, created_by=w.admin.id)
    db_session.add_all([w.client, w.other])
    await db_session.flush()
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


async def _seed_plan(http: AsyncClient, client: Client) -> dict[str, str]:
    """Plano com 3 analíticas (685, 686, 918), 1 sintética (100) e 1 inativa (700)."""
    content = (
        b"codigo_reduzido;nome;tipo\n"
        b"100;Grupo de exemplo;sintetica\n"
        b"685;Conta de exemplo 685;analitica\n"
        b"686;Conta de exemplo 686;analitica\n"
        b"700;Conta de exemplo 700;analitica\n"
        b"918;Conta de exemplo 918;analitica\n"
    )
    resp = await http.post(
        f"{_url(client)}/import", files={"file": ("plano.csv", content, "text/csv")}
    )
    assert resp.status_code == 200, resp.text
    listing = await http.get(_url(client), params={"pageSize": 100})
    ids = {row["code"]: row["id"] for row in listing.json()["data"]}
    deactivate = await http.patch(f"{_url(client)}/accounts/{ids['700']}", json={"active": False})
    assert deactivate.status_code == 200, deactivate.text
    return ids


async def _put(
    http: AsyncClient, client: Client, account_id: str, *, party_type: str, document: str
) -> Response:
    return await http.put(
        f"{_url(client)}/accounts/{account_id}/party",
        json={"partyType": party_type, "document": document},
    )


async def _account(db: AsyncSession, account_id: str) -> ClientAccountingAccount:
    stmt = (
        select(ClientAccountingAccount)
        .where(ClientAccountingAccount.id == account_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one()


async def _events(db: AsyncSession) -> list[UsageEvent]:
    stmt = (
        select(UsageEvent)
        .where(UsageEvent.event == "plano_contabil_vinculo_editado")
        .order_by(UsageEvent.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


class TestVincularETrocar:
    async def test_vincula_cnpj_com_mascara_cifrado_e_devolve_mascarado(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)

        resp = await _put(
            client_with_db,
            world.client,
            ids["685"],
            party_type="fornecedor",
            document=_CNPJ_MASKED_INPUT,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["accountId"] == ids["685"]
        assert data["code"] == "685"
        assert data["partyType"] == "fornecedor"
        assert data["documentKind"] == "cnpj"
        assert data["documentMasked"] == "**.***.***/0001-10"
        assert data["documentResolved"] is True
        assert data["name"] == "Conta de exemplo 685"
        assert _CNPJ not in resp.text

        account = await _account(db_session, ids["685"])
        assert account.party_type == "fornecedor"
        assert account.party_document_encrypted is not None
        assert account.party_document_encrypted.startswith("v1:")
        assert _CNPJ not in account.party_document_encrypted
        await db_session.refresh(world.client)
        assert world.client.party_reports_company_fingerprint is None

        suppliers = await client_with_db.get(
            f"{_url(world.client)}/parties", params={"type": "fornecedor"}
        )
        assert [r["code"] for r in suppliers.json()["data"]] == ["685"]
        assert suppliers.json()["summary"]["fornecedores"] == 1
        clients = await client_with_db.get(
            f"{_url(world.client)}/parties", params={"type": "cliente"}
        )
        assert clients.json()["data"] == []

        events = await _events(db_session)
        assert len(events) == 1
        assert events[0].props == {
            "client_id": str(world.client.id),
            "account_id": ids["685"],
            "party_type": "fornecedor",
            "operacao": "criado",
        }

    async def test_troca_o_vinculo_e_repetir_nao_regrava(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        first = await _put(
            client_with_db, world.client, ids["918"], party_type="fornecedor", document=_CNPJ
        )
        assert first.status_code == 200, first.text

        swapped = await _put(
            client_with_db,
            world.client,
            ids["918"],
            party_type="cliente",
            document=_CPF_MASKED_INPUT,
        )
        assert swapped.status_code == 200, swapped.text
        assert swapped.json()["data"]["partyType"] == "cliente"
        assert swapped.json()["data"]["documentKind"] == "cpf"
        assert swapped.json()["data"]["documentMasked"] == "***.***.*47-25"
        envelope = (await _account(db_session, ids["918"])).party_document_encrypted

        again = await _put(
            client_with_db, world.client, ids["918"], party_type="cliente", document=_CPF
        )
        assert again.status_code == 200, again.text
        assert (await _account(db_session, ids["918"])).party_document_encrypted == envelope

        operations = [e.props["operacao"] for e in await _events(db_session)]
        assert operations == ["criado", "trocado"]


class TestRecusas:
    async def test_documento_com_digito_errado_e_422_no_documento(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        for document in (_CNPJ_WRONG_DIGIT, "123", "abc.def.ghi-jk", "11111111111"):
            resp = await _put(
                client_with_db, world.client, ids["685"], party_type="cliente", document=document
            )
            assert resp.status_code == 422, (document, resp.text)
            error = resp.json()["error"]
            assert error["code"] == "CONTA_CONTABIL_INVALIDA"
            assert error["details"] == {"field": "document", "reason": "documento_invalido"}
        assert (await _account(db_session, ids["685"])).party_document_encrypted is None
        assert await _events(db_session) == []

    @pytest.mark.parametrize(
        ("code", "reason"), [("100", "conta_sintetica"), ("700", "conta_inativa")]
    )
    async def test_conta_sintetica_ou_inativa_e_422(
        self,
        client_with_db: AsyncClient,
        world: World,
        db_session: AsyncSession,
        code: str,
        reason: str,
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        resp = await _put(
            client_with_db, world.client, ids[code], party_type="cliente", document=_CNPJ
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["details"] == {"field": "accountId", "reason": reason}
        assert (await _account(db_session, ids[code])).party_document_encrypted is None

    async def test_mesmo_documento_em_outra_conta_e_409_com_o_codigo_dela(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        first = await _put(
            client_with_db, world.client, ids["685"], party_type="fornecedor", document=_CNPJ
        )
        assert first.status_code == 200, first.text

        resp = await _put(
            client_with_db,
            world.client,
            ids["686"],
            party_type="fornecedor",
            document=_CNPJ_MASKED_INPUT,
        )
        assert resp.status_code == 409, resp.text
        error = resp.json()["error"]
        assert error["code"] == "DOCUMENTO_JA_VINCULADO"
        assert error["details"] == {"accountCode": "685", "accountId": ids["685"]}
        assert _CNPJ not in resp.text
        assert "0001-10" not in resp.text
        assert "Conta de exemplo" not in resp.text
        assert (await _account(db_session, ids["686"])).party_document_encrypted is None

    async def test_a_mesma_empresa_como_cliente_e_fornecedor_em_duas_contas_e_aceita(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """A empresa que é cliente E fornecedor tem uma conta em cada relação do Domínio."""
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        supplier = await _put(
            client_with_db, world.client, ids["685"], party_type="fornecedor", document=_CNPJ
        )
        assert supplier.status_code == 200, supplier.text
        customer = await _put(
            client_with_db, world.client, ids["686"], party_type="cliente", document=_CNPJ
        )
        assert customer.status_code == 200, customer.text
        assert customer.json()["data"]["partyType"] == "cliente"

    async def test_o_mesmo_documento_em_outro_cliente_nao_conflita(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        other_ids = await _seed_plan(client_with_db, world.other)
        assert (
            await _put(
                client_with_db, world.client, ids["685"], party_type="cliente", document=_CNPJ
            )
        ).status_code == 200
        resp = await _put(
            client_with_db, world.other, other_ids["685"], party_type="cliente", document=_CNPJ
        )
        assert resp.status_code == 200, resp.text

    async def test_conta_de_outro_cliente_e_404(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await _seed_plan(client_with_db, world.client)
        other_ids = await _seed_plan(client_with_db, world.other)
        put = await _put(
            client_with_db, world.client, other_ids["685"], party_type="cliente", document=_CNPJ
        )
        assert put.status_code == 404, put.text
        assert "Outro Cliente" not in put.text
        delete = await client_with_db.delete(
            f"{_url(world.client)}/accounts/{other_ids['685']}/party"
        )
        assert delete.status_code == 404, delete.text
        missing = await client_with_db.delete(f"{_url(world.client)}/accounts/{uuid4()}/party")
        assert missing.status_code == 404, missing.text


class TestRemover:
    async def test_remove_zera_os_tres_juntos_e_e_idempotente(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        await _put(client_with_db, world.client, ids["685"], party_type="cliente", document=_CPF)

        first = await client_with_db.delete(f"{_url(world.client)}/accounts/{ids['685']}/party")
        assert first.status_code == 204, first.text
        account = await _account(db_session, ids["685"])
        assert account.party_document_encrypted is None
        assert account.party_document_iv is None
        assert account.party_type is None

        again = await client_with_db.delete(f"{_url(world.client)}/accounts/{ids['685']}/party")
        assert again.status_code == 204, again.text
        never_linked = await client_with_db.delete(
            f"{_url(world.client)}/accounts/{ids['918']}/party"
        )
        assert never_linked.status_code == 204, never_linked.text

        events = await _events(db_session)
        assert [e.props["operacao"] for e in events] == ["criado", "removido"]
        assert events[1].props["party_type"] == "cliente"
        listing = await client_with_db.get(f"{_url(world.client)}/parties")
        assert listing.json()["data"] == []

    async def test_remove_o_vinculo_importado_da_relacao(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            f"{_url(world.client)}/import",
            files={"file": ("plano.csv", _plan_csv(_codes_in_fixture()), "text/csv")},
        )
        assert resp.status_code == 200, resp.text
        imported = await client_with_db.post(
            f"{_url(world.client)}/parties/import",
            files={
                "file": ("relacao.xls", (_FIXTURES / "relacao_clientes.xls").read_bytes(), _XLSX)
            },
        )
        assert imported.status_code == 200, imported.text
        before = (
            await client_with_db.get(f"{_url(world.client)}/parties", params={"pageSize": 100})
        ).json()
        target = next(r for r in before["data"] if r["code"] == "685")

        resp = await client_with_db.delete(
            f"{_url(world.client)}/accounts/{target['accountId']}/party"
        )
        assert resp.status_code == 204, resp.text
        after = (
            await client_with_db.get(f"{_url(world.client)}/parties", params={"pageSize": 100})
        ).json()
        assert after["summary"]["contasVinculadas"] == before["summary"]["contasVinculadas"] - 1
        assert "685" not in {r["code"] for r in after["data"]}
        await db_session.refresh(world.client)
        assert world.client.party_reports_company_fingerprint is not None


class TestPermissaoEEncerramento:
    async def test_client_manager_recebe_403_com_linha_denied(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        await _login(client_with_db, world.tenant_manager)
        put = await _put(
            client_with_db, world.client, ids["685"], party_type="cliente", document=_CNPJ
        )
        assert put.status_code == 403, put.text
        delete = await client_with_db.delete(f"{_url(world.client)}/accounts/{ids['685']}/party")
        assert delete.status_code == 403, delete.text
        denied = list(
            (
                await db_session.execute(
                    select(AccessAudit).where(
                        AccessAudit.action == "denied", AccessAudit.client_id == world.client.id
                    )
                )
            ).scalars()
        )
        assert len(denied) == 2

    async def test_cliente_encerrado_e_409_nas_duas(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        await _put(client_with_db, world.client, ids["685"], party_type="cliente", document=_CNPJ)
        closed = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert closed.status_code in (200, 204), closed.text

        put = await _put(
            client_with_db, world.client, ids["918"], party_type="cliente", document=_CPF
        )
        assert put.status_code == 409, put.text
        delete = await client_with_db.delete(f"{_url(world.client)}/accounts/{ids['685']}/party")
        assert delete.status_code == 409, delete.text
        listing = await client_with_db.get(f"{_url(world.client)}/parties")
        assert listing.status_code == 200, listing.text


class TestNadaDePII:
    async def test_documento_nunca_vai_para_o_log(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        ids = await _seed_plan(client_with_db, world.client)
        with capture_logs() as logs:
            ok = await _put(
                client_with_db,
                world.client,
                ids["685"],
                party_type="cliente",
                document=_CNPJ_MASKED_INPUT,
            )
            conflict = await _put(
                client_with_db, world.client, ids["686"], party_type="cliente", document=_CNPJ
            )
            invalid = await _put(
                client_with_db,
                world.client,
                ids["686"],
                party_type="cliente",
                document=_CNPJ_WRONG_DIGIT,
            )
            removed = await client_with_db.delete(
                f"{_url(world.client)}/accounts/{ids['685']}/party"
            )
        assert (ok.status_code, conflict.status_code, invalid.status_code) == (200, 409, 422)
        assert removed.status_code == 204
        dump = repr(logs)
        for secret in (_CNPJ, _CNPJ_MASKED_INPUT, "45723174000119", "0001-10", "Conta de exemplo"):
            assert secret not in dump, secret
