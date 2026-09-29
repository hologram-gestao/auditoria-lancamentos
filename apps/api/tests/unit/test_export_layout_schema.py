"""Layouts de exportação têm DUAS fontes: modelo e migration (BACK 13.2).

O autogenerate do Alembic não compara CHECK nem nomes: este teste trava que os snapshots
copiados na migration `b3d9e5f17a20` batem com o modelo (precedente
`test_organization_schema.py`/`test_client_source_account_binding_schema.py`), que o
downgrade é real e que nada cifrado nasceu (layout é configuração da organização).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.core import crypto_service
from app.db.models import (
    UQ_EXPORT_LAYOUT_ORG_NAME,
    UQ_EXPORT_LAYOUT_VERSION,
    ExportLayout,
    ExportLayoutVersion,
)
from app.db.models.export_layout import (
    EXPORT_LAYOUT_VERSION_CHECK,
    MAX_EXPORT_LAYOUT_NAME_CHARS,
    MAX_EXPORT_LAYOUT_TARGET_SYSTEM_CHARS,
)

_MIGRATION = "b3d9e5f17a20_s13_export_layouts.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mig_b3d9e5f17a20", _VERSIONS / _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")


class TestModeloEMigrationBatem:
    def test_nomes_tamanhos_e_check(self) -> None:
        mig = _load_migration()
        assert mig._UQ_ORG_NAME == UQ_EXPORT_LAYOUT_ORG_NAME
        assert mig._UQ_VERSION == UQ_EXPORT_LAYOUT_VERSION
        assert mig._CK_VERSION_SQL == EXPORT_LAYOUT_VERSION_CHECK
        assert mig._NAME_MAX == MAX_EXPORT_LAYOUT_NAME_CHARS
        assert mig._TARGET_SYSTEM_MAX == MAX_EXPORT_LAYOUT_TARGET_SYSTEM_CHARS
        layouts = ExportLayout.__table__
        assert layouts.c.name.type.length == mig._NAME_MAX
        assert layouts.c.target_system.type.length == mig._TARGET_SYSTEM_MAX
        check = next(
            c for c in ExportLayoutVersion.__table__.constraints if isinstance(c, CheckConstraint)
        )
        assert check.name == f"ck_export_layout_versions_{mig._CK_VERSION_LABEL}"
        assert str(check.sqltext) == mig._CK_VERSION_SQL
        for name in (
            mig._UQ_ORG_NAME,
            mig._UQ_VERSION,
            mig._FK_ORG,
            mig._FK_CREATED_BY,
            mig._FK_LAYOUT,
            mig._FK_AUTHOR,
            str(check.name),
        ):
            assert len(name) <= 63, name

    def test_colunas_batem(self) -> None:
        upgrade = _source().split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        tables = re.split(r"op\.create_table\(", upgrade)[1:]
        assert len(tables) == 2
        created = [set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', t)) for t in tables]
        assert created[0] == {c.name for c in ExportLayout.__table__.c}
        assert created[1] == {c.name for c in ExportLayoutVersion.__table__.c}

    def test_unicas(self) -> None:
        def unique(table: object, name: str) -> list[str]:
            constraint = next(
                c
                for c in table.constraints  # type: ignore[attr-defined]
                if isinstance(c, UniqueConstraint) and c.name == name
            )
            return [c.name for c in constraint.columns]

        assert unique(ExportLayout.__table__, UQ_EXPORT_LAYOUT_ORG_NAME) == [
            "organization_id",
            "name",
        ]
        assert unique(ExportLayoutVersion.__table__, UQ_EXPORT_LAYOUT_VERSION) == [
            "layout_id",
            "version",
        ]

    def test_fks_restrict(self) -> None:
        for table, column in (
            (ExportLayout.__table__, "organization_id"),
            (ExportLayout.__table__, "created_by"),
            (ExportLayoutVersion.__table__, "layout_id"),
            (ExportLayoutVersion.__table__, "author_id"),
        ):
            (fk,) = table.c[column].foreign_keys
            assert fk.ondelete == "RESTRICT", column

    def test_encadeia_na_16_3_e_downgrade_e_real(self) -> None:
        mig = _load_migration()
        assert mig.down_revision == "a8c4e7d25f19"
        downgrade = _source().split("def downgrade()", 1)[1]
        assert "op.drop_table(_VERSIONS)" in downgrade
        assert "op.drop_table(_LAYOUTS)" in downgrade
        assert downgrade.index("_VERSIONS") < downgrade.index("_LAYOUTS")

    def test_migration_nao_importa_app(self) -> None:
        assert "from app" not in _source()
        assert "import app" not in _source()


class TestNadaCifrado:
    def test_nenhuma_coluna_cifrada_nem_par_de_aad(self) -> None:
        """Layout é configuração da ORGANIZAÇÃO: nenhum campo cifrado, nenhum AAD novo."""
        for table in (ExportLayout.__table__, ExportLayoutVersion.__table__):
            assert not [c.name for c in table.c if "encrypted" in c.name or c.name.endswith("_iv")]
        aad_tables = {
            value[0]
            for name, value in vars(crypto_service).items()
            if name.startswith("AAD_") and isinstance(value, tuple)
        }
        assert "export_layouts" not in aad_tables
        assert "export_layout_versions" not in aad_tables
