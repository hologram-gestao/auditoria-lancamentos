"""Leitura por destino, exportação e importação do de-para contra o banco (BACK 12.5).

Afirma pelo HTTP: paginação pelo alias `pageSize` (e o teto 100 → 400), os quatro
filtros de situação no servidor, busca por código (e nome NÃO buscável), exportação
com linha `export` na trilha, prévia que não grava, aplicação que exige confirmação e
cria VIGÊNCIA NOVA sem sobrescrever a vigente, arquivo inválido recusado, operador
403 e cliente encerrado 409. Cross-tenant/cross-org: bateria da lista canônica.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select, update

from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    Client,
    ClientChartOfAccount,
    ClientMappingDecision,
    ClientMovement,
    DecisionOrigin,
    MappingDestination,
    MappingTarget,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_mapping.portability import EXPORT_COLUMNS
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Portabilidade#1"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _user(
    db: AsyncSession, *, role: UserRole, scope: UserScope = UserScope.SYSTEM, client_id: Any = None
) -> User:
    user = User(
        name="Portabilidade",
        email=f"pt-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


class World:
    admin: User
    operator: User
    client: Client
    destination: MappingDestination


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.client = Client(name="Cliente portabilidade", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    w.operator = await _user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    w.destination = (
        await db_session.execute(
            select(MappingDestination).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == "demonstrativo_contabil",
            )
        )
    ).scalar_one()
    for code in ("1.01", "1.02"):
        db_session.add(
            MappingTarget(destination_id=w.destination.id, code=code, name=f"Alvo {code}")
        )
    # Universo: 5 do plano de contas + 1 só na base de movimentos.
    for code, dre in (
        ("2.01", "1.01"),
        ("2.02", None),
        ("2.03", None),
        ("2.04", None),
        ("2.05", None),
    ):
        db_session.add(
            ClientChartOfAccount(client_id=w.client.id, category_code=code, dre_code=dre)
        )
    db_session.add(
        ClientMovement(
            client_id=w.client.id,
            source_type="omie",
            source_movement_id="1",
            competence=date(2026, 6, 1),
            movement_date=date(2026, 6, 10),
            amount=-10,
            category_code="3.01",
        )
    )
    await db_session.flush()
    return w


def _base(w: World) -> str:
    return f"/api/v1/clients/{w.client.id}/mapping/demonstrativo_contabil"


def _xlsx(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(list(EXPORT_COLUMNS))
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _files(content: bytes, name: str = "de-para.xlsx") -> dict[str, Any]:
    return {"file": (name, content, XLSX)}


async def _decisions(db: AsyncSession, w: World) -> list[ClientMappingDecision]:
    stmt = (
        select(ClientMappingDecision)
        .where(ClientMappingDecision.client_id == w.client.id)
        .order_by(ClientMappingDecision.category_code, ClientMappingDecision.effective_from)
    )
    return list((await db.execute(stmt)).scalars().all())


class TestLista:
    async def test_universo_paginado_pelo_alias_e_teto(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.operator)
        page = await client_with_db.get(_base(world), params={"pageSize": 2, "page": 3})
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["pagination"]["total"] == 6
        assert [r["categoryCode"] for r in body["data"]] == ["3.01"]
        teto = await client_with_db.get(_base(world), params={"pageSize": 101})
        assert teto.status_code == 400
        assert teto.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_filtros_de_situacao_no_servidor(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await client_with_db.post(f"{_base(world)}/inherit", json={})
        await client_with_db.post(
            f"{_base(world)}/decisions", json={"categoryCode": "2.02", "decision": "nao_mapear"}
        )
        await client_with_db.post(
            f"{_base(world)}/decisions",
            json={"categoryCode": "2.03", "decision": "alvo", "targetCode": "1.02"},
        )
        contagens = {}
        for situacao in ("herdada", "confirmada", "nao_mapear", "sem_decisao"):
            resp = await client_with_db.get(_base(world), params={"situation": situacao})
            assert resp.status_code == 200, resp.text
            contagens[situacao] = resp.json()["pagination"]["total"]
        assert contagens == {"herdada": 1, "confirmada": 1, "nao_mapear": 1, "sem_decisao": 3}
        invalida = await client_with_db.get(_base(world), params={"situation": "pendente"})
        assert invalida.status_code == 400

    async def test_busca_por_codigo_e_nome_nao_encontra(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        por_codigo = await client_with_db.get(_base(world), params={"code": "2.0"})
        por_nome = await client_with_db.get(_base(world), params={"code": "Receita"})
        assert por_codigo.json()["pagination"]["total"] == 5
        assert por_nome.json()["pagination"]["total"] == 0
        # Sem origem conectada, o nome não resolve — e a lista sai assim mesmo (200).
        assert all(r["categoryNameResolved"] is False for r in por_codigo.json()["data"])


class TestExportacao:
    async def test_planilha_com_codigos_e_linha_na_trilha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        await client_with_db.post(
            f"{_base(world)}/decisions",
            json={"categoryCode": "2.01", "decision": "alvo", "targetCode": "1.01"},
        )
        antes = await db_session.scalar(
            select(func.count(AccessAudit.id)).where(
                AccessAudit.action == "export", AccessAudit.client_id == world.client.id
            )
        )
        resp = await client_with_db.get(f"{_base(world)}/export")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith(XLSX)
        assert "Cliente portabilidade" not in resp.headers["content-disposition"]
        ws = load_workbook(io.BytesIO(resp.content)).active
        assert ws is not None
        linhas = [list(r) for r in ws.iter_rows(values_only=True)]
        assert linhas[0] == list(EXPORT_COLUMNS)
        linha = next(r for r in linhas if r[1] == "2.01")
        assert (linha[3], linha[4], linha[5], linha[6]) == (
            "demonstrativo_contabil",
            "alvo",
            "1.01",
            "Alvo 1.01",
        )
        assert linha[8]  # vigência
        depois = await db_session.scalar(
            select(func.count(AccessAudit.id)).where(
                AccessAudit.action == "export", AccessAudit.client_id == world.client.id
            )
        )
        assert depois == (antes or 0) + 1


class TestImportacao:
    async def test_previa_nao_grava_e_classifica(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _xlsx(
            [
                ["omie", "2.01", "x", "demonstrativo_contabil", "alvo", "1.01", "", "", ""],
                ["omie", "8.88", "x", "demonstrativo_contabil", "nao_mapear", "", "", "", ""],
                ["omie", "2.02", "x", "demonstrativo_contabil", "alvo", "9.99", "", "", ""],
            ]
        )
        resp = await client_with_db.post(f"{_base(world)}/import/preview", files=_files(content))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["created"] == 1
        assert [(r["line"], r["reason"]) for r in data["rejected"]] == [
            (3, "categoria_inexistente"),
            (4, "alvo_inexistente"),
        ]
        assert await _decisions(db_session, world) == []

    async def test_aplicar_exige_confirmacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        content = _xlsx(
            [["omie", "2.01", "x", "demonstrativo_contabil", "alvo", "1.01", "", "", ""]]
        )
        sem = await client_with_db.post(f"{_base(world)}/import", files=_files(content))
        assert sem.status_code == 409, sem.text
        assert await _decisions(db_session, world) == []
        com = await client_with_db.post(
            f"{_base(world)}/import", files=_files(content), data={"confirm": "true"}
        )
        assert com.status_code == 200, com.text
        assert com.json()["data"]["result"]["created"] == 1

    async def test_importar_sobre_confirmada_cria_vigencia_nova_sem_sobrescrever(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        confirmada = ClientMappingDecision(
            client_id=world.client.id,
            source_type="omie",
            category_code="2.01",
            destination_id=world.destination.id,
            decision_type="alvo",
            target_id=(
                await db_session.execute(
                    select(MappingTarget.id).where(
                        MappingTarget.destination_id == world.destination.id,
                        MappingTarget.code == "1.01",
                    )
                )
            ).scalar_one(),
            origin=DecisionOrigin.CONFIRMADA.value,
            effective_from=date(2026, 1, 1),
            author_id=world.admin.id,
        )
        db_session.add(confirmada)
        await db_session.flush()
        await _login(client_with_db, world.admin)
        content = _xlsx(
            [["omie", "2.01", "x", "demonstrativo_contabil", "alvo", "1.02", "", "", ""]]
        )
        previa = await client_with_db.post(f"{_base(world)}/import/preview", files=_files(content))
        assert previa.json()["data"]["altersConfirmed"] == 1

        resp = await client_with_db.post(
            f"{_base(world)}/import", files=_files(content), data={"confirm": "true"}
        )
        assert resp.status_code == 200, resp.text
        linhas = await _decisions(db_session, world)
        assert len(linhas) == 2, "a vigente antiga FICA; a nova é outra linha"
        assert linhas[0].effective_from == date(2026, 1, 1)
        assert linhas[0].target_id == confirmada.target_id
        assert linhas[1].effective_from > linhas[0].effective_from

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("de-para.xlsx", b"%PDF-1.7 isto nao e xlsx"),
            ("de-para.csv", b"PK\x03\x04qualquer"),
        ],
    )
    async def test_arquivo_invalido_e_400(
        self, client_with_db: AsyncClient, world: World, name: str, content: bytes
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            f"{_base(world)}/import/preview", files=_files(content, name)
        )
        assert resp.status_code == 400, resp.text

    async def test_operador_403_e_encerrado_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        content = _xlsx([])
        await _login(client_with_db, world.operator)
        negado = await client_with_db.post(f"{_base(world)}/import", files=_files(content))
        assert negado.status_code == 403
        await db_session.execute(
            update(Client).where(Client.id == world.client.id).values(closed_at=datetime.now(UTC))
        )
        await _login(client_with_db, world.admin)
        encerrado = await client_with_db.post(
            f"{_base(world)}/import", files=_files(content), data={"confirm": "true"}
        )
        assert encerrado.status_code == 409
