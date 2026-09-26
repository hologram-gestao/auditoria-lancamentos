"""QA da Sprint 12 (86e3ezyvt): a TESE da sprint, ponta a ponta pela API.

A mesma categoria produz resultados DIFERENTES em dois destinos sobre o MESMO dado.
Reproduz o caso medido em 10/09/2026 no escritório parceiro: quatro categorias sem
conta de demonstrativo — `Adiant. de Clientes (Controle)` 24 · `OE - Adiantamento de
Clientes` 21 · `OS - Transferência Entre Contas de Mesma Titularidade` 20 ·
`OS - Adiantamento a Fornecedores` 3 — recebem `nao_mapear` no demonstrativo contábil
e alvo REAL no fluxo de caixa.

⚠️ Dado SINTÉTICO com as mesmas contagens: a fixture real do extrato
(`tests/fixtures/omie/listar_extrato.response.json`, uma conta de cartão) não contém
essas quatro categorias. Os códigos abaixo são inventados; nome de categoria nunca é
persistido (§4.5), então só as CONTAGENS importam para a tese.

O cenário roda com o `admin` da organização E com o `manager` da carteira — é a
armadilha do R6 (`edit_client` é admin-only; se o de-para dependesse dela, o
contador parceiro construiria a carteira e não conseguiria classificá-la). O catálogo
(`manage_mapping_catalog`) é do admin nos dois casos: o manager só decide.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientAssignment,
    ClientChartOfAccount,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    ClientMovement,
    ClientMovementSync,
    MappingDestination,
    Organization,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_mapping.service import current_competence
from app.modules.client_movements.competence import format_competence
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from datetime import date

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@QaDoisDestinos#1"

#: As quatro categorias só-caixa (códigos sintéticos) → quantidade de movimentos.
SO_CAIXA: dict[str, int] = {
    "2.10.01": 24,  # Adiant. de Clientes (Controle)
    "2.10.02": 21,  # OE - Adiantamento de Clientes
    "2.10.03": 20,  # OS - Transferência Entre Contas de Mesma Titularidade
    "2.10.04": 3,  # OS - Adiantamento a Fornecedores
}
#: Categorias comuns, COM conta de demonstrativo no plano de contas: código → (dre, qtd, valor).
COMUNS: dict[str, tuple[str, int, str]] = {
    "1.01.01": ("R.01", 5, "200.00"),
    "2.01.01": ("D.01", 7, "-50.00"),
}
VALOR_SO_CAIXA = Decimal("-10.00")
FLUXO_ALVO_SO_CAIXA = {
    "2.10.01": "FC.ADI",
    "2.10.02": "FC.ADI",
    "2.10.03": "FC.TRF",
    "2.10.04": "FC.ADF",
}


async def _user(
    db: AsyncSession,
    *,
    role: UserRole,
    organization_id: Any = HOLOGRAM_ORGANIZATION_ID,
) -> User:
    user = User(
        name=f"QA S12 {role.value}",
        email=f"qa12-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=UserScope.SYSTEM.value,
        organization_id=organization_id,
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
    manager: User
    admin_b: User
    client: Client
    competence: date
    demonstrativo: MappingDestination
    fluxo: MappingDestination


async def _destination(db: AsyncSession, destination_type: str) -> MappingDestination:
    return (
        await db.execute(
            select(MappingDestination).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == destination_type,
            )
        )
    ).scalar_one()


def _movement(w: World, mid: str, code: str, amount: Decimal) -> ClientMovement:
    return ClientMovement(
        client_id=w.client.id,
        source_type="omie",
        source_movement_id=mid,
        competence=w.competence,
        movement_date=w.competence,
        amount=amount,
        category_code=code,
        source_account_id="CC-1",
    )


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, role=UserRole.ADMIN)
    w.manager = await _user(db_session, role=UserRole.MANAGER)
    w.client = Client(name="Cliente QA S12 SEGREDO", active=True, created_by=w.admin.id)
    db_session.add(w.client)
    await db_session.flush()
    db_session.add(
        ClientAssignment(
            client_id=w.client.id, user_id=w.manager.id, is_primary=True, assigned_by=w.admin.id
        )
    )
    org_b = Organization(name=f"Escritorio B QA {uuid4().hex[:6]}")
    db_session.add(org_b)
    await db_session.flush()
    w.admin_b = await _user(db_session, role=UserRole.ADMIN, organization_id=org_b.id)

    # Competência CORRENTE (a do servidor): as decisões nascem nela sem retroatividade.
    w.competence = current_competence()
    db_session.add(
        ClientMovementSync(
            client_id=w.client.id, competence=w.competence, synced_at=datetime.now(UTC)
        )
    )
    seq = 0
    for code, qtd in SO_CAIXA.items():
        for _ in range(qtd):
            seq += 1
            db_session.add(_movement(w, str(seq), code, VALOR_SO_CAIXA))
    for code, (dre, qtd, valor) in COMUNS.items():
        db_session.add(
            ClientChartOfAccount(client_id=w.client.id, category_code=code, dre_code=dre)
        )
        for _ in range(qtd):
            seq += 1
            db_session.add(_movement(w, str(seq), code, Decimal(valor)))
    # As quatro NÃO têm conta de demonstrativo na origem ("sem destino declarado").
    for code in SO_CAIXA:
        db_session.add(
            ClientChartOfAccount(client_id=w.client.id, category_code=code, dre_code=None)
        )
    await db_session.flush()
    w.demonstrativo = await _destination(db_session, "demonstrativo_contabil")
    w.fluxo = await _destination(db_session, "fluxo_de_caixa")
    return w


def _mapping(w: World, destination_type: str) -> str:
    return f"/api/v1/clients/{w.client.id}/mapping/{destination_type}"


async def _catalog(http: AsyncClient, w: World) -> None:
    """O ADMIN cadastra os alvos (catálogo é da organização)."""
    await _login(http, w.admin)
    demo = await http.post(
        f"/api/v1/mapping-destinations/{w.demonstrativo.id}/targets",
        json={
            "targets": [{"code": "R.01", "name": "Receita"}, {"code": "D.01", "name": "Despesa"}]
        },
    )
    assert demo.status_code == 201, demo.text
    fluxo = await http.post(
        f"/api/v1/mapping-destinations/{w.fluxo.id}/targets",
        json={
            "targets": [
                {"code": "FC.ADI", "name": "Adiantamentos de clientes"},
                {"code": "FC.TRF", "name": "Transferências"},
                {"code": "FC.ADF", "name": "Adiantamentos a fornecedores"},
                {"code": "FC.OPE", "name": "Operacional"},
            ]
        },
    )
    assert fluxo.status_code == 201, fluxo.text


async def _materialize(http: AsyncClient, w: World, destination_type: str) -> dict[str, Any]:
    comp = format_competence(w.competence)
    prev = await http.get(f"{_mapping(w, destination_type)}/preview", params={"competence": comp})
    assert prev.status_code == 200, prev.text
    preview: dict[str, Any] = prev.json()["data"]
    mat = await http.post(
        f"{_mapping(w, destination_type)}/materializations",
        json={"competence": comp, "previewToken": preview["previewToken"]},
    )
    assert mat.status_code == 201, mat.text
    return preview


async def _items_by_category(
    db: AsyncSession, w: World, destination: MappingDestination
) -> dict[str, tuple[str, str | None, int]]:
    mat = (
        await db.execute(
            select(ClientMappingMaterialization).where(
                ClientMappingMaterialization.client_id == w.client.id,
                ClientMappingMaterialization.destination_id == destination.id,
            )
        )
    ).scalar_one()
    rows = (
        await db.execute(
            select(
                ClientMappingMaterializationItem.category_code,
                ClientMappingMaterializationItem.situation,
                ClientMappingMaterializationItem.target_code,
                func.count(),
            )
            .where(ClientMappingMaterializationItem.materialization_id == mat.id)
            .group_by(
                ClientMappingMaterializationItem.category_code,
                ClientMappingMaterializationItem.situation,
                ClientMappingMaterializationItem.target_code,
            )
        )
    ).all()
    out: dict[str, tuple[str, str | None, int]] = {}
    for category, situation, target, count in rows:
        assert category not in out, f"{category} em mais de uma situação no mesmo destino"
        out[category] = (situation, target, count)
    return out


@pytest.mark.parametrize("actor", ["admin", "manager"])
async def test_mesma_categoria_dois_destinos_saidas_diferentes(
    client_with_db: AsyncClient, db_session: AsyncSession, world: World, actor: str
) -> None:
    w = world
    await _catalog(client_with_db, w)
    await _login(client_with_db, w.admin if actor == "admin" else w.manager)

    # Herança: só o demonstrativo herda; as quatro sem conta de demonstrativo ficam SEM decisão.
    herda = await client_with_db.post(f"{_mapping(w, 'demonstrativo_contabil')}/inherit", json={})
    assert herda.status_code == 200, herda.text
    assert herda.json()["data"]["state"] == "ok"
    assert herda.json()["data"]["created"] == len(COMUNS)
    assert herda.json()["data"]["withoutDre"] == len(SO_CAIXA)
    nao_herda = await client_with_db.post(f"{_mapping(w, 'fluxo_de_caixa')}/inherit", json={})
    assert nao_herda.status_code == 200, nao_herda.text
    assert nao_herda.json()["data"]["state"] == "destino_sem_heranca"

    demo = await client_with_db.post(
        f"{_mapping(w, 'demonstrativo_contabil')}/decisions/batch",
        json={"decisions": [{"categoryCode": c, "decision": "nao_mapear"} for c in SO_CAIXA]},
    )
    assert demo.status_code == 200, demo.text
    fluxo_decisions = [
        {"categoryCode": c, "decision": "alvo", "targetCode": FLUXO_ALVO_SO_CAIXA[c]}
        for c in SO_CAIXA
    ] + [{"categoryCode": c, "decision": "alvo", "targetCode": "FC.OPE"} for c in COMUNS]
    fluxo = await client_with_db.post(
        f"{_mapping(w, 'fluxo_de_caixa')}/decisions/batch", json={"decisions": fluxo_decisions}
    )
    assert fluxo.status_code == 200, fluxo.text

    p_demo = await _materialize(client_with_db, w, "demonstrativo_contabil")
    p_fluxo = await _materialize(client_with_db, w, "fluxo_de_caixa")

    total_so_caixa = sum(SO_CAIXA.values())  # 68
    total_comuns = sum(q for _, q, _ in COMUNS.values())  # 12
    s_demo, s_fluxo = p_demo["situations"], p_fluxo["situations"]
    assert s_demo["naoMapear"]["count"] == total_so_caixa
    assert Decimal(s_demo["naoMapear"]["amount"]) == Decimal("680.00")
    assert s_demo["alvo"]["count"] == total_comuns
    assert s_fluxo["alvo"]["count"] == total_so_caixa + total_comuns
    assert s_fluxo["naoMapear"]["count"] == 0
    for s in (s_demo, s_fluxo):
        assert s["semDecisao"]["count"] == 0
        assert s["semCategoria"]["count"] == 0
    assert Decimal(p_demo["coveragePct"]) == Decimal("100.00")
    assert Decimal(p_fluxo["coveragePct"]) == Decimal("100.00")
    assert Decimal(p_demo["naoMapearPct"]) > 0
    assert Decimal(p_fluxo["naoMapearPct"]) == 0

    # A TESE, no registro imutável: mesma categoria, saídas diferentes, contagens 24/21/20/3.
    itens_demo = await _items_by_category(db_session, w, w.demonstrativo)
    itens_fluxo = await _items_by_category(db_session, w, w.fluxo)
    for code, qtd in SO_CAIXA.items():
        assert itens_demo[code] == ("nao_mapear", None, qtd)
        assert itens_fluxo[code] == ("alvo", FLUXO_ALVO_SO_CAIXA[code], qtd)
    assert [itens_demo[c][2] for c in SO_CAIXA] == [24, 21, 20, 3]
    for code, (dre, qtd, _) in COMUNS.items():
        assert itens_demo[code] == ("alvo", dre, qtd)
        assert itens_fluxo[code] == ("alvo", "FC.OPE", qtd)

    # Instrumentação: 1 depara_aplicado por materialização, só IDs e números.
    eventos = (
        (await db_session.execute(select(UsageEvent).where(UsageEvent.event == "depara_aplicado")))
        .scalars()
        .all()
    )
    eventos = [e for e in eventos if e.props.get("client_id") == str(w.client.id)]
    assert sorted(e.props["destino"] for e in eventos) == [
        "demonstrativo_contabil",
        "fluxo_de_caixa",
    ]
    chaves = {
        "client_id",
        "destino",
        "valor_com_decisao_centavos",
        "valor_nao_mapear_centavos",
        "valor_sem_decisao_centavos",
        "categorias_sem_decisao",
    }
    por_destino = {e.props["destino"]: e.props for e in eventos}
    for props in por_destino.values():
        assert set(props) == chaves
        texto = repr(props)
        for proibido in [*SO_CAIXA, *COMUNS, "FC.", "R.01", "D.01", "SEGREDO", "Receita"]:
            assert proibido not in texto
    assert por_destino["demonstrativo_contabil"]["valor_nao_mapear_centavos"] == 68000
    assert por_destino["fluxo_de_caixa"]["valor_nao_mapear_centavos"] == 0


async def test_organizacao_b_nao_alcanca_o_de_para_nem_le_o_nome(
    client_with_db: AsyncClient, world: World
) -> None:
    w = world
    await _login(client_with_db, w.admin_b)
    comp = format_competence(w.competence)
    demo = _mapping(w, "demonstrativo_contabil")
    movements = f"/api/v1/clients/{w.client.id}/movements"
    for method, path, kwargs in [
        ("GET", demo, {}),
        ("GET", f"{demo}/preview", {"params": {"competence": comp}}),
        ("GET", f"{demo}/export", {}),
        ("POST", f"{demo}/inherit", {"json": {}}),
        (
            "POST",
            f"{demo}/decisions",
            {"json": {"categoryCode": "2.10.01", "decision": "nao_mapear"}},
        ),
        ("POST", f"{movements}/sync", {"json": {"competence": comp}}),
        ("GET", f"{movements}/sync-state", {"params": {"competence": comp}}),
    ]:
        resp = await client_with_db.request(method, path, **kwargs)
        assert resp.status_code in (403, 404), (path, resp.status_code, resp.text)
        assert "SEGREDO" not in resp.text


async def test_cliente_encerrado_409_em_toda_escrita(
    client_with_db: AsyncClient, db_session: AsyncSession, world: World
) -> None:
    w = world
    await _catalog(client_with_db, w)
    comp = format_competence(w.competence)
    fluxo = _mapping(w, "fluxo_de_caixa")
    await _login(client_with_db, w.manager)
    prev = await client_with_db.get(f"{fluxo}/preview", params={"competence": comp})
    assert prev.status_code == 200, prev.text
    token = prev.json()["data"]["previewToken"]
    await db_session.execute(
        update(Client).where(Client.id == w.client.id).values(closed_at=datetime.now(UTC))
    )
    await db_session.flush()
    xlsx = {"file": ("de-para.xlsx", b"PK\x03\x04qualquer", "application/octet-stream")}
    for path, kwargs in [
        (f"/api/v1/clients/{w.client.id}/movements/sync", {"json": {"competence": comp}}),
        (f"{fluxo}/decisions", {"json": {"categoryCode": "2.10.01", "decision": "nao_mapear"}}),
        (f"{_mapping(w, 'demonstrativo_contabil')}/inherit", {"json": {}}),
        (
            f"{fluxo}/materializations",
            {"json": {"competence": comp, "previewToken": token, "confirmPartialCoverage": True}},
        ),
        (f"{fluxo}/import", {"files": xlsx, "data": {"confirm": "true"}}),
    ]:
        resp = await client_with_db.post(path, **kwargs)
        assert resp.status_code == 409, (path, resp.status_code, resp.text)
    # A leitura segue valendo para o cliente encerrado.
    leitura = await client_with_db.get(fluxo)
    assert leitura.status_code == 200, leitura.text


async def test_competencia_malformada_e_400_nunca_5xx(
    client_with_db: AsyncClient, world: World
) -> None:
    """Forma inválida é 400 VALIDATION_ERROR — inclusive o ano 0000, que casa com o regex."""
    w = world
    await _login(client_with_db, w.admin)
    fluxo = _mapping(w, "fluxo_de_caixa")
    movements = f"/api/v1/clients/{w.client.id}/movements"
    for comp in ["2026-13", "26-06", "0000-06"]:
        decision = {"categoryCode": "2.10.01", "decision": "nao_mapear", "effectiveFrom": comp}
        for method, path, kwargs in [
            ("GET", f"{movements}/sync-state", {"params": {"competence": comp}}),
            ("POST", f"{movements}/sync", {"json": {"competence": comp}}),
            ("GET", f"{fluxo}/preview", {"params": {"competence": comp}}),
            ("POST", f"{fluxo}/decisions", {"json": decision}),
        ]:
            resp = await client_with_db.request(method, path, **kwargs)
            assert resp.status_code == 400, (comp, path, resp.status_code, resp.text)
            assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
