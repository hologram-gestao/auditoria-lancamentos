"""`scope` vence `role` na decisão de tenant (Sprint 5 / QA 05.8).

**Por que este arquivo existe.** A CHECK do banco (`ck_users_scope_client_id`,
BACK 05.1) cruza `scope` com `client_id`, mas **não** cruza `scope` com `role`:
a linha `scope='client'` + `role='admin'` é representável no Postgres.

Nenhum endpoint a produz hoje — `SystemUserRole` fecha o request de usuários do
sistema e `ClientUserRole` o de usuários do cliente (BACK 05.1/05.5). Mas um
`UPDATE` manual, um backfill de correção ou um caminho de código futuro
produziriam essa linha; e, quando ela existir, a **ordem dos ramos** em
`resolve_client_access` é a única coisa entre ela e o acesso a todos os tenants.

A ordem correta (escopo primeiro, papel depois) já está no código da 05.3. Estes
testes a **travam**: inverter os ramos passa a quebrar aqui, em vez de virar
vazamento cross-tenant silencioso — que é exatamente a falha que a métrica da
sprint (34/34 endpoints) não pegaria, porque nenhum usuário de fixture tem essa
combinação.

Separado de `test_authz_matrix.py` (BACK 05.3) de propósito: arquivo do QA, sem
sobreposição com o arquivo do executor.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authz import (
    CurrentUser,
    resolve_client_access,
    tenant_filter_client_id,
)
from app.db.models import UserRole, UserScope

pytestmark = pytest.mark.unit

TENANT_A = uuid4()
TENANT_B = uuid4()

#: Papéis da equipe Hologram — os que, se combinados com `scope='client'`,
#: escalariam para "vê todo mundo" caso o `role` fosse consultado antes do
#: `scope`. `admin` cai no ramo "libera tudo"; `manager`, no da carteira.
SYSTEM_ROLES_QUE_ESCALARIAM = (UserRole.ADMIN, UserRole.MANAGER)


def _user(role: UserRole, *, scope: UserScope, client_id: object) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="qa-precedencia@example.com",
        name="QA Precedência",
        role=role.value,
        scope=scope.value,
        client_id=client_id,
    )


@pytest.mark.parametrize("role", SYSTEM_ROLES_QUE_ESCALARIAM, ids=lambda r: r.value)
async def test_papel_de_sistema_com_escopo_de_cliente_nao_sai_do_tenant(role: UserRole) -> None:
    """`scope='client'` + `role='admin'|'manager'` continua preso ao próprio tenant.

    `db=None` é seguro: o ramo `is_client_scoped` retorna ANTES de tocar o banco.
    Se alguém reordenar os `if`, o teste falha (AttributeError na query do
    `manager`, ou `True` indevido no `admin`) — os dois são o alerta desejado.
    """
    user = _user(role, scope=UserScope.CLIENT, client_id=TENANT_A)

    assert await resolve_client_access(None, user, TENANT_A) is True  # type: ignore[arg-type]
    assert await resolve_client_access(None, user, TENANT_B) is False, (  # type: ignore[arg-type]
        f"role={role.value} com scope=client alcançou tenant alheio"
    )


async def test_escopo_de_cliente_sem_tenant_nao_alcanca_ninguem() -> None:
    """Linha corrompida (`scope='client'` + `client_id NULL`) nega tudo.

    A CHECK impede esse estado no banco; o código não confia nela mesmo assim
    (negado por padrão), e é isso que se verifica aqui.
    """
    user = _user(UserRole.CLIENT_MANAGER, scope=UserScope.CLIENT, client_id=None)

    assert await resolve_client_access(None, user, TENANT_A) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("role", SYSTEM_ROLES_QUE_ESCALARIAM, ids=lambda r: r.value)
def test_filtro_da_camada_de_dados_tambem_ignora_o_papel(role: UserRole) -> None:
    """R3: o `WHERE` deriva do MESMO escopo — não do papel (defense-in-depth)."""
    user = _user(role, scope=UserScope.CLIENT, client_id=TENANT_A)

    assert tenant_filter_client_id(user) == TENANT_A
