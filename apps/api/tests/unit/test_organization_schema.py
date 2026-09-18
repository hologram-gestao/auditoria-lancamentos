"""A camada de organizações tem DUAS fontes: modelo e migration `3e8f1a6c9d24` (86e36ec7p).

O autogenerate do Alembic não compara CHECK constraint, e só compara
`server_default` contra um banco já migrado — então nada avisaria se o id da
Hologram, o predicado do CHECK ternário ou o nome de uma UNIQUE mudasse num lado
só. Mesmo padrão de `test_client_assignment_schema.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.models import (
    HOLOGRAM_ORGANIZATION_ID,
    HOLOGRAM_ORGANIZATION_NAME,
    PLATFORM_ROLES,
    SCOPE_CONSISTENCY_CHECK,
    SCOPE_CONSISTENCY_CONSTRAINT,
    UQ_CLIENT_CATEGORY_ORGANIZATION_NAME,
    UQ_ORGANIZATION_NAME,
    AccessAudit,
    Client,
    ClientCategory,
    ClientUserRole,
    Organization,
    SystemUserRole,
    User,
    UserRole,
    UserScope,
    organization_id_server_default,
)
from app.db.models.user import SCOPE_CONSISTENCY_CK_LABEL

_MIGRATION = "3e8f1a6c9d24_s8_organizations.py"
_S5_MIGRATION = "d5c81a4e9b27_s5_user_tenancy.py"


def _load_migration(filename: str) -> ModuleType:
    """Carrega a migration pelo CAMINHO — `alembic/versions/` não é pacote importável."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"_migration_{filename[:12]}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModeloEMigrationBatem:
    def test_id_e_nome_da_hologram_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert str(HOLOGRAM_ORGANIZATION_ID) == module._HOLOGRAM_ID
        assert module._HOLOGRAM_NAME == HOLOGRAM_ORGANIZATION_NAME

    def test_server_default_das_colunas_de_org_bate(self) -> None:
        assert organization_id_server_default() == _load_migration(_MIGRATION)._ORG_DEFAULT

    def test_check_ternario_bate(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._NEW_CK == SCOPE_CONSISTENCY_CHECK
        assert module._NEW_CK_LABEL == SCOPE_CONSISTENCY_CK_LABEL

    def test_check_antigo_do_downgrade_e_o_snapshot_da_sprint5(self) -> None:
        """O downgrade recria EXATAMENTE a constraint que a S5 criou — não uma parecida."""
        module = _load_migration(_MIGRATION)
        s5 = _load_migration(_S5_MIGRATION)
        assert module._OLD_CK == s5.SCOPE_CLIENT_ID_CHECK
        assert module._OLD_CK_LABEL == s5.SCOPE_CLIENT_ID_CK_LABEL

    def test_nomes_das_garantias_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._UQ_ORG_NAME == UQ_ORGANIZATION_NAME
        assert module._UQ_CATEGORIES_ORG_NAME == UQ_CLIENT_CATEGORY_ORGANIZATION_NAME


class TestModeloDeclaraAsGarantias:
    def test_check_cruza_os_papeis_dos_enums(self) -> None:
        """Os literais do CHECK são os valores dos enums — mudar um sem o outro é drift."""
        for role in (*PLATFORM_ROLES, *SystemUserRole, *ClientUserRole):
            assert f"'{role.value}'" in SCOPE_CONSISTENCY_CHECK
        assert {r.value for r in UserRole} == {
            r.value for r in (*PLATFORM_ROLES, *SystemUserRole, *ClientUserRole)
        }
        for scope in UserScope:
            assert f"scope = '{scope.value}'" in SCOPE_CONSISTENCY_CHECK
        assert (
            f"scope = '{UserScope.PLATFORM.value}' AND role = '{UserRole.PLATFORM_ADMIN.value}'"
            in SCOPE_CONSISTENCY_CHECK
        )

    def test_users_declara_o_check_ternario(self) -> None:
        """A NAMING_CONVENTION do `Base` já expande o rótulo para o nome final no metadata."""
        check = next(
            c
            for c in User.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == SCOPE_CONSISTENCY_CONSTRAINT
        )
        assert str(check.sqltext) == SCOPE_CONSISTENCY_CHECK

    def test_colunas_de_org_tem_default_do_banco_e_nao_do_orm(self) -> None:
        """Banco: Hologram (linha sem o campo é a forma antiga da tabela).
        ORM: nenhum — o código passa a org da LINHA do ator explicitamente."""
        for table, nullable in ((Client, False), (User, True), (ClientCategory, False)):
            column = table.__table__.c.organization_id
            assert column.nullable is nullable, table.__tablename__
            assert column.default is None, table.__tablename__
            assert column.server_default is not None, table.__tablename__
            assert str(column.server_default.arg) == organization_id_server_default()
            fk = next(iter(column.foreign_keys))
            assert fk.column.table.name == "organizations"
            assert fk.ondelete == "RESTRICT"

    def test_categorias_sao_unicas_por_organizacao(self) -> None:
        table = ClientCategory.__table__
        unique = next(
            c
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_CATEGORY_ORGANIZATION_NAME
        )
        assert [c.name for c in unique.columns] == ["organization_id", "name"]
        assert not any(
            isinstance(c, UniqueConstraint) and c.name == "uq_client_categories_name"
            for c in table.constraints
        )

    def test_organizations_tem_nome_unico_e_nasce_ativa(self) -> None:
        table = Organization.__table__
        unique = next(
            c
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_ORGANIZATION_NAME
        )
        assert [c.name for c in unique.columns] == ["name"]
        active = table.c.active
        assert active.nullable is False
        assert str(active.server_default.arg).lower() == "true"

    def test_actor_organization_id_na_trilha_e_sem_fk(self) -> None:
        column = AccessAudit.__table__.c.actor_organization_id
        assert column.nullable is True
        assert not column.foreign_keys
