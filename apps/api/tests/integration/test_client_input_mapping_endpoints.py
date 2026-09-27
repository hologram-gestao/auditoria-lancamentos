"""Rotas do mapeamento de entrada do arquivo (Sprint 14 / BACK 14.1 — R1/R5).

    GET /api/v1/clients/{client_id}/input-mapping  → quem alcança o cliente
    PUT /api/v1/clients/{client_id}/input-mapping  → `manage_input_mapping`

O que este módulo afirma:
  - cliente sem mapeamento: 200 com `mapping: null` (NUNCA 404 — 404 é o código
    anti-enumeração do tenant);
  - PUT cria (`created: true`) e um segundo PUT SUBSTITUI sem duplicar linha
    (`created: false`, mesma `id`, `updated_by` do segundo autor, `created_by` do
    primeiro) — a UNIQUE `(client_id)` é a garantia, e ela é provada NO BANCO;
  - convenção de sinal ausente ou incoerente com os campos: **400
    `VALIDATION_ERROR`** genérico (§4.8), e a contagem não muda;
  - o `client_operator` LÊ 200 e recebe 403 no PUT com 1 linha `denied` em
    `access_audit` (user_scope, actor_client_id, actor_organization_id);
    `client_manager`, gerente da carteira, admin da org e plataforma: 200;
  - cliente encerrado: PUT 409 `ClientClosedError`, GET segue 200.

O cross-tenant e o cross-org das duas rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import func, null, select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    AccessAudit,
    Client,
    ClientAssignment,
    ClientInputMapping,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Mapeamento#1"


def _csv_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fileFormat": "csv",
        "csvDelimiter": ";",
        "encoding": "utf-8",
        "dateColumn": "Data",
        "descriptionColumn": "Histórico",
        "amountColumn": "Valor",
        "categoryColumn": "Categoria",
        "categoryMode": "coluna_categoria",
        "dateFormat": "dd/mm/yyyy",
        "decimalSeparator": ",",
        "signConvention": "valor_com_sinal",
    }
    base.update(overrides)
    return base


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
        name="Mapeamento",
        email=f"map-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
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
    platform: User
    tenant_manager: User
    tenant_operator: User
    client: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.platform = await _seed_user(
        db_session, role=UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM
    )
    # Cliente SEM ERP — o que a Sprint 14 existe para atender.
    w.client = Client(name="Cliente sem sistema", active=True, created_by=w.admin.id)
    db_session.add(w.client)
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
    return f"/api/v1/clients/{client.id}/input-mapping"


async def _rows(db: AsyncSession, client_id: Any) -> list[ClientInputMapping]:
    stmt = (
        select(ClientInputMapping)
        .where(ClientInputMapping.client_id == client_id)
        .execution_options(populate_existing=True)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _denied(db: AsyncSession, client_id: Any) -> list[AccessAudit]:
    stmt = select(AccessAudit).where(
        AccessAudit.action == "denied", AccessAudit.client_id == client_id
    )
    return list((await db.execute(stmt)).scalars().all())


class TestLeitura:
    async def test_sem_mapeamento_e_200_com_null_nunca_404(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(_url(world.client))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"data": {"mapping": None}}

    async def test_operador_do_cliente_le(self, client_with_db: AsyncClient, world: World) -> None:
        await _login(client_with_db, world.tenant_operator)
        resp = await client_with_db.get(_url(world.client))
        assert resp.status_code == 200, resp.text

    async def test_cliente_inexistente_e_404(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(f"/api/v1/clients/{uuid4()}/input-mapping")
        assert resp.status_code == 404

    async def test_sem_login_e_401(self, client_with_db: AsyncClient, world: World) -> None:
        assert (await client_with_db.get(_url(world.client))).status_code == 401


class TestEscrita:
    async def test_put_cria_e_get_devolve_o_mesmo(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.put(_url(world.client), json=_csv_payload())
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["created"] is True
        mapping = data["mapping"]
        assert mapping["signConvention"] == "valor_com_sinal"
        assert mapping["csvDelimiter"] == ";"
        assert mapping["dateColumn"] == "Data"
        assert mapping["categoryMode"] == "coluna_categoria"
        assert mapping["natureColumn"] is None

        lido = await client_with_db.get(_url(world.client))
        assert lido.status_code == 200
        assert lido.json()["data"]["mapping"] == mapping

        rows = await _rows(db_session, world.client.id)
        assert len(rows) == 1
        assert rows[0].created_by == world.admin.id
        assert rows[0].updated_by == world.admin.id

    async def test_segundo_put_substitui_sem_duplicar(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        primeiro = await client_with_db.put(_url(world.client), json=_csv_payload())
        assert primeiro.status_code == 200, primeiro.text
        first_id = primeiro.json()["data"]["mapping"]["id"]

        # Outro autor, outra convenção: o mapeamento inteiro é SUBSTITUÍDO.
        await _login(client_with_db, world.tenant_manager)
        segundo = await client_with_db.put(
            _url(world.client),
            json=_csv_payload(
                signConvention="coluna_natureza",
                natureColumn="D/C",
                debitValue="D",
                creditValue="C",
            ),
        )
        assert segundo.status_code == 200, segundo.text
        data = segundo.json()["data"]
        assert data["created"] is False
        assert data["mapping"]["id"] == first_id
        assert data["mapping"]["signConvention"] == "coluna_natureza"
        assert data["mapping"]["natureColumn"] == "D/C"

        rows = await _rows(db_session, world.client.id)
        assert len(rows) == 1
        assert rows[0].sign_convention == "coluna_natureza"
        assert rows[0].created_by == world.admin.id
        assert rows[0].updated_by == world.tenant_manager.id

    async def test_colunas_separadas_grava_sem_coluna_de_valor(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.put(
            _url(world.client),
            json=_csv_payload(
                signConvention="colunas_separadas",
                amountColumn=None,
                debitColumn="Débito",
                creditColumn="Crédito",
            ),
        )
        assert resp.status_code == 200, resp.text
        rows = await _rows(db_session, world.client.id)
        assert rows[0].amount_column is None
        assert (rows[0].debit_column, rows[0].credit_column) == ("Débito", "Crédito")

    async def test_xlsx_com_classificacao_livre(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """R4: o cliente que manda só descrição aponta a própria descrição."""
        await _login(client_with_db, world.admin)
        resp = await client_with_db.put(
            _url(world.client),
            json=_csv_payload(
                fileFormat="xlsx",
                csvDelimiter=None,
                encoding=None,
                categoryColumn="Histórico",
                categoryMode="classificacao_livre",
            ),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["mapping"]["categoryMode"] == "classificacao_livre"

    @pytest.mark.parametrize(
        "payload",
        [
            {k: v for k, v in _csv_payload().items() if k != "signConvention"},
            _csv_payload(signConvention="inferir"),
            _csv_payload(signConvention="coluna_natureza"),
            _csv_payload(signConvention="colunas_separadas"),
            _csv_payload(csvDelimiter=None),
            _csv_payload(fileFormat="pdf", csvDelimiter=None, encoding=None),
            _csv_payload(dateColumn="   "),
            _csv_payload(client_id=str(uuid4())),
        ],
        ids=[
            "sem_sinal",
            "sinal_fora_do_vocabulario",
            "natureza_sem_campos",
            "separadas_sem_colunas",
            "csv_sem_delimitador",
            "pdf",
            "coluna_vazia",
            "campo_desconhecido",
        ],
    )
    async def test_forma_invalida_e_400_generico_e_nada_grava(
        self,
        client_with_db: AsyncClient,
        world: World,
        db_session: AsyncSession,
        payload: dict[str, Any],
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.put(_url(world.client), json=payload)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert await _rows(db_session, world.client.id) == []

    async def test_unique_por_cliente_e_do_banco(
        self, world: World, db_session: AsyncSession
    ) -> None:
        """Duas linhas para o mesmo cliente, direto no ORM: a UNIQUE recusa."""

        def _row() -> ClientInputMapping:
            return ClientInputMapping(
                client_id=world.client.id,
                file_format="xlsx",
                date_column="Data",
                description_column="Hist",
                amount_column="Valor",
                date_format="dd/mm/yyyy",
                decimal_separator=",",
                sign_convention="valor_com_sinal",
                created_by=world.admin.id,
                updated_by=world.admin.id,
            )

        db_session.add(_row())
        await db_session.flush()
        db_session.add(_row())
        with pytest.raises(IntegrityError) as exc:
            await db_session.flush()
        assert "uq_client_input_mappings_client_id" in str(exc.value)
        await db_session.rollback()

    async def test_check_de_coerencia_e_do_banco(
        self, world: World, db_session: AsyncSession
    ) -> None:
        """A borda recusa cedo; se algo escapar, o CHECK `sign_coherent` recusa."""
        db_session.add(
            ClientInputMapping(
                client_id=world.client.id,
                file_format="xlsx",
                date_column="Data",
                description_column="Hist",
                amount_column=None,  # valor_com_sinal SEM coluna de valor
                date_format="dd/mm/yyyy",
                decimal_separator=",",
                sign_convention="valor_com_sinal",
                created_by=world.admin.id,
                updated_by=world.admin.id,
            )
        )
        with pytest.raises(IntegrityError) as exc:
            await db_session.flush()
        assert "ck_client_input_mappings_sign_coherent" in str(exc.value)
        await db_session.rollback()


class TestPermissao:
    @pytest.mark.parametrize("papel", ["platform", "admin", "manager", "tenant_manager"])
    async def test_quem_tem_a_celula_escreve(
        self, client_with_db: AsyncClient, world: World, papel: str
    ) -> None:
        await _login(client_with_db, getattr(world, papel))
        resp = await client_with_db.put(_url(world.client), json=_csv_payload())
        assert resp.status_code == 200, resp.text

    async def test_operador_le_200_e_escreve_403_com_trilha(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        """O par que prova que a permissão é PRÓPRIA: ler passa, configurar não."""
        await _login(client_with_db, world.tenant_operator)
        assert (await client_with_db.get(_url(world.client))).status_code == 200

        resp = await client_with_db.put(_url(world.client), json=_csv_payload())
        assert resp.status_code == 403, resp.text
        assert "Cliente sem sistema" not in resp.text
        assert await _rows(db_session, world.client.id) == []

        denied = await _denied(db_session, world.client.id)
        assert len(denied) == 1
        assert denied[0].user_scope == "client"
        assert denied[0].actor_client_id == world.client.id
        # A org do operador é a Hologram pelo `server_default` — comparada com a
        # constante, não com o atributo da instância (expirado após o flush; ler
        # dispararia um SELECT síncrono e `MissingGreenlet`).
        assert denied[0].actor_organization_id == HOLOGRAM_ORGANIZATION_ID
        assert denied[0].user_id == world.tenant_operator.id


class TestClienteEncerrado:
    async def test_put_e_409_e_get_segue_200(
        self, client_with_db: AsyncClient, world: World, db_session: AsyncSession
    ) -> None:
        await _login(client_with_db, world.admin)
        assert (
            await client_with_db.put(_url(world.client), json=_csv_payload())
        ).status_code == 200
        assert (
            await client_with_db.post(f"/api/v1/clients/{world.client.id}/close")
        ).status_code == 204

        resp = await client_with_db.put(_url(world.client), json=_csv_payload())
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "CONFLICT"
        assert "encerrado" in resp.json()["error"]["userMessage"]

        # O encerramento PURGA o mapeamento (configuração): a leitura devolve null.
        lido = await client_with_db.get(_url(world.client))
        assert lido.status_code == 200, lido.text
        assert lido.json()["data"]["mapping"] is None
        assert (
            await db_session.execute(
                select(func.count(ClientInputMapping.id)).where(
                    ClientInputMapping.client_id == world.client.id
                )
            )
        ).scalar_one() == 0
