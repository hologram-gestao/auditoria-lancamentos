"""Redefinição de senha pela plataforma (86e3ewukz) — o que dá para travar sem banco.

1. a permissão nova é SÓ da plataforma (célula na matriz e no espelho);
2. o predicado de revogação: token com `iat` anterior a `password_changed_at`
   morre; igual (mesmo segundo) ou posterior vive; NULL nunca revoga;
3. o evento leva SÓ IDs e o escopo — e-mail, nome ou senha são recusados
   na emissão (`extra="forbid"`);
4. a migration existe, encadeia no head anterior e tem downgrade real
   (modelo ↔ migration: mesma tabela e coluna).
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.authz import PERMISSION_MATRIX, Permission
from app.core.security import token_predates_password_change
from app.db.models import User, UserRole
from app.modules.usage_events.schemas import SenhaRedefinidaPelaPlataformaProps, UsageEventName
from app.modules.users.schemas import (
    CLIENT_USER_MIN_PASSWORD_LENGTH,
    STAFF_MIN_PASSWORD_LENGTH,
    ResetPasswordRequest,
)

_MIGRATION = "a7c2e9f31b58_users_password_changed_at.py"


def _load_migration(filename: str) -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"_migration_{filename[:12]}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPermissao:
    def test_so_a_plataforma_redefine_senha(self) -> None:
        assert PERMISSION_MATRIX[Permission.RESET_USER_PASSWORD] == frozenset(
            {UserRole.PLATFORM_ADMIN}
        )

    def test_nao_reusa_permissao_existente(self) -> None:
        """Célula PRÓPRIA: nem `manage_org_users` (admin da org) nem `manage_platform`."""
        assert Permission.RESET_USER_PASSWORD not in {
            Permission.MANAGE_ORG_USERS,
            Permission.MANAGE_PLATFORM,
        }
        assert Permission.RESET_USER_PASSWORD.value == "reset_user_password"


class TestRevogacaoPorIat:
    def test_sem_carimbo_nunca_revoga(self) -> None:
        assert token_predates_password_change(0, None) is False

    def test_token_anterior_ao_carimbo_morre(self) -> None:
        changed = datetime(2026, 9, 26, 12, 0, 0, 500_000, tzinfo=UTC)
        iat = int((changed - timedelta(seconds=1)).timestamp())
        assert token_predates_password_change(iat, changed) is True

    def test_token_do_mesmo_segundo_e_posterior_vivem(self) -> None:
        """Comparação em segundos inteiros: o login logo depois da redefinição não
        pode nascer morto; `iat` é inteiro e o carimbo tem microssegundos."""
        changed = datetime(2026, 9, 26, 12, 0, 0, 900_000, tzinfo=UTC)
        assert token_predates_password_change(int(changed.timestamp()), changed) is False
        assert token_predates_password_change(int(changed.timestamp()) + 1, changed) is False


class TestEvento:
    def test_nome_literal(self) -> None:
        assert (
            UsageEventName.SENHA_REDEFINIDA_PELA_PLATAFORMA.value
            == "senha_redefinida_pela_plataforma"
        )

    def test_props_tem_exatamente_os_campos_declarados(self) -> None:
        assert set(SenhaRedefinidaPelaPlataformaProps.model_fields) == {
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
            SenhaRedefinidaPelaPlataformaProps.model_validate({**base, **extra})

    def test_escopo_fora_do_enum_e_recusado(self) -> None:
        with pytest.raises(ValidationError):
            SenhaRedefinidaPelaPlataformaProps.model_validate(
                {"actor_user_id": uuid4(), "target_user_id": uuid4(), "target_scope": "root"}
            )


class TestSchemaDoBody:
    def test_minimo_do_schema_e_o_de_staff(self) -> None:
        """O de usuário de cliente (10) é do SERVIÇO, que conhece o alvo."""
        assert STAFF_MIN_PASSWORD_LENGTH == 8
        assert CLIENT_USER_MIN_PASSWORD_LENGTH == 10
        with pytest.raises(ValidationError):
            ResetPasswordRequest.model_validate({"password": "1234567"})
        assert ResetPasswordRequest.model_validate({"password": "12345678"}).password == "12345678"

    def test_campo_extra_e_recusado(self) -> None:
        with pytest.raises(ValidationError):
            ResetPasswordRequest.model_validate({"password": "Senh@Nova#123", "email": "x@y.z"})


class TestModeloEMigrationBatem:
    def test_coluna_existe_no_modelo(self) -> None:
        column = User.__table__.c["password_changed_at"]
        assert column.nullable is True
        assert column.type.timezone is True  # type: ignore[attr-defined]

    def test_migration_cobre_a_mesma_coluna_e_encadeia_no_head(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module.revision == "a7c2e9f31b58"
        assert module.down_revision == "e6b2c9d47f13"
        assert User.__tablename__ == module._TABLE
        assert module._COLUMN == "password_changed_at"

    def test_downgrade_e_real(self) -> None:
        source = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
        ).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert "op.drop_column(_TABLE, _COLUMN)" in downgrade
