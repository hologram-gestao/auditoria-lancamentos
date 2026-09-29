"""Gerar, listar e baixar o arquivo contábil contra Postgres (BACK 13.4 — R2/R3, gate do CI).

Ponta a ponta pelo caminho REAL, como faria uma pessoa (precedente: QA da S16):

    cliente → origem `arquivo` → mapeamento de entrada → `extrato_cliente.csv` pela
    ingestão da S14 → `plano_contabil.csv` → conta padrão do banco 649 → as 23 decisões
    de `decisoes_depara.csv` com histórico → prévia → materialização no `conta_contabil`
    → layout pelo modelo Domínio → POST gerar → GET download

e o arquivo baixado é, byte a byte, `lancamentos_esperados.csv` reordenado pela ordem
documentada (data, `source_movement_id`, id do item): 32/32 linhas, Latin-1, CRLF no
fim, sem cabeçalho. Mais: duas gerações = mesmo SHA-256; mudar histórico, plano e banco
DEPOIS de materializar não muda os bytes; `depara_aplicado` e `arquivo_contabil_gerado`
compartilham `materializacao_id`; trilha `export` na geração e em cada download; padrão
= última materialização, versão anterior por id, competência sem materialização = 409 sem
materializar nada; SHA-256 divergente = 409 + alerta sem bytes; cliente encerrado = gerar
e baixar 409 com o histórico legível; papéis de cliente = 403 com linha `denied`; gerente
fora da carteira negado; layout de outra organização = 404; exclusão definitiva apaga as
gerações antes dos usuários.

Roda com a política REAL de transação por request (commit no fim, rollback na exceção).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from decimal import Decimal
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
    AccountingFileGeneration,
    ClientMappingMaterialization,
    ClientMovement,
    ExportLayout,
    ExportLayoutVersion,
    Organization,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.db.session import get_db_session
from app.main import app as fastapi_app
from app.modules.accounting_files import service as accounting_service_module
from app.modules.export_layouts.definition import DOMINIO_TEMPLATE
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Arquivo#13"
SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "accounting_sample"
    / "cliente_exemplo_2026_08"
)
EXTRATO = (SAMPLE / "extrato_cliente.csv").read_bytes()
PLANO = (SAMPLE / "plano_contabil.csv").read_bytes()
ESPERADO = (SAMPLE / "lancamentos_esperados.csv").read_bytes()
COMPETENCE = "2026-08"
NEXT_COMPETENCE = "2026-09"
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


HISTORICOS = tuple({row["historico_padrao"] for row in _decisoes()})


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


async def _user(
    db: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
    organization: Organization | None = None,
) -> User:
    extra: dict[str, Any] = {}
    if organization is not None:
        extra["organization_id"] = organization.id
    user = User(
        name="Arquivo S13",
        email=f"s13-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _mapping(client_id: str) -> str:
    return f"/api/v1/clients/{client_id}/mapping/conta_contabil"


def _files(client_id: str) -> str:
    return f"/api/v1/clients/{client_id}/accounting-files"


@dataclass
class Seeded:
    admin: User
    client_id: str
    layout_id: str
    materialization_id: str
    codes: dict[str, str]
    accounts: dict[str, dict[str, Any]]


async def _accounts(http: AsyncClient, client_id: str) -> dict[str, dict[str, Any]]:
    resp = await http.get(f"/api/v1/clients/{client_id}/accounting-chart", params={"pageSize": 100})
    assert resp.status_code == 200, resp.text
    return {a["code"]: a for a in resp.json()["data"]}


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


async def _bind(http: AsyncClient, client_id: str, account_id: str) -> None:
    resp = await http.put(
        f"/api/v1/clients/{client_id}/source-accounts",
        json={"sourceType": "arquivo", "sourceAccountId": None, "accountingAccountId": account_id},
    )
    assert resp.status_code == 200, resp.text


async def _materialize(http: AsyncClient, client_id: str) -> str:
    preview = await http.get(f"{_mapping(client_id)}/preview", params={"competence": COMPETENCE})
    assert preview.status_code == 200, preview.text
    mat = await http.post(
        f"{_mapping(client_id)}/materializations",
        json={"competence": COMPETENCE, "previewToken": preview.json()["data"]["previewToken"]},
    )
    assert mat.status_code == 201, mat.text
    materialization_id: str = mat.json()["data"]["id"]
    return materialization_id


async def _seed(http: AsyncClient, db: AsyncSession) -> Seeded:
    """O caminho real inteiro, até a materialização e o layout Domínio da organização."""
    await MappingCatalogRepository(db).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    admin = await _user(db, role=UserRole.ADMIN)
    await _login(http, admin)
    created = await http.post("/api/v1/clients", json={"name": "Cliente Exemplo S13"})
    assert created.status_code == 201, created.text
    client_id: str = created.json()["id"]
    conn = await http.post(
        f"/api/v1/clients/{client_id}/connections", json={"provider_type": "arquivo"}
    )
    assert conn.status_code == 201, conn.text
    put = await http.put(f"/api/v1/clients/{client_id}/input-mapping", json=MAPPING)
    assert put.status_code == 200, put.text
    processed = await http.post(
        f"/api/v1/clients/{client_id}/file-origin/process",
        files={"file": ("extrato_cliente.csv", EXTRATO, "text/csv")},
        data={"competence": COMPETENCE, "declaredTotal": DECLARED_TOTAL},
    )
    assert processed.status_code == 201, processed.text
    imported = await http.post(
        f"/api/v1/clients/{client_id}/accounting-chart/import",
        files={"file": ("plano.csv", PLANO, "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    accounts = await _accounts(http, client_id)
    universe = await http.get(
        _mapping(client_id), params={"situation": "sem_decisao", "pageSize": 100}
    )
    assert universe.status_code == 200, universe.text
    codes = {i["categoryName"]: i["categoryCode"] for i in universe.json()["data"]}
    await _decide_all(http, client_id, codes, accounts, effective_from=COMPETENCE)
    await _bind(http, client_id, accounts["649"]["id"])
    materialization_id = await _materialize(http, client_id)
    layout = await http.post(
        "/api/v1/export-layouts/from-template", json={"templateKey": "dominio_lancamentos_csv"}
    )
    assert layout.status_code == 201, layout.text
    return Seeded(
        admin=admin,
        client_id=client_id,
        layout_id=layout.json()["data"]["id"],
        materialization_id=materialization_id,
        codes=codes,
        accounts=accounts,
    )


async def _generate(http: AsyncClient, s: Seeded, **over: Any) -> Any:
    body = {"layoutId": s.layout_id, "competence": COMPETENCE} | over
    return await http.post(_files(s.client_id), json=body)


async def _download(http: AsyncClient, s: Seeded, generation_id: str) -> Any:
    return await http.get(f"{_files(s.client_id)}/{generation_id}/download")


async def _expected_reordered(db: AsyncSession, client_id: str) -> bytes:
    """`lancamentos_esperados.csv` reordenado pela ORDEM DOCUMENTADA.

    A linha i do esperado é a linha i do extrato (a amostra está alinhada); a ingestão
    da S14 grava `source_movement_id = <hash>:<número da linha>`. Ordena-se pelo número
    da linha para casar com o esperado, e depois por (data, `source_movement_id` em
    texto) — a mesma chave do gerador.
    """
    movements = (
        await db.execute(
            select(ClientMovement.source_movement_id, ClientMovement.movement_date).where(
                ClientMovement.client_id == UUID(client_id)
            )
        )
    ).all()
    by_line = sorted(movements, key=lambda m: int(m[0].rsplit(":", 1)[1]))
    want = ESPERADO.split(b"\r\n")[:-1]
    assert len(by_line) == len(want) == 32
    order = sorted(range(32), key=lambda i: (by_line[i][1], by_line[i][0]))
    return b"".join(want[i] + b"\r\n" for i in order)


async def _count(db: AsyncSession, model: Any, **where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    return int((await db.execute(stmt)).scalar_one())


class TestPontaAPonta:
    async def test_do_extrato_ao_download_32_de_32_byte_a_byte(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        with capture_logs() as logs:
            gen = await _generate(http, s)
            assert gen.status_code == 201, gen.text
            meta = gen.json()["data"]
            got = await _download(http, s, meta["id"])
        assert got.status_code == 200, got.text

        assert meta["lines"] == 32
        assert Decimal(meta["totalAmount"]) == Decimal("53570.99")
        assert meta["materializationId"] == s.materialization_id
        assert meta["materializationVersion"] == 1
        assert meta["layoutVersion"] == 1
        assert meta["fileName"] == "lancamentos_2026-08_v1.csv"
        assert len(meta["sha256"]) == 64

        content = got.content
        want = ESPERADO.split(b"\r\n")
        lines = content.split(b"\r\n")
        assert lines[-1] == b"", "CRLF também depois da última linha"
        assert sorted(lines[:-1]) == sorted(want[:-1]), "32/32 linhas (multiconjunto)"
        assert content == await _expected_reordered(db_session, s.client_id)
        assert got.headers["content-type"] == "text/csv; charset=iso-8859-1"
        disposition = got.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="lancamentos_2026-08_v1.csv"')
        assert "Cliente Exemplo" not in disposition

        dumped = json.dumps(logs, ensure_ascii=False, default=str)
        for historico in HISTORICOS:
            assert historico not in dumped, "histórico no log"

    async def test_duas_geracoes_mesmo_sha_e_imutavel_depois_de_materializar(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        first = (await _generate(http, s)).json()["data"]
        second = (await _generate(http, s)).json()["data"]
        assert first["id"] != second["id"], "cada POST é uma geração"
        assert first["sha256"] == second["sha256"]
        original = (await _download(http, s, first["id"])).content

        # Histórico novo (vigência de setembro), plano reimportado e banco trocado.
        await _decide_all(
            http,
            s.client_id,
            s.codes,
            s.accounts,
            effective_from=NEXT_COMPETENCE,
            history_suffix=" - NOVO",
        )
        linhas = PLANO.decode("utf-8").splitlines()
        reimport = "\n".join(
            line.replace(";", ";RENOMEADA ", 1) if line.startswith("650;") else line
            for line in linhas
        ).encode("utf-8")
        again = await http.post(
            f"/api/v1/clients/{s.client_id}/accounting-chart/import",
            files={"file": ("plano.csv", reimport, "text/csv")},
        )
        assert again.status_code == 200, again.text
        await _bind(http, s.client_id, s.accounts["650"]["id"])

        assert (await _download(http, s, first["id"])).content == original
        third = (await _generate(http, s)).json()["data"]
        assert third["sha256"] == first["sha256"]

    async def test_os_dois_eventos_da_mesma_materializacao_casam(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        meta = (await _generate(http, s)).json()["data"]
        rows = (
            (
                await db_session.execute(
                    select(UsageEvent)
                    .where(UsageEvent.event.in_(["depara_aplicado", "arquivo_contabil_gerado"]))
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        mine = [r for r in rows if r.props.get("client_id") == s.client_id]
        depara = [r for r in mine if r.event == "depara_aplicado"]
        arquivo = [r for r in mine if r.event == "arquivo_contabil_gerado"]
        assert len(depara) == 1
        assert len(arquivo) == 1
        assert depara[0].props["materializacao_id"] == s.materialization_id
        assert arquivo[0].props == {
            "client_id": s.client_id,
            "destino": "conta_contabil",
            "competencia": COMPETENCE,
            "materializacao_id": s.materialization_id,
            "layout_id": s.layout_id,
            "layout_versao": 1,
            "linhas": 32,
            "valor_total_centavos": 5_357_099,
        }
        assert meta["materializationId"] == depara[0].props["materializacao_id"]

    async def test_trilha_export_na_geracao_e_em_cada_download(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        cid = UUID(s.client_id)
        before = await _count(db_session, AccessAudit, client_id=cid, action="export")
        meta = (await _generate(http, s)).json()["data"]
        assert await _count(db_session, AccessAudit, client_id=cid, action="export") == before + 1
        for n in (2, 3):
            assert (await _download(http, s, meta["id"])).status_code == 200
            assert (
                await _count(db_session, AccessAudit, client_id=cid, action="export") == before + n
            )


class TestQualMaterializacao:
    async def test_padrao_e_a_ultima_e_a_anterior_por_id(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        v2 = await _materialize(http, s.client_id)
        default = (await _generate(http, s)).json()["data"]
        assert default["materializationId"] == v2
        assert default["materializationVersion"] == 2
        assert default["fileName"] == "lancamentos_2026-08_v2.csv"
        older = await _generate(http, s, materializationId=s.materialization_id)
        assert older.status_code == 201, older.text
        assert older.json()["data"]["materializationVersion"] == 1
        other_month = await _generate(
            http, s, competence=NEXT_COMPETENCE, materializationId=s.materialization_id
        )
        assert other_month.status_code == 404

    async def test_competencia_sem_materializacao_e_409_e_nada_materializado(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        cid = UUID(s.client_id)
        before = await _count(db_session, ClientMappingMaterialization, client_id=cid)
        gens = await _count(db_session, AccountingFileGeneration, client_id=cid)
        resp = await _generate(http, s, competence=NEXT_COMPETENCE)
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "ARQUIVO_SEM_MATERIALIZACAO"
        assert await _count(db_session, ClientMappingMaterialization, client_id=cid) == before
        assert await _count(db_session, AccountingFileGeneration, client_id=cid) == gens

    async def test_forma_invalida_e_400(self, http: AsyncClient, db_session: AsyncSession) -> None:
        s = await _seed(http, db_session)
        for body in (
            {"layoutId": s.layout_id, "competence": "08/2026"},
            {"layoutId": "nao-e-uuid", "competence": COMPETENCE},
            {"competence": COMPETENCE},
        ):
            resp = await http.post(_files(s.client_id), json=body)
            assert resp.status_code == 400, resp.text
            assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        page = await http.get(_files(s.client_id), params={"pageSize": 101})
        assert page.status_code == 400


class TestDivergencia:
    async def test_sha_divergente_e_409_com_alerta_e_sem_bytes(
        self, http: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = await _seed(http, db_session)
        meta = (await _generate(http, s)).json()["data"]
        generation = await db_session.get(AccountingFileGeneration, UUID(meta["id"]))
        assert generation is not None
        generation.sha256 = "0" * 64
        await db_session.flush()
        alerts: list[Any] = []

        async def _capture(alert: Any, settings: Any) -> None:
            alerts.append(alert)

        monkeypatch.setattr(accounting_service_module, "send_alert", _capture)
        cid = UUID(s.client_id)
        exports = await _count(db_session, AccessAudit, client_id=cid, action="export")
        resp = await _download(http, s, meta["id"])
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "ARQUIVO_DIVERGENTE"
        assert b"R$" not in resp.content
        (alert,) = alerts
        assert alert.code.value == "accounting_file_divergent"
        assert alert.client_id == s.client_id
        assert await _count(db_session, AccessAudit, client_id=cid, action="export") == exports


class TestEncerramentoEExclusao:
    async def test_encerrado_gera_e_baixa_409_e_lista_os_metadados(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        meta = (await _generate(http, s)).json()["data"]
        closed = await http.post(f"/api/v1/clients/{s.client_id}/close")
        assert closed.status_code == 204, closed.text
        assert (await _generate(http, s)).status_code == 409
        assert (await _download(http, s, meta["id"])).status_code == 409
        listing = await http.get(_files(s.client_id))
        assert listing.status_code == 200, listing.text
        (item,) = listing.json()["data"]
        assert item["id"] == meta["id"]
        assert item["sha256"] == meta["sha256"]
        assert await _count(db_session, AccountingFileGeneration, client_id=UUID(s.client_id)) == 1

    async def test_exclusao_definitiva_apaga_as_geracoes_antes_dos_usuarios(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        await _generate(http, s)
        tenant_user = await _user(
            db_session,
            role=UserRole.CLIENT_MANAGER,
            scope=UserScope.CLIENT,
            client_id=UUID(s.client_id),
        )
        assert tenant_user.id is not None
        resp = await http.delete(f"/api/v1/clients/{s.client_id}")
        assert resp.status_code == 204, resp.text
        assert await _count(db_session, AccountingFileGeneration, client_id=UUID(s.client_id)) == 0
        # O layout é da organização: fica.
        assert await _count(db_session, ExportLayout, id=UUID(s.layout_id)) == 1
        assert await _count(db_session, ExportLayoutVersion, layout_id=UUID(s.layout_id)) == 1


class TestAutorizacao:
    async def test_papeis_de_cliente_403_com_linha_denied_nas_tres_rotas(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        meta = (await _generate(http, s)).json()["data"]
        calls = [
            ("POST", _files(s.client_id), {"layoutId": s.layout_id, "competence": COMPETENCE}),
            ("GET", _files(s.client_id), None),
            ("GET", f"{_files(s.client_id)}/{meta['id']}/download", None),
        ]
        for role in (UserRole.CLIENT_MANAGER, UserRole.CLIENT_OPERATOR):
            user = await _user(
                db_session, role=role, scope=UserScope.CLIENT, client_id=UUID(s.client_id)
            )
            await _login(http, user)
            for method, url, body in calls:
                before = await _count(db_session, AccessAudit, user_id=user.id, action="denied")
                resp = await http.request(method, url, json=body)
                assert resp.status_code == 403, (role, method, url, resp.text)
                assert "Cliente Exemplo" not in resp.text
                assert b"R$" not in resp.content
                after = await _count(db_session, AccessAudit, user_id=user.id, action="denied")
                assert after == before + 1, (role, method, url)
                row = (
                    await db_session.execute(
                        select(AccessAudit)
                        .where(AccessAudit.user_id == user.id, AccessAudit.action == "denied")
                        .order_by(AccessAudit.timestamp.desc())
                        .limit(1)
                    )
                ).scalar_one()
                assert row.user_scope == "client"
                assert row.actor_client_id == UUID(s.client_id)
                assert row.actor_organization_id is not None

    async def test_gerente_fora_da_carteira_e_negado(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        meta = (await _generate(http, s)).json()["data"]
        outsider = await _user(db_session, role=UserRole.MANAGER)
        await _login(http, outsider)
        assert (await _generate(http, s)).status_code == 403
        assert (await http.get(_files(s.client_id))).status_code == 403
        assert (await _download(http, s, meta["id"])).status_code == 403

    async def test_layout_de_outra_organizacao_e_404(
        self, http: AsyncClient, db_session: AsyncSession
    ) -> None:
        s = await _seed(http, db_session)
        org_b = Organization(name=f"Escritorio B {uuid4().hex[:6]}")
        db_session.add(org_b)
        await db_session.flush()
        foreign = ExportLayout(
            organization_id=org_b.id,
            name="Layout B",
            target_system="Domínio",
            created_by=s.admin.id,
        )
        db_session.add(foreign)
        await db_session.flush()
        db_session.add(
            ExportLayoutVersion(
                layout_id=foreign.id,
                version=1,
                definition=DOMINIO_TEMPLATE.definition.to_json(),
                author_id=s.admin.id,
            )
        )
        await db_session.flush()
        resp = await _generate(http, s, layoutId=str(foreign.id))
        assert resp.status_code == 404
        missing = await _generate(http, s, layoutId=str(uuid4()))
        assert missing.status_code == 404
        assert resp.json()["error"]["code"] == missing.json()["error"]["code"]
