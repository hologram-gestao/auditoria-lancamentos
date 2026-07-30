"""Invariantes dos enums de papel/escopo (Sprint 5 / R1 — BACK 05.1).

O enum de papel é FONTE ÚNICA. Estes testes existem para que um papel novo não
entre no `UserRole` sem ser classificado como de sistema ou de cliente — o que
faria a whitelist de criação (e, por tabela, a matriz de permissões) ficar
silenciosamente incompleta.
"""

from __future__ import annotations

import pytest

from app.db.models import (
    CLIENT_ROLES,
    SYSTEM_ROLES,
    ClientUserRole,
    SystemUserRole,
    UserRole,
    UserScope,
)

pytestmark = pytest.mark.unit


def test_todo_papel_esta_classificado() -> None:
    """Nenhum papel fica fora das duas whitelists (nem em ambas)."""
    assert set(UserRole) == SYSTEM_ROLES | CLIENT_ROLES
    assert not SYSTEM_ROLES & CLIENT_ROLES


def test_whitelists_derivam_do_userrole() -> None:
    """As whitelists não redigitam valores — cada membro existe em `UserRole`."""
    for role in (*SystemUserRole, *ClientUserRole):
        assert UserRole(role.value)


def test_escopos_declarados() -> None:
    assert {s.value for s in UserScope} == {"system", "client"}


def test_papeis_de_cliente_declarados() -> None:
    assert {r.value for r in ClientUserRole} == {"client_manager", "client_operator"}


def test_papeis_de_sistema_declarados() -> None:
    assert {r.value for r in SystemUserRole} == {"admin", "manager"}
