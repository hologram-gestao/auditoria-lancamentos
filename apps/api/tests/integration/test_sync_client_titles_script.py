"""O comando em lote da carteira (BACK 11.2) — `scripts/sync_client_titles.py`.

Quatro comportamentos, e os dois últimos são o que separa um lote utilizável de
um que quebra no primeiro cliente mal configurado:

  - varre os clientes ABERTOS e **pula o encerrado** (§4.12 — cliente encerrado
    não opera nem tem origem a consultar);
  - **um cliente de cada vez**, sem chamadas concorrentes à origem;
  - **falha de um cliente é registrada e PULADA** — o lote continua e o
    `titles_synced_at` de quem falhou fica intocado;
  - **idempotente**: duas execuções seguidas deixam o mesmo estado.

⚠️ O `main()` do script não é chamado: ele faz `init_db`/`close_db` sobre a URL
real das settings e derrubaria a engine da fixture. O que se testa são as duas
funções que carregam o comportamento — `_client_ids_to_sync` e a varredura —, com
a session da fixture no lugar do `session_factory` do processo. É a mesma
separação que permite ao módulo rodar sob Cloud Run Job sem nada de teste dentro.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import respx
from scripts.sync_client_titles import _client_ids_to_sync

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Client, TitleStatus, User, UserRole, UserScope
from app.integrations.omie.client_locks import OriginClientLocks
from app.integrations.providers import omie_adapter
from app.modules.client_titles.repository import ClientTitlesRepository
from app.modules.client_titles.service import ClientTitlesSyncService
from app.modules.clients.repository import ClientRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

OMIE_PAGAR_URL = "https://app.omie.com.br/api/v1/financas/contapagar/"
OMIE_RECEBER_URL = "https://app.omie.com.br/api/v1/financas/contareceber/"

HOJE = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _no_inter_call_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(omie_adapter, "_INTER_CALL_DELAY_SECONDS", 0)


def _titulo(omie_id: int, *, vencimento: date) -> dict[str, Any]:
    return {
        "codigo_lancamento_omie": omie_id,
        "data_vencimento": vencimento.strftime("%d/%m/%Y"),
        "valor_documento": 100.0,
        "codigo_cliente_fornecedor": 2624256082,
        "codigo_categoria": "2.04.94",
        "id_conta_corrente": 2617722760,
        "numero_documento": "00123/A",
        "status_titulo": "ATRASADO",
    }


def _envelope(key: str, items: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "pagina": 1,
            "total_de_paginas": 1,
            "registros": len(items),
            "total_de_registros": len(items),
            key: items,
        },
    )


def _fault(code: str, message: str) -> httpx.Response:
    return httpx.Response(200, json={"faultcode": code, "faultstring": message})


async def _seed(
    db: AsyncSession, *, name: str, with_origin: bool = True, closed: bool = False
) -> Client:
    creator = User(
        name="Seed",
        email=f"batch-{uuid4().hex[:8]}@hologram.com.br",
        password_hash=hash_password("Senh@Lote#1"),
        role=UserRole.ADMIN.value,
        active=True,
        scope=UserScope.SYSTEM.value,
    )
    db.add(creator)
    await db.flush()

    kwargs: dict[str, Any] = {}
    if with_origin:
        hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
        ct_key, iv_key = encrypt(f"batch-key-{uuid4().hex[:6]}", hex_key)
        ct_secret, iv_secret = encrypt(f"batch-secret-{uuid4().hex[:6]}", hex_key)
        kwargs = {
            "omie_app_key_encrypted": ct_key,
            "omie_app_key_iv": iv_key,
            "omie_app_secret_encrypted": ct_secret,
            "omie_app_secret_iv": iv_secret,
        }

    client = Client(
        name=name,
        active=True,
        created_by=creator.id,
        closed_at=datetime(2026, 8, 1, tzinfo=UTC) if closed else None,
        **kwargs,
    )
    db.add(client)
    await db.flush()
    return client


async def _run_batch(db: AsyncSession, client_ids: list[UUID]) -> tuple[int, int]:
    """A varredura do script, com a session da fixture. Devolve `(ok, falhas)`.

    Espelha `main()` no que importa: um cliente de cada vez, e uma falha é
    contada e pulada em vez de abortar o lote. O `locks` é compartilhado entre
    os clientes de propósito — é o do processo, em produção.
    """
    locks = OriginClientLocks()
    ok = 0
    falhas = 0
    for client_id in client_ids:
        client = await db.get(Client, client_id)
        assert client is not None
        service = ClientTitlesSyncService(
            db,
            repository=ClientTitlesRepository(db),
            clients=ClientRepository(db),
            settings=get_settings(),
            locks=locks,
        )
        try:
            await service.sync(client)
        except Exception:
            falhas += 1
        else:
            ok += 1
    return ok, falhas


class TestSelecaoDeClientes:
    async def test_cliente_encerrado_fica_fora_do_lote(self, db_session: AsyncSession) -> None:
        """§4.12: encerrado não opera, e a origem dele já foi destruída.

        Sincronizá-lo seria uma chamada garantidamente inútil à origem — e, com o
        crypto-shredding, uma que falharia.
        """
        aberto = await _seed(db_session, name="Aberto")
        encerrado = await _seed(db_session, name="Encerrado", closed=True)

        ids = await _client_ids_to_sync(db_session)

        assert aberto.id in ids
        assert encerrado.id not in ids

    async def test_cliente_desativado_continua_no_lote(self, db_session: AsyncSession) -> None:
        """`active=False` não é encerramento.

        Cliente desativado segue sendo um cliente aberto cujo aging alguém pode
        precisar consultar; só `closed_at` é terminal.
        """
        client = await _seed(db_session, name="Desativado")
        client.active = False
        await db_session.flush()

        assert client.id in await _client_ids_to_sync(db_session)

    async def test_a_ordem_e_estavel(self, db_session: AsyncSession) -> None:
        """Ordem por `created_at`: duas execuções percorrem a mesma sequência, o
        que torna uma interrupção diagnosticável pelo log."""
        primeiro = await _client_ids_to_sync(db_session)
        segundo = await _client_ids_to_sync(db_session)
        assert primeiro == segundo


class TestVarredura:
    @respx.mock
    async def test_falha_de_um_cliente_nao_derruba_o_lote(self, db_session: AsyncSession) -> None:
        """O comportamento que faz o lote diário sobreviver a um cadastro ruim.

        Três clientes: o primeiro sem origem (409 da taxonomia), o segundo com a
        origem instável (`faultstring`), o terceiro saudável. O terceiro **tem**
        de terminar sincronizado.
        """
        sem_origem = await _seed(db_session, name="Sem origem", with_origin=False)
        instavel = await _seed(db_session, name="Instavel")
        saudavel = await _seed(db_session, name="Saudavel")
        repo = ClientTitlesRepository(db_session)

        chamadas = {"n": 0}

        def _responder(request: httpx.Request) -> httpx.Response:
            chamadas["n"] += 1
            body = json.loads(request.content)
            # A credencial do cliente instável é a 1ª a ser usada; identificá-la
            # pela app_key evita depender da ordem das rotas do respx.
            if body["app_key"].startswith("batch-key") and chamadas["n"] <= 1:
                return _fault("SOAP-ENV:Client-9999", "Instabilidade")
            return _envelope("conta_pagar_cadastro", [_titulo(1, vencimento=date(2026, 6, 1))])

        respx.post(OMIE_PAGAR_URL).mock(side_effect=_responder)
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        ok, falhas = await _run_batch(db_session, [sem_origem.id, instavel.id, saudavel.id])

        assert falhas >= 1, "o cliente sem origem tem de contar como falha pulada"
        assert ok >= 1, "o cliente saudável tem de ser sincronizado depois da falha"
        # o saudável chegou ao fim
        assert await repo.count_for_client(saudavel.id) >= 1
        # e o sem origem não ganhou carimbo nenhum
        assert await repo.get_sync_state(sem_origem.id) == (None, None)

    @respx.mock
    async def test_duas_execucoes_do_lote_deixam_o_mesmo_estado(
        self, db_session: AsyncSession
    ) -> None:
        """Idempotência do lote — o que permite simplesmente rodar de novo."""
        client = await _seed(db_session, name="Idempotente")
        repo = ClientTitlesRepository(db_session)
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=_envelope(
                "conta_pagar_cadastro",
                [
                    _titulo(1, vencimento=date(2026, 6, 1)),
                    _titulo(2, vencimento=date(2026, 7, 1)),
                ],
            )
        )
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        await _run_batch(db_session, [client.id])
        primeira = await repo.count_for_client(client.id)
        rows, _ = await repo.list_for_client(client.id, today=HOJE, limit=50, offset=0)
        status_primeira = {r.external_id: r.status for r in rows}

        await _run_batch(db_session, [client.id])
        assert await repo.count_for_client(client.id) == primeira
        rows, _ = await repo.list_for_client(client.id, today=HOJE, limit=50, offset=0)
        assert {r.external_id: r.status for r in rows} == status_primeira
        assert set(status_primeira.values()) == {TitleStatus.EM_ABERTO.value}

    @respx.mock
    async def test_dois_clientes_sao_sincronizados_em_serie(self, db_session: AsyncSession) -> None:
        """Nunca duas chamadas à origem em voo ao mesmo tempo.

        `asyncio.gather` sobre clientes seria a forma mais rápida de descobrir o
        limite da origem em produção — e dois clientes da mesma organização podem
        compartilhar credencial.
        """
        a = await _seed(db_session, name="Cliente A")
        b = await _seed(db_session, name="Cliente B")
        em_voo = 0
        pico = 0

        def _responder(request: httpx.Request) -> httpx.Response:
            nonlocal em_voo, pico
            em_voo += 1
            pico = max(pico, em_voo)
            em_voo -= 1
            return _envelope("conta_pagar_cadastro", [])

        respx.post(OMIE_PAGAR_URL).mock(side_effect=_responder)
        respx.post(OMIE_RECEBER_URL).mock(return_value=_envelope("conta_receber_cadastro", []))

        ok, falhas = await _run_batch(db_session, [a.id, b.id])

        assert (ok, falhas) == (2, 0)
        assert pico == 1
