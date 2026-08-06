"""Integração — CRUD do glossário no tenant (Sprint 6, BACK 06.3).

Cobre os critérios de aceite da task:

    - `client_manager` do tenant cria, edita e remove no PRÓPRIO glossário;
      `client_operator` LÊ mas leva 403 em qualquer escrita — teste por papel
      para cada verbo.
    - A decisão de permissão vem da `PERMISSION_MATRIX` (`manage_glossary`) e o
      tenant de `resolve_client_access`; nenhuma rota compara `role`/`client_id`.
    - Cross-tenant com body VÁLIDO: usuário do tenant A não lê nem edita o
      glossário do tenant B, nem forjando `client_id` — e a negação **não vaza**
      nome/razão social do alvo, gravando exatamente 1 linha em `access_audit`.
    - Validação de servidor: tipo, tamanho por campo e teto de entradas.
    - Toda escrita (incl. remoção) bump a versão e emite `glossario_editado`
      sem PII; falha do emissor **não** derruba a escrita (fail-soft).
    - Paginação `?page&pageSize` (máx 100) com envelope `{data, pagination}`.

O caso negativo cross-tenant parametrizado da lista canônica está em
`test_sensitive_endpoints.py` — aqui ficam as asserções específicas (corpo sem
PII do alvo, contagem de `access_audit`, papel x verbo).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PERMISSION_MATRIX, Permission
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    MAX_DESCRIPTION_CHARS,
    MAX_ENTRIES_PER_CLIENT,
    MAX_NAME_CHARS,
    AccessAudit,
    Client,
    ClientAssignment,
    ClientGlossaryEntry,
    GlossaryEntryKind,
    UsageEvent,
    User,
    UserRole,
    UserScope,
)
from app.modules.glossary.repository import ClientGlossaryRepository
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import UsageEventName

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
SECRET_NAME_B = "Fulana Participacoes LTDA"
REGRA = "IOF nunca e classificado como juros."


def _entry_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": "regra", "name": REGRA}
    body.update(overrides)
    return body


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: UUID | None = None,
) -> User:
    user = User(
        name="Glossario",
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


async def _seed_client(session: AsyncSession, *, name: str, creator: User) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("k", hex_key)
    ct_secret, iv_secret = encrypt("s", hex_key)
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


@pytest.fixture
async def scene(db_session: AsyncSession) -> dict[str, Any]:
    """Dois tenants, com gerente e operador em cada lado do tenant A."""
    admin = await _seed_user(db_session, email="gl-admin@hologram.com.br", role=UserRole.ADMIN)
    cli_a = await _seed_client(db_session, name="Austral Glossario", creator=admin)
    cli_b = await _seed_client(db_session, name=SECRET_NAME_B, creator=admin)
    gerente_a = await _seed_user(
        db_session,
        email="gerente-a@austral.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    operador_a = await _seed_user(
        db_session,
        email="operador-a@austral.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    return {
        "admin": admin,
        "cli_a": cli_a,
        "cli_b": cli_b,
        "gerente_a": gerente_a,
        "operador_a": operador_a,
    }


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _url(client_id: UUID, entry_id: UUID | None = None) -> str:
    base = f"/api/v1/clients/{client_id}/glossary"
    return base if entry_id is None else f"{base}/{entry_id}"


async def _create(http: AsyncClient, client_id: UUID, **overrides: Any) -> dict[str, Any]:
    resp = await http.post(_url(client_id), json=_entry_body(**overrides))
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


# ----------------------------------------------------------------------
# Matriz de permissões: papel x verbo
# ----------------------------------------------------------------------


class TestMatrizDePermissoes:
    def test_permissao_nova_esta_na_matriz_declarativa(self) -> None:
        """A decisão vem de `PERMISSION_MATRIX`, não de `if role ==` na rota."""
        assert PERMISSION_MATRIX[Permission.MANAGE_GLOSSARY] == frozenset(
            {UserRole.ADMIN, UserRole.MANAGER, UserRole.CLIENT_MANAGER}
        )
        assert UserRole.CLIENT_OPERATOR not in PERMISSION_MATRIX[Permission.MANAGE_GLOSSARY]

    async def test_gerente_do_cliente_cria_edita_e_remove(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gerente-a@austral.com.br")

        created = await _create(
            client_with_db, cli_a.id, kind="categoria", code="3.1.02", name="Taxas bancarias"
        )
        assert created["kind"] == "categoria"
        assert created["code"] == "3.1.02"

        entry_id = UUID(created["id"])
        edited = await client_with_db.patch(
            _url(cli_a.id, entry_id),
            json=_entry_body(kind="categoria", name="Taxas e tarifas", code="3.1.02"),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["data"]["name"] == "Taxas e tarifas"

        removed = await client_with_db.delete(_url(cli_a.id, entry_id))
        assert removed.status_code == 200, removed.text
        assert removed.json()["data"]["deleted"] is True

        listed = await client_with_db.get(_url(cli_a.id))
        assert listed.json()["data"]["entries"] == []

    async def test_operador_le_mas_nao_escreve(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        """Operador precisa do glossário como REFERÊNCIA — leitura liberada."""
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gerente-a@austral.com.br")
        created = await _create(client_with_db, cli_a.id)
        entry_id = UUID(created["id"])

        await _login(client_with_db, "operador-a@austral.com.br")

        leitura = await client_with_db.get(_url(cli_a.id))
        assert leitura.status_code == 200, leitura.text
        assert leitura.json()["data"]["entries"][0]["name"] == REGRA

        for method, url, body in (
            ("POST", _url(cli_a.id), _entry_body()),
            ("PATCH", _url(cli_a.id, entry_id), _entry_body()),
            ("DELETE", _url(cli_a.id, entry_id), None),
        ):
            resp = await client_with_db.request(method, url, json=body)
            assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}: {resp.text}"

    async def test_admin_do_sistema_escreve_no_tenant(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gl-admin@hologram.com.br")

        created = await _create(client_with_db, cli_a.id)

        assert created["name"] == REGRA

    async def test_manager_fora_da_carteira_nao_escreve(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Matriz libera o papel; a CARTEIRA é `resolve_client_access`."""
        cli_a: Client = scene["cli_a"]
        cli_b: Client = scene["cli_b"]
        admin: User = scene["admin"]
        manager = await _seed_user(
            db_session, email="mgr-carteira@hologram.com.br", role=UserRole.MANAGER
        )
        db_session.add(
            ClientAssignment(client_id=cli_a.id, user_id=manager.id, assigned_by=admin.id)
        )
        await db_session.flush()
        await _login(client_with_db, "mgr-carteira@hologram.com.br")

        dentro = await client_with_db.post(_url(cli_a.id), json=_entry_body())
        fora = await client_with_db.post(_url(cli_b.id), json=_entry_body())

        assert dentro.status_code == 201, dentro.text
        assert fora.status_code == 403, fora.text


# ----------------------------------------------------------------------
# Isolamento cross-tenant
# ----------------------------------------------------------------------


class TestCrossTenant:
    async def test_gerente_de_a_nao_toca_no_glossario_de_b(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Body VÁLIDO: um 400 de validação passaria sem tocar a autorização."""
        cli_b: Client = scene["cli_b"]
        await _login(client_with_db, "gerente-a@austral.com.br")
        antes = await _count_denied(db_session)

        resp = await client_with_db.post(_url(cli_b.id), json=_entry_body())

        assert resp.status_code == 403, resp.text
        # A negação não vaza nome/razão social do alvo.
        assert SECRET_NAME_B not in resp.text
        assert await _count_denied(db_session) == antes + 1

    async def test_leitura_do_outro_tenant_tambem_e_negada(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        cli_b: Client = scene["cli_b"]
        await _login(client_with_db, "operador-a@austral.com.br")

        resp = await client_with_db.get(_url(cli_b.id))

        assert resp.status_code in {403, 404}, resp.text
        assert SECRET_NAME_B not in resp.text

    async def test_entrada_de_outro_tenant_por_pk_devolve_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Anti-IDOR: o `entry_id` existe, mas em OUTRO tenant."""
        cli_a: Client = scene["cli_a"]
        cli_b: Client = scene["cli_b"]
        admin: User = scene["admin"]
        # O admin cria a entrada NO tenant B.
        await _login(client_with_db, "gl-admin@hologram.com.br")
        do_b = await _create(client_with_db, cli_b.id)
        entry_b = UUID(do_b["id"])
        del admin

        # O gerente de A tenta editá-la passando o PRÓPRIO client_id no path.
        await _login(client_with_db, "gerente-a@austral.com.br")
        resp = await client_with_db.patch(_url(cli_a.id, entry_b), json=_entry_body())

        assert resp.status_code == 404, resp.text
        assert SECRET_NAME_B not in resp.text
        # A entrada de B continua intacta.
        row = await db_session.scalar(
            select(ClientGlossaryEntry).where(ClientGlossaryEntry.id == entry_b)
        )
        assert row is not None
        assert row.deleted_at is None


async def _count_denied(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count(AccessAudit.id)).where(AccessAudit.action == "denied")
        )
        or 0
    )


# ----------------------------------------------------------------------
# Validação de entrada (servidor)
# ----------------------------------------------------------------------


class TestValidacaoDeServidor:
    @pytest.mark.parametrize(
        ("body", "motivo"),
        [
            pytest.param({"kind": "inventado", "name": "x"}, "kind fora do enum", id="kind"),
            pytest.param({"kind": "regra"}, "name ausente", id="sem-name"),
            pytest.param({"kind": "regra", "name": "   "}, "name só espaços", id="name-vazio"),
            pytest.param(
                {"kind": "regra", "name": "a" * (MAX_NAME_CHARS + 1)},
                "name acima do teto",
                id="name-grande",
            ),
            pytest.param(
                {"kind": "regra", "name": "ok", "code": "c" * 41},
                "code acima do teto",
                id="code-grande",
            ),
            pytest.param(
                {
                    "kind": "regra",
                    "name": "ok",
                    "description": "d" * (MAX_DESCRIPTION_CHARS + 1),
                },
                "description acima do teto",
                id="description-grande",
            ),
            pytest.param(
                {"kind": "regra", "name": "ok", "client_id": str(uuid4())},
                "campo desconhecido (tenant nunca vem do body)",
                id="extra-forbid",
            ),
        ],
    )
    async def test_corpo_invalido_e_recusado(
        self,
        client_with_db: AsyncClient,
        scene: dict[str, Any],
        body: dict[str, Any],
        motivo: str,
    ) -> None:
        """400 VALIDATION_ERROR (o handler global converte o 422 do Pydantic)."""
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gerente-a@austral.com.br")

        resp = await client_with_db.post(_url(cli_a.id), json=body)

        assert resp.status_code == 400, f"{motivo}: {resp.status_code} {resp.text}"
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_teto_de_entradas_por_cliente(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """O teto é o que impede o bloco de prompt da 06.4 de crescer sem fim."""
        cli_a: Client = scene["cli_a"]
        repo = ClientGlossaryRepository(db_session)
        # Enche até o teto direto no banco (mais rápido que 200 requests).
        for _ in range(MAX_ENTRIES_PER_CLIENT):
            db_session.add(
                ClientGlossaryEntry(
                    client_id=cli_a.id,
                    kind=GlossaryEntryKind.REGRA.value,
                    name_encrypted="v1:k1:abcd",
                    name_iv="0" * 24,
                )
            )
        await db_session.flush()
        assert await repo.count_active(client_id=cli_a.id) == MAX_ENTRIES_PER_CLIENT
        await _login(client_with_db, "gerente-a@austral.com.br")

        resp = await client_with_db.post(_url(cli_a.id), json=_entry_body())

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "GLOSSARY_LIMIT_EXCEEDED"
        assert "limite" in resp.json()["error"]["userMessage"].lower()

    @pytest.mark.parametrize("page_size", [0, 101], ids=["zero", "acima-do-maximo"])
    async def test_page_size_fora_do_intervalo(
        self, client_with_db: AsyncClient, scene: dict[str, Any], page_size: int
    ) -> None:
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gerente-a@austral.com.br")

        resp = await client_with_db.get(_url(cli_a.id), params={"pageSize": page_size})

        assert resp.status_code == 400, resp.text


# ----------------------------------------------------------------------
# Versão + evento de outcome
# ----------------------------------------------------------------------


class TestVersaoEEvento:
    async def test_toda_escrita_bump_a_versao_e_emite_o_evento(
        self, client_with_db: AsyncClient, db_session: AsyncSession, scene: dict[str, Any]
    ) -> None:
        """Criar, editar e REMOVER — os três mexem na versão e emitem evento."""
        cli_a: Client = scene["cli_a"]
        repo = ClientGlossaryRepository(db_session)
        await _login(client_with_db, "gerente-a@austral.com.br")

        created = await _create(client_with_db, cli_a.id)
        entry_id = UUID(created["id"])
        v_create = await repo.get_version(client_id=cli_a.id)

        await client_with_db.patch(_url(cli_a.id, entry_id), json=_entry_body(name="Outra"))
        v_edit = await repo.get_version(client_id=cli_a.id)

        deleted = await client_with_db.delete(_url(cli_a.id, entry_id))
        v_delete = await repo.get_version(client_id=cli_a.id)

        assert (v_create, v_edit, v_delete) == (1, 2, 3)
        assert deleted.json()["data"]["version"] == 3

        eventos = await _glossario_editado(db_session)
        assert len(eventos) == 3
        # `n_categorias` é o total ATIVO depois da escrita: 1 → 1 → 0.
        assert [e["n_categorias"] for e in eventos] == [1, 1, 0]
        # Sem PII: só o tenant e um contador.
        for props in eventos:
            assert set(props) == {"client_id", "n_categorias"}
            assert props["client_id"] == str(cli_a.id)
            assert REGRA not in str(props)

    async def test_versao_na_listagem_acompanha_as_escritas(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gerente-a@austral.com.br")

        antes = await client_with_db.get(_url(cli_a.id))
        await _create(client_with_db, cli_a.id)
        depois = await client_with_db.get(_url(cli_a.id))

        assert antes.json()["data"]["version"] == 0
        assert depois.json()["data"]["version"] == 1

    async def test_falha_do_emissor_nao_derruba_a_escrita(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        scene: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fail-soft: instrumentação quebrada não impede o usuário de salvar."""
        cli_a: Client = scene["cli_a"]

        async def _boom(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("sink fora do ar")

        monkeypatch.setattr(UsageEventRepository, "insert_many_ignore_duplicate", _boom)
        await _login(client_with_db, "gerente-a@austral.com.br")

        resp = await client_with_db.post(_url(cli_a.id), json=_entry_body())

        assert resp.status_code == 201, resp.text
        assert await _count_entries(db_session, cli_a.id) == 1
        assert await _glossario_editado(db_session) == []


async def _glossario_editado(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(UsageEvent.props)
        .where(UsageEvent.event == UsageEventName.GLOSSARIO_EDITADO.value)
        .order_by(UsageEvent.created_at)
    )
    return [dict(p) for p in rows.scalars().all()]


async def _count_entries(session: AsyncSession, client_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count(ClientGlossaryEntry.id)).where(
                ClientGlossaryEntry.client_id == client_id,
                ClientGlossaryEntry.deleted_at.is_(None),
            )
        )
        or 0
    )


# ----------------------------------------------------------------------
# Paginação / envelope
# ----------------------------------------------------------------------


class TestPaginacao:
    async def test_envelope_e_pagina_conforme_a_secao_7(
        self, client_with_db: AsyncClient, scene: dict[str, Any]
    ) -> None:
        cli_a: Client = scene["cli_a"]
        await _login(client_with_db, "gerente-a@austral.com.br")
        for i in range(3):
            await _create(client_with_db, cli_a.id, name=f"Regra {i}")

        resp = await client_with_db.get(_url(cli_a.id), params={"page": 2, "pageSize": 2})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"data", "pagination"}
        assert body["pagination"] == {
            "page": 2,
            "pageSize": 2,
            "total": 3,
            "totalPages": 2,
        }
        assert len(body["data"]["entries"]) == 1
