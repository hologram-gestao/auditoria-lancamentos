"""`scope` vence `role` na decisão de tenant (Sprint 5 / QA 05.8 + organizações).

**Por que este arquivo existe.** Desde a camada de organizações a CHECK do banco
(`ck_users_scope_consistency`) cruza `scope` com `role`, então a linha
`scope='client'` + `role='admin'` já não é representável no Postgres. O código
**continua não confiando nela** (negado por padrão): um `UPDATE` manual num banco
sem a constraint, um backfill de correção ou um caminho futuro produziriam essa
linha — e, quando ela existir, a **ordem dos ramos** em `resolve_client_access`
é a única coisa entre ela e o acesso a todos os tenants.

A ordem correta (cliente primeiro, plataforma bem formada depois, papel por
último) está no código. Estes testes a **travam**: inverter os ramos passa a
quebrar aqui, em vez de virar vazamento cross-tenant silencioso — que é
exatamente a falha que a métrica da sprint não pegaria, porque nenhum usuário de
fixture tem essa combinação.

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

#: Papéis de staff — os que, se combinados com `scope='client'`, escalariam
#: para "vê todo mundo" caso o `role` fosse consultado antes do `scope`.
#: `platform_admin` e `admin` cairiam no ramo "libera"; `manager`, no da carteira.
SYSTEM_ROLES_QUE_ESCALARIAM = (UserRole.PLATFORM_ADMIN, UserRole.ADMIN, UserRole.MANAGER)
ORG_A = uuid4()


def _user(
    role: UserRole, *, scope: UserScope, client_id: object, organization_id: object = ORG_A
) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="qa-precedencia@example.com",
        name="QA Precedência",
        role=role.value,
        scope=scope.value,
        client_id=client_id,
        organization_id=organization_id,
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


class TestPlataforma:
    """A plataforma alcança tudo — mas só a linha BEM FORMADA (sem org, sem tenant)."""

    async def test_plataforma_bem_formada_alcanca_qualquer_tenant_sem_consultar_o_banco(
        self,
    ) -> None:
        user = _user(
            UserRole.PLATFORM_ADMIN,
            scope=UserScope.PLATFORM,
            client_id=None,
            organization_id=None,
        )
        # `db=None` é seguro: o ramo da plataforma retorna ANTES de tocar o banco.
        assert await resolve_client_access(None, user, TENANT_A) is True  # type: ignore[arg-type]
        assert await resolve_client_access(None, user, TENANT_B) is True  # type: ignore[arg-type]
        assert tenant_filter_client_id(user) is None

    @pytest.mark.parametrize(
        ("client_id", "organization_id"),
        [(None, ORG_A), (TENANT_A, None), (TENANT_A, ORG_A)],
        ids=["com-org", "com-tenant", "com-os-dois"],
    )
    async def test_plataforma_corrompida_nao_ganha_o_alcance_total(
        self, client_id: object, organization_id: object
    ) -> None:
        """`scope='platform'` com org ou tenant preenchidos é linha corrompida (a
        CHECK a recusa); o código não a promove a superusuário — nega, em todos
        os casos, sem consultar o banco."""
        user = _user(
            UserRole.PLATFORM_ADMIN,
            scope=UserScope.PLATFORM,
            client_id=client_id,
            organization_id=organization_id,
        )
        assert user.is_platform is False
        assert await resolve_client_access(None, user, TENANT_B) is False  # type: ignore[arg-type]
