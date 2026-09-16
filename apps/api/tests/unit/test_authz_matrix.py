"""Matriz permissão x papel e decisão de tenant (Sprint 5 / R2 + R4 — BACK 05.3;
camada de organizações — 86e36ecar).

A matriz do PRD (§4) é declarativa e ÚNICA (`app.core.authz.PERMISSION_MATRIX`).
Aqui ela é verificada célula a célula — **inclusive cada `❌`**, que é o que a
sprint chama de "caso negativo" — nos cinco papéis, com a plataforma em toda
linha (decisão D1 revisada).

Estes testes são unitários de propósito: a decisão de permissão não toca banco.
A parte que toca (`resolve_client_access` para o staff de organização) é coberta
nos testes de integração (`test_tenant_isolation.py`, `test_authz_organizations.py`).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.core.authz import (
    PERMISSION_MATRIX,
    CurrentUser,
    Permission,
    has_permission,
    reach_filter,
    scoped_by_organization,
    tenant_filter_client_id,
)
from app.db.models import Client, User, UserRole, UserScope

pytestmark = pytest.mark.unit

TENANT_A = uuid4()
TENANT_B = uuid4()
ORG_A = uuid4()


def _user(
    role: UserRole,
    *,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
    organization_id: object = ORG_A,
) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="x@example.com",
        name="X",
        role=role.value,
        scope=scope.value,
        client_id=client_id,
        organization_id=organization_id,
    )


def _client_user(role: UserRole, tenant: object = TENANT_A) -> CurrentUser:
    return _user(role, scope=UserScope.CLIENT, client_id=tenant)


def _platform_user() -> CurrentUser:
    return _user(UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM, organization_id=None)


def _user_for(role: UserRole) -> CurrentUser:
    if role is UserRole.PLATFORM_ADMIN:
        return _platform_user()
    if role in {UserRole.CLIENT_MANAGER, UserRole.CLIENT_OPERATOR}:
        return _client_user(role)
    return _user(role)


# Colunas na ordem da tabela do primer (§4.9).
_ROLES = (
    UserRole.PLATFORM_ADMIN,
    UserRole.ADMIN,
    UserRole.MANAGER,
    UserRole.CLIENT_MANAGER,
    UserRole.CLIENT_OPERATOR,
)
# Transcrição literal da tabela: (platform_admin, admin, manager, client_manager, client_operator).
_TABLE: dict[Permission, tuple[bool, bool, bool, bool, bool]] = {
    Permission.RUN_RECONCILIATION: (True, True, True, True, True),
    Permission.REVIEW_EXPORT: (True, True, True, True, True),
    Permission.SYNC_OMIE_ACCOUNTS: (True, True, True, True, True),
    # O `manager` entra pela carteira na task D2 (86e36ecjp) — hoje ❌.
    Permission.MANAGE_CLIENT_USERS: (True, True, False, True, False),
    # `client_operator` só LÊ; o "dentro da carteira" do manager é `resolve_client_access`.
    Permission.MANAGE_GLOSSARY: (True, True, True, True, False),
    Permission.CREATE_CLIENT: (True, True, True, False, False),
    Permission.EDIT_CLIENT: (True, True, False, False, False),
    Permission.VIEW_OTHER_TENANT: (True, True, True, False, False),  # admin: org; manager: carteira
    Permission.MANAGE_ORG_USERS: (True, True, False, False, False),
    Permission.MANAGE_CLIENT_CATEGORIES: (True, True, False, False, False),
    # D3 final é só plataforma; o admin sai da célula com a tela (onda 2).
    Permission.MANAGE_ANOMALY_TYPES: (True, True, False, False, False),
    Permission.MANAGE_PLATFORM: (True, False, False, False, False),
    Permission.RUN_ALERT_TEST: (True, True, False, False, False),
}
MATRIX_CELLS = [
    (permission, role, expected)
    for permission, row in _TABLE.items()
    for role, expected in zip(_ROLES, row, strict=True)
]


@pytest.mark.parametrize(
    ("permission", "role", "expected"),
    MATRIX_CELLS,
    ids=[f"{p.value}-{r.value}" for p, r, _ in MATRIX_CELLS],
)
def test_celula_da_matriz(permission: Permission, role: UserRole, *, expected: bool) -> None:
    assert has_permission(_user_for(role), permission) is expected


def test_toda_permissao_esta_na_matriz() -> None:
    """Permissão nova sem célula quebraria com KeyError em runtime — pega aqui."""
    assert set(PERMISSION_MATRIX) == set(Permission)


def test_toda_celula_da_tabela_do_prd_foi_transcrita() -> None:
    """Guarda contra transcrição parcial: 13 permissões x 5 papéis = 65 células."""
    assert len(MATRIX_CELLS) == len(Permission) * len(UserRole)
    assert {(p, r) for p, r, _ in MATRIX_CELLS} == {(p, r) for p in Permission for r in UserRole}


def test_plataforma_esta_em_toda_linha() -> None:
    """A regra do Lucas (D1 revisada): a plataforma vê e faz tudo. Permissão nova
    sem a célula da plataforma quebra aqui, não em produção."""
    for permission in Permission:
        assert UserRole.PLATFORM_ADMIN in PERMISSION_MATRIX[permission], permission


def test_papel_desconhecido_e_negado_por_padrao() -> None:
    user = CurrentUser(
        id=str(uuid4()),
        email="x@example.com",
        name="X",
        role="papel_que_nao_existe",
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=ORG_A,
    )
    for permission in Permission:
        assert has_permission(user, permission) is False


class TestTenantFilter:
    def test_usuario_de_cliente_filtra_pelo_proprio_tenant(self) -> None:
        assert tenant_filter_client_id(_client_user(UserRole.CLIENT_OPERATOR)) == TENANT_A

    def test_staff_e_plataforma_nao_impoem_filtro_de_tenant(self) -> None:
        """`None` = sem restrição por tenant; o alcance deles é a org/carteira."""
        assert tenant_filter_client_id(_user(UserRole.ADMIN)) is None
        assert tenant_filter_client_id(_user(UserRole.MANAGER)) is None
        assert tenant_filter_client_id(_platform_user()) is None


def _sql(expr: object) -> str:
    return str(expr.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


class TestScopedByOrganization:
    """Tabelas com coluna de org: as MESMAS respostas de `reach_filter`."""

    def test_plataforma_nao_impoe_filtro(self) -> None:
        stmt = scoped_by_organization(select(User.id), User.organization_id, _platform_user())
        assert "WHERE" not in _sql(stmt)

    def test_staff_e_cliente_filtram_pela_propria_organizacao(self) -> None:
        for user in (
            _user(UserRole.ADMIN),
            _user(UserRole.MANAGER),
            _client_user(UserRole.CLIENT_OPERATOR),
        ):
            stmt = scoped_by_organization(select(User.id), User.organization_id, user)
            assert "users.organization_id = " in _sql(stmt)

    def test_staff_sem_organizacao_e_plataforma_corrompida_nao_veem_nada(self) -> None:
        for user in (
            _user(UserRole.ADMIN, organization_id=None),
            _user(UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM, organization_id=ORG_A),
        ):
            stmt = scoped_by_organization(select(User.id), User.organization_id, user)
            assert _sql(stmt).rstrip().endswith("WHERE false")


class TestReachFilter:
    """A decisão única projetada em WHERE — o que cada escopo alcança numa coleção."""

    def test_plataforma_sem_filtro(self) -> None:
        assert reach_filter(_platform_user(), Client.id) is None

    def test_cliente_e_igualdade_de_tenant(self) -> None:
        condition = reach_filter(_client_user(UserRole.CLIENT_OPERATOR), Client.id)
        assert condition is not None
        assert "clients.id = " in _sql(condition)

    def test_admin_e_exists_na_propria_organizacao(self) -> None:
        condition = reach_filter(_user(UserRole.ADMIN), Client.id)
        assert condition is not None
        sql = _sql(condition)
        assert "EXISTS" in sql
        assert "organization_id" in sql
        assert "client_assignments" not in sql

    def test_manager_e_carteira_dentro_da_organizacao(self) -> None:
        condition = reach_filter(_user(UserRole.MANAGER), Client.id)
        assert condition is not None
        sql = _sql(condition)
        assert "client_assignments" in sql
        assert "organization_id" in sql

    def test_staff_sem_organizacao_nao_alcanca_nada(self) -> None:
        """Linha corrompida: `false`, nunca "todas"."""
        condition = reach_filter(_user(UserRole.ADMIN, organization_id=None), Client.id)
        assert condition is not None
        assert _sql(condition).strip() == "false"

    def test_plataforma_com_organizacao_e_corrompida_e_nao_alcanca_nada(self) -> None:
        corrupted = _user(UserRole.PLATFORM_ADMIN, scope=UserScope.PLATFORM, organization_id=ORG_A)
        condition = reach_filter(corrupted, Client.id)
        assert condition is not None
        assert _sql(condition).strip() == "false"
