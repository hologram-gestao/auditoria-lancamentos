"""A conta do banco de cada conta de origem tem DUAS fontes: modelo e migration (BACK 16.3).

O autogenerate do Alembic NÃO compara `postgresql_where`: o índice único PARCIAL do slot
padrão só é protegido por este teste, que compara o predicado das duas fontes
(precedente `uq_client_assignments_primary`). Trava também a purga e a ordem da exclusão.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import Index, UniqueConstraint

from app.db.models import (
    UQ_SOURCE_ACCOUNT_BINDING,
    UQ_SOURCE_ACCOUNT_BINDING_DEFAULT,
    ClientMappingMaterializationItem,
    ClientSourceAccountBinding,
    default_binding_index_predicate,
)
from app.db.models.client_source_account_binding import FK_SOURCE_ACCOUNT_BINDING_ACCOUNT
from app.modules.clients.repository import ClientRepository

_MIGRATION = "a8c4e7d25f19_s16_source_account_bindings.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mig_a8c4e7d25f19", _VERSIONS / _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModeloEMigrationBatem:
    def test_predicado_do_indice_parcial_bate(self) -> None:
        mig = _load_migration()
        assert default_binding_index_predicate() == mig._UQ_DEFAULT_PREDICATE
        index = next(
            i
            for i in ClientSourceAccountBinding.__table__.indexes
            if isinstance(i, Index) and i.name == UQ_SOURCE_ACCOUNT_BINDING_DEFAULT
        )
        assert index.unique
        assert [c.name for c in index.columns] == ["client_id", "source_type"]
        where = index.dialect_options["postgresql"]["where"]
        assert str(where) == default_binding_index_predicate()

    def test_nomes_e_tamanhos_batem(self) -> None:
        mig = _load_migration()
        assert mig._UQ_SOURCE == UQ_SOURCE_ACCOUNT_BINDING
        assert mig._UQ_DEFAULT == UQ_SOURCE_ACCOUNT_BINDING_DEFAULT
        assert mig._FK_ACCOUNT == FK_SOURCE_ACCOUNT_BINDING_ACCOUNT
        table = ClientSourceAccountBinding.__table__
        assert table.c.source_type.type.length == mig._SOURCE_TYPE_MAX
        assert table.c.source_account_id.type.length == mig._SOURCE_ACCOUNT_MAX
        items = ClientMappingMaterializationItem.__table__
        assert items.c.bank_account_code.type.length == mig._ACCOUNT_CODE_MAX
        for name in (
            mig._UQ_SOURCE,
            mig._UQ_DEFAULT,
            mig._FK_CLIENT,
            mig._FK_ACCOUNT,
            mig._FK_CREATED_BY,
            mig._FK_UPDATED_BY,
        ):
            assert len(name) <= 63, name

    def test_colunas_batem(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        created = set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', upgrade))
        model = {c.name for c in ClientSourceAccountBinding.__table__.c}
        assert model <= created
        assert {"bank_account_code", "history_present"} <= created
        assert created - model == {"bank_account_code", "history_present"}

    def test_encadeia_na_16_2_e_downgrade_e_real(self) -> None:
        mig = _load_migration()
        assert mig.down_revision == "f5b8d2e61c37"
        downgrade = (
            (_VERSIONS / _MIGRATION).read_text(encoding="utf-8").split("def downgrade()", 1)[1]
        )
        assert "op.drop_table(_TABLE)" in downgrade
        assert 'op.drop_column(_ITEMS, "bank_account_code")' in downgrade


class TestGarantias:
    def test_unique_das_contas_explicitas(self) -> None:
        unique = next(
            c
            for c in ClientSourceAccountBinding.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_SOURCE_ACCOUNT_BINDING
        )
        assert [c.name for c in unique.columns] == ["client_id", "source_type", "source_account_id"]

    def test_fks(self) -> None:
        table = ClientSourceAccountBinding.__table__
        (client_fk,) = table.c.client_id.foreign_keys
        assert client_fk.ondelete == "CASCADE"
        (account_fk,) = table.c.accounting_account_id.foreign_keys
        assert account_fk.column.table.name == "client_accounting_accounts"
        for column in ("created_by", "updated_by"):
            (fk,) = table.c[column].foreign_keys
            assert fk.ondelete == "RESTRICT"

    def test_snapshot_do_banco_sem_fk(self) -> None:
        items = ClientMappingMaterializationItem.__table__
        assert not items.c.bank_account_code.foreign_keys
        assert items.c.history_present.nullable


class TestPurgaEExclusao:
    def test_close_client_purge_apaga_antes_do_plano(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert source.index("delete(ClientSourceAccountBinding)") < source.index(
            "delete(ClientAccountingAccount)"
        )

    def test_exclusao_definitiva_apaga_antes_do_plano_e_dos_usuarios(self) -> None:
        source = inspect.getsource(ClientRepository.delete_client_cascade)
        binding = source.index("delete(ClientSourceAccountBinding)")
        assert binding < source.index("delete(ClientAccountingAccount)")
        assert binding < source.index("delete(User)")
