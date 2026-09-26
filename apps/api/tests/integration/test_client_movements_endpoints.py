"""Rotas da BASE DE MOVIMENTOS (Sprint 12 / BACK 12.2 — R0) e a instrumentação.

    POST /api/v1/clients/{client_id}/movements/sync        → `sync_client_movements`
    GET  /api/v1/clients/{client_id}/movements/sync-state  → quem alcança o cliente

O que este módulo afirma:
  - o `client_operator` LÊ o estado (200) e NÃO sincroniza (403), e a negação vira
    1 linha `denied` em `access_audit`;
  - cliente encerrado: sincronizar 409, ler 200; cliente sem origem: 409 tipado;
  - competência malformada: **400 `VALIDATION_ERROR`**, nunca 422 (§4.8);
  - "nunca sincronizada" é CAMPO (`neverSynced`), não zero;
  - `movimentos_sincronizados`: o teste CONTA LINHAS (duas sincronizações = duas
    linhas, com as contagens certas); falha no meio e 409 geram zero; o POST do
    browser com esse nome é 400.

O cross-tenant e o cross-org das duas rotas rodam na bateria dos três atacantes
(`test_sensitive_endpoints.py`), que lê a lista canônica.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    OmieAccountCache,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_movements import service as service_module

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Movimentos#1"
OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"
CONTA = 2625046958
EVENTO = "movimentos_sincronizados"


@pytest.fixture(autouse=True)
def _no_inter_account_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "INTER_ACCOUNT_DELAY_SECONDS", 0)


def _mov(omie_id: int, *, categoria: str | None = "2.04.94") -> dict[str, Any]:
    raw: dict[str, Any] = {
        "nCodLancamento": omie_id,
        "cNatureza": "D",
        "dDataLancamento": "10/06/2026",
        "nValorDocumento": 100.0,
        "cSituacao": "Conciliado",
    }
    if categoria is not None:
        raw["cCodCategoria"] = categoria
    return raw


def _extrato(movimentos: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(200, json={"nCodCC": CONTA, "listaMovimentos": movimentos})


async def _seed_user(
    session: AsyncSession,
    *,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Movimentos",
        email=f"mov-{role.value}-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User, with_origin: bool = True) -> Client:
    kwargs: dict[str, Any] = {}
    if with_origin:
        hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
        ct_key, iv_key = encrypt(f"mov-key-{uuid4().hex[:6]}", hex_key)
        ct_secret, iv_secret = encrypt(f"mov-secret-{uuid4().hex[:6]}", hex_key)
        kwargs = {
            "omie_app_key_encrypted": ct_key,
            "omie_app_key_iv": iv_key,
            "omie_app_secret_encrypted": ct_secret,
            "omie_app_secret_iv": iv_secret,
        }
    client = Client(name="Cliente movimentos", active=True, created_by=creator.id, **kwargs)
    session.add(client)
    await session.flush()
    if with_origin:
        session.add(
            OmieAccountCache(
                client_id=client.id,
                omie_conta_id=CONTA,
                name="Conta",
                bank_name="077",
                account_type="CC",
            )
        )
        await session.flush()
    return client


class World:
    admin: User
    manager: User
    tenant_manager: User
    tenant_operator: User
    client: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    w.admin = await _seed_user(db_session, role=UserRole.ADMIN)
    w.manager = await _seed_user(db_session, role=UserRole.MANAGER)
    w.client = await _seed_client(db_session, creator=w.admin)
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


def _sync_url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/movements/sync"


def _state_url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/movements/sync-state"


async def _eventos(db: AsyncSession, client_id: Any) -> list[UsageEvent]:
    rows = (await db.execute(select(UsageEvent).where(UsageEvent.event == EVENTO))).scalars().all()
    return [row for row in rows if row.props.get("client_id") == str(client_id)]


async def _denied(db: AsyncSession, client_id: Any) -> int:
    stmt = select(func.count(AccessAudit.id)).where(
        AccessAudit.action == "denied", AccessAudit.client_id == client_id
    )
    return int((await db.execute(stmt)).scalar_one())


class TestPermissao:
    @respx.mock
    @pytest.mark.parametrize("papel", ["admin", "manager", "tenant_manager"])
    async def test_quem_tem_a_celula_sincroniza(
        self, client_with_db: AsyncClient, world: World, papel: str
    ) -> None:
        respx.post(OMIE_EXTRATO_URL).mock(return_value=_extrato([_mov(1), _mov(2, categoria=None)]))
        await _login(client_with_db, getattr(world, papel))

        resp = await client_with_db.post(_sync_url(world.client), json={"competence": "2026-06"})

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert (data["movimentos"], data["semCategoria"], data["contas"]) == (2, 1, 1)
        assert data["state"]["neverSynced"] is False
        assert data["state"]["competence"] == "2026-06"

    async def test_operador_le_o_estado_e_nao_sincroniza_e_a_negacao_fica_na_trilha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O par que prova que `sync_client_movements` não foi reusada de uma
        permissão de leitura: o MESMO papel lê 200 e sincroniza 403."""
        await _login(client_with_db, world.tenant_operator)
        antes = await _denied(db_session, world.client.id)

        leitura = await client_with_db.get(
            _state_url(world.client), params={"competence": "2026-06"}
        )
        escrita = await client_with_db.post(_sync_url(world.client), json={"competence": "2026-06"})

        assert leitura.status_code == 200, leitura.text
        assert escrita.status_code == 403, escrita.text
        assert await _denied(db_session, world.client.id) == antes + 1
        assert "Cliente movimentos" not in escrita.text


class TestErros:
    async def test_cliente_encerrado_409_na_escrita_e_200_na_leitura(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await db_session.execute(
            update(Client).where(Client.id == world.client.id).values(closed_at=datetime.now(UTC))
        )
        await db_session.flush()
        await _login(client_with_db, world.admin)

        escrita = await client_with_db.post(_sync_url(world.client), json={"competence": "2026-06"})
        leitura = await client_with_db.get(
            _state_url(world.client), params={"competence": "2026-06"}
        )

        assert escrita.status_code == 409, escrita.text
        assert leitura.status_code == 200, leitura.text

    async def test_cliente_sem_origem_recebe_o_409_tipado_da_s9(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        sem_origem = await _seed_client(db_session, creator=world.admin, with_origin=False)
        await _login(client_with_db, world.admin)

        resp = await client_with_db.post(_sync_url(sem_origem), json={"competence": "2026-06"})

        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "SEM_CONEXAO"
        assert await _eventos(db_session, sem_origem.id) == []

    @pytest.mark.parametrize("competencia", ["2026-13", "06/2026", "2026-6", "junho"])
    async def test_competencia_malformada_e_400_no_corpo(
        self, client_with_db: AsyncClient, world: World, competencia: str
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(_sync_url(world.client), json={"competence": competencia})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_competencia_ausente_e_400(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        corpo = await client_with_db.post(_sync_url(world.client), json={})
        query = await client_with_db.get(_state_url(world.client))
        assert corpo.status_code == 400, corpo.text
        assert query.status_code == 400, query.text
        assert query.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_competencia_malformada_e_400_na_query(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        resp = await client_with_db.get(_state_url(world.client), params={"competence": "2026-00"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


class TestEstadoDaBase:
    @respx.mock
    async def test_nunca_falhou_e_sincronizada_sao_campos_distintos(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        params = {"competence": "2026-06"}

        nunca = (await client_with_db.get(_state_url(world.client), params=params)).json()["data"]
        assert nunca == {
            "competence": "2026-06",
            "neverSynced": True,
            "syncedAt": None,
            "syncFailedAt": None,
        }

        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(
            return_value=httpx.Response(200, json={"faultcode": "x-9999", "faultstring": "x"})
        )
        falha = await client_with_db.post(_sync_url(world.client), json=params)
        assert falha.status_code >= 400
        so_falha = (await client_with_db.get(_state_url(world.client), params=params)).json()[
            "data"
        ]
        assert so_falha["neverSynced"] is True, "tentou e falhou continua NUNCA sincronizada"
        assert so_falha["syncFailedAt"] is not None

        route.mock(return_value=_extrato([]))
        ok = await client_with_db.post(_sync_url(world.client), json=params)
        assert ok.status_code == 200, ok.text
        vazia = (await client_with_db.get(_state_url(world.client), params=params)).json()["data"]
        assert vazia["neverSynced"] is False, "competência VAZIA não é nunca sincronizada"
        assert vazia["syncedAt"] is not None
        assert vazia["syncFailedAt"] is None


class TestEventoMovimentosSincronizados:
    @respx.mock
    async def test_duas_sincronizacoes_geram_duas_linhas_com_as_contagens(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """ADR-066-BE: o teste CONTA LINHAS — não verifica só que o emit foi chamado."""
        await _login(client_with_db, world.admin)
        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(return_value=_extrato([_mov(1), _mov(2, categoria=None)]))
        assert (
            await client_with_db.post(_sync_url(world.client), json={"competence": "2026-06"})
        ).status_code == 200
        route.mock(return_value=_extrato([_mov(1), _mov(2), _mov(3)]))
        assert (
            await client_with_db.post(_sync_url(world.client), json={"competence": "2026-06"})
        ).status_code == 200

        eventos = await _eventos(db_session, world.client.id)
        assert len(eventos) == 2
        payloads = sorted(
            (e.props["movimentos"], e.props["sem_categoria"], e.props["contas"]) for e in eventos
        )
        assert payloads == [(2, 1, 1), (3, 0, 1)]
        for evento in eventos:
            assert evento.session_id is None
            assert set(evento.props) == {
                "client_id",
                "competencia",
                "movimentos",
                "sem_categoria",
                "contas",
            }
            assert evento.props["competencia"] == "2026-06"

    @respx.mock
    async def test_falha_no_meio_nao_emite(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _login(client_with_db, world.admin)
        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(200, json={"faultcode": "x-9999", "faultstring": "x"})
        )
        resp = await client_with_db.post(_sync_url(world.client), json={"competence": "2026-06"})
        assert resp.status_code >= 400
        assert await _eventos(db_session, world.client.id) == []

    async def test_o_browser_nao_emite_este_evento(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        """Aceitar do browser deixaria forjar a métrica: validação de forma → 400."""
        await _login(client_with_db, world.admin)
        resp = await client_with_db.post(
            "/api/v1/usage-events",
            json={
                "event": EVENTO,
                "session_id": str(uuid4()),
                "props": {
                    "client_id": str(world.client.id),
                    "competencia": "2026-06",
                    "movimentos": 1,
                    "sem_categoria": 0,
                    "contas": 1,
                },
            },
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
