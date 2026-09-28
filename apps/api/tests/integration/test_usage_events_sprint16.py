"""Instrumentação do outcome da Sprint 16 contra Postgres (BACK 16.4).

- `plano_contabil_importado`: importação bem-sucedida grava EXATAMENTE 1 linha com as 4
  props; importação recusada grava 0 (teste que CONTA linhas); sem dedup;
- completude de partida na prévia, na resposta da materialização e na listagem das
  materializações do `conta_contabil` (`null` nos outros destinos), e o número não muda
  depois do encerramento (snapshot imutável);
- ponta a ponta com a amostra REAL anonimizada (MSFG, agosto/2026): plano, 23 decisões
  com histórico, conta do banco `649` padrão e os 32 movimentos → completude 100,00 e,
  linha a linha, data, débito, crédito, valor absoluto e histórico batendo com
  `lancamentos_esperados.csv`.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto_service import new_client_dek
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientAccountingAccount,
    ClientMovement,
    ClientMovementSync,
    DecimalSeparator,
    MappingDestination,
    MappingTarget,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_file_ingestion.reader import parse_amount
from app.modules.client_mapping.accounting import AccountingDecisionSupport
from app.modules.client_mapping.materialization import ClientMappingApplyService
from app.modules.client_mapping.partida import derive_partida
from app.modules.client_mapping.repository import ClientMappingRepository
from app.modules.client_mapping.service import ClientMappingDecisionService
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.mapping_catalog.repository import MappingCatalogRepository
from app.modules.mapping_catalog.service import MappingCatalogService

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Outcome16#1"
AGO = date(2026, 8, 1)
_MONTH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "accounting_sample"
    / "cliente_exemplo_2026_08"
)


class World:
    admin: User
    client: Client


@pytest.fixture
async def world(db_session: AsyncSession, client_with_db: AsyncClient) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = User(
        name="Outcome",
        email=f"out-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    db_session.add(w.admin)
    await db_session.flush()
    w.client = Client(name="Cliente Exemplo", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    _cipher, w.client.dek_wrapped = await new_client_dek(w.client.id, settings=get_settings())
    await db_session.flush()
    resp = await client_with_db.post(
        "/api/v1/auth/login", json={"email": w.admin.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return w


async def _import(http: AsyncClient, w: World, content: bytes) -> Response:
    return await http.post(
        f"/api/v1/clients/{w.client.id}/accounting-chart/import",
        files={"file": ("plano.csv", content, "text/csv")},
    )


async def _events(db: AsyncSession, w: World) -> list[UsageEvent]:
    stmt = select(UsageEvent).where(UsageEvent.event == "plano_contabil_importado")
    rows = (await db.execute(stmt)).scalars().all()
    return [r for r in rows if r.props.get("client_id") == str(w.client.id)]


class TestEventoDaImportacao:
    async def test_sucesso_grava_uma_linha_e_recusa_grava_zero(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        recusada = await _import(
            client_with_db, world, b"codigo_reduzido;nome;tipo\n649;A;analitica\n649;B;analitica\n"
        )
        assert recusada.status_code == 422
        assert await _events(db_session, world) == []

        ok = await _import(client_with_db, world, (_MONTH / "plano_contabil.csv").read_bytes())
        assert ok.status_code == 200, ok.text
        (event,) = await _events(db_session, world)
        assert event.session_id is None
        assert event.props == {
            "client_id": str(world.client.id),
            "contas": 20,
            "contas_novas": 20,
            "contas_inativadas": 0,
        }

        again = await _import(
            client_with_db, world, b"codigo_reduzido;nome;tipo\n649;Banco;analitica\n"
        )
        assert again.status_code == 200
        eventos = await _events(db_session, world)
        assert len(eventos) == 2, "sem dedup: cada importação bem-sucedida é uma linha"
        assert {e.props["contas_inativadas"] for e in eventos} == {0, 19}


# ---------------------------------------------------------------------------
# Ponta a ponta com a amostra real
# ---------------------------------------------------------------------------


def _extrato() -> list[tuple[date, str, Decimal]]:
    text = (_MONTH / "extrato_cliente.csv").read_text(encoding="utf-8")
    return [
        (
            datetime.strptime(r["Data"], "%d/%m/%Y").date(),
            r["Descricao"].strip(),
            parse_amount(r["Valor"], DecimalSeparator.COMMA),
        )
        for r in csv.DictReader(io.StringIO(text), delimiter=";")
    ]


def _decisoes() -> list[dict[str, str]]:
    text = (_MONTH / "decisoes_depara.csv").read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def _esperados() -> list[list[str]]:
    raw = (_MONTH / "lancamentos_esperados.csv").read_bytes().decode("latin-1")
    return [line.split(";", 4) for line in raw.split("\r\n") if line]


def _brl(amount: Decimal) -> str:
    """`R$ 1.234,56` — só para COMPARAR com o arquivo (a formatação é da Sprint 13)."""
    inteiro, centavos = f"{abs(amount):.2f}".split(".")
    return f"R$ {int(inteiro):,}".replace(",", ".") + f",{centavos}"


async def _seed_sample(http: AsyncClient, db: AsyncSession, w: World) -> None:
    """Plano, base de agosto (32 linhas sem conta de origem), 23 decisões e banco 649."""
    ok = await _import(http, w, (_MONTH / "plano_contabil.csv").read_bytes())
    assert ok.status_code == 200, ok.text
    stmt = select(ClientAccountingAccount).where(ClientAccountingAccount.client_id == w.client.id)
    accounts = {a.code: a.id for a in (await db.execute(stmt)).scalars().all()}
    # Categoria de origem do arquivo = a descrição aparada (classificação livre); o
    # código estável é `arq-NN`, como o registry da S14 geraria.
    codes = {
        row["categoria_origem"].strip(): f"arq-{n:02d}"
        for n, row in enumerate(_decisoes(), start=1)
    }
    db.add(ClientMovementSync(client_id=w.client.id, competence=AGO, synced_at=datetime.now(UTC)))
    for n, (entry_date, description, amount) in enumerate(_extrato(), start=1):
        db.add(
            ClientMovement(
                client_id=w.client.id,
                source_type="arquivo",
                source_movement_id=f"{n:02d}",
                source_account_id=None,
                competence=AGO,
                movement_date=entry_date,
                amount=amount,
                category_code=codes[description],
            )
        )
    await db.flush()
    batch = await http.post(
        f"/api/v1/clients/{w.client.id}/mapping/conta_contabil/decisions/batch",
        json={
            "effectiveFrom": "2026-08",
            "confirmRetroactive": True,
            "decisions": [
                {
                    "categoryCode": codes[row["categoria_origem"].strip()],
                    "sourceType": "arquivo",
                    "decision": "alvo",
                    "accountingAccountId": str(accounts[row["conta_contabil"]]),
                    "history": row["historico_padrao"],
                }
                for row in _decisoes()
            ],
        },
    )
    assert batch.status_code == 200, batch.text
    bind = await http.put(
        f"/api/v1/clients/{w.client.id}/source-accounts",
        json={
            "sourceType": "arquivo",
            "sourceAccountId": None,
            "accountingAccountId": str(accounts["649"]),
        },
    )
    assert bind.status_code == 200, bind.text


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


_BASE = "/api/v1/clients/{cid}/mapping/{kind}"


class TestPontaAPontaComAAmostra:
    async def test_as_32_linhas_batem_com_completude_100(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_sample(client_with_db, db_session, world)
        base = _BASE.format(cid=world.client.id, kind="conta_contabil")
        preview = await client_with_db.get(f"{base}/preview", params={"competence": "2026-08"})
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["pendingSourceAccounts"] == []
        assert data["partidaCompleteness"]["pct"] == "100.00"
        # Σ|valor| = entradas 29.377,33 + saídas 24.193,66 (README da amostra).
        assert Decimal(data["partidaCompleteness"]["targetAmount"]) == Decimal("53570.99")

        mat = await client_with_db.post(
            f"{base}/materializations",
            json={"competence": "2026-08", "previewToken": data["previewToken"]},
        )
        assert mat.status_code == 201, mat.text
        assert mat.json()["data"]["partidaCompleteness"]["pct"] == "100.00"

        lines = await _apply_service(db_session).materialized_lines(
            world.client, UUID(mat.json()["data"]["id"])
        )
        assert len(lines) == 32
        for line, esperado in zip(lines, _esperados(), strict=True):
            item = line.item
            assert item.accounting_account_code is not None
            assert item.bank_account_code is not None
            partida = derive_partida(
                item.amount, item.accounting_account_code, item.bank_account_code
            )
            assert partida is not None
            assert [
                item.movement_date.strftime("%d/%m/%Y"),
                partida.debit,
                partida.credit,
                _brl(item.amount),
                line.history,
            ] == esperado

        listing = await client_with_db.get(f"{base}/materializations")
        (summary,) = listing.json()["data"]
        assert summary["partidaCompleteness"] == mat.json()["data"]["partidaCompleteness"]

    async def test_nula_nos_outros_destinos_e_estavel_apos_encerrar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_sample(client_with_db, db_session, world)
        base = _BASE.format(cid=world.client.id, kind="conta_contabil")
        preview = (
            await client_with_db.get(f"{base}/preview", params={"competence": "2026-08"})
        ).json()["data"]
        mat = await client_with_db.post(
            f"{base}/materializations",
            json={"competence": "2026-08", "previewToken": preview["previewToken"]},
        )
        assert mat.status_code == 201, mat.text
        antes = (await client_with_db.get(f"{base}/materializations")).json()["data"][0][
            "partidaCompleteness"
        ]
        assert antes["pct"] == "100.00"

        fluxo = (
            await db_session.execute(
                select(MappingDestination).where(
                    MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                    MappingDestination.destination_type == "fluxo_de_caixa",
                )
            )
        ).scalar_one()
        db_session.add(MappingTarget(destination_id=fluxo.id, code="1", name="Tudo"))
        await db_session.flush()
        outro = _BASE.format(cid=world.client.id, kind="fluxo_de_caixa")
        outro_preview = (
            await client_with_db.get(f"{outro}/preview", params={"competence": "2026-08"})
        ).json()["data"]
        assert outro_preview["partidaCompleteness"] is None
        outro_mat = await client_with_db.post(
            f"{outro}/materializations",
            json={
                "competence": "2026-08",
                "previewToken": outro_preview["previewToken"],
                "confirmPartialCoverage": True,
            },
        )
        assert outro_mat.status_code == 201, outro_mat.text
        assert outro_mat.json()["data"]["partidaCompleteness"] is None
        outro_lista = (await client_with_db.get(f"{outro}/materializations")).json()["data"]
        assert outro_lista[0]["partidaCompleteness"] is None

        closed = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert closed.status_code == 204, closed.text
        depois = (await client_with_db.get(f"{base}/materializations")).json()["data"][0][
            "partidaCompleteness"
        ]
        assert depois == antes, "o snapshot é imutável: a purga não muda o número"
