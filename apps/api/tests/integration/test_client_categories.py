"""Testes de integração do catálogo de categorias de cliente — 86e34jd8m.

Cobre:
    - RBAC: sem login 401; manager lê (200) mas não escreve (403); admin escreve.
    - CRUD: criar, nome único sem caixa (409), listar ordenado por nome com
      `clients_count`, renomear/tom (PATCH), renomear para nome existente (409),
      tom inválido (400 — o handler global converte validação em VALIDATION_ERROR),
      404 em id desconhecido.
    - DELETE: 204 órfã; 409 em uso (clientes continuam classificados).
    - Encaixe no cliente: POST /clients com `category_id` (e 400 se inexistente),
      PATCH tri-estado (omitido mantém, null limpa, UUID troca), `category` na
      response, e `GET /clients?category_id=` filtrando com total correto.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Client, ClientAssignment, ClientCategory, User, UserRole

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"
ADMIN_EMAIL = "admin-cat@hologram.com.br"
MANAGER_EMAIL = "manager-cat@hologram.com.br"

BASE = "/api/v1/client-categories"


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Test User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_category(
    session: AsyncSession, *, name: str, tone: str = "neutral"
) -> ClientCategory:
    category = ClientCategory(name=name, tone=tone)
    session.add(category)
    await session.flush()
    return category


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    category: ClientCategory | None = None,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
        category_id=category.id if category is not None else None,
    )
    session.add(client)
    await session.flush()
    session.add(
        ClientAssignment(
            client_id=client.id, user_id=creator.id, assigned_by=creator.id, is_primary=True
        )
    )
    await session.flush()
    return client


async def _login_as(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _login_admin(client: AsyncClient, session: AsyncSession) -> User:
    admin = await _seed_user(session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
    await _login_as(client, ADMIN_EMAIL)
    return admin


# ----------------------------------------------------------------------
# RBAC
# ----------------------------------------------------------------------


class TestClientCategoriesRBAC:
    async def test_sem_login_401(self, client_with_db: AsyncClient) -> None:
        assert (await client_with_db.get(BASE)).status_code == 401
        assert (await client_with_db.post(BASE, json={"name": "X"})).status_code == 401

    async def test_manager_le_mas_nao_escreve(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=MANAGER_EMAIL, role=UserRole.MANAGER)
        cat = await _seed_category(db_session, name="Fintech")
        await _login_as(client_with_db, MANAGER_EMAIL)

        lista = await client_with_db.get(BASE)
        assert lista.status_code == 200, lista.text
        assert [c["name"] for c in lista.json()["data"]] == ["Fintech"]

        assert (await client_with_db.post(BASE, json={"name": "Nova"})).status_code == 403
        assert (
            await client_with_db.patch(f"{BASE}/{cat.id}", json={"name": "Outra"})
        ).status_code == 403
        assert (await client_with_db.delete(f"{BASE}/{cat.id}")).status_code == 403


# ----------------------------------------------------------------------
# CRUD do catálogo
# ----------------------------------------------------------------------


class TestClientCategoriesCrud:
    async def test_cria_lista_ordenado_e_conta_clientes(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _login_admin(client_with_db, db_session)

        criada = await client_with_db.post(BASE, json={"name": "  Varejo  ", "tone": "success"})
        assert criada.status_code == 201, criada.text
        body = criada.json()
        assert body["name"] == "Varejo"  # normalizado
        assert body["tone"] == "success"
        assert body["clients_count"] == 0

        fintech = await _seed_category(db_session, name="fintech", tone="info")
        await _seed_client(db_session, name="A", creator=admin, category=fintech)
        await _seed_client(db_session, name="B", creator=admin, category=fintech)

        lista = await client_with_db.get(BASE)
        assert lista.status_code == 200, lista.text
        rows = lista.json()["data"]
        # Ordem por nome sem caixa: "fintech" antes de "Varejo".
        assert [r["name"] for r in rows] == ["fintech", "Varejo"]
        assert {r["name"]: r["clients_count"] for r in rows} == {"fintech": 2, "Varejo": 0}

    async def test_nome_e_unico_sem_caixa(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        await _seed_category(db_session, name="Fintech")
        dup = await client_with_db.post(BASE, json={"name": "fintech"})
        assert dup.status_code == 409, dup.text
        assert dup.json()["error"]["code"] == "CONFLICT"

    async def test_tom_invalido_e_nome_vazio_sao_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        assert (
            await client_with_db.post(BASE, json={"name": "X", "tone": "rosa"})
        ).status_code == 400
        assert (await client_with_db.post(BASE, json={"name": "   "})).status_code == 400

    async def test_patch_renomeia_muda_tom_e_recusa_nome_de_outra(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        cat = await _seed_category(db_session, name="Varejo")
        outra = await _seed_category(db_session, name="Fintech")
        del outra

        ok = await client_with_db.patch(
            f"{BASE}/{cat.id}", json={"name": "Comércio", "tone": "warning"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["name"] == "Comércio"
        assert ok.json()["tone"] == "warning"

        # Só o tom: nome fica.
        so_tom = await client_with_db.patch(f"{BASE}/{cat.id}", json={"tone": "primary"})
        assert so_tom.json()["name"] == "Comércio"
        assert so_tom.json()["tone"] == "primary"

        colisao = await client_with_db.patch(f"{BASE}/{cat.id}", json={"name": "FINTECH"})
        assert colisao.status_code == 409, colisao.text

        # Renomear para o PRÓPRIO nome com outra caixa é permitido.
        mesma = await client_with_db.patch(f"{BASE}/{cat.id}", json={"name": "comércio"})
        assert mesma.status_code == 200, mesma.text

        assert (
            await client_with_db.patch(f"{BASE}/{uuid4()}", json={"name": "X"})
        ).status_code == 404

    async def test_delete_orfa_204_e_em_uso_409(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _login_admin(client_with_db, db_session)
        orfa = await _seed_category(db_session, name="Órfã")
        em_uso = await _seed_category(db_session, name="Em uso")
        cliente = await _seed_client(db_session, name="C", creator=admin, category=em_uso)

        assert (await client_with_db.delete(f"{BASE}/{orfa.id}")).status_code == 204
        assert (
            await db_session.execute(select(ClientCategory).where(ClientCategory.id == orfa.id))
        ).scalar_one_or_none() is None

        bloqueado = await client_with_db.delete(f"{BASE}/{em_uso.id}")
        assert bloqueado.status_code == 409, bloqueado.text
        assert "1 cliente" in bloqueado.json()["error"]["userMessage"]
        # O cliente continua classificado — nada foi limpo em silêncio.
        await db_session.refresh(cliente)
        assert cliente.category_id == em_uso.id

        assert (await client_with_db.delete(f"{BASE}/{uuid4()}")).status_code == 404


# ----------------------------------------------------------------------
# Encaixe no cliente: criar, editar (tri-estado), response e filtro
# ----------------------------------------------------------------------


class TestClientCategoryOnClients:
    async def test_post_cliente_com_categoria_e_400_se_inexistente(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _login_admin(client_with_db, db_session)
        cat = await _seed_category(db_session, name="Fintech", tone="info")

        # S9: cliente nasce SEM origem — a categoria não depende de credencial.
        criado = await client_with_db.post(
            "/api/v1/clients",
            json={"name": "Novo", "category_id": str(cat.id)},
        )
        assert criado.status_code == 201, criado.text
        assert criado.json()["category"] == {"id": str(cat.id), "name": "Fintech", "tone": "info"}

        invalido = await client_with_db.post(
            "/api/v1/clients",
            json={"name": "Outro", "category_id": str(uuid4())},
        )
        assert invalido.status_code == 400, invalido.text
        assert invalido.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_patch_tri_estado(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _login_admin(client_with_db, db_session)
        fintech = await _seed_category(db_session, name="Fintech")
        varejo = await _seed_category(db_session, name="Varejo")
        cliente = await _seed_client(db_session, name="C", creator=admin, category=fintech)
        url = f"/api/v1/clients/{cliente.id}"

        # Omitido: mantém.
        mantem = await client_with_db.patch(url, json={"name": "C renomeado"})
        assert mantem.status_code == 200, mantem.text
        assert mantem.json()["category"]["id"] == str(fintech.id)

        # UUID: troca.
        troca = await client_with_db.patch(url, json={"category_id": str(varejo.id)})
        assert troca.json()["category"]["name"] == "Varejo"

        # null explícito: limpa.
        limpa = await client_with_db.patch(url, json={"category_id": None})
        assert limpa.status_code == 200, limpa.text
        assert limpa.json()["category"] is None

        # Inexistente: 400 e nada muda.
        ruim = await client_with_db.patch(url, json={"category_id": str(uuid4())})
        assert ruim.status_code == 400, ruim.text

    async def test_lista_filtra_por_categoria_com_total_certo(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _login_admin(client_with_db, db_session)
        fintech = await _seed_category(db_session, name="Fintech")
        varejo = await _seed_category(db_session, name="Varejo")
        await _seed_client(db_session, name="F1", creator=admin, category=fintech)
        await _seed_client(db_session, name="F2", creator=admin, category=fintech)
        await _seed_client(db_session, name="V1", creator=admin, category=varejo)
        await _seed_client(db_session, name="Sem", creator=admin)

        tudo = await client_with_db.get("/api/v1/clients")
        assert tudo.json()["pagination"]["total"] == 4
        nomes = {c["name"]: c["category"] for c in tudo.json()["data"]}
        assert nomes["Sem"] is None
        assert nomes["V1"]["name"] == "Varejo"

        so_fintech = await client_with_db.get(
            "/api/v1/clients", params={"category_id": str(fintech.id)}
        )
        assert so_fintech.status_code == 200, so_fintech.text
        assert sorted(c["name"] for c in so_fintech.json()["data"]) == ["F1", "F2"]
        assert so_fintech.json()["pagination"]["total"] == 2

        # Filtro + busca combinam com E.
        combinado = await client_with_db.get(
            "/api/v1/clients", params={"category_id": str(fintech.id), "search": "F2"}
        )
        assert [c["name"] for c in combinado.json()["data"]] == ["F2"]

        assert (
            await client_with_db.get("/api/v1/clients", params={"category_id": "nao-e-uuid"})
        ).status_code == 400
