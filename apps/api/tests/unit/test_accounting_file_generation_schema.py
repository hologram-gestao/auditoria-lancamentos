"""O registro de geração do arquivo contábil: só metadados, duas fontes batendo (BACK 13.4).

- as colunas da tabela são EXATAMENTE as de metadados — nenhuma de conteúdo, histórico ou
  nome de arquivo (o conteúdo nunca persiste);
- modelo x migration `c7e2a9d4b816` (nomes, CHECKs, FK composta, índice), downgrade real;
- a exclusão definitiva apaga as gerações ANTES das materializações e dos usuários, e o
  encerramento NÃO as apaga.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index

from app.db.models import ACCOUNTING_FILE_GENERATION_CHECKS, AccountingFileGeneration
from app.db.models.accounting_file_generation import (
    FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION,
    FK_ACCOUNTING_FILE_GENERATION_MATERIALIZATION,
    IX_ACCOUNTING_FILE_GENERATION_CLIENT_MATERIALIZATION,
    SHA256_HEX_LENGTH,
)
from app.modules.clients.repository import ClientRepository

_MIGRATION = "c7e2a9d4b816_s13_accounting_file_generations.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_TABLE = AccountingFileGeneration.__table__


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mig_c7e2a9d4b816", _VERSIONS / _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")


class TestSoMetadados:
    def test_as_colunas_sao_exatamente_as_de_metadados(self) -> None:
        """Nenhuma coluna de conteúdo: o arquivo traz histórico e é regenerável."""
        assert {c.name for c in _TABLE.c} == {
            "id",
            "client_id",
            "materialization_id",
            "layout_id",
            "layout_version",
            "competence",
            "line_count",
            "total_amount",
            "sha256",
            "author_id",
            "created_at",
        }
        for column in _TABLE.c:
            for proibido in ("content", "conteudo", "history", "historico", "file_name", "bytes"):
                assert proibido not in column.name

    def test_valor_em_decimal_e_hash_de_64(self) -> None:
        assert _TABLE.c.total_amount.type.precision == 14
        assert _TABLE.c.total_amount.type.scale == 2
        assert _TABLE.c.sha256.type.length == SHA256_HEX_LENGTH == 64


class TestModeloEMigrationBatem:
    def test_checks_nomes_e_indice(self) -> None:
        mig = _load_migration()
        assert mig._CHECKS == ACCOUNTING_FILE_GENERATION_CHECKS
        checks = {
            str(c.name): str(c.sqltext)
            for c in _TABLE.constraints
            if isinstance(c, CheckConstraint)
        }
        assert checks == {
            f"ck_accounting_file_generations_{label}": sql
            for label, sql in ACCOUNTING_FILE_GENERATION_CHECKS
        }
        assert mig._FK_MATERIALIZATION == FK_ACCOUNTING_FILE_GENERATION_MATERIALIZATION
        assert mig._FK_LAYOUT_VERSION == FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION
        assert (
            mig._IX_CLIENT_MATERIALIZATION == IX_ACCOUNTING_FILE_GENERATION_CLIENT_MATERIALIZATION
        )
        assert mig._SHA256_LEN == SHA256_HEX_LENGTH
        index = next(i for i in _TABLE.indexes if isinstance(i, Index))
        assert index.name == IX_ACCOUNTING_FILE_GENERATION_CLIENT_MATERIALIZATION
        assert [c.name for c in index.columns] == ["client_id", "materialization_id"]
        for name in (
            *checks,
            mig._FK_CLIENT,
            mig._FK_MATERIALIZATION,
            mig._FK_LAYOUT_VERSION,
            mig._FK_AUTHOR,
            mig._IX_CLIENT_MATERIALIZATION,
        ):
            assert len(name) <= 63, name

    def test_fks(self) -> None:
        (client_fk,) = _TABLE.c.client_id.foreign_keys
        assert client_fk.ondelete == "CASCADE"
        (mat_fk,) = _TABLE.c.materialization_id.foreign_keys
        assert mat_fk.ondelete == "RESTRICT"
        assert mat_fk.column.table.name == "client_mapping_materializations"
        (author_fk,) = _TABLE.c.author_id.foreign_keys
        assert author_fk.ondelete == "RESTRICT"
        composite = next(
            c
            for c in _TABLE.constraints
            if isinstance(c, ForeignKeyConstraint)
            and c.name == FK_ACCOUNTING_FILE_GENERATION_LAYOUT_VERSION
        )
        assert [c.name for c in composite.columns] == ["layout_id", "layout_version"]
        assert [e.target_fullname for e in composite.elements] == [
            "export_layout_versions.layout_id",
            "export_layout_versions.version",
        ]
        assert composite.ondelete == "RESTRICT"

    def test_colunas_batem_e_downgrade_e_real(self) -> None:
        mig = _load_migration()
        assert mig.down_revision == "b3d9e5f17a20"
        upgrade = _source().split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        created = set(re.findall(r'sa\.Column\(\s*"([a-z0-9_]+)"', upgrade))
        assert created == {c.name for c in _TABLE.c}
        downgrade = _source().split("def downgrade()", 1)[1]
        assert "op.drop_table(_TABLE)" in downgrade
        assert "from app" not in _source()


class TestSaidaDoCliente:
    def test_exclusao_apaga_as_geracoes_antes_das_materializacoes_e_dos_usuarios(self) -> None:
        source = inspect.getsource(ClientRepository.delete_client_cascade)
        generations = source.index("delete(AccountingFileGeneration)")
        assert generations < source.index("delete(ClientMappingMaterialization)")
        assert generations < source.index("delete(User)")

    def test_encerramento_mantem_as_geracoes(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        code = source.split('"""', 2)[2]
        assert "AccountingFileGeneration" not in code
        assert "ClientMappingMaterialization" not in code
