"""A base de movimentos, ponta a ponta contra a origem e o banco (BACK 12.1 — R0).

O `respx` intercepta o TRANSPORTE, então o caminho exercitado é o de produção:
fallback datado da S9 sintetiza a conexão → adaptador → `OmieClient` → HTTP. O que
este módulo afirma contra o BANCO (o unitário `test_client_movements_sync_service`
afirma a ordem das escritas e o lock, sem banco):

  - a fixture REAL do extrato vira 80 linhas persistidas, as 32 de saldo fora;
  - o 2º ciclo atualiza, insere e marca `ausente_na_origem`, sem `DELETE`;
    reaparecer volta a `presente`; sem categoria persiste com `category_code` nulo;
  - falha no meio preserva as linhas e o carimbo de sucesso;
  - os 409 não carimbam; sincronizar um cliente não encosta no outro.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import NoOriginConnectionError, OmieFaultError
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientMovement,
    MovementStatus,
    OmieAccountCache,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.client_locks import OriginClientLocks
from app.modules.client_movements import service as service_module
from app.modules.client_movements.repository import ClientMovementsRepository
from app.modules.client_movements.service import ClientMovementsSyncService
from app.modules.clients.repository import ClientRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"
FAKE_APP_KEY = "movements-app-key-12345"
FAKE_APP_SECRET = "movements-app-secret-67890"
JUNHO = date(2026, 6, 1)
CONTA = 2625046958

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "omie" / "listar_extrato.response.json"
)


@pytest.fixture(autouse=True)
def _no_inter_account_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "INTER_ACCOUNT_DELAY_SECONDS", 0)


def _mov(
    omie_id: int,
    *,
    dia: str = "10/06/2026",
    valor: float = 100.0,
    natureza: str = "D",
    categoria: str | None = "2.04.94",
    observacao: str = "PAGTO FORNECEDOR ACME LTDA",
) -> dict[str, Any]:
    """Um movimento na FORMA da resposta real, com texto livre preenchido de propósito."""
    raw: dict[str, Any] = {
        "nCodLancamento": omie_id,
        "cNatureza": natureza,
        "dDataLancamento": dia,
        "nValorDocumento": valor,
        "cSituacao": "Conciliado",
        "cObservacoes": observacao,
        "cRazCliente": "ACME LTDA",
        "cDesCliente": "ACME",
        "nCodCliente": 2624256082,
    }
    if categoria is not None:
        raw["cCodCategoria"] = categoria
        raw["cDesCategoria"] = "Despesas ACME"
    return raw


def _extrato(movimentos: list[dict[str, Any]]) -> httpx.Response:
    saldo = {"cDesCliente": "SALDO ANTERIOR", "dDataLancamento": "01/06/2026", "nSaldo": 0}
    return httpx.Response(200, json={"nCodCC": CONTA, "listaMovimentos": [saldo, *movimentos]})


def _fault() -> httpx.Response:
    return httpx.Response(200, json={"faultcode": "SOAP-ENV:Client-9999", "faultstring": "x"})


async def _creator(session: AsyncSession) -> User:
    user = User(
        name="Seed",
        email=f"movements-seed-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password("Senh@Movimentos#1"),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, contas: tuple[int, ...] = (CONTA,)) -> Client:
    """Cliente com as colunas antigas (fallback datado da S9) e contas no cache."""
    creator = await _creator(session)
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name="Cliente base de movimentos",
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    for conta in contas:
        session.add(
            OmieAccountCache(
                client_id=client.id,
                omie_conta_id=conta,
                name=f"Conta {conta}",
                bank_name="077",
                account_type="CC",
            )
        )
    await session.flush()
    return client


def _service(db: AsyncSession) -> ClientMovementsSyncService:
    return ClientMovementsSyncService(
        db,
        repository=ClientMovementsRepository(db),
        clients=ClientRepository(db),
        settings=get_settings(),
        locks=OriginClientLocks(),
    )


async def _rows(db: AsyncSession, client: Client, competence: date) -> dict[str, ClientMovement]:
    # Relê do BANCO: com expire_on_commit=False o select devolveria o objeto que já
    # está no identity map, com o status do ciclo anterior. populate_existing força o
    # refresh. NUNCA expire_all() aqui: expira o `client` da fixture e o próximo
    # `client.id` estoura MissingGreenlet (lição da S11).
    stmt = (
        select(ClientMovement)
        .where(ClientMovement.client_id == client.id, ClientMovement.competence == competence)
        .execution_options(populate_existing=True)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {row.source_movement_id: row for row in rows}


class TestFixtureReal:
    @respx.mock
    async def test_80_movimentos_persistidos_e_as_32_linhas_de_saldo_fora(
        self, db_session: AsyncSession
    ) -> None:
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        respx.post(OMIE_EXTRATO_URL).mock(return_value=httpx.Response(200, json=payload))
        client = await _seed_client(db_session, contas=(int(payload["nCodCC"]),))

        result = await _service(db_session).sync(client, date(2026, 7, 1))

        rows = await _rows(db_session, client, date(2026, 7, 1))
        assert result.movimentos == len(rows) == 80
        assert {r.status for r in rows.values()} == {MovementStatus.PRESENTE.value}
        assert {r.source_type for r in rows.values()} == {"omie"}
        assert all(r.category_code for r in rows.values())
        assert all(r.source_account_id == str(payload["nCodCC"]) for r in rows.values())


class TestDoisCiclos:
    @respx.mock
    async def test_atualiza_insere_e_marca_ausente_sem_apagar(
        self, db_session: AsyncSession
    ) -> None:
        client = await _seed_client(db_session)
        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(return_value=_extrato([_mov(1, valor=100.0), _mov(2), _mov(3)]))
        await _service(db_session).sync(client, JUNHO)
        repo = ClientMovementsRepository(db_session)
        antes = await repo.count_for_client(client.id)
        assert antes == 3

        # 2º ciclo: 1 mudou de valor, 2 saiu, 3 ficou igual, 4 é novo e sem categoria.
        route.mock(return_value=_extrato([_mov(1, valor=175.5), _mov(3), _mov(4, categoria=None)]))
        result = await _service(db_session).sync(client, JUNHO)

        rows = await _rows(db_session, client, JUNHO)
        assert await repo.count_for_client(client.id) == 4, "nenhuma linha é apagada"
        assert rows["1"].amount == Decimal("-175.50")
        assert rows["2"].status == MovementStatus.AUSENTE_NA_ORIGEM.value
        assert rows["3"].status == MovementStatus.PRESENTE.value
        assert rows["4"].status == MovementStatus.PRESENTE.value
        assert rows["4"].category_code is None, "sem categoria de origem é CONTADO, não descartado"
        assert (result.movimentos, result.sem_categoria, result.ausentes) == (3, 1, 1)

        # 3º ciclo: o 2 reaparece e volta a valer.
        route.mock(return_value=_extrato([_mov(1), _mov(2), _mov(3), _mov(4, categoria=None)]))
        await _service(db_session).sync(client, JUNHO)
        rows = await _rows(db_session, client, JUNHO)
        assert rows["2"].status == MovementStatus.PRESENTE.value

    @respx.mock
    async def test_sincronizar_junho_nao_encosta_em_julho(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(return_value=_extrato([_mov(10, dia="15/07/2026")]))
        await _service(db_session).sync(client, date(2026, 7, 1))

        route.mock(return_value=_extrato([]))
        await _service(db_session).sync(client, JUNHO)

        julho = await _rows(db_session, client, date(2026, 7, 1))
        assert julho["10"].status == MovementStatus.PRESENTE.value

    @respx.mock
    async def test_a_descricao_da_origem_nao_chega_ao_banco(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        respx.post(OMIE_EXTRATO_URL).mock(return_value=_extrato([_mov(1)]))
        await _service(db_session).sync(client, JUNHO)
        row = (await _rows(db_session, client, JUNHO))["1"]
        gravado = [str(v) for v in row.__dict__.values() if v is not None]
        assert not any("ACME" in valor.upper() for valor in gravado)


class TestFalhaPreservaABaseAnterior:
    @respx.mock
    async def test_falha_preserva_linhas_e_carimbo_de_sucesso(
        self, db_session: AsyncSession
    ) -> None:
        client = await _seed_client(db_session)
        repo = ClientMovementsRepository(db_session)
        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(return_value=_extrato([_mov(1), _mov(2)]))
        await _service(db_session).sync(client, JUNHO)
        primeiro = await repo.get_sync_state(client.id, JUNHO)
        assert primeiro.synced_at is not None
        assert primeiro.sync_failed_at is None

        route.mock(return_value=_fault())
        with pytest.raises(OmieFaultError):
            await _service(db_session).sync(client, JUNHO)

        depois = await repo.get_sync_state(client.id, JUNHO)
        assert depois.synced_at == primeiro.synced_at, "a falha NUNCA toca o carimbo de sucesso"
        assert depois.sync_failed_at is not None
        rows = await _rows(db_session, client, JUNHO)
        assert {r.status for r in rows.values()} == {MovementStatus.PRESENTE.value}
        assert len(rows) == 2

    @respx.mock
    async def test_sucesso_depois_da_falha_limpa_o_aviso(self, db_session: AsyncSession) -> None:
        client = await _seed_client(db_session)
        repo = ClientMovementsRepository(db_session)
        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(return_value=_fault())
        with pytest.raises(OmieFaultError):
            await _service(db_session).sync(client, JUNHO)
        estado = await repo.get_sync_state(client.id, JUNHO)
        assert estado.nunca_sincronizada is True
        assert estado.sync_failed_at is not None

        route.mock(return_value=_extrato([_mov(1)]))
        await _service(db_session).sync(client, JUNHO)
        estado = await repo.get_sync_state(client.id, JUNHO)
        assert estado.nunca_sincronizada is False
        assert estado.sync_failed_at is None

    async def test_os_409_nao_carimbam(self, db_session: AsyncSession) -> None:
        creator = await _creator(db_session)
        client = Client(name="Sem origem", active=True, created_by=creator.id)
        db_session.add(client)
        await db_session.flush()

        with pytest.raises(NoOriginConnectionError):
            await _service(db_session).sync(client, JUNHO)

        estado = await ClientMovementsRepository(db_session).get_sync_state(client.id, JUNHO)
        assert (estado.synced_at, estado.sync_failed_at) == (None, None)


class TestIsolamentoEntreClientes:
    @respx.mock
    async def test_um_cliente_nao_encosta_na_base_do_outro(self, db_session: AsyncSession) -> None:
        a = await _seed_client(db_session)
        b = await _seed_client(db_session)
        route = respx.post(OMIE_EXTRATO_URL)
        route.mock(return_value=_extrato([_mov(1)]))
        await _service(db_session).sync(b, JUNHO)

        route.mock(return_value=_extrato([]))
        await _service(db_session).sync(a, JUNHO)

        rows_b = await _rows(db_session, b, JUNHO)
        assert rows_b["1"].status == MovementStatus.PRESENTE.value
        estado_a = await ClientMovementsRepository(db_session).get_sync_state(a.id, JUNHO)
        assert estado_a.synced_at is not None
