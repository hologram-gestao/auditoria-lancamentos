"""Contexto do título (Sprint 15 / BACK 15.1 — R1 + R2 + R3).

As DUAS permissões novas em ação, no MESMO molde da carteira (S11):

    `view_title_context`    → todos, inclusive o `client_operator`
    `manage_title_context`  → todos MENOS o `client_operator`

Também cobre: append-only (registrar de novo NÃO apaga, histórico mais recente
primeiro), `type` fora do vocabulário fechado respondendo **400
`VALIDATION_ERROR`** (nunca 422 — convenção de 23/09/2026), título de outro
cliente (404 sem vazar que existe alhures), cliente encerrado (409 no registro),
round-trip de cifra (o texto volta idêntico ao que foi enviado) e o filtro
`hasNoContext` de `GET /clients/{client_id}/titles`.

O cross-tenant e o cross-org da dupla de rotas rodam na bateria dos três
atacantes (`test_sensitive_endpoints.py`), que lê a lista canônica — as duas
entradas novas estão lá. Aqui fica o cross-tenant DENTRO da mesma organização
(título do vizinho), que é o caso que o critério de aceite pede nominalmente.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientAssignment,
    ClientTitle,
    TitleContext,
    TitleStatus,
    TitleType,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_titles.repository import ClientTitlesRepository

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Contexto#1"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: Any = None,
) -> User:
    user = User(
        name="Contexto",
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


async def _seed_client(session: AsyncSession, *, creator: User, name: str) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(f"ctx-key-{uuid4().hex[:6]}", hex_key)
    ct_secret, iv_secret = encrypt(f"ctx-secret-{uuid4().hex[:6]}", hex_key)
    client = Client(
        name=name,
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
    manager_na_carteira: User
    tenant_manager: User
    tenant_operator: User
    client: Client
    #: Segundo cliente da MESMA organização — o alvo do cross-tenant intra-org.
    vizinho: Client
    vizinho_operator: User


@pytest.fixture
async def world(db_session: AsyncSession) -> World:
    w = World()
    suffix = uuid4().hex[:8]
    w.admin = await _seed_user(
        db_session, email=f"ctx-admin-{suffix}@hologram.com.br", role=UserRole.ADMIN
    )
    w.manager_na_carteira = await _seed_user(
        db_session, email=f"ctx-mgr-{suffix}@hologram.com.br", role=UserRole.MANAGER
    )
    w.client = await _seed_client(db_session, creator=w.admin, name="Cliente do contexto")
    w.vizinho = await _seed_client(db_session, creator=w.admin, name="Cliente vizinho")
    db_session.add(
        ClientAssignment(
            client_id=w.client.id,
            user_id=w.manager_na_carteira.id,
            assigned_by=w.admin.id,
            is_primary=True,
        )
    )
    w.tenant_manager = await _seed_user(
        db_session,
        email=f"ctx-cli-mgr-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.tenant_operator = await _seed_user(
        db_session,
        email=f"ctx-cli-op-{suffix}@cliente.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.client.id,
    )
    w.vizinho_operator = await _seed_user(
        db_session,
        email=f"ctx-viz-op-{suffix}@vizinho.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=w.vizinho.id,
    )
    await db_session.flush()
    return w


async def _login(client: AsyncClient, user: User) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _titles_base(client: Client) -> str:
    return f"/api/v1/clients/{client.id}/titles"


def _context_url(client: Client, title_id: Any) -> str:
    return f"{_titles_base(client)}/{title_id}/context"


def _row(
    external_id: str,
    *,
    due_date: date,
    amount: str = "1000.00",
    title_type: TitleType = TitleType.A_RECEBER,
    status: TitleStatus = TitleStatus.EM_ABERTO,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "title_type": title_type.value,
        "due_date": due_date,
        "amount": Decimal(amount),
        "status": status.value,
        "category_code": "2.04.94",
        "supplier_code": None,
        "omie_conta_id": 2617722760,
        "document_number": "00123/A",
    }


async def _seed_title(
    db: AsyncSession, client: Client, *, external_id: str = "1", due_date: date | None = None
) -> str:
    """Grava UM título e devolve a PK dele (UUID em string)."""
    repo = ClientTitlesRepository(db)
    now = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    row = _row(external_id, due_date=due_date or date(2026, 8, 1))
    await repo.reconcile_cycle(client.id, [row], synced_at=now)
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


# ----------------------------------------------------------------------
# R1/R3 — registrar e ler (caminho feliz + round-trip de cifra)
# ----------------------------------------------------------------------


class TestRegistrarELer:
    async def test_caminho_feliz_registra_e_aparece_no_historico(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.post(
            _context_url(world.client, title_id),
            json={
                "type": "acordo_de_pagamento",
                "text": "fechamento quadrimestral acordado com o cliente",
            },
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()["data"]
        assert body["type"] == "acordo_de_pagamento"
        assert body["text"] == "fechamento quadrimestral acordado com o cliente"
        assert body["titleId"] == title_id
        assert body["decryptFailed"] is False
        assert body["author"]["name"] == world.tenant_manager.name

        historico = await client_with_db.get(_context_url(world.client, title_id))
        assert historico.status_code == 200, historico.text
        entradas = historico.json()["data"]
        assert len(entradas) == 1
        assert entradas[0]["text"] == "fechamento quadrimestral acordado com o cliente"

    async def test_texto_cifrado_no_banco_nunca_em_claro(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Round-trip da cifra: o que está no BANCO não é o texto enviado."""
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_manager)
        texto = "cliente paga adiantado 6 a 12 meses"

        resp = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "pagamento_antecipado", "text": texto},
        )
        assert resp.status_code == 200, resp.text

        row = (
            await db_session.execute(select(TitleContext).where(TitleContext.title_id == title_id))
        ).scalar_one()
        assert texto not in row.text_encrypted
        assert row.text_encrypted != texto

    async def test_registrar_de_novo_nao_apaga_o_anterior_append_only(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Histórico completo, mais recente primeiro — sem UPDATE/DELETE."""
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_manager)

        primeiro = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "acordo_de_pagamento", "text": "primeiro registro"},
        )
        segundo = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "perda_provavel", "text": "segundo registro, mudou de ideia"},
        )
        assert primeiro.status_code == 200, primeiro.text
        assert segundo.status_code == 200, segundo.text

        historico = await client_with_db.get(_context_url(world.client, title_id))
        entradas = historico.json()["data"]
        assert len(entradas) == 2
        # Mais recente primeiro.
        assert entradas[0]["text"] == "segundo registro, mudou de ideia"
        assert entradas[1]["text"] == "primeiro registro"

    async def test_registrar_contexto_nao_altera_nenhum_campo_do_titulo(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O contexto é leitura sobre o título — nunca escrita nele (guardrail do PRD)."""
        title_id = await _seed_title(db_session, world.client, due_date=date(2026, 5, 1))
        await _login(client_with_db, world.tenant_manager)

        before = await client_with_db.get(_titles_base(world.client))
        antes = before.json()["data"][0]

        resp = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "acordo_de_pagamento", "text": "não muda o título"},
        )
        assert resp.status_code == 200, resp.text

        after = await client_with_db.get(_titles_base(world.client))
        depois = after.json()["data"][0]
        assert depois["amount"] == antes["amount"]
        assert depois["dueDate"] == antes["dueDate"]
        assert depois["status"] == antes["status"]


# ----------------------------------------------------------------------
# Validação de forma — 400 genérico, nunca 422 inventado
# ----------------------------------------------------------------------


class TestValidacao:
    async def test_tipo_fora_do_vocabulario_e_400_generico(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "tipo_inventado", "text": "x"},
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_texto_vazio_e_400_generico(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "outro", "text": ""},
        )

        assert resp.status_code == 400, resp.text


# ----------------------------------------------------------------------
# R5 — as duas permissões
# ----------------------------------------------------------------------


class TestPermissoes:
    @pytest.mark.parametrize(
        "quem",
        ["admin", "manager_na_carteira", "tenant_manager", "tenant_operator"],
        ids=["admin", "manager-carteira", "client_manager", "client_operator"],
    )
    async def test_todos_esses_papeis_leem_o_historico(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World, quem: str
    ) -> None:
        """`view_title_context` é ✅ nos cinco papéis (a plataforma inclusa)."""
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_manager)
        seed = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "outro", "text": "contexto plantado"},
        )
        assert seed.status_code == 200, seed.text

        await _login(client_with_db, getattr(world, quem))
        resp = await client_with_db.get(_context_url(world.client, title_id))

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["data"]) == 1

    async def test_o_par_de_permissoes_nao_foi_reusado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """O teste nominal do par: `client_operator` LÊ 200 e registra 403."""
        title_id = await _seed_title(db_session, world.client)
        await _login(client_with_db, world.tenant_operator)

        leitura = await client_with_db.get(_context_url(world.client, title_id))
        escrita = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "outro", "text": "não deveria gravar"},
        )

        assert leitura.status_code == 200, leitura.text
        assert escrita.status_code == 403, escrita.text


# ----------------------------------------------------------------------
# Isolamento — título de outro cliente (mesma organização)
# ----------------------------------------------------------------------


class TestIsolamento:
    async def test_titulo_do_vizinho_e_404_sem_vazar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        """Título de OUTRO cliente é 404 — mesma organização, tenant diferente."""
        titulo_vizinho = await _seed_title(db_session, world.vizinho, external_id="viz-1")
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.post(
            _context_url(world.client, titulo_vizinho),
            json={"type": "outro", "text": "tentando registrar no título alheio"},
        )

        assert resp.status_code == 404, resp.text
        assert world.vizinho.name not in resp.text

    async def test_titulo_inexistente_e_404(
        self, client_with_db: AsyncClient, world: World
    ) -> None:
        await _login(client_with_db, world.tenant_manager)

        resp = await client_with_db.get(_context_url(world.client, uuid4()))

        assert resp.status_code == 404, resp.text


# ----------------------------------------------------------------------
# §4.12 — cliente encerrado
# ----------------------------------------------------------------------


class TestClienteEncerrado:
    async def test_registrar_409_e_ler_200(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        title_id = await _seed_title(db_session, world.client)
        await db_session.execute(
            update(Client)
            .where(Client.id == world.client.id)
            .values(closed_at=datetime(2026, 9, 1, tzinfo=UTC))
        )
        await db_session.flush()
        await _login(client_with_db, world.admin)

        leitura = await client_with_db.get(_context_url(world.client, title_id))
        escrita = await client_with_db.post(
            _context_url(world.client, title_id),
            json={"type": "outro", "text": "cliente encerrado"},
        )

        assert leitura.status_code == 200, leitura.text
        assert escrita.status_code == 409, escrita.text


# ----------------------------------------------------------------------
# R2 — filtro "vencidos sem contexto" no GET /titles
# ----------------------------------------------------------------------


class TestFiltroSemContexto:
    async def test_filtro_has_no_context_exclui_titulo_com_registro(
        self, client_with_db: AsyncClient, db_session: AsyncSession, world: World
    ) -> None:
        com_contexto = await _seed_title(db_session, world.client, external_id="com-ctx")
        await _seed_title(db_session, world.client, external_id="sem-ctx")
        await _login(client_with_db, world.tenant_manager)

        seed = await client_with_db.post(
            _context_url(world.client, com_contexto),
            json={"type": "acordo_de_pagamento", "text": "tem contexto"},
        )
        assert seed.status_code == 200, seed.text

        resp = await client_with_db.get(_titles_base(world.client), params={"hasNoContext": "true"})

        assert resp.status_code == 200, resp.text
        ids = {row["externalId"] for row in resp.json()["data"]}
        assert "sem-ctx" in ids
        assert "com-ctx" not in ids
