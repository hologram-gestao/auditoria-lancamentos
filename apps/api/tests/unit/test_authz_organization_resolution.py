"""Unit — as duas decisões de organização do `authz` (86e36ecqz) e o rótulo de equipe.

`resolve_organization_for_creation` (onde um recurso NASCE) e
`resolve_organization_filter` (o que um `?organizationId=` pode restringir) são
consultadas por `/users`, `/clients` e `/client-categories`. Sem DB: o leitor
de organização é um fake, porque a decisão é pura sobre a LINHA do ator.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.authz import (
    CurrentUser,
    resolve_organization_filter,
    resolve_organization_for_creation,
)
from app.core.exceptions import (
    OrganizationInactiveError,
    OrganizationMismatchError,
    OrganizationNotFoundError,
    ValidationAppError,
)
from app.db.models import Organization, User, UserRole, UserScope
from app.modules.reconciliations.service import (
    HOLOGRAM_TEAM_LABEL,
    TEAM_LABEL_PREFIX,
    author_for_viewer,
    team_label,
)

ORG_A = uuid4()
ORG_B = uuid4()
ORG_SUSPENSA = uuid4()


def _user(
    scope: UserScope,
    role: UserRole,
    *,
    organization_id: UUID | None = None,
    client_id: UUID | None = None,
    organization_name: str | None = None,
) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="x@x.com",
        name="X",
        role=role.value,
        scope=scope.value,
        client_id=client_id,
        organization_id=organization_id,
        organization_name=organization_name,
    )


PLATFORM = _user(UserScope.PLATFORM, UserRole.PLATFORM_ADMIN)
ADMIN_A = _user(UserScope.SYSTEM, UserRole.ADMIN, organization_id=ORG_A, organization_name="A")
MANAGER_A = _user(UserScope.SYSTEM, UserRole.MANAGER, organization_id=ORG_A)
CLIENT_USER = _user(
    UserScope.CLIENT, UserRole.CLIENT_MANAGER, organization_id=ORG_A, client_id=uuid4()
)
STAFF_SEM_ORG = _user(UserScope.SYSTEM, UserRole.ADMIN, organization_id=None)
#: Linha `platform` carregando organização: o CHECK do banco não deixa existir,
#: e o authz a trata como negada em toda decisão — esta inclusive.
PLATFORM_MALFORMADA = _user(UserScope.PLATFORM, UserRole.PLATFORM_ADMIN, organization_id=ORG_A)


async def _get_organization(organization_id: UUID) -> Organization | None:
    if organization_id == ORG_A:
        return Organization(id=ORG_A, name="A", active=True)
    if organization_id == ORG_SUSPENSA:
        return Organization(id=ORG_SUSPENSA, name="Suspensa", active=False)
    return None


class TestResolveOrganizationForCreation:
    async def test_plataforma_precisa_escolher(self) -> None:
        with pytest.raises(ValidationAppError):
            await resolve_organization_for_creation(
                PLATFORM, None, get_organization=_get_organization, subject="cliente"
            )

    async def test_plataforma_escolha_inexistente_e_404(self) -> None:
        with pytest.raises(OrganizationNotFoundError):
            await resolve_organization_for_creation(
                PLATFORM, uuid4(), get_organization=_get_organization, subject="cliente"
            )

    async def test_plataforma_escolha_suspensa_e_409(self) -> None:
        with pytest.raises(OrganizationInactiveError):
            await resolve_organization_for_creation(
                PLATFORM, ORG_SUSPENSA, get_organization=_get_organization, subject="usuário"
            )

    async def test_plataforma_escolha_valida_passa(self) -> None:
        assert (
            await resolve_organization_for_creation(
                PLATFORM, ORG_A, get_organization=_get_organization, subject="categoria"
            )
            == ORG_A
        )

    @pytest.mark.parametrize("requested", [None, ORG_A])
    async def test_staff_cria_na_propria(self, requested: UUID | None) -> None:
        assert (
            await resolve_organization_for_creation(
                ADMIN_A, requested, get_organization=_get_organization, subject="cliente"
            )
            == ORG_A
        )

    async def test_staff_nao_aponta_outra(self) -> None:
        with pytest.raises(OrganizationMismatchError):
            await resolve_organization_for_creation(
                MANAGER_A, ORG_B, get_organization=_get_organization, subject="cliente"
            )

    @pytest.mark.parametrize(
        "actor",
        [CLIENT_USER, STAFF_SEM_ORG, PLATFORM_MALFORMADA],
        ids=["cliente", "staff-sem-org", "plataforma-malformada"],
    )
    async def test_quem_nao_tem_organizacao_para_criar_e_403(self, actor: CurrentUser) -> None:
        with pytest.raises(OrganizationMismatchError):
            await resolve_organization_for_creation(
                actor, ORG_A, get_organization=_get_organization, subject="cliente"
            )

    @pytest.mark.parametrize("subject", ["o usuário", "a categoria", "o cliente"])
    async def test_a_mensagem_concorda_com_o_recurso(self, subject: str) -> None:
        with pytest.raises(ValidationAppError) as exc:
            await resolve_organization_for_creation(
                PLATFORM, None, get_organization=_get_organization, subject=subject
            )
        assert exc.value.user_message == f"Escolha a organização de destino para {subject}."


class TestResolveOrganizationFilter:
    @pytest.mark.parametrize("requested", [None, ORG_B])
    def test_plataforma_pede_o_que_quiser(self, requested: UUID | None) -> None:
        assert resolve_organization_filter(PLATFORM, requested) == requested

    @pytest.mark.parametrize("requested", [None, ORG_A])
    def test_staff_sem_pedido_ou_pedindo_a_propria_e_no_op(self, requested: UUID | None) -> None:
        assert resolve_organization_filter(ADMIN_A, requested) is None

    def test_staff_pedindo_outra_e_403(self) -> None:
        with pytest.raises(OrganizationMismatchError):
            resolve_organization_filter(MANAGER_A, ORG_B)

    def test_cliente_pedindo_qualquer_uma_e_403(self) -> None:
        with pytest.raises(OrganizationMismatchError):
            resolve_organization_filter(CLIENT_USER, ORG_A)
        assert resolve_organization_filter(CLIENT_USER, None) is None


class TestTeamLabel:
    def test_hologram_continua_equipe_hologram(self) -> None:
        assert HOLOGRAM_TEAM_LABEL == "Equipe Hologram"
        assert team_label("Hologram") == HOLOGRAM_TEAM_LABEL

    def test_outra_organizacao_ganha_o_proprio_rotulo(self) -> None:
        assert team_label("Prospecta") == "Equipe Prospecta"

    @pytest.mark.parametrize("name", [None, ""])
    def test_sem_nome_cai_no_prefixo_e_nunca_em_none(self, name: str | None) -> None:
        assert team_label(name) == TEAM_LABEL_PREFIX

    def test_mascara_usa_a_organizacao_do_observador(self) -> None:
        author = User(
            name="Ana", email="ana@a.com", password_hash="x", role="admin", scope="system"
        )
        viewer = _user(
            UserScope.CLIENT,
            UserRole.CLIENT_OPERATOR,
            organization_id=ORG_B,
            client_id=uuid4(),
            organization_name="Prospecta",
        )
        masked = author_for_viewer(author, viewer)
        assert masked.name == "Equipe Prospecta"
        assert masked.email is None
        # Staff e plataforma veem o autor real; cliente vendo colega também.
        assert author_for_viewer(author, ADMIN_A).email == "ana@a.com"
        assert author_for_viewer(author, PLATFORM).email == "ana@a.com"
        colleague = User(
            name="Op", email="op@c.com", password_hash="x", role="client_operator", scope="client"
        )
        assert author_for_viewer(colleague, viewer).email == "op@c.com"
