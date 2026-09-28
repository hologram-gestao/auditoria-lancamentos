"""As garantias do mapeamento de entrada têm DUAS fontes: modelo e migration (BACK 14.1).

O autogenerate do Alembic **não** compara CHECK constraint — então nada avisaria se
um predicado de vocabulário ou de coerência mudasse num lado só, e o banco passaria
a proteger outra coisa. Mesmo padrão de `test_client_connection_schema.py` e
`test_client_movements_schema.py`.

Este módulo também trava as leis do R1 que o schema carrega:

1. **um mapeamento por cliente** — `UNIQUE(client_id)`;
2. **sinal não se infere** — `sign_convention` é `NOT NULL` com CHECK de coerência;
3. **nada cifrado** — nome de coluna é estrutura, não PII;
4. **purga no encerramento e ordem da exclusão** — a tabela está em
   `close_client_purge` e sai ANTES dos usuários em `delete_client_cascade`.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.models import (
    INPUT_MAPPING_CATEGORY_COHERENT_CHECK,
    INPUT_MAPPING_CATEGORY_COHERENT_CONSTRAINT,
    INPUT_MAPPING_CSV_COHERENT_CHECK,
    INPUT_MAPPING_CSV_COHERENT_CONSTRAINT,
    INPUT_MAPPING_SIGN_COHERENT_CHECK,
    INPUT_MAPPING_SIGN_COHERENT_CONSTRAINT,
    MAX_INPUT_COLUMN_NAME_CHARS,
    MAX_NATURE_LITERAL_CHARS,
    UQ_CLIENT_INPUT_MAPPING_CLIENT,
    CategoryMode,
    ClientInputMapping,
    CsvDelimiter,
    DecimalSeparator,
    InputDateFormat,
    InputEncoding,
    InputFileFormat,
    SignConvention,
)
from app.db.models import client_input_mapping as cim
from app.modules.clients.repository import ClientRepository

_MIGRATION = "b4c8e2d71f95_s14_client_input_mappings.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_b4c8e2d71f95", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks() -> dict[str, str]:
    return {
        str(c.name): str(c.sqltext)
        for c in ClientInputMapping.__table__.constraints
        if isinstance(c, CheckConstraint)
    }


class TestModeloEMigrationBatem:
    def test_predicados_de_vocabulario_batem(self) -> None:
        mig = _load_migration()
        assert cim.input_file_format_check() == mig._CK_FILE_FORMAT
        assert cim.input_category_mode_check() == mig._CK_CATEGORY_MODE
        assert cim.input_date_format_check() == mig._CK_DATE_FORMAT
        assert cim.input_decimal_separator_check() == mig._CK_DECIMAL_SEPARATOR
        assert cim.input_sign_convention_check() == mig._CK_SIGN_CONVENTION
        assert cim.input_csv_delimiter_check() == mig._CK_CSV_DELIMITER
        assert cim.input_encoding_check() == mig._CK_ENCODING

    def test_predicados_de_coerencia_batem(self) -> None:
        mig = _load_migration()
        assert INPUT_MAPPING_CSV_COHERENT_CHECK == mig._CK_CSV_COHERENT
        assert INPUT_MAPPING_CATEGORY_COHERENT_CHECK == mig._CK_CATEGORY_COHERENT
        assert INPUT_MAPPING_SIGN_COHERENT_CHECK == mig._CK_SIGN_COHERENT

    def test_nomes_das_garantias_batem(self) -> None:
        mig = _load_migration()
        prefix = "ck_client_input_mappings_"
        assert mig._UQ_CLIENT == UQ_CLIENT_INPUT_MAPPING_CLIENT
        assert prefix + mig._CK_SIGN_COHERENT_LABEL == INPUT_MAPPING_SIGN_COHERENT_CONSTRAINT
        assert prefix + mig._CK_CSV_COHERENT_LABEL == INPUT_MAPPING_CSV_COHERENT_CONSTRAINT
        assert (
            prefix + mig._CK_CATEGORY_COHERENT_LABEL == INPUT_MAPPING_CATEGORY_COHERENT_CONSTRAINT
        )
        labels_modelo = {
            cim.INPUT_MAPPING_FILE_FORMAT_CK_LABEL,
            cim.INPUT_MAPPING_CATEGORY_MODE_CK_LABEL,
            cim.INPUT_MAPPING_DATE_FORMAT_CK_LABEL,
            cim.INPUT_MAPPING_DECIMAL_SEPARATOR_CK_LABEL,
            cim.INPUT_MAPPING_SIGN_CONVENTION_CK_LABEL,
            cim.INPUT_MAPPING_CSV_DELIMITER_CK_LABEL,
            cim.INPUT_MAPPING_ENCODING_CK_LABEL,
            cim.INPUT_MAPPING_CSV_COHERENT_CK_LABEL,
            cim.INPUT_MAPPING_CATEGORY_COHERENT_CK_LABEL,
            cim.INPUT_MAPPING_SIGN_COHERENT_CK_LABEL,
        }
        labels_migration = {
            mig._CK_FILE_FORMAT_LABEL,
            mig._CK_CATEGORY_MODE_LABEL,
            mig._CK_DATE_FORMAT_LABEL,
            mig._CK_DECIMAL_SEPARATOR_LABEL,
            mig._CK_SIGN_CONVENTION_LABEL,
            mig._CK_CSV_DELIMITER_LABEL,
            mig._CK_ENCODING_LABEL,
            mig._CK_CSV_COHERENT_LABEL,
            mig._CK_CATEGORY_COHERENT_LABEL,
            mig._CK_SIGN_COHERENT_LABEL,
        }
        assert labels_modelo == labels_migration
        assert {"ck_client_input_mappings_" + label for label in labels_modelo} == set(_checks())

    def test_tamanhos_e_default_batem(self) -> None:
        mig = _load_migration()
        assert mig._COLUMN_NAME_MAX == MAX_INPUT_COLUMN_NAME_CHARS
        assert mig._NATURE_LITERAL_MAX == MAX_NATURE_LITERAL_CHARS
        default_mode: str = CategoryMode.COLUNA_CATEGORIA.value
        assert default_mode == mig._DEFAULT_CATEGORY_MODE

    def test_colunas_do_modelo_e_da_migration_batem(self) -> None:
        """Drift de coluna: o conjunto criado pela migration é o do modelo."""
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        for column in ClientInputMapping.__table__.c:
            if column.name in {"created_at", "updated_at"}:
                assert "*_timestamps()" in upgrade
                continue
            assert f'"{column.name}"' in upgrade, column.name

    def test_migration_encadeia_no_head_anterior(self) -> None:
        mig = _load_migration()
        assert mig.revision == "b4c8e2d71f95"
        assert mig.down_revision == "a7c2e9f31b58"

    def test_nenhuma_outra_migration_encadeia_no_mesmo_pai(self) -> None:
        """Dois filhos do mesmo pai = dois heads no Alembic = deploy quebrado."""
        pais = [
            match.group(1)
            for path in _VERSIONS.glob("*.py")
            if (
                match := re.search(
                    r'^down_revision[^=]*=\s*"([0-9a-f]+)"',
                    path.read_text(encoding="utf-8"),
                    re.MULTILINE,
                )
            )
        ]
        repetidos = {pai for pai in pais if pais.count(pai) > 1}
        assert not repetidos, f"mais de uma migration encadeada em {repetidos}"

    def test_downgrade_e_real(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert "op.drop_table(_TABLE)" in downgrade


class TestModeloDeclaraAsGarantias:
    def test_checks_de_vocabulario_cobrem_todos_os_valores_dos_enums(self) -> None:
        pares = (
            (cim.input_file_format_check(), InputFileFormat),
            (cim.input_category_mode_check(), CategoryMode),
            (cim.input_date_format_check(), InputDateFormat),
            (cim.input_decimal_separator_check(), DecimalSeparator),
            (cim.input_sign_convention_check(), SignConvention),
            (cim.input_csv_delimiter_check(), CsvDelimiter),
            (cim.input_encoding_check(), InputEncoding),
        )
        for predicate, enum in pares:
            for member in enum:
                assert f"'{member.value}'" in predicate, (enum, member)

    def test_vocabularios_fechados(self) -> None:
        assert {m.value for m in InputFileFormat} == {"csv", "xlsx"}
        assert {m.value for m in CategoryMode} == {"coluna_categoria", "classificacao_livre"}
        assert {m.value for m in SignConvention} == {
            "valor_com_sinal",
            "coluna_natureza",
            "colunas_separadas",
        }
        assert {m.value for m in DecimalSeparator} == {",", "."}
        assert {m.value for m in CsvDelimiter} == {";", ",", "|"}

    def test_tabela_declara_os_checks_de_coerencia(self) -> None:
        checks = _checks()
        assert checks[INPUT_MAPPING_SIGN_COHERENT_CONSTRAINT] == INPUT_MAPPING_SIGN_COHERENT_CHECK
        assert checks[INPUT_MAPPING_CSV_COHERENT_CONSTRAINT] == INPUT_MAPPING_CSV_COHERENT_CHECK
        assert (
            checks[INPUT_MAPPING_CATEGORY_COHERENT_CONSTRAINT]
            == INPUT_MAPPING_CATEGORY_COHERENT_CHECK
        )

    def test_check_de_sinal_cobre_as_tres_convencoes(self) -> None:
        for convention in SignConvention:
            assert f"sign_convention = '{convention.value}'" in INPUT_MAPPING_SIGN_COHERENT_CHECK

    def test_um_mapeamento_por_cliente(self) -> None:
        unique = next(
            c
            for c in ClientInputMapping.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_INPUT_MAPPING_CLIENT
        )
        assert [c.name for c in unique.columns] == ["client_id"]

    def test_sinal_e_obrigatorio(self) -> None:
        """Invariante do PRD: sinal não se infere — e não se omite."""
        assert ClientInputMapping.__table__.c.sign_convention.nullable is False

    def test_obrigatorios_e_nulaveis(self) -> None:
        table = ClientInputMapping.__table__
        for name in ("date_column", "description_column", "date_format", "decimal_separator"):
            assert table.c[name].nullable is False, name
        for name in (
            "amount_column",
            "category_column",
            "account_column",
            "document_column",
            "csv_delimiter",
            "encoding",
            "nature_column",
            "debit_value",
            "credit_value",
            "debit_column",
            "credit_column",
        ):
            assert table.c[name].nullable is True, name

    def test_fk_para_clients_cascade_e_autoria_restrict(self) -> None:
        table = ClientInputMapping.__table__
        fk_client = next(iter(table.c.client_id.foreign_keys))
        assert fk_client.column.table.name == "clients"
        assert fk_client.ondelete == "CASCADE"
        for name in ("created_by", "updated_by"):
            fk = next(iter(table.c[name].foreign_keys))
            assert fk.column.table.name == "users", name
            assert fk.ondelete == "RESTRICT", name

    def test_nada_cifrado(self) -> None:
        """Nome de coluna é estrutura; conteúdo de célula nunca chega aqui."""
        for column in ClientInputMapping.__table__.c:
            assert not column.name.endswith("_encrypted"), column.name
            assert not column.name.endswith("_iv"), column.name


class TestPurgaEExclusao:
    def test_close_client_purge_declara_a_tabela(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert "delete(ClientInputMapping)" in source

    def test_exclusao_definitiva_apaga_o_mapeamento_antes_dos_usuarios(self) -> None:
        """`created_by`/`updated_by` são RESTRICT: a ordem é a do de-para (ADR-074-BE)."""
        source = inspect.getsource(ClientRepository.delete_client_cascade)
        assert source.index("delete(ClientInputMapping)") < source.index("delete(User)")
