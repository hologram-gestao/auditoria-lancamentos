"""Isolamento por tenant ponta a ponta (Sprint 5 / R2 + R4 — BACK 05.3).

O que a sprint mede: **nenhum usuário de um tenant alcança dado de outro**.
Aqui isso é verificado nas ROTAS: usuário `scope='client'` só toca o próprio
`client_id`, cross-tenant devolve 403/404 sem vazar nada do alvo e grava
exatamente 1 linha `denied`, o `system` não sofre regressão, e as células `❌`
da matriz bloqueiam de verdade.

Também cobre os dois guardrails do PRD:
    - **revogação imediata**: mudar o tenant na LINHA vale já no próximo request
      com o token ANTIGO (o token não é a última palavra);
    - **sem query extra**: autenticar não passou a custar um SELECT a mais.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AccessAudit,
    Client,
    ClientAssignment,
    ReconciliationSession,
    User,
    UserRole,
    UserScope,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@IsolamentoS5#1"
SECRET_NAME_B = "Fulana Distribuidora LTDA"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Isolamento",
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
    session: AsyncSession, *, creator: User, manager: User | None, name: str
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt("iso-app-key", hex_key)
    ct_s, iv_s = encrypt("iso-app-secret", hex_key)
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


async def _seed_session(
    session: AsyncSession, *, client: Client, creator: User
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=_hex64(f"iso-{uuid4().hex}"),
        status="reviewing",
        balance_start=Decimal("0.00"),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _login(client: AsyncClient, email: str) -> dict[str, object]:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["user"])


async def _denied_rows(db: AsyncSession) -> list[AccessAudit]:
    return list(
        (await db.execute(select(AccessAudit).where(AccessAudit.action == "denied")))
        .scalars()
        .all()
    )


@pytest.fixture
async def cenario(db_session: AsyncSession) -> dict[str, object]:
    """Dois tenants (A e B), um operador e um gerente em A, e a equipe do sistema."""
    admin = await _seed_user(db_session, email="iso-admin@hologram.com.br", role=UserRole.ADMIN)
    mgr_sistema = await _seed_user(
        db_session, email="iso-mgr@hologram.com.br", role=UserRole.MANAGER
    )
    cli_a = await _seed_client(
        db_session, creator=admin, manager=mgr_sistema, name="Austral Serviços"
    )
    cli_b = await _seed_client(db_session, creator=admin, manager=None, name=SECRET_NAME_B)
    operador_a = await _seed_user(
        db_session,
        email="op-a@austral.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    gerente_a = await _seed_user(
        db_session,
        email="ger-a@austral.com.br",
        role=UserRole.CLIENT_MANAGER,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    sess_b = await _seed_session(db_session, client=cli_b, creator=admin)
    return {
        "admin": admin,
        "mgr_sistema": mgr_sistema,
        "cli_a": cli_a,
        "cli_b": cli_b,
        "operador_a": operador_a,
        "gerente_a": gerente_a,
        "sess_b": sess_b,
    }


class TestSessaoExpoeEscopo:
    async def test_login_devolve_scope_role_e_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        """O front faz o gating a partir daqui (contrato é fonte única)."""
        payload = await _login(client_with_db, "op-a@austral.com.br")
        assert payload["scope"] == UserScope.CLIENT.value
        assert payload["role"] == UserRole.CLIENT_OPERATOR.value
        assert payload["client_id"] == str(cenario["cli_a"].id)  # type: ignore[union-attr]
        assert "password_hash" not in payload

    async def test_usuario_system_vem_sem_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        payload = await _login(client_with_db, "iso-mgr@hologram.com.br")
        assert payload["scope"] == UserScope.SYSTEM.value
        assert payload["client_id"] is None


class TestAcessoAoProprioTenant:
    async def test_operador_acessa_o_proprio_cliente(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "op-a@austral.com.br")
        cli_a = cenario["cli_a"]
        resp = await client_with_db.get(f"/api/v1/clients/{cli_a.id}/reconciliations")  # type: ignore[union-attr]
        assert resp.status_code == 200, resp.text

    async def test_operador_pode_rodar_conciliacao_no_proprio_tenant(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        """Matriz §4: criar/rodar conciliação é ✅ para operador do cliente."""
        await _login(client_with_db, "op-a@austral.com.br")
        cli_a = cenario["cli_a"]
        resp = await client_with_db.get(
            "/api/v1/reconciliations/check-duplicate",
            params={
                "client_id": str(cli_a.id),  # type: ignore[union-attr]
                "omie_conta_id": 42,
                "month": "2026-04",
                "hash": _hex64("qualquer"),
            },
        )
        assert resp.status_code == 200, resp.text


class TestCrossTenant:
    async def test_operador_nao_acessa_outro_tenant_e_grava_uma_linha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "op-a@austral.com.br")
        cli_b = cenario["cli_b"]

        resp = await client_with_db.get(f"/api/v1/clients/{cli_b.id}/reconciliations")  # type: ignore[union-attr]

        assert resp.status_code in {403, 404}
        # ZERO dado do alvo no corpo.
        assert SECRET_NAME_B not in resp.text

        denied = await _denied_rows(db_session)
        assert len(denied) == 1
        assert denied[0].user_scope == UserScope.CLIENT.value
        assert denied[0].actor_client_id == cenario["cli_a"].id  # type: ignore[union-attr]
        assert denied[0].client_id == cli_b.id  # type: ignore[union-attr]

    async def test_operador_nao_le_conciliacao_de_outro_tenant_pela_pk(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        """Sem `client_id` na requisição — o caso mais fácil de esquecer."""
        await _login(client_with_db, "op-a@austral.com.br")
        sess_b = cenario["sess_b"]

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess_b.id}")  # type: ignore[union-attr]

        assert resp.status_code == 404  # conversão anti-enumeração preservada
        assert SECRET_NAME_B not in resp.text
        assert len(await _denied_rows(db_session)) == 1

    async def test_forjar_client_id_no_query_nao_devolve_dado_alheio(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "op-a@austral.com.br")
        cli_b = cenario["cli_b"]

        resp = await client_with_db.get(
            "/api/v1/reconciliations/check-duplicate",
            params={
                "client_id": str(cli_b.id),  # type: ignore[union-attr]
                "omie_conta_id": 42,
                "month": "2026-04",
                "hash": _hex64("qualquer"),
            },
        )
        assert resp.status_code in {403, 404}
        assert SECRET_NAME_B not in resp.text


class TestRevogacaoImediata:
    async def test_mudar_tenant_na_linha_vale_no_proximo_request(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        """O token ANTIGO continua válido, mas o escopo é o da LINHA, já atualizado."""
        await _login(client_with_db, "op-a@austral.com.br")
        cli_a, cli_b = cenario["cli_a"], cenario["cli_b"]

        # Antes: acessa A, não acessa B.
        assert (
            await client_with_db.get(f"/api/v1/clients/{cli_a.id}/reconciliations")
        ).status_code == 200  # type: ignore[union-attr]

        # Admin move o usuário para o tenant B — SEM reemitir token.
        operador = cenario["operador_a"]
        operador.client_id = cli_b.id  # type: ignore[union-attr]
        await db_session.flush()

        # Depois: o MESMO cookie já enxerga B e perde A.
        assert (
            await client_with_db.get(f"/api/v1/clients/{cli_b.id}/reconciliations")
        ).status_code == 200  # type: ignore[union-attr]
        assert (
            await client_with_db.get(f"/api/v1/clients/{cli_a.id}/reconciliations")  # type: ignore[union-attr]
        ).status_code in {403, 404}

    async def test_desativar_revoga_no_proximo_request(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "op-a@austral.com.br")
        cli_a = cenario["cli_a"]

        operador = cenario["operador_a"]
        operador.active = False  # type: ignore[union-attr]
        await db_session.flush()

        resp = await client_with_db.get(f"/api/v1/clients/{cli_a.id}/reconciliations")  # type: ignore[union-attr]
        assert resp.status_code == 401


class TestCelulasNegativasDaMatriz:
    async def test_papel_de_cliente_nao_edita_dados_do_cliente(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        """❌ 'Editar dados do cliente' para client_manager e client_operator."""
        cli_a = cenario["cli_a"]
        for email in ("ger-a@austral.com.br", "op-a@austral.com.br"):
            await _login(client_with_db, email)
            resp = await client_with_db.patch(
                f"/api/v1/clients/{cli_a.id}",  # type: ignore[union-attr]
                json={"name": "Renomeado pelo cliente"},
            )
            assert resp.status_code == 403, f"{email}: {resp.status_code} {resp.text}"

    async def test_manager_de_sistema_nao_edita_dados_do_cliente(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        """❌ da matriz para o manager do sistema — mudança declarada no PRD §4."""
        await _login(client_with_db, "iso-mgr@hologram.com.br")
        cli_a = cenario["cli_a"]
        resp = await client_with_db.patch(
            f"/api/v1/clients/{cli_a.id}",  # type: ignore[union-attr]
            json={"name": "Renomeado pelo manager"},
        )
        assert resp.status_code == 403

    async def test_admin_edita_dados_do_cliente(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        """✅ da mesma linha da matriz — o bloqueio acima não é 'quebrou tudo'."""
        await _login(client_with_db, "iso-admin@hologram.com.br")
        cli_a = cenario["cli_a"]
        resp = await client_with_db.patch(
            f"/api/v1/clients/{cli_a.id}",  # type: ignore[union-attr]
            json={"name": "Renomeado pelo admin"},
        )
        assert resp.status_code == 200, resp.text

    async def test_papel_de_cliente_nao_lista_clientes_do_sistema(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        """❌ 'Ver outro tenant': a listagem da equipe Hologram é staff-only."""
        await _login(client_with_db, "op-a@austral.com.br")
        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 403
        assert SECRET_NAME_B not in resp.text


class TestRegressaoSystem:
    async def test_admin_continua_vendo_todos(
        self, client_with_db: AsyncClient, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "iso-admin@hologram.com.br")
        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200
        ids = {c["id"] for c in resp.json()["data"]}
        # Superset, não igualdade: a listagem é global e outros testes da suíte
        # semeiam clientes. O que importa é que o admin enxerga os DOIS tenants.
        assert {str(cenario["cli_a"].id), str(cenario["cli_b"].id)} <= ids  # type: ignore[union-attr]

    async def test_manager_continua_limitado_a_carteira(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "iso-mgr@hologram.com.br")

        resp = await client_with_db.get("/api/v1/clients")
        assert resp.status_code == 200
        ids = {c["id"] for c in resp.json()["data"]}
        # A carteira dele é só o tenant A — e, principalmente, B NÃO aparece.
        assert ids == {str(cenario["cli_a"].id)}  # type: ignore[union-attr]

        cli_b = cenario["cli_b"]
        fora = await client_with_db.get(f"/api/v1/clients/{cli_b.id}/reconciliations")  # type: ignore[union-attr]
        assert fora.status_code in {403, 404}
        assert SECRET_NAME_B not in fora.text

        denied = await _denied_rows(db_session)
        assert len(denied) == 1
        assert denied[0].user_scope == UserScope.SYSTEM.value
        assert denied[0].actor_client_id is None


class TestGuardrailDePerformance:
    async def test_autenticar_nao_custa_query_extra(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        """`scope`/`client_id` vêm da MESMA leitura que já checava `active`.

        Contamos os `SELECT ... FROM users` de um request autenticado de usuário
        de cliente: tem de ser exatamente 1 (o do `get_current_user`). Uma
        segunda leitura de `users` por request seria a regressão que o guardrail
        do PRD proíbe.
        """
        await _login(client_with_db, "op-a@austral.com.br")
        cli_a = cenario["cli_a"]

        statements: list[str] = []

        def _capture(conn, cursor, statement, parameters, context, executemany) -> None:
            statements.append(statement)

        sync_engine = db_session.get_bind().engine  # type: ignore[union-attr]
        event.listen(sync_engine, "before_cursor_execute", _capture)
        try:
            resp = await client_with_db.get(f"/api/v1/clients/{cli_a.id}/reconciliations")  # type: ignore[union-attr]
        finally:
            event.remove(sync_engine, "before_cursor_execute", _capture)

        assert resp.status_code == 200, resp.text
        user_selects = [s for s in statements if "FROM users" in s]
        assert len(user_selects) == 1, user_selects
        # E nenhuma consulta a `client_assignments`: o usuário de cliente decide
        # por `client_id`, não por carteira.
        assert not [s for s in statements if "client_assignments" in s]


class TestAuditoriaDoMissPorTenant:
    """O filtro de tenant no SELECT (R3) não pode apagar a trilha (R6).

    Quando o `SELECT` já filtrado mata a busca, a sessão alheia é
    indistinguível de inexistente **para o cliente** — mas a tentativa continua
    sendo cross-tenant e tem de aparecer na `access_audit`.
    """

    async def test_sessao_inexistente_nao_gera_linha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        """404 comum não é negação cross-tenant — a trilha não vira log de 404."""
        await _login(client_with_db, "op-a@austral.com.br")

        resp = await client_with_db.get(f"/api/v1/reconciliations/{uuid4()}")

        assert resp.status_code == 404
        assert await _denied_rows(db_session) == []

    async def test_sessao_de_outro_tenant_gera_linha(
        self, client_with_db: AsyncClient, db_session: AsyncSession, cenario: dict[str, object]
    ) -> None:
        await _login(client_with_db, "op-a@austral.com.br")
        sess_b = cenario["sess_b"]

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess_b.id}")  # type: ignore[union-attr]

        assert resp.status_code == 404
        denied = await _denied_rows(db_session)
        assert len(denied) == 1
        assert denied[0].actor_client_id == cenario["cli_a"].id  # type: ignore[union-attr]
        assert denied[0].client_id == cenario["cli_b"].id  # type: ignore[union-attr]
