"""API de usuários DO CLIENTE, escopada ao tenant (Sprint 5 / R5 — BACK 05.5).

Cobre os critérios de aceite da task, com foco nos dois vetores que a revisão
adversarial apontou:

    - **IDOR**: gerente forja `user_id` de outro tenant, ou de um admin do
      sistema, na URL;
    - **escalação de privilégio**: gerente forja `role='admin'` /
      `scope='system'` / `client_id` no body.

E os básicos por endpoint: feliz, 401, 403 (papel errado), 404, isolamento de
tenant, senha curta, `password_hash` ausente de toda response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import Client, ClientAssignment, User, UserRole, UserScope
from app.modules.users.schemas import CLIENT_USER_MIN_PASSWORD_LENGTH

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@UsuariosCliente#1"
NOVA_SENHA_VALIDA = "SenhaInicial#2026"
SECRET_NAME_B = "Fulana Holding LTDA"


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
    name: str = "Usuario",
) -> User:
    user = User(
        name=name,
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


async def _seed_client(
    session: AsyncSession, *, creator: User, name: str, manager: User | None = None
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("cu-app-key", hex_key)
    ct_s, iv_s = encrypt("cu-app-secret", hex_key)
    cli = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(cli)
    await session.flush()
    if manager is not None:
        session.add(ClientAssignment(client_id=cli.id, user_id=manager.id, assigned_by=creator.id))
        await session.flush()
    return cli


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def cenario(db_session: AsyncSession) -> dict[str, Any]:
    """Tenants A e B, com gerente/operador em A e um usuário em B."""
    admin = await _seed_user(db_session, email="cu-admin@hologram.com.br", role=UserRole.ADMIN)
    mgr_sistema = await _seed_user(
        db_session, email="cu-mgr@hologram.com.br", role=UserRole.MANAGER
    )
    cli_a = await _seed_client(db_session, creator=admin, name="Austral CU", manager=mgr_sistema)
    cli_b = await _seed_client(db_session, creator=admin, name=SECRET_NAME_B)

    gerente_a = await _seed_user(
        db_session,
        email="ger@austral.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    operador_a = await _seed_user(
        db_session,
        email="op@austral.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    usuario_b = await _seed_user(
        db_session,
        email="alguem@fulana.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=cli_b.id,
        name=SECRET_NAME_B,
    )
    return {
        "admin": admin,
        "mgr_sistema": mgr_sistema,
        "cli_a": cli_a,
        "cli_b": cli_b,
        "gerente_a": gerente_a,
        "operador_a": operador_a,
        "usuario_b": usuario_b,
    }


def _base(cenario: dict[str, Any], tenant: str = "cli_a") -> str:
    return f"/api/v1/clients/{cenario[tenant].id}/users"


class TestCaminhoFeliz:
    async def test_gerente_cria_operador_no_proprio_tenant(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")

        resp = await client_with_db.post(
            _base(cenario),
            json={
                "name": "João Operador",
                "email": "joao@austral.com.br",
                "password": NOVA_SENHA_VALIDA,
                "role": UserRole.CLIENT_OPERATOR.value,
            },
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["role"] == UserRole.CLIENT_OPERATOR.value
        assert body["scope"] == UserScope.CLIENT.value
        assert body["client_id"] == str(cenario["cli_a"].id)
        assert "password_hash" not in resp.text
        assert NOVA_SENHA_VALIDA not in resp.text

        criado = (
            await db_session.execute(select(User).where(User.email == "joao@austral.com.br"))
        ).scalar_one()
        assert criado.client_id == cenario["cli_a"].id
        assert criado.scope == UserScope.CLIENT.value
        assert criado.password_hash != NOVA_SENHA_VALIDA  # hasheada

    async def test_gerente_lista_so_o_proprio_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")

        resp = await client_with_db.get(_base(cenario))

        assert resp.status_code == 200, resp.text
        emails = {u["email"] for u in resp.json()["data"]}
        assert emails == {"ger@austral.com.br", "op@austral.com.br"}
        # Nem o usuário do tenant B, nem a equipe Hologram (client_id nulo).
        assert "alguem@fulana.com.br" not in emails
        assert "cu-admin@hologram.com.br" not in emails
        assert SECRET_NAME_B not in resp.text
        assert "password_hash" not in resp.text

    async def test_admin_do_sistema_opera_em_qualquer_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "cu-admin@hologram.com.br")

        for tenant in ("cli_a", "cli_b"):
            resp = await client_with_db.get(_base(cenario, tenant))
            assert resp.status_code == 200, resp.text

    async def test_gerente_edita_usuario_do_proprio_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        alvo = cenario["operador_a"]

        resp = await client_with_db.patch(
            f"{_base(cenario)}/{alvo.id}",
            json={"name": "Operador Renomeado", "role": UserRole.CLIENT_MANAGER.value},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Operador Renomeado"
        assert resp.json()["role"] == UserRole.CLIENT_MANAGER.value

    async def test_obter_usuario_do_proprio_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        resp = await client_with_db.get(f"{_base(cenario)}/{cenario['operador_a'].id}")
        assert resp.status_code == 200, resp.text
        assert "password_hash" not in resp.text


class TestAutenticacaoEPapel:
    async def test_sem_auth_e_401(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        resp = await client_with_db.get(_base(cenario))
        assert resp.status_code == 401

    async def test_operador_do_cliente_recebe_403_em_toda_a_rota(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        """Célula ❌ da matriz: operador do cliente NÃO gere usuários."""
        await _login(client_with_db, "op@austral.com.br")
        base = _base(cenario)
        alvo = cenario["gerente_a"].id

        chamadas = [
            client_with_db.get(base),
            client_with_db.post(
                base,
                json={
                    "name": "X",
                    "email": "x@austral.com.br",
                    "password": NOVA_SENHA_VALIDA,
                    "role": UserRole.CLIENT_OPERATOR.value,
                },
            ),
            client_with_db.get(f"{base}/{alvo}"),
            client_with_db.patch(f"{base}/{alvo}", json={"name": "Y"}),
            client_with_db.post(f"{base}/{alvo}/deactivate"),
            client_with_db.post(f"{base}/{alvo}/activate"),
        ]
        for chamada in chamadas:
            resp = await chamada
            assert resp.status_code == 403, resp.text

    async def test_manager_do_sistema_recebe_403(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        """Célula ❌ da matriz: gerente do SISTEMA não gere usuários do cliente."""
        await _login(client_with_db, "cu-mgr@hologram.com.br")
        resp = await client_with_db.get(_base(cenario))
        assert resp.status_code == 403, resp.text


class TestIDOR:
    async def test_gerente_nao_alcanca_usuario_de_outro_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        """Forjar o `user_id` do outro tenant NA ROTA DO PRÓPRIO tenant → 404."""
        await _login(client_with_db, "ger@austral.com.br")
        alvo_b = cenario["usuario_b"].id
        base_a = _base(cenario)

        for resp in [
            await client_with_db.get(f"{base_a}/{alvo_b}"),
            await client_with_db.patch(f"{base_a}/{alvo_b}", json={"name": "Sequestrado"}),
            await client_with_db.post(f"{base_a}/{alvo_b}/deactivate"),
        ]:
            assert resp.status_code == 404, resp.text
            assert SECRET_NAME_B not in resp.text

    async def test_gerente_nao_alcanca_o_tenant_alheio_pela_rota(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        """Trocar o `client_id` do PATH pelo do outro tenant → 403/404, sem vazar."""
        await _login(client_with_db, "ger@austral.com.br")
        resp = await client_with_db.get(_base(cenario, "cli_b"))
        assert resp.status_code in {403, 404}, resp.text
        assert SECRET_NAME_B not in resp.text

    async def test_gerente_nao_alcanca_admin_do_sistema(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, Any]
    ) -> None:
        """Admin do sistema tem `client_id IS NULL` — o SELECT escopado não o acha."""
        await _login(client_with_db, "ger@austral.com.br")
        admin_id = cenario["admin"].id

        resp = await client_with_db.post(f"{_base(cenario)}/{admin_id}/deactivate")

        assert resp.status_code == 404, resp.text
        # E o admin continua ativo — nada foi escrito antes da checagem.
        await db_session.refresh(cenario["admin"])
        assert cenario["admin"].active is True


class TestEscalacaoDePrivilegio:
    @pytest.mark.parametrize("role", ["admin", "manager", "system", "superuser"])
    async def test_papel_forjado_e_rejeitado(
        self, client_with_db: AsyncClient, cenario: dict[str, Any], role: str
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")

        resp = await client_with_db.post(
            _base(cenario),
            json={
                "name": "Escalador",
                "email": "escalador@austral.com.br",
                "password": NOVA_SENHA_VALIDA,
                "role": role,
            },
        )

        assert resp.status_code in {400, 422}, resp.text

    async def test_client_id_no_body_e_rejeitado(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, Any]
    ) -> None:
        """`extra="forbid"`: mandar `client_id` dá 4xx em vez de ser ignorado calado."""
        await _login(client_with_db, "ger@austral.com.br")

        resp = await client_with_db.post(
            _base(cenario),
            json={
                "name": "Forjado",
                "email": "forjado@austral.com.br",
                "password": NOVA_SENHA_VALIDA,
                "role": UserRole.CLIENT_OPERATOR.value,
                "client_id": str(cenario["cli_b"].id),
                "scope": UserScope.SYSTEM.value,
            },
        )

        assert resp.status_code in {400, 422}, resp.text
        assert (
            await db_session.execute(select(User).where(User.email == "forjado@austral.com.br"))
        ).scalar_one_or_none() is None

    async def test_papel_forjado_no_patch_e_rejeitado(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        resp = await client_with_db.patch(
            f"{_base(cenario)}/{cenario['operador_a'].id}",
            json={"role": UserRole.ADMIN.value},
        )
        assert resp.status_code in {400, 422}, resp.text


class TestSenhaInicial:
    async def test_senha_curta_e_rejeitada(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        curta = "a" * (CLIENT_USER_MIN_PASSWORD_LENGTH - 1)

        resp = await client_with_db.post(
            _base(cenario),
            json={
                "name": "Senha Curta",
                "email": "curta@austral.com.br",
                "password": curta,
                "role": UserRole.CLIENT_OPERATOR.value,
            },
        )

        assert resp.status_code in {400, 422}, resp.text

    async def test_senha_no_limite_e_aceita(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        no_limite = "a" * CLIENT_USER_MIN_PASSWORD_LENGTH

        resp = await client_with_db.post(
            _base(cenario),
            json={
                "name": "Senha No Limite",
                "email": "limite@austral.com.br",
                "password": no_limite,
                "role": UserRole.CLIENT_OPERATOR.value,
            },
        )

        assert resp.status_code == 201, resp.text

    async def test_email_duplicado_devolve_409(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")

        resp = await client_with_db.post(
            _base(cenario),
            json={
                "name": "Duplicado",
                "email": "op@austral.com.br",
                "password": NOVA_SENHA_VALIDA,
                "role": UserRole.CLIENT_OPERATOR.value,
            },
        )

        assert resp.status_code == 409, resp.text


class TestRevogacao:
    async def test_desativar_revoga_no_proximo_request(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        """Com o MESMO token de antes — não relogando, que mascararia o teste."""
        rota_do_tenant = f"/api/v1/clients/{cenario['cli_a'].id}/reconciliations"

        # 1. O operador loga e navega. Guardamos o cookie EMITIDO agora.
        await _login(client_with_db, "op@austral.com.br")
        token_do_operador = client_with_db.cookies["access_token"]
        assert (await client_with_db.get(rota_do_tenant)).status_code == 200

        # 2. O gerente do mesmo tenant o desativa (troca o cookie da sessão HTTP).
        await _login(client_with_db, "ger@austral.com.br")
        desativa = await client_with_db.post(
            f"{_base(cenario)}/{cenario['operador_a'].id}/deactivate"
        )
        assert desativa.status_code == 200, desativa.text
        assert desativa.json()["active"] is False

        # 3. Restaura o token ANTIGO do operador — ainda válido e não expirado.
        #    O acesso tem de morrer mesmo assim (checagem de `active` a cada
        #    request), sem esperar a expiração natural do JWT.
        client_with_db.cookies.set("access_token", token_do_operador)
        depois = await client_with_db.get(rota_do_tenant)
        assert depois.status_code == 401

    async def test_nao_se_desativa(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        resp = await client_with_db.post(f"{_base(cenario)}/{cenario['gerente_a'].id}/deactivate")
        assert resp.status_code == 403, resp.text

    async def test_reativar_devolve_acesso(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        alvo = cenario["operador_a"].id
        assert (await client_with_db.post(f"{_base(cenario)}/{alvo}/deactivate")).status_code == 200
        reativa = await client_with_db.post(f"{_base(cenario)}/{alvo}/activate")
        assert reativa.status_code == 200, reativa.text
        assert reativa.json()["active"] is True


class TestNaoEncontrado:
    async def test_user_id_inexistente_e_404(
        self, client_with_db: AsyncClient, cenario: dict[str, Any]
    ) -> None:
        await _login(client_with_db, "ger@austral.com.br")
        resp = await client_with_db.get(f"{_base(cenario)}/{uuid4()}")
        assert resp.status_code == 404
