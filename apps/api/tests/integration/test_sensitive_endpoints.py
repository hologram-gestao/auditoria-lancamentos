"""Cobertura cross-tenant de TODA a lista canônica (Sprint 5 / R3 — BACK 05.4).

Este módulo é o que transforma "100% dos endpoints sensíveis" de afirmação em
medição:

1. **A lista bate com a realidade** — todo path registrado existe em
   `app.routes` (exceto os marcados como pendentes), e toda rota `/api/v1` está
   classificada (sensível OU explicitamente não-sensível). Endpoint novo com
   `{client_id}`/`{session_id}` que ninguém registrar **quebra o CI**.
2. **Caso negativo por endpoint** — um operador do tenant A dispara CADA
   endpoint da lista contra recursos do tenant B e nunca recebe 2xx nem vê
   qualquer dado de B.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.core.sensitive_endpoints import (
    NON_TENANT_ENDPOINTS,
    PENDING_ENDPOINTS,
    SENSITIVE_ENDPOINTS,
    ScopeKind,
)
from app.db.models import (
    Client,
    Notification,
    NotificationType,
    ReconciliationFile,
    ReconciliationFileStatus,
    ReconciliationSession,
    User,
    UserRole,
    UserScope,
)
from app.main import app as fastapi_app

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@ListaCanonica#1"
SECRET_NAME_B = "Fulana Participacoes LTDA"


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _app_routes() -> set[str]:
    keys: set[str] = set()
    for route in fastapi_app.routes:
        path = getattr(route, "path", None)
        if not path or not path.startswith("/api/v1"):
            continue
        for method in getattr(route, "methods", None) or []:
            if method in {"HEAD", "OPTIONS"}:
                continue
            keys.add(f"{method} {path}")
    return keys


class TestListaCanonicaBateComOCodigo:
    def test_todo_endpoint_registrado_existe(self) -> None:
        routes = _app_routes()
        faltando = [
            e.key
            for e in SENSITIVE_ENDPOINTS
            if e.key not in routes and e.key not in PENDING_ENDPOINTS
        ]
        assert not faltando, f"Endpoints na lista que não existem no app: {faltando}"

    def test_toda_rota_da_api_esta_classificada(self) -> None:
        """Rota nova cai fora das duas listas e o teste falha — nada passa por omissão."""
        registrados = {e.key for e in SENSITIVE_ENDPOINTS} | set(NON_TENANT_ENDPOINTS)
        nao_classificadas = sorted(_app_routes() - registrados)
        assert not nao_classificadas, (
            "Rotas /api/v1 sem classificação de tenant. Registre em "
            "app/core/sensitive_endpoints.py (SENSITIVE_ENDPOINTS ou "
            f"NON_TENANT_ENDPOINTS): {nao_classificadas}"
        )

    def test_rota_com_parametro_de_tenant_e_sempre_sensivel(self) -> None:
        """`{client_id}`/`{session_id}` na URL implica endpoint escopável.

        Exceções são explícitas (edição/atribuição de cliente são admin-only e
        estão em NON_TENANT_ENDPOINTS com o motivo).
        """
        sensiveis = {e.key for e in SENSITIVE_ENDPOINTS}
        suspeitas = [
            key
            for key in _app_routes()
            if ("{client_id}" in key or "{session_id}" in key)
            and key not in sensiveis
            and key not in NON_TENANT_ENDPOINTS
        ]
        assert not suspeitas, suspeitas

    def test_denominador_da_metrica_e_estavel(self) -> None:
        """A lista é o denominador — duplicata silenciosa mudaria a conta."""
        chaves = [e.key for e in SENSITIVE_ENDPOINTS]
        assert len(chaves) == len(set(chaves))
        assert set(PENDING_ENDPOINTS) <= set(chaves)


# ----------------------------------------------------------------------
# Caso negativo cross-tenant, endpoint por endpoint
# ----------------------------------------------------------------------

#: Body mínimo VÁLIDO por endpoint. Precisa passar a validação do Pydantic,
#: senão o 422 mascararia o teste (passaria sem nunca chegar na autorização).
_BODIES: dict[str, dict[str, Any]] = {
    "POST /api/v1/reconciliations": {
        "clientId": "{client_b}",
        "omieContaId": 42,
        "referenceMonth": "2026-04",
        "balanceStart": "0.00",
        "files": [],
    },
    "POST /api/v1/reconciliations/parse": {
        "clientId": "{client_b}",
        "fileName": "extrato.pdf",
        "fileContentBase64": "",
    },
    "POST /api/v1/reconciliations/{session_id}/files": {
        "files": [{"file_hash": _hex64("parte"), "error_code": "VALIDATION_ERROR"}]
    },
    "POST /api/v1/reconciliations/{session_id}/anomalies": {
        "anomaly_type_id": "{uuid}",
        "file_entry_id": "{entry_id}",
        "context": "x",
    },
    "PATCH /api/v1/reconciliations/{session_id}/anomalies/{anomaly_id}": {"resolved": False},
    "PATCH /api/v1/reconciliations/{session_id}/file-entries/{entry_id}": {"user_action": "flag"},
    "PATCH /api/v1/reconciliations/{session_id}/omie-entries/{entry_id}": {"user_action": "flag"},
    "POST /api/v1/usage-events": {
        "event": "autor_navegou_fora",
        "session_id": "{session_b}",
        "props": {"segundos_apos_criar": 10},
    },
}

#: Query string mínima por endpoint.
_QUERIES: dict[str, dict[str, str]] = {
    "GET /api/v1/reconciliations/check-duplicate": {
        "client_id": "{client_b}",
        "omie_conta_id": "42",
        "month": "2026-04",
        "hash": _hex64("x"),
    },
    "GET /api/v1/omie/lancamentos": {"ids": "1,2", "session_id": "{session_b}"},
}


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Lista",
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
    ct_k, iv_k = encrypt("lista-app-key", hex_key)
    ct_s, iv_s = encrypt("lista-app-secret", hex_key)
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
    return cli


@pytest.fixture
async def tenants(db_session: AsyncSession) -> dict[str, Any]:
    """Tenant A (o atacante) e tenant B (o alvo), com recursos reais em B."""
    admin = await _seed_user(db_session, email="lista-admin@hologram.com.br", role=UserRole.ADMIN)
    cli_a = await _seed_client(db_session, creator=admin, name="Austral Lista")
    cli_b = await _seed_client(db_session, creator=admin, name=SECRET_NAME_B)
    operador_a = await _seed_user(
        db_session,
        email="op-lista@austral.com.br",
        role=UserRole.CLIENT_OPERATOR,
        scope=UserScope.CLIENT,
        client_id=cli_a.id,
    )
    sess_b = ReconciliationSession(
        client_id=cli_b.id,
        created_by=admin.id,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=_hex64(f"lista-{uuid4().hex}"),
        status="reviewing",
        balance_start=Decimal("0.00"),
        total_file_entries=0,
        conciliated_count=0,
        sem_omie_count=0,
        omie_sem_arquivo_count=0,
        anomaly_count=0,
    )
    db_session.add(sess_b)
    await db_session.flush()

    file_b = ReconciliationFile(
        session_id=sess_b.id,
        file_hash=_hex64(f"file-{uuid4().hex}"),
        status=ReconciliationFileStatus.PARSED.value,
    )
    db_session.add(file_b)

    # A notificação NÃO carrega nome de cliente (só IDs + conta/mês) — o
    # `SECRET_NAME_B` entra aqui apenas para o teste conseguir provar vazamento
    # se ele existisse; em produção este campo é um código de erro.
    notif_b = Notification(
        user_id=admin.id,
        client_id=cli_b.id,
        session_id=sess_b.id,
        tipo=NotificationType.PROCESSADA.value,
        omie_conta_id=42,
        reference_month=date(2026, 4, 1),
    )
    db_session.add(notif_b)
    await db_session.flush()

    return {
        "admin": admin,
        "cli_a": cli_a,
        "cli_b": cli_b,
        "operador_a": operador_a,
        "sess_b": sess_b,
        "file_b": file_b,
        "notif_b": notif_b,
    }


def _substitute(value: str, ctx: dict[str, str]) -> str:
    for token, replacement in ctx.items():
        value = value.replace("{" + token + "}", replacement)
    return value


COVERED = [e for e in SENSITIVE_ENDPOINTS if e.key not in PENDING_ENDPOINTS]


@pytest.mark.parametrize("endpoint", COVERED, ids=lambda e: e.key)
async def test_cross_tenant_por_endpoint(
    endpoint: Any,
    client_with_db: AsyncClient,
    tenants: dict[str, Any],
) -> None:
    """Operador do tenant A dispara o endpoint contra recursos do tenant B.

    Critério: **nunca** 2xx, e o corpo **nunca** contém dado de B. Notificações
    e coleções globais respondem 200 com conteúdo vazio de B — por isso a
    asserção é "sem dado de B", não "status de erro", para essas.
    """
    login = await client_with_db.post(
        "/api/v1/auth/login",
        json={"email": "op-lista@austral.com.br", "password": PLAIN_PASSWORD},
    )
    assert login.status_code == 200, login.text

    ctx = {
        "client_id": str(tenants["cli_b"].id),
        "client_b": str(tenants["cli_b"].id),
        "session_id": str(tenants["sess_b"].id),
        "session_b": str(tenants["sess_b"].id),
        "file_id": str(tenants["file_b"].id),
        "notification_id": str(tenants["notif_b"].id),
        "anomaly_id": str(uuid4()),
        "entry_id": str(uuid4()),
        "user_id": str(tenants["admin"].id),
        "uuid": str(uuid4()),
    }

    url = _substitute(endpoint.path, ctx)
    params = {k: _substitute(v, ctx) for k, v in _QUERIES.get(endpoint.key, {}).items()}
    raw_body = _BODIES.get(endpoint.key)
    body = None
    if raw_body is not None:
        body = {k: (_substitute(v, ctx) if isinstance(v, str) else v) for k, v in raw_body.items()}

    resp = await client_with_db.request(endpoint.method, url, params=params or None, json=body)

    # Nunca vaza dado do tenant alvo — a asserção que vale para TODOS.
    assert SECRET_NAME_B not in resp.text, f"{endpoint.key} vazou dado do tenant B"

    if endpoint.kind is ScopeKind.COLLECTION and "{" not in endpoint.path:
        # Coleções globais (notificações) respondem 200 com a lista vazia de B.
        assert resp.status_code < 500
        return

    assert resp.status_code not in range(200, 300), (
        f"{endpoint.key} devolveu {resp.status_code} para recurso de outro tenant"
    )
    assert resp.status_code in {403, 404}, f"{endpoint.key} -> {resp.status_code}: {resp.text}"


async def test_notificacoes_do_outro_tenant_nao_aparecem(
    client_with_db: AsyncClient, tenants: dict[str, Any]
) -> None:
    """Coleção global: o operador de A não vê a notificação criada no tenant B."""
    login = await client_with_db.post(
        "/api/v1/auth/login",
        json={"email": "op-lista@austral.com.br", "password": PLAIN_PASSWORD},
    )
    assert login.status_code == 200

    listagem = await client_with_db.get("/api/v1/notifications")
    assert listagem.status_code == 200, listagem.text
    assert listagem.json()["data"] == []
    assert SECRET_NAME_B not in listagem.text

    contagem = await client_with_db.get("/api/v1/notifications/unread-count")
    assert contagem.status_code == 200
    assert contagem.json()["data"]["unread"] == 0

    leitura = await client_with_db.post(f"/api/v1/notifications/{tenants['notif_b'].id}/read")
    assert leitura.status_code == 404
    assert SECRET_NAME_B not in leitura.text


async def test_admin_continua_alcancando_os_dois_tenants(
    client_with_db: AsyncClient, tenants: dict[str, Any]
) -> None:
    """Regressão: o filtro novo é no-op para `system` (nada quebrou para a equipe)."""
    login = await client_with_db.post(
        "/api/v1/auth/login",
        json={"email": "lista-admin@hologram.com.br", "password": PLAIN_PASSWORD},
    )
    assert login.status_code == 200

    for client_id in (tenants["cli_a"].id, tenants["cli_b"].id):
        resp = await client_with_db.get(f"/api/v1/clients/{client_id}/reconciliations")
        assert resp.status_code == 200, resp.text

    detalhe = await client_with_db.get(f"/api/v1/reconciliations/{tenants['sess_b'].id}")
    assert detalhe.status_code == 200, detalhe.text
