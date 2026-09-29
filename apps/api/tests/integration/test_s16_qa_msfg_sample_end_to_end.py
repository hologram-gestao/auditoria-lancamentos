"""QA da Sprint 16 (86e3ftyvn): a amostra MSFG de ponta a ponta, pelo caminho REAL.

O teste de `test_usage_events_sprint16.py` semeia os 32 movimentos direto no banco e
presume os códigos `arq-NN`. Este aqui faz o que uma pessoa faria, sem atalho:

    cadastrar o cliente → conectar a origem `arquivo` → declarar o mapeamento
    (`classificacao_livre` sobre a descrição, SEM coluna de conta) → enviar
    `extrato_cliente.csv` para 08/2026 (a ingestão da S14 apara a descrição e gera o
    código da categoria) → importar `plano_contabil.csv` → conta padrão do banco = 649 →
    as 23 decisões de `decisoes_depara.csv` no `conta_contabil`, resolvendo o código
    pela LISTA do de-para (a categoria da planilha é a descrição aparada) → prévia →
    materialização.

E prova, linha a linha, os CINCO campos (data, débito, crédito, valor absoluto,
histórico) contra `lancamentos_esperados.csv` (Latin-1, CRLF, sem cabeçalho), com
32 linhas (15 entradas, 17 saídas), totais 29.377,33 / 24.193,66 e completude 100,00.

Depois de materializar: reimportar o plano (renomeando e inativando), trocar o
histórico (vigência nova) e trocar a conta do banco NÃO muda a materialização.

Guardrails: 1 `plano_contabil_importado` por importação bem-sucedida, props só com IDs
e contagens; nenhum nome de conta nem histórico em log (stdlib e structlog) nem em
`usage_events`. Roda com a política REAL de transação por request (commit no fim,
rollback na exceção), como o QA da S14.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.db.session import get_db_session
from app.main import app as fastapi_app
from app.modules.client_mapping.accounting import AccountingDecisionSupport
from app.modules.client_mapping.materialization import ClientMappingApplyService
from app.modules.client_mapping.partida import derive_partida
from app.modules.client_mapping.repository import ClientMappingRepository
from app.modules.client_mapping.service import ClientMappingDecisionService
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.service import MappingCatalogService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@QaAmostra#16"
SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "accounting_sample"
    / "cliente_exemplo_2026_08"
)
EXTRATO = (SAMPLE / "extrato_cliente.csv").read_bytes()
PLANO = (SAMPLE / "plano_contabil.csv").read_bytes()
COMPETENCE = "2026-08"
NEXT_COMPETENCE = "2026-09"
BANK_CODE = "649"
#: Entradas 29.377,33 menos saídas 24.193,66 (README da amostra).
DECLARED_TOTAL = "5183,67"

MAPPING = {
    "fileFormat": "csv",
    "csvDelimiter": ";",
    "encoding": "utf-8",
    "dateColumn": "Data",
    "descriptionColumn": "Descricao",
    "amountColumn": "Valor",
    "categoryColumn": "Descricao",
    "categoryMode": "classificacao_livre",
    "dateFormat": "dd/mm/yyyy",
    "decimalSeparator": ",",
    "signConvention": "valor_com_sinal",
}


def _decisoes() -> list[dict[str, str]]:
    text = (SAMPLE / "decisoes_depara.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def _plano() -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(PLANO.decode("utf-8")), delimiter=";"))


def _brl_to_decimal(value: str) -> Decimal:
    """`R$ 5.466,87` → `Decimal('5466.87')` (a formatação é da Sprint 13)."""
    digits = value.removeprefix("R$").strip().replace(".", "").replace(",", ".")
    return Decimal(digits)


def _esperados() -> list[tuple[str, str, str, Decimal, str]]:
    raw = (SAMPLE / "lancamentos_esperados.csv").read_bytes()
    assert b"\r\n" in raw, "a verdade do R6 é CRLF (travada como -text)"
    lines = raw.decode("latin-1").split("\r\n")
    rows: list[tuple[str, str, str, Decimal, str]] = []
    for line in lines:
        if not line:
            continue
        data, debito, credito, valor, historico = line.split(";", 4)
        rows.append((data, debito, credito, _brl_to_decimal(valor), historico))
    return rows


#: Texto que NUNCA pode aparecer em log nem em telemetria: nomes de conta e históricos.
SECRETS = tuple(
    {row["nome"] for row in _plano()} | {row["historico_padrao"] for row in _decisoes()}
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def http(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Política REAL de transação por request: `commit()` no fim, `rollback()` na exceção."""

    async def _override() -> AsyncGenerator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    fastapi_app.dependency_overrides[get_db_session] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture(autouse=True)
def _no_secret_in_logs(caplog: pytest.LogCaptureFixture) -> Any:
    """Nome de conta e histórico fora do log — stdlib (`caplog`) E structlog."""
    caplog.set_level(logging.DEBUG)
    with capture_logs() as events:
        yield
    dumped = caplog.text + json.dumps(events, ensure_ascii=False, default=str)
    for forbidden in SECRETS:
        assert forbidden not in dumped, "nome de conta ou histórico vazou para o log"


async def _admin(db: AsyncSession) -> User:
    user = User(
        name="QA S16 admin",
        email=f"qa16-admin-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    db.add(user)
    await db.flush()
    return user


def _apply_service(db: AsyncSession) -> ClientMappingApplyService:
    catalog = MappingCatalogRepository(db)
    decisions = ClientMappingDecisionService(
        ClientMappingRepository(db),
        catalog=catalog,
        catalog_service=MappingCatalogService(catalog),
        accounting=AccountingDecisionSupport(db, settings=get_settings()),
    )
    return ClientMappingApplyService(
        db,
        repository=ClientMappingRepository(db),
        movements=ClientMovementsRepository(db),
        decisions=decisions,
    )


async def _events(db: AsyncSession, event: str, client_id: str) -> list[UsageEvent]:
    stmt = (
        select(UsageEvent)
        .where(UsageEvent.event == event)
        .execution_options(populate_existing=True)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [r for r in rows if r.props.get("client_id") == client_id]


async def _accounts_by_code(http: AsyncClient, client_id: str) -> dict[str, dict[str, Any]]:
    resp = await http.get(f"/api/v1/clients/{client_id}/accounting-chart", params={"pageSize": 100})
    assert resp.status_code == 200, resp.text
    return {a["code"]: a for a in resp.json()["data"]}


async def _import_plan(http: AsyncClient, client_id: str, content: bytes) -> dict[str, Any]:
    resp = await http.post(
        f"/api/v1/clients/{client_id}/accounting-chart/import",
        files={"file": ("plano.csv", content, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


async def _bind_default(http: AsyncClient, client_id: str, account_id: str) -> None:
    resp = await http.put(
        f"/api/v1/clients/{client_id}/source-accounts",
        json={"sourceType": "arquivo", "sourceAccountId": None, "accountingAccountId": account_id},
    )
    assert resp.status_code == 200, resp.text


def _mapping(client_id: str) -> str:
    return f"/api/v1/clients/{client_id}/mapping/conta_contabil"


async def _decide_all(
    http: AsyncClient,
    client_id: str,
    codes: dict[str, str],
    accounts: dict[str, dict[str, Any]],
    *,
    effective_from: str,
    history_suffix: str = "",
) -> None:
    resp = await http.post(
        f"{_mapping(client_id)}/decisions/batch",
        json={
            "effectiveFrom": effective_from,
            "confirmRetroactive": True,
            "decisions": [
                {
                    "categoryCode": codes[row["categoria_origem"]],
                    "sourceType": "arquivo",
                    "decision": "alvo",
                    "accountingAccountId": accounts[row["conta_contabil"]]["id"],
                    "history": row["historico_padrao"] + history_suffix,
                }
                for row in _decisoes()
            ],
        },
    )
    assert resp.status_code == 200, resp.text


class TestAmostraMsfgPeloCaminhoReal:
    async def test_cinco_campos_32_de_32_completude_100_e_materializacao_imutavel(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        await MappingCatalogRepository(db_session).seed_default_destinations(
            HOLOGRAM_ORGANIZATION_ID
        )
        admin = await _admin(db_session)
        login = await http.post(
            "/api/v1/auth/login", json={"email": admin.email, "password": PLAIN_PASSWORD}
        )
        assert login.status_code == 200, login.text

        # a. Cliente sem ERP, origem arquivo, mapeamento sem coluna de conta.
        created = await http.post("/api/v1/clients", json={"name": "Cliente Exemplo QA S16"})
        assert created.status_code == 201, created.text
        client_id: str = created.json()["id"]
        conn = await http.post(
            f"/api/v1/clients/{client_id}/connections", json={"provider_type": "arquivo"}
        )
        assert conn.status_code == 201, conn.text
        put = await http.put(f"/api/v1/clients/{client_id}/input-mapping", json=MAPPING)
        assert put.status_code == 200, put.text
        assert put.json()["data"]["mapping"]["accountColumn"] is None

        # b. O extrato de agosto pela ingestão da S14 (total declarado conferido).
        processed = await http.post(
            f"/api/v1/clients/{client_id}/file-origin/process",
            files={"file": ("extrato_cliente.csv", EXTRATO, "text/csv")},
            data={"competence": COMPETENCE, "declaredTotal": DECLARED_TOTAL},
        )
        assert processed.status_code == 201, processed.text
        assert processed.json()["data"]["rows"] == 32
        assert processed.json()["data"]["categoriesCreated"] == 23

        # c. O plano do cliente: 20 contas, 1 evento só com contagens.
        imported = await _import_plan(http, client_id, PLANO)
        assert imported == {"contas": 20, "contasNovas": 20, "contasInativadas": 0}
        (evento,) = await _events(db_session, "plano_contabil_importado", client_id)
        assert evento.props == {
            "client_id": client_id,
            "contas": 20,
            "contas_novas": 20,
            "contas_inativadas": 0,
        }
        accounts = await _accounts_by_code(http, client_id)
        assert len(accounts) == 20

        # e. As 23 decisões — o código vem da LISTA, pela grafia que a ingestão gravou.
        universe = await http.get(
            _mapping(client_id), params={"situation": "sem_decisao", "pageSize": 100}
        )
        assert universe.status_code == 200, universe.text
        codes = {i["categoryName"]: i["categoryCode"] for i in universe.json()["data"]}
        assert set(codes) == {row["categoria_origem"] for row in _decisoes()}, (
            "a categoria de classificacao_livre é a descrição APARADA"
        )
        await _decide_all(http, client_id, codes, accounts, effective_from=COMPETENCE)

        # Sem conta do banco: a prévia aponta o slot padrão e a materialização é 409.
        pending = await http.get(
            f"{_mapping(client_id)}/preview", params={"competence": COMPETENCE}
        )
        assert pending.status_code == 200, pending.text
        assert pending.json()["data"]["pendingSourceAccounts"] == [
            {"sourceType": "arquivo", "sourceAccountId": None}
        ]
        blocked = await http.post(
            f"{_mapping(client_id)}/materializations",
            json={
                "competence": COMPETENCE,
                "previewToken": pending.json()["data"]["previewToken"],
            },
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "CONTA_DO_BANCO_PENDENTE"
        assert blocked.json()["error"]["details"] == {
            "pendingSourceAccounts": [{"sourceType": "arquivo", "sourceAccountId": None}]
        }
        assert await _events(db_session, "fechamento_produzido", client_id) == []

        # d. Conta padrão do banco = 649 (o arquivo não tem coluna de conta).
        await _bind_default(http, client_id, accounts[BANK_CODE]["id"])

        # f. Prévia completa → materialização.
        preview = await http.get(
            f"{_mapping(client_id)}/preview", params={"competence": COMPETENCE}
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["pendingSourceAccounts"] == []
        assert Decimal(data["partidaCompleteness"]["pct"]) == Decimal("100.00")
        assert Decimal(data["partidaCompleteness"]["targetAmount"]) == Decimal("53570.99")
        assert len(data["accountingCategories"]) == 23
        assert all(not c["historyMissing"] for c in data["accountingCategories"])
        assert all(not c["pendingSourceAccounts"] for c in data["accountingCategories"])

        mat = await http.post(
            f"{_mapping(client_id)}/materializations",
            json={"competence": COMPETENCE, "previewToken": data["previewToken"]},
        )
        assert mat.status_code == 201, mat.text
        materialization_id = UUID(mat.json()["data"]["id"])
        assert Decimal(mat.json()["data"]["partidaCompleteness"]["pct"]) == Decimal("100.00")

        client = await db_session.get(Client, UUID(client_id))
        assert client is not None

        async def _five_fields() -> list[tuple[str, str, str, Decimal, str]]:
            lines = await _apply_service(db_session).materialized_lines(client, materialization_id)
            rows: list[tuple[str, str, str, Decimal, str]] = []
            for line in lines:
                item = line.item
                assert item.accounting_account_code is not None
                assert item.bank_account_code is not None
                assert line.history is not None
                partida = derive_partida(
                    item.amount, item.accounting_account_code, item.bank_account_code
                )
                assert partida is not None
                rows.append(
                    (
                        item.movement_date.strftime("%d/%m/%Y"),
                        partida.debit,
                        partida.credit,
                        abs(item.amount),
                        line.history,
                    )
                )
            return rows

        esperados = _esperados()
        obtidos = await _five_fields()
        assert len(esperados) == len(obtidos) == 32
        # Multiconjunto: a ordem de gravação não é contrato; os cinco campos são.
        assert sorted(obtidos) == sorted(esperados)
        entradas = [r for r in obtidos if r[1] == BANK_CODE]
        saidas = [r for r in obtidos if r[2] == BANK_CODE]
        assert (len(entradas), len(saidas)) == (15, 17)
        assert sum(r[3] for r in entradas) == Decimal("29377.33")
        assert sum(r[3] for r in saidas) == Decimal("24193.66")

        # Imutabilidade: plano reimportado (renomeia 650, some 662), histórico novo
        # (vigência de setembro — agosto está materializado) e banco trocado.
        linhas = PLANO.decode("utf-8").splitlines()
        reimport = "\n".join(
            line.replace(";", ";RENOMEADA ", 1) if line.startswith("650;") else line
            for line in linhas
            if not line.startswith("662;")
        ).encode("utf-8")
        reimported = await _import_plan(http, client_id, reimport)
        assert reimported["contasInativadas"] == 1
        after = await _accounts_by_code(http, client_id)
        assert after["662"]["active"] is False
        assert after["650"]["name"].startswith("RENOMEADA ")
        postable = {c: a for c, a in after.items() if a["postable"]}
        await _decide_all(
            http,
            client_id,
            codes,
            {**postable, "662": after["650"]},
            effective_from=NEXT_COMPETENCE,
            history_suffix=" - NOVO",
        )
        await _bind_default(http, client_id, after["650"]["id"])

        assert sorted(await _five_fields()) == sorted(esperados)
        listing = await http.get(f"{_mapping(client_id)}/materializations")
        assert listing.status_code == 200, listing.text
        (summary,) = [m for m in listing.json()["data"] if m["id"] == str(materialization_id)]
        assert Decimal(summary["partidaCompleteness"]["pct"]) == Decimal("100.00")

        # Guardrails de telemetria: 2 importações = 2 eventos; nada de nome nem histórico.
        assert len(await _events(db_session, "plano_contabil_importado", client_id)) == 2
        props = json.dumps(
            [r.props for r in (await db_session.execute(select(UsageEvent))).scalars().all()],
            ensure_ascii=False,
        )
        for forbidden in SECRETS:
            assert forbidden not in props, "nome de conta ou histórico em usage_events"
