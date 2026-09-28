"""A ingestão do arquivo do cliente sem ERP, ponta a ponta contra o banco (BACK 14.3 — R1/R2/R3).

    POST /api/v1/clients/{client_id}/file-origin/inspect
    POST /api/v1/clients/{client_id}/file-origin/process
    GET  /api/v1/clients/{client_id}/file-origin/imports

O que este módulo afirma contra o Postgres (o unitário `test_file_ingestion_service`
afirma a ORDEM das recusas e das escritas, sem banco):

  - CSV e XLSX válidos → 201 sem interação: N movimentos `presente` com
    `source_type='arquivo'`, descrição decifrável SÓ com a DEK do cliente, `synced_at`
    carimbado, 1 registro em `client_file_imports`, 1 `arquivo_processado{rejeitado=false}`;
  - toda recusa (sem mapeamento, cabeçalho, linhas, total, PDF, zip quebrado/bomba, reenvio)
    deixa a contagem de movimentos INALTERADA e gera exatamente 1 evento de recusa — que
    sobrevive ao `rollback()` REAL da request;
  - o 409 do reenvio é a UNIQUE do banco; duas linhas idênticas são dois movimentos;
    arquivo corrigido marca o anterior `ausente_na_origem` e nunca apaga;
  - ENTRE SPRINTS (fecha a S-4 da S12): a prévia do de-para reporta 0 em «sem categoria de
    origem» para arquivo categorizado; classificação livre gera uma categoria por valor
    distinto; célula vazia é o buraco de ingestão; o segundo mês não toca o mapeamento;
  - encerrado → 409 em inspect e process; operador envia (201); sincronizar cliente
    só-arquivo → 409 `ORIGEM_POR_ARQUIVO` sem marcar nada ausente;
  - nenhum conteúdo de célula nem nome de coluna em log (`caplog` + structlog) ou em `usage_events`.

O cross-tenant e o cross-org das três rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import select
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.core.crypto import CryptoError
from app.core.crypto_service import (
    AAD_MOVEMENT_DESCRIPTION,
    field_locator,
    load_client_cipher,
    provision_client_cipher,
)
from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    Client,
    ClientConnection,
    ClientFileCategory,
    ClientFileImport,
    ClientInputMapping,
    ClientMovement,
    ClientMovementSync,
    MovementStatus,
    ProviderType,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.db.session import get_db_session
from app.main import app as fastapi_app
from app.modules.client_file_ingestion.repository import ClientFileImportRepository
from app.modules.mapping_catalog.repository import MappingCatalogRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Arquivo#1"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SECRET_DESCRIPTION = "PAGTO ACME LTDA SEGREDO DA CELULA"
SECRET_CATEGORY = "Aluguel Sala Rua das Flores"
HEADER = ["Data", "Histórico", "Valor", "Categoria", "Documento"]
ROWS: list[list[Any]] = [
    ["05/06/2026", SECRET_DESCRIPTION, "-1.500,00", SECRET_CATEGORY, "NF 1"],
    ["10/06/2026", "Energia elétrica", "-320,45", "Energia", "NF 2"],
    ["15/06/2026", "Venda balcão", "980,00", "Receita", ""],
]
TOTAL = "-840,45"
JUN = date(2026, 6, 1)
JUL = date(2026, 7, 1)


# ---------------------------------------------------------------------------
# Construtores e fixtures
# ---------------------------------------------------------------------------


def _csv(rows: list[list[Any]], header: list[str] | None = None) -> bytes:
    lines = [";".join(header or HEADER)]
    lines.extend(";".join("" if c is None else str(c) for c in row) for row in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xlsx(rows: list[list[Any]], header: list[str] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(header or HEADER)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def _seed_user(
    session: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name=f"Pessoa {role.value}",
        email=f"arq-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


def _mapping(client: Client, author: User, **overrides: Any) -> ClientInputMapping:
    base: dict[str, Any] = {
        "client_id": client.id,
        "file_format": "csv",
        "csv_delimiter": ";",
        "encoding": "utf-8",
        "date_column": "Data",
        "description_column": "Histórico",
        "amount_column": "Valor",
        "category_column": "Categoria",
        "category_mode": "coluna_categoria",
        "document_column": "Documento",
        "date_format": "dd/mm/yyyy",
        "decimal_separator": ",",
        "sign_convention": "valor_com_sinal",
        "created_by": author.id,
        "updated_by": author.id,
    }
    base.update(overrides)
    return ClientInputMapping(**base)


class World:
    admin: User
    operator: User
    client: Client
    client_xlsx: Client
    client_nomap: Client
    client_livre: Client
    mapping: ClientInputMapping


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    await MappingCatalogRepository(db_session).seed_default_destinations(HOLOGRAM_ORGANIZATION_ID)
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.client = Client(name="Cliente sem sistema (CSV)", active=True, created_by=w.admin.id)
    w.client_xlsx = Client(name="Cliente sem sistema (XLSX)", active=True, created_by=w.admin.id)
    w.client_nomap = Client(name="Cliente sem mapeamento", active=True, created_by=w.admin.id)
    w.client_livre = Client(name="Cliente classificação livre", active=True, created_by=w.admin.id)
    db_session.add_all([w.client, w.client_xlsx, w.client_nomap, w.client_livre])
    await db_session.flush()
    # Todos têm a origem por ARQUIVO (S14 R1): conexão sem credencial, `ativa` ao nascer.
    for client in (w.client, w.client_xlsx, w.client_nomap, w.client_livre):
        db_session.add(
            ClientConnection(
                client_id=client.id, provider_type=ProviderType.ARQUIVO.value, label="Arquivo"
            )
        )
        # O `POST /connections` do `arquivo` provisiona a DEK (14.1, §4.8); o
        # registry de categorias só a CARREGA (retrabalho da 14.4).
        await provision_client_cipher(client, settings=get_settings())
    w.mapping = _mapping(w.client, w.admin)
    db_session.add(w.mapping)
    db_session.add(
        _mapping(w.client_xlsx, w.admin, file_format="xlsx", csv_delimiter=None, encoding=None)
    )
    db_session.add(
        _mapping(
            w.client_livre,
            w.admin,
            category_column="Classe",
            category_mode="classificacao_livre",
            document_column=None,
        )
    )
    w.operator = await _seed_user(
        db_session, role=UserRole.CLIENT_OPERATOR, scope=UserScope.CLIENT, client_id=w.client.id
    )
    await db_session.flush()
    return w


@pytest.fixture
async def client_with_request_rollback(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """A POLÍTICA REAL de transação por request (ver `test_client_connections.py`).

    `yield` → `commit()`; exceção → `rollback()` e re-levanta. É o que prova que a
    recusa (`arquivo_processado{rejeitado=true}`) sobrevive ao `AppError`.
    """

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
def _nada_de_celula_no_log(caplog: pytest.LogCaptureFixture) -> Any:
    """Guardrail da sprint: nenhum conteúdo de célula nem nome de coluna em log.

    Os DOIS canais: `caplog` (stdlib — o handler de 500 e as libs) e
    `capture_logs` (structlog, que a app usa e que o `caplog` não enxerga depois
    que ela configura o structlog). Só com o `caplog`, este guard passava sem
    medir nada.
    """
    caplog.set_level(logging.INFO)
    with capture_logs() as events:
        yield
    dumped = caplog.text + json.dumps(events, ensure_ascii=False, default=str)
    for forbidden in (SECRET_DESCRIPTION, SECRET_CATEGORY, "Histórico", "Categoria"):
        assert forbidden not in dumped, f"`{forbidden}` vazou para o log"


async def _login(http: AsyncClient, user: User) -> None:
    resp = await http.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _base(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/file-origin"


async def _process(
    http: AsyncClient,
    client: Client,
    content: bytes,
    *,
    competence: str = "2026-06",
    declared_total: str | None = None,
    name: str = "extrato.csv",
    mime: str = "text/csv",
) -> Any:
    data = {"competence": competence}
    if declared_total is not None:
        data["declaredTotal"] = declared_total
    return await http.post(
        f"{_base(client)}/process", files={"file": (name, content, mime)}, data=data
    )


async def _inspect(http: AsyncClient, client: Client, content: bytes, **form: str) -> Any:
    return await http.post(
        f"{_base(client)}/inspect",
        files={"file": ("extrato.csv", content, "text/csv")},
        data=form or None,
    )


async def _movements(db: AsyncSession, client_id: Any) -> list[ClientMovement]:
    # Relê do BANCO (populate_existing) — nunca `expire_all()` (lição da S11).
    stmt = (
        select(ClientMovement)
        .where(ClientMovement.client_id == client_id)
        .order_by(ClientMovement.source_movement_id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _imports(db: AsyncSession, client_id: Any) -> list[ClientFileImport]:
    stmt = (
        select(ClientFileImport)
        .where(ClientFileImport.client_id == client_id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _categories(db: AsyncSession, client_id: Any) -> list[ClientFileCategory]:
    stmt = select(ClientFileCategory).where(ClientFileCategory.client_id == client_id)
    return list((await db.execute(stmt)).scalars().all())


async def _events(db: AsyncSession, client_id: Any) -> list[UsageEvent]:
    stmt = select(UsageEvent).where(UsageEvent.event == "arquivo_processado")
    rows = (await db.execute(stmt)).scalars().all()
    found = [r for r in rows if r.props.get("client_id") == str(client_id)]
    for row in found:
        dumped = json.dumps(row.props, ensure_ascii=False)
        for forbidden in (SECRET_DESCRIPTION, SECRET_CATEGORY, "Histórico", "Categoria"):
            assert forbidden not in dumped, "conteúdo de célula/coluna em usage_events"
    return found


async def _sync_state(
    db: AsyncSession, client_id: Any, competence: date
) -> ClientMovementSync | None:
    stmt = (
        select(ClientMovementSync)
        .where(
            ClientMovementSync.client_id == client_id, ClientMovementSync.competence == competence
        )
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _error(resp: Any) -> dict[str, Any]:
    body = resp.json()["error"]
    assert isinstance(body, dict)
    return body


async def _assert_nothing_processed(db: AsyncSession, client: Client, motivo: str) -> None:
    """Sem processamento parcial: zero movimentos, zero registros, 1 recusa."""
    assert await _movements(db, client.id) == []
    assert await _imports(db, client.id) == []
    assert await _sync_state(db, client.id, JUN) is None
    (event,) = await _events(db, client.id)
    assert event.props["rejeitado"] is True
    assert event.props["motivo"] == motivo


# ---------------------------------------------------------------------------
# Aceito
# ---------------------------------------------------------------------------


class TestProcessamentoAceito:
    async def test_csv_valido_vira_a_base_da_competencia_sem_interacao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        content = _csv(ROWS)
        await _login(client_with_db, world.admin)
        resp = await _process(client_with_db, world.client, content)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["rows"] == 3
        assert data["columnsRecognized"] == 5
        assert data["categoriesCreated"] == 3
        assert data["absent"] == 0
        assert data["competence"] == "2026-06"
        assert data["mappingId"] == str(world.mapping.id)

        # N movimentos `presente`, `source_type='arquivo'`, identidade da LINHA.
        rows = await _movements(db_session, world.client.id)
        prefix = _hash(content)[:16]
        assert [r.source_movement_id for r in rows] == [f"{prefix}:2", f"{prefix}:3", f"{prefix}:4"]
        assert {r.source_type for r in rows} == {"arquivo"}
        assert {r.status for r in rows} == {MovementStatus.PRESENTE.value}
        assert {r.competence for r in rows} == {JUN}
        by_id = {r.source_movement_id: r for r in rows}
        assert by_id[f"{prefix}:2"].amount == Decimal("-1500.00")
        assert by_id[f"{prefix}:2"].movement_date == date(2026, 6, 5)
        assert by_id[f"{prefix}:2"].document == "NF 1"
        assert by_id[f"{prefix}:4"].document is None
        assert all(r.supplier_code is None and r.source_account_id is None for r in rows)
        assert all(r.category_code is not None and r.category_code.startswith("arq-") for r in rows)
        assert len(await _categories(db_session, world.client.id)) == 3

        # Descrição SÓ cifrada, decifrável com a DEK do cliente e com o AAD da pk.
        row = by_id[f"{prefix}:2"]
        assert row.description_encrypted is not None
        assert row.description_iv is not None
        assert SECRET_DESCRIPTION not in row.description_encrypted
        await db_session.refresh(world.client)
        cipher = await load_client_cipher(world.client, settings=get_settings())
        assert (
            cipher.decrypt(
                row.description_encrypted,
                row.description_iv,
                field_locator(AAD_MOVEMENT_DESCRIPTION, row.id),
            )
            == SECRET_DESCRIPTION
        )
        # A DEK de OUTRO cliente não decifra.
        other = await provision_client_cipher(world.client_xlsx, settings=get_settings())
        with pytest.raises(CryptoError):
            other.decrypt(
                row.description_encrypted,
                row.description_iv,
                field_locator(AAD_MOVEMENT_DESCRIPTION, row.id),
            )

        # Carimbo, registro e a métrica da ingestão.
        state = await _sync_state(db_session, world.client.id, JUN)
        assert state is not None
        assert state.synced_at is not None
        (record,) = await _imports(db_session, world.client.id)
        assert record.file_hash == _hash(content)
        assert record.rows == 3
        assert record.created_by == world.admin.id
        assert record.mapping_id == world.mapping.id
        assert str(record.id) == data["importId"]
        (event,) = await _events(db_session, world.client.id)
        assert event.props == {
            "client_id": str(world.client.id),
            "mapeamento_id": str(world.mapping.id),
            "linhas": 3,
            "colunas_reconhecidas": 5,
            "rejeitado": False,
            "motivo": "nenhum",
        }

    async def test_xlsx_valido(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        rows: list[list[Any]] = [
            [datetime(2026, 6, 5), SECRET_DESCRIPTION, -1500.0, SECRET_CATEGORY, "NF 1"],
            [datetime(2026, 6, 10), "Energia", -320.45, "Energia", 2],
        ]
        await _login(client_with_db, world.admin)
        resp = await _process(
            client_with_db, world.client_xlsx, _xlsx(rows), name="planilha.xlsx", mime=XLSX_MIME
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["rows"] == 2
        movements = await _movements(db_session, world.client_xlsx.id)
        assert sorted(r.amount for r in movements) == [Decimal("-1500.00"), Decimal("-320.45")]
        assert {r.document for r in movements} == {"NF 1", "2"}

    async def test_duas_linhas_identicas_sao_dois_movimentos(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _process(client_with_db, world.client, _csv([ROWS[0], ROWS[0]]))
        assert resp.status_code == 201, resp.text
        rows = await _movements(db_session, world.client.id)
        assert len(rows) == 2
        assert len({r.source_movement_id for r in rows}) == 2

    async def test_arquivo_corrigido_marca_o_anterior_ausente_e_nunca_apaga(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        original = _csv(ROWS)
        assert (await _process(client_with_db, world.client, original)).status_code == 201
        corrigido = _csv(
            [ROWS[1], ["16/06/2026", "Venda balcão corrigida", "990,00", "Receita", ""]]
        )
        resp = await _process(client_with_db, world.client, corrigido)
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["absent"] == 3

        rows = await _movements(db_session, world.client.id)
        assert len(rows) == 5, "nunca apaga"
        por_status = {
            status: sorted(r.source_movement_id for r in rows if r.status == status)
            for status in (MovementStatus.PRESENTE.value, MovementStatus.AUSENTE_NA_ORIGEM.value)
        }
        assert por_status[MovementStatus.AUSENTE_NA_ORIGEM.value] == sorted(
            f"{_hash(original)[:16]}:{n}" for n in (2, 3, 4)
        )
        assert por_status[MovementStatus.PRESENTE.value] == sorted(
            f"{_hash(corrigido)[:16]}:{n}" for n in (2, 3)
        )
        assert len(await _imports(db_session, world.client.id)) == 2

    async def test_segundo_mes_processa_sem_tocar_o_mapeamento(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O ganho da S-1: do segundo mês em diante, enviar o arquivo é um passo só."""
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, _csv(ROWS))).status_code == 201
        await db_session.refresh(world.mapping)
        updated_before = world.mapping.updated_at

        julho = [["03/07/2026", "Aluguel julho", "-1.500,00", SECRET_CATEGORY, "NF 3"]]
        resp = await _process(client_with_db, world.client, _csv(julho), competence="2026-07")
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["categoriesCreated"] == 0, "a categoria de junho é a mesma"

        await db_session.refresh(world.mapping)
        assert world.mapping.updated_at == updated_before
        assert (await _sync_state(db_session, world.client.id, JUL)) is not None
        assert len(await _movements(db_session, world.client.id)) == 4
        assert len(await _categories(db_session, world.client.id)) == 3

    async def test_operador_do_cliente_envia(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """A célula `upload_client_file` do operador (R5)."""
        await _login(client_with_db, world.operator)
        resp = await _process(client_with_db, world.client, _csv(ROWS))
        assert resp.status_code == 201, resp.text
        (record,) = await _imports(db_session, world.client.id)
        assert record.created_by == world.operator.id

    async def test_lista_de_arquivos_com_filtro_e_autor_mascarado_por_escopo(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, _csv(ROWS))).status_code == 201
        julho = [["03/07/2026", "x", "-1,00", "A", ""]]
        assert (
            await _process(client_with_db, world.client, _csv(julho), competence="2026-07")
        ).status_code == 201

        tudo = await client_with_db.get(f"{_base(world.client)}/imports")
        assert tudo.status_code == 200, tudo.text
        assert sorted(i["competence"] for i in tudo.json()["data"]) == ["2026-06", "2026-07"]
        (junho,) = [i for i in tudo.json()["data"] if i["competence"] == "2026-06"]
        assert junho["rows"] == 3
        assert junho["fileHash"] == _hash(_csv(ROWS))
        assert junho["author"] == {"name": world.admin.name, "email": world.admin.email}

        filtrado = await client_with_db.get(
            f"{_base(world.client)}/imports", params={"competence": "2026-07"}
        )
        assert [i["competence"] for i in filtrado.json()["data"]] == ["2026-07"]

        # Usuário do cliente vendo autor de staff: "Equipe …", sem e-mail (§3.15).
        await _login(client_with_db, world.operator)
        como_operador = await client_with_db.get(f"{_base(world.client)}/imports")
        assert como_operador.status_code == 200, como_operador.text
        for item in como_operador.json()["data"]:
            assert item["author"]["name"].startswith("Equipe")
            assert item["author"]["email"] is None
        assert world.admin.email not in como_operador.text

        malformada = await client_with_db.get(
            f"{_base(world.client)}/imports", params={"competence": "2026-13"}
        )
        assert malformada.status_code == 400
        assert _error(malformada)["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Recusas — sem processamento parcial
# ---------------------------------------------------------------------------


class TestRecusasSemProcessamentoParcial:
    async def test_sem_mapeamento_e_409_com_as_colunas_encontradas(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _process(client_with_db, world.client_nomap, _csv(ROWS))
        assert resp.status_code == 409, resp.text
        error = _error(resp)
        assert error["code"] == "SEM_MAPEAMENTO"
        assert error["details"]["foundColumns"] == HEADER
        assert SECRET_DESCRIPTION not in resp.text
        await _assert_nothing_processed(db_session, world.client_nomap, "sem_mapeamento")

    async def test_cabecalho_divergente_nomeia_a_coluna(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O exemplo do PRD: «Histórico» virou «Descrição»."""
        await _login(client_with_db, world.admin)
        header = ["Data", "Descrição", "Valor", "Categoria", "Documento"]
        resp = await _process(client_with_db, world.client, _csv(ROWS, header))
        assert resp.status_code == 422, resp.text
        error = _error(resp)
        assert error["code"] == "CABECALHO_DIVERGENTE"
        assert error["details"] == {"missingColumns": ["Histórico"], "foundColumns": header}
        assert SECRET_DESCRIPTION not in resp.text
        await _assert_nothing_processed(db_session, world.client, "cabecalho_divergente")

    async def test_linhas_invalidas_com_numero_e_motivo_sem_a_celula(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        rows: list[list[Any]] = [
            ["05/06/2026", SECRET_DESCRIPTION, "abc", "A", ""],
            ["99/99/2026", "x", "1,00", "A", ""],
            ["05/06/2026", "y", "", "A", ""],
            ["05/06/2026", "ok", "1,00", "A", ""],
        ]
        resp = await _process(client_with_db, world.client, _csv(rows))
        assert resp.status_code == 422, resp.text
        error = _error(resp)
        assert error["code"] == "LINHAS_INVALIDAS"
        assert error["details"] == {
            "lines": [
                {"line": 2, "reason": "valor_nao_numerico"},
                {"line": 3, "reason": "data_invalida"},
                {"line": 4, "reason": "campo_obrigatorio_vazio"},
            ],
            "total": 3,
        }
        assert SECRET_DESCRIPTION not in resp.text
        assert "abc" not in json.dumps(error["details"])
        await _assert_nothing_processed(db_session, world.client, "linhas_invalidas")

    async def test_total_divergente_bloqueia_e_total_igual_passa(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _process(client_with_db, world.client, _csv(ROWS), declared_total="-840,46")
        assert resp.status_code == 422, resp.text
        error = _error(resp)
        assert error["code"] == "TOTAL_DIVERGENTE"
        assert error["details"] == {"declaredTotal": "-840.46", "computedTotal": "-840.45"}
        await _assert_nothing_processed(db_session, world.client, "total_divergente")

        ok = await _process(client_with_db, world.client, _csv(ROWS), declared_total=TOTAL)
        assert ok.status_code == 201, ok.text

    async def test_pdf_e_422_com_motivo_acionavel(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        pdf = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        resp = await _process(
            client_with_db, world.client, pdf, name="extrato.pdf", mime="application/pdf"
        )
        assert resp.status_code == 422, resp.text
        error = _error(resp)
        assert error["code"] == "FORMATO_NAO_SUPORTADO"
        assert "PDF" in error["userMessage"]
        assert "CSV ou XLSX" in error["userMessage"]
        await _assert_nothing_processed(db_session, world.client, "formato_nao_suportado")

    @pytest.mark.parametrize("caso", ["zip_quebrado", "bomba"])
    async def test_xlsx_malformado_ou_bomba_e_422_com_mensagem_fixa_sem_500(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World, caso: str
    ) -> None:
        if caso == "zip_quebrado":
            content = b"PK\x03\x04" + b"\xff" * 128
        else:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("xl/worksheets/sheet1.xml", b"0" * 2_000_000)
            content = buf.getvalue()
        await _login(client_with_db, world.admin)
        resp = await _process(
            client_with_db, world.client_xlsx, content, name="planilha.xlsx", mime=XLSX_MIME
        )
        assert resp.status_code == 422, resp.text
        error = _error(resp)
        assert error["code"] == "ARQUIVO_INVALIDO"
        assert error["userMessage"].startswith("Não foi possível ler o arquivo.")
        await _assert_nothing_processed(db_session, world.client_xlsx, "arquivo_invalido")

    async def test_a_recusa_sobrevive_ao_rollback_real_da_request(
        self, client_with_request_rollback: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Política REAL de transação: o `AppError` faz `rollback()` — e a métrica fica."""
        await _login(client_with_request_rollback, world.admin)
        header = ["Data", "Descrição", "Valor", "Categoria", "Documento"]
        resp = await _process(client_with_request_rollback, world.client, _csv(ROWS, header))
        assert resp.status_code == 422, resp.text
        db_session.expunge_all()
        (event,) = await _events(db_session, world.client.id)
        assert event.props["motivo"] == "cabecalho_divergente"
        assert await _movements(db_session, world.client.id) == []
        assert await _imports(db_session, world.client.id) == []

    async def test_mesmo_arquivo_duas_vezes_e_409_e_a_unique_e_do_banco(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        content = _csv(ROWS)
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, content)).status_code == 201
        resp = await _process(client_with_db, world.client, content)
        assert resp.status_code == 409, resp.text
        assert _error(resp)["code"] == "ARQUIVO_JA_PROCESSADO"

        assert len(await _movements(db_session, world.client.id)) == 3
        assert len(await _imports(db_session, world.client.id)) == 1
        events = await _events(db_session, world.client.id)
        assert [e.props["motivo"] for e in events] == ["nenhum", "arquivo_ja_processado"]

        # Sob corrida (duas abas) a leitura amigável não protege nada: a UNIQUE decide,
        # e o repositório a lê pelo NOME da constraint, dentro de um SAVEPOINT.
        duplicado = ClientFileImport(
            client_id=world.client.id,
            competence=JUN,
            file_hash=_hash(content),
            rows=3,
            created_by=world.admin.id,
        )
        assert await ClientFileImportRepository(db_session).add(duplicado) is False
        # A sessão continua utilizável depois do SAVEPOINT desfeito.
        assert len(await _imports(db_session, world.client.id)) == 1
        # Competência DIFERENTE com o mesmo conteúdo não é reenvio.
        outro_mes = ClientFileImport(
            client_id=world.client.id,
            competence=JUL,
            file_hash=_hash(content),
            rows=3,
            created_by=world.admin.id,
        )
        assert await ClientFileImportRepository(db_session).add(outro_mes) is True

    async def test_forma_invalida_e_400_generico(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        for kwargs in ({"competence": "2026-13"}, {"declared_total": "abc"}):
            resp = await _process(client_with_db, world.client, _csv(ROWS), **kwargs)
            assert resp.status_code == 400, resp.text
            assert _error(resp)["code"] == "VALIDATION_ERROR"
        assert await _movements(db_session, world.client.id) == []
        assert await _events(db_session, world.client.id) == []


# ---------------------------------------------------------------------------
# Entre sprints — a prévia do de-para (S12) vê os lançamentos do arquivo (S-4)
# ---------------------------------------------------------------------------


def _preview_url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/mapping/demonstrativo_contabil/preview"


class TestEntreSprints:
    async def test_arquivo_categorizado_da_previa_com_zero_sem_categoria(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, _csv(ROWS))).status_code == 201

        preview = await client_with_db.get(
            _preview_url(world.client), params={"competence": "2026-06"}
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["situations"]["semCategoria"]["count"] == 0
        assert data["situations"]["semDecisao"]["count"] == 3
        assert data["baseState"]["syncedAt"] is not None
        undecided = data["undecidedCategories"]
        assert len(undecided) == 3
        assert {u["sourceType"] for u in undecided} == {"arquivo"}
        assert all(u["categoryCode"].startswith("arq-") for u in undecided)
        assert SECRET_CATEGORY not in preview.text, "a prévia carrega código, nunca o rótulo"

    async def test_classificacao_livre_gera_uma_categoria_por_valor_distinto(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        header = ["Data", "Histórico", "Valor", "Classe"]
        rows = [
            ["05/06/2026", "a", "-1,00", "Aluguel"],
            ["06/06/2026", "b", "-2,00", "Aluguel"],
            ["07/06/2026", "c", "-3,00", "Energia"],
            ["08/06/2026", "d", "-4,00", "aluguel"],  # grafia diferente = outra categoria
        ]
        await _login(client_with_db, world.admin)
        resp = await _process(client_with_db, world.client_livre, _csv(rows, header))
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["categoriesCreated"] == 3
        assert len(await _categories(db_session, world.client_livre.id)) == 3

        preview = await client_with_db.get(
            _preview_url(world.client_livre), params={"competence": "2026-06"}
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["situations"]["semCategoria"]["count"] == 0
        assert len(data["undecidedCategories"]) == 3

    async def test_celula_de_categoria_vazia_e_o_buraco_de_ingestao(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        rows = [ROWS[0], ["10/06/2026", "Sem categoria", "-5,00", "", ""]]
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, _csv(rows))).status_code == 201
        movements = await _movements(db_session, world.client.id)
        assert sorted(r.category_code is None for r in movements) == [False, True]

        preview = await client_with_db.get(
            _preview_url(world.client), params={"competence": "2026-06"}
        )
        assert preview.status_code == 200, preview.text
        situations = preview.json()["data"]["situations"]
        assert situations["semCategoria"]["count"] == 1
        assert situations["semDecisao"]["count"] == 1


# ---------------------------------------------------------------------------
# Estado do cliente e da origem
# ---------------------------------------------------------------------------


class TestEstadoDoClienteEDaOrigem:
    async def test_cliente_encerrado_e_409_em_inspect_e_process(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, _csv(ROWS))).status_code == 201
        fechado = await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        assert fechado.status_code == 204, fechado.text

        inspecao = await _inspect(client_with_db, world.client, _csv(ROWS))
        assert inspecao.status_code == 409, inspecao.text
        assert "encerrado" in _error(inspecao)["userMessage"]
        processo = await _process(client_with_db, world.client, _csv(ROWS))
        assert processo.status_code == 409, processo.text
        assert "encerrado" in _error(processo)["userMessage"]

        # O encerramento PURGA o registro dos arquivos (trilha operacional) — e o
        # histórico continua legível, vazio.
        assert await _imports(db_session, world.client.id) == []
        lista = await client_with_db.get(f"{_base(world.client)}/imports")
        assert lista.status_code == 200, lista.text
        assert lista.json()["data"] == []

    async def test_sincronizar_cliente_so_arquivo_e_409_e_nada_fica_ausente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (await _process(client_with_db, world.client, _csv(ROWS))).status_code == 201
        before = await _sync_state(db_session, world.client.id, JUN)
        assert before is not None
        synced_before = before.synced_at

        resp = await client_with_db.post(
            f"/api/v1/clients/{world.client.id}/movements/sync", json={"competence": "2026-06"}
        )
        assert resp.status_code == 409, resp.text
        assert _error(resp)["code"] == "ORIGEM_POR_ARQUIVO"

        rows = await _movements(db_session, world.client.id)
        assert {r.status for r in rows} == {MovementStatus.PRESENTE.value}
        after = await _sync_state(db_session, world.client.id, JUN)
        assert after is not None
        assert after.synced_at == synced_before
        assert after.sync_failed_at is None


# ---------------------------------------------------------------------------
# Inspeção
# ---------------------------------------------------------------------------


class TestInspecao:
    async def test_formato_colunas_e_amostra_sem_persistir_nada(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await _inspect(client_with_db, world.client, _csv(ROWS))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["format"] == "csv"
        assert data["columns"] == HEADER
        assert data["sample"][0] == [
            "05/06/2026",
            SECRET_DESCRIPTION,
            "-1.500,00",
            SECRET_CATEGORY,
            "NF 1",
        ]
        assert len(data["sample"]) == 3
        assert data["hasMapping"] is True
        # A amostra só existe na resposta.
        assert await _movements(db_session, world.client.id) == []
        assert await _imports(db_session, world.client.id) == []
        assert await _events(db_session, world.client.id) == []

    async def test_sem_mapeamento_le_com_o_delimitador_do_pedido(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        virgula = _csv(ROWS).replace(b";", b",")
        resp = await _inspect(client_with_db, world.client_nomap, virgula, csvDelimiter=",")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["columns"] == HEADER
        assert resp.json()["data"]["hasMapping"] is False

    async def test_pdf_e_422_e_delimitador_fora_do_vocabulario_e_400(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        pdf = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        resp = await _inspect(client_with_db, world.client, pdf)
        assert resp.status_code == 422, resp.text
        assert _error(resp)["code"] == "FORMATO_NAO_SUPORTADO"
        errado = await _inspect(client_with_db, world.client, _csv(ROWS), csvDelimiter="#")
        assert errado.status_code == 400, errado.text
        assert _error(errado)["code"] == "VALIDATION_ERROR"

    async def test_operador_inspeciona_e_sem_login_e_401(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        assert (await _inspect(client_with_db, world.client, _csv(ROWS))).status_code == 401
        await _login(client_with_db, world.operator)
        assert (await _inspect(client_with_db, world.client, _csv(ROWS))).status_code == 200
