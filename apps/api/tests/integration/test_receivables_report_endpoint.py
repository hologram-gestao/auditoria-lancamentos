"""Relatório de recebíveis (Sprint 15 / BACK 15.2 — R4).

O caso real da reunião de 17/06/2026 (CONTEXT.md): o mesmo total vencido, hoje
100% "inadimplência", passa a se dividir em dois grupos assim que alguém
registra o contexto (BACK 15.1) — e o `perda_provavel` é o único tipo que
CONTINUA contando como inadimplência real.

Cross-tenant e cross-org rodam na bateria de `test_sensitive_endpoints.py` (a
rota nova entrou na lista canônica, 77/77). Aqui ficam: baseline sem contexto
(R4), a classificação pelo contexto MAIS RECENTE, `perda_provavel` ≠ acordo, a
separação a pagar/a receber, título a vencer fora do relatório, e o evento
`recebiveis_classificados` contado em LINHAS.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientTitle,
    TitleContext,
    TitleStatus,
    TitleType,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_titles.repository import ClientTitlesRepository
from app.modules.usage_events.schemas import UsageEventName

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Relatorio#1"
TODAY = date(2026, 9, 24)


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Relatorio",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(f"rel-key-{uuid4().hex[:6]}", hex_key)
    ct_secret, iv_secret = encrypt(f"rel-secret-{uuid4().hex[:6]}", hex_key)
    client = Client(
        name="Cliente do relatório",
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


class World:
    admin: User
    tenant_manager: User
    tenant_operator: User
    client: Client


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    suffix = uuid4().hex[:8]
    w.admin = await _seed_user(
        db_session, email=f"rel-admin-{suffix}@hologram.com.br", role=UserRole.ADMIN
    )
    w.client = await _seed_client(db_session, creator=w.admin)
    w.tenant_manager = await _seed_user(
        db_session,
        email=f"rel-cli-mgr-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.tenant_operator = await _seed_user(
        db_session,
        email=f"rel-cli-op-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    await db_session.flush()
    return w


async def _login(client: AsyncClient, user: User) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _report_url(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/titles/receivables-report"


def _context_url(client: Client, title_id: Any) -> str:
    return f"/api/v1/clients/{client.id}/titles/{title_id}/context"


def _row(
    external_id: str,
    *,
    due_date: date,
    amount: str,
    title_type: TitleType,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "title_type": title_type.value,
        "due_date": due_date,
        "amount": Decimal(amount),
        "status": TitleStatus.EM_ABERTO.value,
        "category_code": "2.04.94",
        "supplier_code": None,
        "omie_conta_id": 2617722760,
        "document_number": None,
    }


async def _seed_title(
    db: AsyncSession,
    client: Client,
    *,
    external_id: str,
    due_date: date,
    amount: str = "1000.00",
    title_type: TitleType = TitleType.A_RECEBER,
) -> str:
    repo = ClientTitlesRepository(db)
    now = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    # ⚠️ `reconcile_cycle` é um CICLO de sincronização: todo título do cliente que
    # não vier no payload vira `ausente_na_origem`. Semear um título por chamada
    # derrubava do aberto os semeados antes, e o relatório (que só soma
    # `em_aberto`) perdia o valor deles — foi assim que três casos deste arquivo
    # falharam na validação humana de 24/09/2026, com o relatório certo. O ciclo
    # recebe o conjunto inteiro já semeado + o novo.
    ja_semeados = (
        (
            await db.execute(
                select(ClientTitle).where(
                    ClientTitle.client_id == client.id,
                    ClientTitle.status == TitleStatus.EM_ABERTO.value,
                    ClientTitle.external_id != external_id,
                )
            )
        )
        .scalars()
        .all()
    )
    payload = [
        _row(
            t.external_id,
            due_date=t.due_date,
            amount=str(t.amount),
            title_type=TitleType(t.title_type),
        )
        for t in ja_semeados
    ]
    payload.append(_row(external_id, due_date=due_date, amount=amount, title_type=title_type))
    await repo.reconcile_cycle(client.id, payload, synced_at=now)
    await repo.mark_sync_succeeded(client.id, at=now)
    await db.flush()
    title_id = (
        await db.execute(
            select(ClientTitle.id).where(
                ClientTitle.client_id == client.id, ClientTitle.external_id == external_id
            )
        )
    ).scalar_one()
    return str(title_id)


async def _eventos(db: AsyncSession, client_id: Any) -> list[UsageEvent]:
    rows = await db.execute(
        select(UsageEvent)
        .where(UsageEvent.event == UsageEventName.RECEBIVEIS_CLASSIFICADOS.value)
        .order_by(UsageEvent.created_at.asc())
    )
    return [e for e in rows.scalars().all() if e.props.get("client_id") == str(client_id)]


class TestBaseline:
    async def test_cliente_sem_nenhum_contexto_e_100_por_cento_inadimplencia(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """R4 baseline: sem contexto registrado, tudo vencido conta como inadimplência."""
        await _seed_title(
            db_session, world.client, external_id="1", due_date=date(2026, 5, 1), amount="500.00"
        )
        await _seed_title(
            db_session, world.client, external_id="2", due_date=date(2026, 7, 1), amount="300.00"
        )
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.get(_report_url(world.client))

        assert resp.status_code == 200, resp.text
        receber = resp.json()["data"]["aReceber"]
        assert receber["inadimplencia"]["total"] == "800.00"
        assert receber["inadimplencia"]["qtd"] == 2
        assert receber["vencidoComContexto"]["total"] == "0.00"
        assert receber["vencidoComContexto"]["qtd"] == 0

    async def test_titulo_a_vencer_nao_aparece_no_relatorio(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Só o VENCIDO entra — título a vencer não é inadimplência nem acordo."""
        await _seed_title(
            db_session, world.client, external_id="futuro", due_date=date(2026, 12, 1)
        )
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.get(_report_url(world.client))

        assert resp.status_code == 200, resp.text
        receber = resp.json()["data"]["aReceber"]
        assert receber["inadimplencia"]["total"] == "0.00"
        assert receber["vencidoComContexto"]["total"] == "0.00"


class TestClassificacaoPorContexto:
    async def test_perda_provavel_conta_como_inadimplencia_nao_como_acordo(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """R4, o caso nominal do critério de aceite."""
        title_id = await _seed_title(
            db_session, world.client, external_id="1", due_date=date(2026, 5, 1), amount="900.00"
        )
        await _login(client_with_db, world.tenant_manager)
        registro = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "perda_provavel", "text": "cliente sumiu, não paga há meses"},
        )
        assert registro.status_code == 200, registro.text

        resp = await client_with_db.get(_report_url(world.client))

        assert resp.status_code == 200, resp.text
        receber = resp.json()["data"]["aReceber"]
        assert receber["inadimplencia"]["total"] == "900.00"
        assert receber["vencidoComContexto"]["total"] == "0.00"

    async def test_acordo_de_pagamento_conta_como_vencido_com_contexto(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        title_id = await _seed_title(
            db_session, world.client, external_id="1", due_date=date(2026, 5, 1), amount="900.00"
        )
        await _login(client_with_db, world.tenant_manager)
        registro = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "acordo_de_pagamento", "text": "fechamento quadrimestral acordado"},
        )
        assert registro.status_code == 200, registro.text

        resp = await client_with_db.get(_report_url(world.client))

        assert resp.status_code == 200, resp.text
        receber = resp.json()["data"]["aReceber"]
        assert receber["inadimplencia"]["total"] == "0.00"
        assert receber["vencidoComContexto"]["total"] == "900.00"

    async def test_classificacao_usa_o_contexto_mais_recente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Acordo primeiro, depois perda — o relatório segue o MAIS RECENTE."""
        title_id = await _seed_title(
            db_session, world.client, external_id="1", due_date=date(2026, 5, 1), amount="900.00"
        )
        await _login(client_with_db, world.tenant_manager)
        primeiro = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "acordo_de_pagamento", "text": "acordo inicial"},
        )
        segundo = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "perda_provavel", "text": "cliente quebrou o acordo"},
        )
        assert primeiro.status_code == 200, primeiro.text
        assert segundo.status_code == 200, segundo.text
        # ⚠️ `created_at` vem de `now()` no banco, e a fixture roda o teste inteiro
        # numa transação só: os dois POSTs saíam com o MESMO instante e o
        # `DISTINCT ON ... ORDER BY created_at DESC` desempatava ao acaso (falhou
        # assim na validação humana de 24/09/2026). Em produção cada registro é um
        # request e uma transação próprios, então os instantes diferem. Aqui o
        # acordo é datado explicitamente ANTES, que é o cenário que o teste descreve.
        await db_session.execute(
            update(TitleContext)
            .where(TitleContext.id == UUID(primeiro.json()["data"]["id"]))
            .values(created_at=datetime(2026, 9, 24, 3, 0, tzinfo=UTC))
        )
        await db_session.flush()

        resp = await client_with_db.get(_report_url(world.client))

        receber = resp.json()["data"]["aReceber"]
        assert receber["inadimplencia"]["total"] == "900.00"
        assert receber["vencidoComContexto"]["total"] == "0.00"


class TestSeparacaoPorTipo:
    async def test_a_pagar_e_a_receber_sao_grupos_independentes(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_title(
            db_session,
            world.client,
            external_id="receber-1",
            due_date=date(2026, 5, 1),
            amount="500.00",
            title_type=TitleType.A_RECEBER,
        )
        await _seed_title(
            db_session,
            world.client,
            external_id="pagar-1",
            due_date=date(2026, 6, 1),
            amount="200.00",
            title_type=TitleType.A_PAGAR,
        )
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.get(_report_url(world.client))

        data = resp.json()["data"]
        assert data["aReceber"]["inadimplencia"]["total"] == "500.00"
        assert data["aPagar"]["inadimplencia"]["total"] == "200.00"


class TestPermissao:
    async def test_client_operator_le_o_relatorio(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Mesma permissão da carteira (`view_client_receivables`) — todos leem."""
        await _seed_title(db_session, world.client, external_id="1", due_date=date(2026, 5, 1))
        await _login(client_with_db, world.tenant_operator)

        resp = await client_with_db.get(_report_url(world.client))

        assert resp.status_code == 200, resp.text


class TestEventoRecebiveisClassificados:
    """O evento é contado em LINHAS — mesma lei do `TestEventoCarteiraSincronizada`."""

    async def test_duas_leituras_geram_duas_linhas(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        await _seed_title(
            db_session, world.client, external_id="1", due_date=date(2026, 5, 1), amount="900.00"
        )
        await _login(client_with_db, world.tenant_manager)

        primeira = await client_with_db.get(_report_url(world.client))
        segunda = await client_with_db.get(_report_url(world.client))
        assert primeira.status_code == 200, primeira.text
        assert segunda.status_code == 200, segunda.text

        eventos = await _eventos(db_session, world.client.id)
        assert len(eventos) == 2, "cada cálculo é uma linha — o evento NÃO tem dedup"

    async def test_o_payload_tem_exatamente_as_quatro_chaves_do_prd(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        title_id = await _seed_title(
            db_session, world.client, external_id="1", due_date=date(2026, 5, 1), amount="900.00"
        )
        await _login(client_with_db, world.tenant_manager)
        await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "acordo_de_pagamento", "text": "acordo"},
        )

        resp = await client_with_db.get(_report_url(world.client))
        assert resp.status_code == 200, resp.text

        eventos = await _eventos(db_session, world.client.id)
        assert len(eventos) == 1
        props = eventos[0].props
        assert set(props) == {
            "client_id",
            "valor_vencido_total_centavos",
            "valor_sem_contexto_centavos",
            "titulos_vencidos",
        }
        assert props["valor_vencido_total_centavos"] == 90_000
        assert props["valor_sem_contexto_centavos"] == 0
        assert props["titulos_vencidos"] == 1
