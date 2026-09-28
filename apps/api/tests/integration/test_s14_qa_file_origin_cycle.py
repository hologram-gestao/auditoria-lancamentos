"""QA da Sprint 14 (86e3f6r4x): o cliente SEM sistema, do arquivo ao resultado, pela API.

O R6 ponta a ponta, na ordem em que uma pessoa faria:

    cadastrar cliente (sem Omie) → conectar origem `arquivo` (sem credencial) → declarar
    o mapeamento → processar o mês 1 → categorias do arquivo no universo do de-para, com
    a grafia original e sem decisão → prévia com 0 «sem categoria de origem» → decidir →
    materializar → 1 `fechamento_produzido{tipo_origem='arquivo'}` → processar o mês 2
    SEM tocar o mapeamento → a categoria nova entra sem decisão e a cobertura a sinaliza.

E os casos negativos DIRIGIDOS: as três recusas de conteúdo (cabeçalho, linha, total)
com a contagem de `client_movements` idêntica antes e depois e exatamente 1
`arquivo_processado{rejeitado=true}` cada; reenvio 409 sem duplicar; sincronizar
cliente só-arquivo 409; organização A não alcança nada do cliente da organização B
(sem vazar o nome); operador envia e não configura (com trilha); encerrado 409 no
envio e na alteração do mapeamento; nenhum conteúdo de célula em log nem em
`usage_events`.

⚠️ Fixtures SINTÉTICAS (`tests/fixtures/file_origin/`, ver o README de lá): o sandbox
do QA não tem arquivo real anonimizado; a rodada com arquivo real é a validação
humana em dev.

Roda com a política REAL de transação por request (commit no fim, rollback na
exceção) — é o que prova que a recusa não deixa nada para trás.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from structlog.testing import capture_logs

from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    ClientFileCategory,
    ClientInputMapping,
    ClientMovement,
    MappingDestination,
    Organization,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.db.session import get_db_session
from app.main import app as fastapi_app
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@QaArquivo#14"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "file_origin"
JUL_FILE = (FIXTURES / "extrato_2026_07.csv").read_bytes()
AUG_FILE = (FIXTURES / "extrato_2026_08.csv").read_bytes()
JUL_TOTAL = "1772,18"
AUG_TOTAL = "-2710,42"
CLIENT_NAME = "Padaria Sem Sistema QA S14"

JUL_LABELS = {
    "Receita de Serviços",
    "Aluguel",
    "Energia Elétrica",
    "Despesas Bancárias",
    "Material de Escritório",
    "Impostos",
}
AUG_NEW_LABELS = {"Despesas bancárias", "Pró-labore"}

#: Conteúdo de célula que NUNCA pode aparecer em log nem em telemetria.
CELL_CONTENT = (
    "CLIENTE ALFA",
    "ALUGUEL SALA",
    "PRO-LABORE SOCIO",
    "Receita de Serviços",
    "Pró-labore",
    "Material de Escritório",
    "NF 1021",
)

MAPPING = {
    "fileFormat": "csv",
    "csvDelimiter": ";",
    "encoding": "utf-8",
    "dateColumn": "Data",
    "descriptionColumn": "Histórico",
    "amountColumn": "Valor",
    "categoryColumn": "Categoria",
    "categoryMode": "coluna_categoria",
    "documentColumn": "Documento",
    "dateFormat": "dd/mm/yyyy",
    "decimalSeparator": ",",
    "signConvention": "valor_com_sinal",
}


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
def _no_cell_content_in_logs(caplog: pytest.LogCaptureFixture) -> Any:
    """Os dois canais: `caplog` (stdlib) e `capture_logs` (structlog, que o `caplog`
    não enxerga depois que a app configura o structlog — foi o que deixou
    `test_falha_de_decifragem_…` vermelho na suíte completa)."""
    caplog.set_level(logging.DEBUG)
    with capture_logs() as events:
        yield
    dumped = caplog.text + json.dumps(events, ensure_ascii=False, default=str)
    for forbidden in CELL_CONTENT:
        assert forbidden not in dumped, f"conteúdo de célula `{forbidden}` vazou para o log"


async def _user(
    db: AsyncSession,
    role: UserRole,
    *,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
    organization_id: Any = None,
) -> User:
    extra: dict[str, Any] = {}
    if organization_id is not None:
        extra["organization_id"] = organization_id
    user = User(
        name=f"QA S14 {role.value}",
        email=f"qa14-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
        **extra,
    )
    db.add(user)
    await db.flush()
    return user


async def _login(http: AsyncClient, user: User) -> None:
    http.cookies.clear()
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


class World:
    admin: User
    admin_b: User
    client_id: str
    demonstrativo_id: UUID


@pytest.fixture
async def world(db_session: AsyncSession, http: AsyncClient) -> World:
    """Admin da Hologram + admin de OUTRA organização + o cliente SEM ERP criado pela API."""
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _user(db_session, UserRole.ADMIN)
    org_b = Organization(name=f"Escritorio B QA14 {uuid4().hex[:6]}")
    db_session.add(org_b)
    await db_session.flush()
    w.admin_b = await _user(db_session, UserRole.ADMIN, organization_id=org_b.id)
    w.demonstrativo_id = (
        await db_session.execute(
            select(MappingDestination.id).where(
                MappingDestination.organization_id == HOLOGRAM_ORGANIZATION_ID,
                MappingDestination.destination_type == "demonstrativo_contabil",
            )
        )
    ).scalar_one()

    await _login(http, w.admin)
    # 1. Cadastrar SEM credencial nenhuma (S9): o cliente nasce sem origem.
    created = await http.post("/api/v1/clients", json={"name": CLIENT_NAME})
    assert created.status_code == 201, created.text
    w.client_id = created.json()["id"]
    detail = await http.get(f"/api/v1/clients/{w.client_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["origin_status"] == "sem_origem"

    # 2. Conectar a origem ARQUIVO: sem credencial, ativa ao nascer, sem «escrever».
    conn = await http.post(
        f"/api/v1/clients/{w.client_id}/connections", json={"provider_type": "arquivo"}
    )
    assert conn.status_code == 201, conn.text
    assert conn.json()["data"]["status"] == "ativa"
    assert conn.json()["data"]["capabilities"] == ["listar_lancamentos"]
    detail = await http.get(f"/api/v1/clients/{w.client_id}")
    assert detail.json()["origin_status"] == "ativa"

    # Catálogo do destino (é da organização; o admin cadastra os alvos).
    targets = await http.post(
        f"/api/v1/mapping-destinations/{w.demonstrativo_id}/targets",
        json={
            "targets": [
                {"code": "R.01", "name": "Receita operacional"},
                {"code": "D.01", "name": "Despesa operacional"},
                {"code": "D.02", "name": "Tributos"},
            ]
        },
    )
    assert targets.status_code == 201, targets.text
    return w


# ---------------------------------------------------------------------------
# Helpers de leitura
# ---------------------------------------------------------------------------


def _file_origin(w: World) -> str:
    return f"/api/v1/clients/{w.client_id}/file-origin"


def _demo(w: World) -> str:
    return f"/api/v1/clients/{w.client_id}/mapping/demonstrativo_contabil"


async def _process(
    http: AsyncClient,
    w: World,
    content: bytes,
    competence: str,
    declared_total: str | None = None,
) -> Any:
    data = {"competence": competence}
    if declared_total is not None:
        data["declaredTotal"] = declared_total
    return await http.post(
        f"{_file_origin(w)}/process",
        files={"file": ("extrato.csv", content, "text/csv")},
        data=data,
    )


async def _movement_count(db: AsyncSession, client_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(ClientMovement)
        .where(ClientMovement.client_id == UUID(client_id))
    )
    return int((await db.execute(stmt)).scalar_one())


async def _events(db: AsyncSession, event: str, client_id: str) -> list[UsageEvent]:
    stmt = (
        select(UsageEvent)
        .where(UsageEvent.event == event)
        .execution_options(populate_existing=True)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [r for r in rows if r.props.get("client_id") == client_id]


async def _assert_no_cell_content_in_usage_events(db: AsyncSession) -> None:
    rows = (await db.execute(select(UsageEvent))).scalars().all()
    dumped = json.dumps([r.props for r in rows], ensure_ascii=False)
    for forbidden in CELL_CONTENT:
        assert forbidden not in dumped, f"conteúdo de célula `{forbidden}` em usage_events"


async def _universe(http: AsyncClient, w: World, situation: str) -> list[dict[str, Any]]:
    resp = await http.get(_demo(w), params={"situation": situation, "pageSize": 100})
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["data"]
    return items


async def _preview(http: AsyncClient, w: World, competence: str) -> dict[str, Any]:
    resp = await http.get(f"{_demo(w)}/preview", params={"competence": competence})
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


def _error(resp: Any) -> dict[str, Any]:
    body: dict[str, Any] = resp.json()["error"]
    return body


# Decisão por grafia (o QA decide pelo NOME que a tela mostra, como a pessoa faria).
DECISIONS = {
    "Receita de Serviços": ("alvo", "R.01"),
    "Aluguel": ("alvo", "D.01"),
    "Energia Elétrica": ("alvo", "D.01"),
    "Despesas Bancárias": ("alvo", "D.01"),
    "Material de Escritório": ("nao_mapear", None),
    "Impostos": ("alvo", "D.02"),
    "Despesas bancárias": ("alvo", "D.01"),
    "Pró-labore": ("alvo", "D.01"),
}


async def _decide(
    http: AsyncClient, w: World, items: list[dict[str, Any]], effective_from: str
) -> None:
    decisions = []
    for item in items:
        decision, target = DECISIONS[item["categoryName"]]
        body: dict[str, Any] = {
            "categoryCode": item["categoryCode"],
            "sourceType": "arquivo",
            "decision": decision,
        }
        if target:
            body["targetCode"] = target
        decisions.append(body)
    resp = await http.post(
        f"{_demo(w)}/decisions/batch",
        json={
            "decisions": decisions,
            "effectiveFrom": effective_from,
            "confirmRetroactive": True,
        },
    )
    assert resp.status_code in {200, 201}, resp.text


async def _materialize(
    http: AsyncClient, w: World, competence: str, *, confirm_partial: bool = False
) -> Any:
    token = (await _preview(http, w, competence))["previewToken"]
    return await http.post(
        f"{_demo(w)}/materializations",
        json={
            "competence": competence,
            "previewToken": token,
            "confirmPartialCoverage": confirm_partial,
        },
    )


# ---------------------------------------------------------------------------
# O ciclo completo
# ---------------------------------------------------------------------------


class TestClienteSemSistemaDoArquivoAoResultado:
    async def test_mes_1_e_mes_2_sem_reconfigurar(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        w = world
        # Sem mapeamento: nada processa direto — a tela é conduzida a criá-lo.
        sem = await _process(http, w, JUL_FILE, "2026-07")
        assert sem.status_code == 409, sem.text
        assert _error(sem)["code"] == "SEM_MAPEAMENTO"
        assert _error(sem)["details"]["foundColumns"] == [
            "Data",
            "Histórico",
            "Valor",
            "Categoria",
            "Documento",
        ]
        assert await _movement_count(db_session, w.client_id) == 0

        # 3. Declarar o mapeamento (uma vez).
        got = await http.get(f"/api/v1/clients/{w.client_id}/input-mapping")
        assert got.status_code == 200, got.text
        assert got.json()["data"]["mapping"] is None
        put = await http.put(f"/api/v1/clients/{w.client_id}/input-mapping", json=MAPPING)
        assert put.status_code == 200, put.text
        assert put.json()["data"]["created"] is True
        mapping_id = put.json()["data"]["mapping"]["id"]
        mapping_updated_at = put.json()["data"]["mapping"]["updatedAt"]

        # 4. Mês 1: processa inteiro, com o total conferido.
        jul = await _process(http, w, JUL_FILE, "2026-07", declared_total=JUL_TOTAL)
        assert jul.status_code == 201, jul.text
        jul_data = jul.json()["data"]
        assert jul_data["rows"] == 8  # duas linhas idênticas = dois movimentos
        assert jul_data["categoriesCreated"] == 6
        assert jul_data["mappingId"] == mapping_id
        assert await _movement_count(db_session, w.client_id) == 8

        # 5. As categorias do arquivo no universo do de-para: grafia ORIGINAL, sem decisão.
        undecided = await _universe(http, w, "sem_decisao")
        assert {i["categoryName"] for i in undecided} == JUL_LABELS
        assert {i["sourceType"] for i in undecided} == {"arquivo"}
        assert all(i["categoryNameResolved"] for i in undecided)
        assert all(i["categoryCode"].startswith("arq-") for i in undecided)

        # 6. Prévia: 0 em «sem categoria de origem» (fecha a S-4 da S12).
        prev = await _preview(http, w, "2026-07")
        assert prev["situations"]["semCategoria"]["count"] == 0
        assert prev["situations"]["semDecisao"]["count"] == 8

        # 7. Decidir tudo → 8. materializar sem cobertura parcial.
        await _decide(http, w, undecided, "2026-07")
        prev = await _preview(http, w, "2026-07")
        assert prev["situations"]["semDecisao"]["count"] == 0
        assert prev["situations"]["semCategoria"]["count"] == 0
        mat = await _materialize(http, w, "2026-07")
        assert mat.status_code == 201, mat.text

        # 9. A métrica da sprint: 1 linha fechamento_produzido{tipo_origem='arquivo'}.
        fechamentos = await _events(db_session, "fechamento_produzido", w.client_id)
        assert [f.props for f in fechamentos] == [
            {"client_id": w.client_id, "tipo_origem": "arquivo", "competencia": "2026-07"}
        ]

        # 10. Mês 2, SEM tocar o mapeamento: um passo só.
        aug = await _process(http, w, AUG_FILE, "2026-08", declared_total=AUG_TOTAL)
        assert aug.status_code == 201, aug.text
        aug_data = aug.json()["data"]
        assert aug_data["rows"] == 6
        assert aug_data["mappingId"] == mapping_id
        # Pró-labore é nova; «Despesas bancárias» é OUTRA grafia (não fundida).
        assert aug_data["categoriesCreated"] == 2
        mapping_row = (
            await db_session.execute(
                select(ClientInputMapping)
                .where(ClientInputMapping.client_id == UUID(w.client_id))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert str(mapping_row.id) == mapping_id
        got = await http.get(f"/api/v1/clients/{w.client_id}/input-mapping")
        assert got.json()["data"]["mapping"]["updatedAt"] == mapping_updated_at

        # 11. A categoria nova entra sem decisão, e a cobertura a sinaliza.
        undecided_aug = await _universe(http, w, "sem_decisao")
        assert {i["categoryName"] for i in undecided_aug} == AUG_NEW_LABELS
        prev_aug = await _preview(http, w, "2026-08")
        assert prev_aug["situations"]["semCategoria"]["count"] == 0
        assert prev_aug["situations"]["semDecisao"]["count"] == 2
        assert {u["categoryCode"] for u in prev_aug["undecidedCategories"]} == {
            i["categoryCode"] for i in undecided_aug
        }
        # O fechamento NÃO sai até alguém decidir (ou confirmar cobertura parcial).
        blocked = await _materialize(http, w, "2026-08")
        assert blocked.status_code == 409, blocked.text
        assert _error(blocked)["code"] == "COBERTURA_PARCIAL_REQUER_CONFIRMACAO"
        assert len(await _events(db_session, "fechamento_produzido", w.client_id)) == 1

        await _decide(http, w, undecided_aug, "2026-08")
        mat_aug = await _materialize(http, w, "2026-08")
        assert mat_aug.status_code == 201, mat_aug.text
        fechamentos = await _events(db_session, "fechamento_produzido", w.client_id)
        assert sorted(f.props["competencia"] for f in fechamentos) == ["2026-07", "2026-08"]
        assert {f.props["tipo_origem"] for f in fechamentos} == {"arquivo"}

        # As decisões de julho sobrevivem: nenhuma categoria de julho voltou a «sem decisão».
        assert await _universe(http, w, "sem_decisao") == []
        cats = (
            (
                await db_session.execute(
                    select(ClientFileCategory).where(
                        ClientFileCategory.client_id == UUID(w.client_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(cats) == 8

        # Telemetria da ingestão: 1 aceito por mês + a recusa do «sem mapeamento».
        processed = await _events(db_session, "arquivo_processado", w.client_id)
        assert sorted((p.props["rejeitado"], p.props["linhas"]) for p in processed) == [
            (False, 6),
            (False, 8),
            (True, 0),
        ]
        await _assert_no_cell_content_in_usage_events(db_session)


# ---------------------------------------------------------------------------
# Casos negativos dirigidos
# ---------------------------------------------------------------------------


async def _with_july(http: AsyncClient, w: World, db: AsyncSession) -> int:
    put = await http.put(f"/api/v1/clients/{w.client_id}/input-mapping", json=MAPPING)
    assert put.status_code == 200, put.text
    jul = await _process(http, w, JUL_FILE, "2026-07")
    assert jul.status_code == 201, jul.text
    return await _movement_count(db, w.client_id)


class TestNenhumProcessamentoParcial:
    @pytest.mark.parametrize(
        ("content", "total", "code", "motivo"),
        [
            (
                JUL_FILE.replace("Histórico".encode(), b"Descri\xc3\xa7\xc3\xa3o", 1),
                None,
                "CABECALHO_DIVERGENTE",
                "cabecalho_divergente",
            ),
            (
                AUG_FILE.replace(b"-398,12", b"abc"),
                None,
                "LINHAS_INVALIDAS",
                "linhas_invalidas",
            ),
            (AUG_FILE, "-2710,43", "TOTAL_DIVERGENTE", "total_divergente"),
        ],
        ids=["cabecalho", "linha", "total"],
    )
    async def test_recusa_nao_deixa_nada_para_tras(
        self,
        http: AsyncClient,
        db_session: AsyncSession,
        world: World,
        content: bytes,
        total: str | None,
        code: str,
        motivo: str,
    ) -> None:
        before = await _with_july(http, world, db_session)
        rejected_before = [
            e
            for e in await _events(db_session, "arquivo_processado", world.client_id)
            if e.props["rejeitado"]
        ]
        competence = "2026-07" if code == "CABECALHO_DIVERGENTE" else "2026-08"

        resp = await _process(http, world, content, competence, declared_total=total)
        assert resp.status_code == 422, resp.text
        err = _error(resp)
        assert err["code"] == code
        if code == "CABECALHO_DIVERGENTE":
            # A coluna divergente é NOMEADA (nome de coluna é estrutura, não célula).
            assert err["details"]["missingColumns"] == ["Histórico"]
        if code == "LINHAS_INVALIDAS":
            assert err["details"]["lines"] == [{"line": 4, "reason": "valor_nao_numerico"}]
        assert "abc" not in resp.text

        assert await _movement_count(db_session, world.client_id) == before
        rejected = [
            e
            for e in await _events(db_session, "arquivo_processado", world.client_id)
            if e.props["rejeitado"]
        ]
        assert len(rejected) == len(rejected_before) + 1
        assert rejected[-1].props["motivo"] == motivo
        await _assert_no_cell_content_in_usage_events(db_session)

    async def test_mesmo_arquivo_duas_vezes_e_409_sem_duplicar(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        before = await _with_july(http, world, db_session)
        again = await _process(http, world, JUL_FILE, "2026-07")
        assert again.status_code == 409, again.text
        assert _error(again)["code"] == "ARQUIVO_JA_PROCESSADO"
        assert await _movement_count(db_session, world.client_id) == before == 8
        imports = await http.get(f"{_file_origin(world)}/imports")
        assert imports.status_code == 200, imports.text
        assert len(imports.json()["data"]) == 1

    async def test_sincronizar_cliente_so_arquivo_e_409(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        before = await _with_july(http, world, db_session)
        resp = await http.post(
            f"/api/v1/clients/{world.client_id}/movements/sync", json={"competence": "2026-07"}
        )
        assert resp.status_code == 409, resp.text
        assert _error(resp)["code"] == "ORIGEM_POR_ARQUIVO"
        statuses = (
            (
                await db_session.execute(
                    select(ClientMovement.status)
                    .where(ClientMovement.client_id == UUID(world.client_id))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        assert len(statuses) == before
        assert set(statuses) == {"presente"}


class TestQuemPodeOQue:
    async def test_organizacao_b_nao_alcanca_o_cliente_da_organizacao_a(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        before = await _with_july(http, world, db_session)
        await _login(http, world.admin_b)
        base = f"/api/v1/clients/{world.client_id}"
        attempts = [
            await http.post(
                f"{base}/file-origin/inspect",
                files={"file": ("extrato.csv", AUG_FILE, "text/csv")},
            ),
            await _process(http, world, AUG_FILE, "2026-08"),
            await http.get(f"{base}/file-origin/imports"),
            await http.get(f"{base}/input-mapping"),
            await http.put(f"{base}/input-mapping", json={**MAPPING, "dateColumn": "Dt"}),
        ]
        for resp in attempts:
            assert resp.status_code in {403, 404}, resp.text
            assert CLIENT_NAME not in resp.text
            assert "Histórico" not in resp.text
        assert await _movement_count(db_session, world.client_id) == before
        mapping_row = (
            await db_session.execute(
                select(ClientInputMapping)
                .where(ClientInputMapping.client_id == UUID(world.client_id))
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert mapping_row.date_column == "Data"

    async def test_operador_envia_mas_nao_configura(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _with_july(http, world, db_session)
        operator = await _user(
            db_session,
            UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=UUID(world.client_id),
        )
        await _login(http, operator)
        # Lê o resumo do que será aplicado…
        got = await http.get(f"/api/v1/clients/{world.client_id}/input-mapping")
        assert got.status_code == 200, got.text
        assert got.json()["data"]["mapping"]["descriptionColumn"] == "Histórico"
        # …envia o mês…
        aug = await _process(http, world, AUG_FILE, "2026-08")
        assert aug.status_code == 201, aug.text
        # …e NÃO altera o mapeamento, com a negação na trilha.
        put = await http.put(
            f"/api/v1/clients/{world.client_id}/input-mapping",
            json={**MAPPING, "dateColumn": "Dt"},
        )
        assert put.status_code == 403, put.text
        denied = (
            (
                await db_session.execute(
                    select(AccessAudit).where(
                        AccessAudit.action == "denied",
                        AccessAudit.client_id == UUID(world.client_id),
                        AccessAudit.user_id == operator.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(denied) == 1

    async def test_cliente_encerrado_recusa_envio_e_alteracao(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _with_july(http, world, db_session)
        closed = await http.post(f"/api/v1/clients/{world.client_id}/close")
        assert closed.status_code == 204, closed.text

        aug = await _process(http, world, AUG_FILE, "2026-08")
        assert aug.status_code == 409, aug.text
        put = await http.put(
            f"/api/v1/clients/{world.client_id}/input-mapping",
            json={**MAPPING, "dateColumn": "Dt"},
        )
        assert put.status_code == 409, put.text
        # Leitura segue de pé depois do encerramento.
        got = await http.get(f"/api/v1/clients/{world.client_id}/input-mapping")
        assert got.status_code == 200, got.text


class TestValorForaDoPadraoRecusaOArquivo:
    """Valor que não é dinheiro válido na convenção DECLARADA recusa o arquivo (R2).

    `1E+30` estoura o `quantize` (`InvalidOperation` não é `ValueError`); um CNPJ na
    coluna de valor estoura `Numeric(14,2)` no upsert; `1500.50` com vírgula decimal
    DECLARADA não é número válido (o ponto é milhar e o grupo não tem 3 dígitos) e
    não pode entrar calado como R$ 150.050,00.
    """

    @pytest.mark.parametrize(
        "cell",
        ["1E+30", "12345678901234,00", "1500.50"],
        ids=["expoente", "cnpj_na_coluna_de_valor", "ponto_decimal_sob_virgula"],
    )
    async def test_recusa_tipada_e_nada_entra(
        self, http: AsyncClient, db_session: AsyncSession, world: World, cell: str
    ) -> None:
        before = await _with_july(http, world, db_session)
        content = AUG_FILE.replace(b"-398,12", cell.encode())
        resp = await _process(http, world, content, "2026-08")
        assert resp.status_code == 422, resp.text
        err = _error(resp)
        assert err["code"] == "LINHAS_INVALIDAS"
        assert [p["line"] for p in err["details"]["lines"]] == [4]
        assert await _movement_count(db_session, world.client_id) == before


class TestCodigoDaCategoriaNaoDerivaDoRotulo:
    async def test_mesmo_rotulo_em_dois_clientes_tem_codigos_diferentes(
        self, http: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Código derivado do rótulo (hash) seria texto do cliente enumerável.

        É o teste que a mutação «code = hash(rótulo)» tem de deixar vermelho.
        """
        await _with_july(http, world, db_session)
        other = await http.post("/api/v1/clients", json={"name": "Outro Sem Sistema QA S14"})
        assert other.status_code == 201, other.text
        other_id = other.json()["id"]
        conn = await http.post(
            f"/api/v1/clients/{other_id}/connections", json={"provider_type": "arquivo"}
        )
        assert conn.status_code == 201, conn.text
        put = await http.put(f"/api/v1/clients/{other_id}/input-mapping", json=MAPPING)
        assert put.status_code == 200, put.text
        resp = await http.post(
            f"/api/v1/clients/{other_id}/file-origin/process",
            files={"file": ("extrato.csv", JUL_FILE, "text/csv")},
            data={"competence": "2026-07"},
        )
        assert resp.status_code == 201, resp.text

        async def _codes(client_id: str) -> set[str]:
            stmt = select(ClientFileCategory.code).where(
                ClientFileCategory.client_id == UUID(client_id)
            )
            return set((await db_session.execute(stmt)).scalars().all())

        mine, theirs = await _codes(world.client_id), await _codes(other_id)
        assert len(mine) == len(theirs) == 6
        assert mine.isdisjoint(theirs)
