"""Matriz permissão x papel e decisão de tenant (Sprint 5 / R2 + R4 — BACK 05.3).

A matriz do PRD (§4) é declarativa e ÚNICA (`app.core.authz.PERMISSION_MATRIX`).
Aqui ela é verificada célula a célula — **inclusive cada `❌`**, que é o que a
sprint chama de "caso negativo".

Estes testes são unitários de propósito: a decisão de permissão não toca banco.
A parte que toca (`resolve_client_access` para o `manager` de sistema) é coberta
nos testes de integração (`test_tenant_isolation.py`).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authz import (
    PERMISSION_MATRIX,
    CurrentUser,
    Permission,
    has_permission,
    tenant_filter_client_id,
)
from app.db.models import UserRole, UserScope

pytestmark = pytest.mark.unit

TENANT_A = uuid4()
TENANT_B = uuid4()


def _user(
    role: UserRole, *, scope: UserScope = UserScope.SYSTEM, client_id: object = None
) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="x@example.com",
        name="X",
        role=role.value,
        scope=scope.value,
        client_id=client_id,
    )


def _client_user(role: UserRole, tenant: object = TENANT_A) -> CurrentUser:
    return _user(role, scope=UserScope.CLIENT, client_id=tenant)


# (permissão, papel, esperado) — transcrição literal da tabela do PRD §4.
MATRIX_CELLS = [
    # Criar/rodar conciliação
    (Permission.RUN_RECONCILIATION, UserRole.CLIENT_MANAGER, True),
    (Permission.RUN_RECONCILIATION, UserRole.CLIENT_OPERATOR, True),
    (Permission.RUN_RECONCILIATION, UserRole.ADMIN, True),
    (Permission.RUN_RECONCILIATION, UserRole.MANAGER, True),
    # Revisar / exportar
    (Permission.REVIEW_EXPORT, UserRole.CLIENT_MANAGER, True),
    (Permission.REVIEW_EXPORT, UserRole.CLIENT_OPERATOR, True),
    (Permission.REVIEW_EXPORT, UserRole.ADMIN, True),
    (Permission.REVIEW_EXPORT, UserRole.MANAGER, True),
    # Sincronizar contas do Omie
    (Permission.SYNC_OMIE_ACCOUNTS, UserRole.CLIENT_MANAGER, True),
    (Permission.SYNC_OMIE_ACCOUNTS, UserRole.CLIENT_OPERATOR, True),
    (Permission.SYNC_OMIE_ACCOUNTS, UserRole.ADMIN, True),
    (Permission.SYNC_OMIE_ACCOUNTS, UserRole.MANAGER, True),
    # Gerir usuários do próprio cliente
    (Permission.MANAGE_CLIENT_USERS, UserRole.CLIENT_MANAGER, True),
    (Permission.MANAGE_CLIENT_USERS, UserRole.CLIENT_OPERATOR, False),  # ❌
    (Permission.MANAGE_CLIENT_USERS, UserRole.ADMIN, True),
    (Permission.MANAGE_CLIENT_USERS, UserRole.MANAGER, False),  # ❌
    # Manter o glossário do tenant (Sprint 6 / BACK 06.3)
    (Permission.MANAGE_GLOSSARY, UserRole.CLIENT_MANAGER, True),
    (Permission.MANAGE_GLOSSARY, UserRole.CLIENT_OPERATOR, False),  # ❌ (só LÊ)
    (Permission.MANAGE_GLOSSARY, UserRole.ADMIN, True),
    # ✅ pelo papel; a CARTEIRA continua sendo `resolve_client_access`.
    (Permission.MANAGE_GLOSSARY, UserRole.MANAGER, True),
    # Editar dados do cliente (§9)
    (Permission.EDIT_CLIENT, UserRole.CLIENT_MANAGER, False),  # ❌
    (Permission.EDIT_CLIENT, UserRole.CLIENT_OPERATOR, False),  # ❌
    (Permission.EDIT_CLIENT, UserRole.ADMIN, True),
    (Permission.EDIT_CLIENT, UserRole.MANAGER, False),  # ❌
    # Ver outro tenant
    (Permission.VIEW_OTHER_TENANT, UserRole.CLIENT_MANAGER, False),  # ❌
    (Permission.VIEW_OTHER_TENANT, UserRole.CLIENT_OPERATOR, False),  # ❌
    (Permission.VIEW_OTHER_TENANT, UserRole.ADMIN, True),
    (Permission.VIEW_OTHER_TENANT, UserRole.MANAGER, True),  # limitado à carteira
]


@pytest.mark.parametrize(("permission", "role", "expected"), MATRIX_CELLS)
def test_celula_da_matriz(permission: Permission, role: UserRole, *, expected: bool) -> None:
    scope = (
        UserScope.CLIENT if role in {UserRole.CLIENT_MANAGER, UserRole.CLIENT_OPERATOR} else None
    )
    user = _client_user(role) if scope else _user(role)
    assert has_permission(user, permission) is expected


def test_toda_permissao_esta_na_matriz() -> None:
    """Permissão nova sem célula quebraria com KeyError em runtime — pega aqui."""
    assert set(PERMISSION_MATRIX) == set(Permission)


def test_toda_celula_da_tabela_do_prd_foi_transcrita() -> None:
    """Guarda contra transcrição parcial: 6 permissões x 4 papéis = 24 células."""
    assert len(MATRIX_CELLS) == len(Permission) * len(UserRole)
    assert {(p, r) for p, r, _ in MATRIX_CELLS} == {(p, r) for p in Permission for r in UserRole}


def test_papel_desconhecido_e_negado_por_padrao() -> None:
    user = CurrentUser(
        id=str(uuid4()),
        email="x@example.com",
        name="X",
        role="papel_que_nao_existe",
        scope=UserScope.SYSTEM.value,
        client_id=None,
    )
    for permission in Permission:
        assert has_permission(user, permission) is False


class TestTenantFilter:
    def test_usuario_de_cliente_filtra_pelo_proprio_tenant(self) -> None:
        assert tenant_filter_client_id(_client_user(UserRole.CLIENT_OPERATOR)) == TENANT_A

    def test_usuario_system_nao_impoe_filtro_de_tenant(self) -> None:
        """`None` = sem restrição por tenant; o escopo dele é a carteira."""
        assert tenant_filter_client_id(_user(UserRole.ADMIN)) is None
        assert tenant_filter_client_id(_user(UserRole.MANAGER)) is None
