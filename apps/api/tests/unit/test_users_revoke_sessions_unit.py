"""Revogação de sessão SEM troca de senha (86e3anx4u, parte 1) — o que dá para travar sem banco.

1. o serviço grava SÓ o carimbo (`password_changed_at`): hash e `active` ficam como
   estavam, e a própria sessão é 409 tipado sem gravar nada;
2. o evento `sessoes_encerradas` leva SÓ IDs e o escopo do alvo — e-mail, nome ou
   senha são recusados na emissão (`extra="forbid"`) e o evento NÃO é aceito do
   browser;
3. nenhuma permissão nova: a matriz segue com as mesmas células e as rotas reusam
   `manage_org_users` / `manage_client_users` (a mesma pergunta de "desativar");
4. as duas rotas estão na lista canônica, como `DETAIL_PK`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.authz import PERMISSION_MATRIX, CurrentUser, Permission
from app.core.exceptions import CannotRevokeOwnSessionsError
from app.core.sensitive_endpoints import SENSITIVE_ENDPOINTS, ScopeKind
from app.db.models import User, UserRole, UserScope
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    SessoesEncerradasProps,
    UsageEventName,
)
from app.modules.users.service import UserService

STAFF_ROUTE = "POST /api/v1/users/{user_id}/sessions/revoke"
CLIENT_ROUTE = "POST /api/v1/clients/{client_id}/users/{user_id}/sessions/revoke"


class _FakeRepo:
    def __init__(self) -> None:
        self.added: list[User] = []

    async def add(self, user: User) -> None:
        self.added.append(user)


class _FakeUsageEvents:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def emit_sessoes_encerradas(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


def _viewer(user_id: UUID | None = None) -> CurrentUser:
    return CurrentUser(
        id=str(user_id or uuid4()),
        email="admin@org.com",
        name="Admin",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=uuid4(),
        organization_name="Org",
    )


def _target(scope: UserScope = UserScope.SYSTEM) -> User:
    user = User(
        name="Alvo",
        email="alvo@org.com",
        password_hash="$2b$12$hash-que-nao-pode-mudar",
        role=(UserRole.MANAGER if scope is UserScope.SYSTEM else UserRole.CLIENT_OPERATOR).value,
        active=True,
        scope=scope.value,
        client_id=uuid4() if scope is UserScope.CLIENT else None,
    )
    user.id = uuid4()
    return user


class TestServico:
    async def test_grava_so_o_carimbo_e_emite_o_evento_so_com_ids(self) -> None:
        repo = _FakeRepo()
        events = _FakeUsageEvents()
        service = UserService(repo, usage_events=events)  # type: ignore[arg-type]
        target = _target()
        hash_before = target.password_hash
        viewer = _viewer()
        before = datetime.now(UTC) - timedelta(seconds=1)

        await service.revoke_sessions(target, viewer=viewer)

        assert target.password_changed_at is not None
        assert target.password_changed_at >= before
        assert target.password_hash == hash_before, "o hash NÃO muda: mesma senha"
        assert target.active is True, "a conta continua ativa"
        assert repo.added == [target]
        assert events.calls == [
            {
                "actor_user_id": UUID(viewer.id),
                "target_user_id": target.id,
                "target_scope": "system",
            }
        ]

    async def test_usuario_de_cliente_sai_com_escopo_client(self) -> None:
        events = _FakeUsageEvents()
        service = UserService(_FakeRepo(), usage_events=events)  # type: ignore[arg-type]
        target = _target(UserScope.CLIENT)
        await service.revoke_sessions(target, viewer=_viewer())
        assert events.calls[0]["target_scope"] == "client"

    async def test_a_propria_sessao_e_409_e_nada_e_gravado(self) -> None:
        repo = _FakeRepo()
        events = _FakeUsageEvents()
        service = UserService(repo, usage_events=events)  # type: ignore[arg-type]
        target = _target()
        with pytest.raises(CannotRevokeOwnSessionsError) as exc:
            await service.revoke_sessions(target, viewer=_viewer(target.id))
        assert exc.value.status_code == 409
        assert "OUTRA pessoa" in exc.value.user_message
        assert target.password_changed_at is None
        assert repo.added == []
        assert events.calls == []

    async def test_sem_servico_de_eventos_a_revogacao_continua_valendo(self) -> None:
        repo = _FakeRepo()
        service = UserService(repo)  # type: ignore[arg-type]
        target = _target()
        await service.revoke_sessions(target, viewer=_viewer())
        assert target.password_changed_at is not None
        assert repo.added == [target]


class TestEvento:
    def test_nome_literal(self) -> None:
        assert UsageEventName.SESSOES_ENCERRADAS.value == "sessoes_encerradas"

    def test_props_tem_exatamente_os_campos_declarados(self) -> None:
        assert set(SessoesEncerradasProps.model_fields) == {
            "actor_user_id",
            "target_user_id",
            "target_scope",
        }

    @pytest.mark.parametrize(
        "extra",
        [{"email": "alvo@cliente.com"}, {"name": "Alvo"}, {"password": "Senh@Nova#123"}],
        ids=["email", "name", "password"],
    )
    def test_pii_e_senha_sao_recusados_na_emissao(self, extra: dict[str, str]) -> None:
        base = {"actor_user_id": uuid4(), "target_user_id": uuid4(), "target_scope": "client"}
        with pytest.raises(ValidationError):
            SessoesEncerradasProps.model_validate({**base, **extra})

    def test_escopo_fora_do_enum_e_recusado(self) -> None:
        """`platform` É válido: a plataforma encerra as sessões de um par pela lista de
        administradores da plataforma (86e3chrxw)."""
        base = {"actor_user_id": uuid4(), "target_user_id": uuid4()}
        for scope in ("platform", "system", "client"):
            SessoesEncerradasProps.model_validate({**base, "target_scope": scope})
        with pytest.raises(ValidationError):
            SessoesEncerradasProps.model_validate({**base, "target_scope": "root"})

    def test_nao_e_aceito_do_browser(self) -> None:
        assert UsageEventName.SESSOES_ENCERRADAS not in CLIENT_EMITTED_EVENTS


class TestSemPermissaoNova:
    def test_a_matriz_nao_ganhou_celula(self) -> None:
        """Encerrar sessões e desativar são a MESMA pergunta ("quem gere esta pessoa"):
        as rotas reusam `manage_org_users` e `manage_client_users`. A redefinição de
        senha, que é suporte, continua só na plataforma — e não é reusada aqui."""
        assert len(PERMISSION_MATRIX) == 29
        assert not [p for p in Permission if "revoke" in p.value or "session" in p.value]
        assert PERMISSION_MATRIX[Permission.MANAGE_ORG_USERS] == frozenset(
            {UserRole.PLATFORM_ADMIN, UserRole.ADMIN}
        )
        assert UserRole.CLIENT_MANAGER in PERMISSION_MATRIX[Permission.MANAGE_CLIENT_USERS]
        assert UserRole.CLIENT_OPERATOR not in PERMISSION_MATRIX[Permission.MANAGE_CLIENT_USERS]


class TestListaCanonica:
    def test_as_duas_rotas_entraram_como_detail_pk(self) -> None:
        by_key = {e.key: e for e in SENSITIVE_ENDPOINTS}
        assert by_key[STAFF_ROUTE].kind is ScopeKind.DETAIL_PK
        assert by_key[CLIENT_ROUTE].kind is ScopeKind.DETAIL_PK
        assert by_key[STAFF_ROUTE].module == "app/modules/users/routes.py"
        assert by_key[CLIENT_ROUTE].module == "app/modules/users/client_routes.py"
