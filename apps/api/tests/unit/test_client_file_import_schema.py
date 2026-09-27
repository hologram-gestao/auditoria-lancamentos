"""As garantias da 14.3 têm DUAS fontes: modelo e migration `d9e4a1b57c26` (BACK 14.3 — R3).

O autogenerate do Alembic **não** compara CHECK constraint — então nada avisaria se
o predicado do par da descrição ou o CHECK de competência do registro mudasse num
lado só. Mesmo padrão de `test_client_input_mapping_schema.py` e
`test_client_file_category_schema.py`.

Este módulo também trava as leis do R3 que o schema carrega:

1. **o R3 NÃO cria segunda entidade de lançamento** — a migration só ACRESCENTA
   colunas nuláveis a `client_movements` (adendo do PRD de 25/09);
2. **descrição só cifrada** — `description_encrypted` + `description_iv` com CHECK
   do par; nunca uma coluna `description` em claro;
3. **documento em claro** — identificador, como `supplier_code` (ADR-082-BE);
4. **o 409 do reenvio é do banco** — `UNIQUE(client_id, competence, file_hash)`;
5. **purga no encerramento e ordem da exclusão** — `client_file_imports` está em
   `close_client_purge` e sai ANTES dos usuários em `delete_client_cascade`
   (`created_by` é RESTRICT).
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, Date, Integer, String, Text, UniqueConstraint

from app.db.models import (
    FILE_HASH_HEX_LENGTH,
    FILE_IMPORT_COMPETENCE_CHECK,
    FILE_IMPORT_COMPETENCE_CONSTRAINT,
    IV_HEX_LENGTH,
    MAX_MOVEMENT_DOCUMENT_CHARS,
    MAX_TITLE_DOCUMENT_CHARS,
    MOVEMENT_COMPETENCE_CHECK,
    MOVEMENT_DESCRIPTION_PAIR_CONSTRAINT,
    UQ_CLIENT_FILE_IMPORT_CLIENT_COMPETENCE_HASH,
    ClientFileImport,
    ClientMovement,
    movement_description_pair_check,
)
from app.modules.clients.repository import ClientRepository

_MIGRATION = "d9e4a1b57c26_s14_file_movements_and_imports.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

#: As TRÊS colunas que só o arquivo preenche (nuláveis; a linha do Omie fica com as três nulas).
_FILE_ONLY_COLUMNS = ("description_encrypted", "description_iv", "document")


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_d9e4a1b57c26", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source() -> str:
    return (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")


def _checks(table: object) -> dict[str, str]:
    constraints = table.constraints
    return {str(c.name): str(c.sqltext) for c in constraints if isinstance(c, CheckConstraint)}


class TestModeloEMigrationBatem:
    def test_predicado_do_check_do_par_da_descricao_bate(self) -> None:
        assert movement_description_pair_check() == _load_migration()._CK_DESCRIPTION_PAIR

    def test_predicado_do_check_de_competencia_do_registro_bate(self) -> None:
        mig = _load_migration()
        assert FILE_IMPORT_COMPETENCE_CHECK == mig._CK_IMPORT_COMPETENCE
        # O MESMO predicado da base de movimentos: competência é o dia 1, nos dois.
        assert FILE_IMPORT_COMPETENCE_CHECK == MOVEMENT_COMPETENCE_CHECK

    def test_nomes_das_garantias_batem(self) -> None:
        mig = _load_migration()
        assert mig._UQ_IMPORT == UQ_CLIENT_FILE_IMPORT_CLIENT_COMPETENCE_HASH
        assert mig._CK_DESCRIPTION_PAIR_NAME == MOVEMENT_DESCRIPTION_PAIR_CONSTRAINT
        assert (
            f"ck_client_file_imports_{mig._CK_IMPORT_COMPETENCE_LABEL}"
            == FILE_IMPORT_COMPETENCE_CONSTRAINT
        )

    def test_tamanhos_batem(self) -> None:
        mig = _load_migration()
        assert mig._IV_HEX_LENGTH == IV_HEX_LENGTH
        assert mig._DOCUMENT_MAX == MAX_MOVEMENT_DOCUMENT_CHARS
        assert mig._FILE_HASH_HEX_LENGTH == FILE_HASH_HEX_LENGTH

    def test_colunas_da_tabela_de_registros_batem(self) -> None:
        """Drift de coluna: o conjunto criado pela migration é o do modelo."""
        upgrade = _source().split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        imports_block = upgrade.split("op.create_table(", 1)[1]
        for column in ClientFileImport.__table__.c:
            assert f'"{column.name}"' in imports_block, column.name

    def test_as_tres_colunas_de_arquivo_entram_por_add_column_e_nada_mais(self) -> None:
        """O R3 não cria segunda entidade: a migration ACRESCENTA a `client_movements`."""
        upgrade = _source().split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        adds = re.findall(r'op\.add_column\(\s*_MOVEMENTS,\s*sa\.Column\("(\w+)"', upgrade)
        assert adds == list(_FILE_ONLY_COLUMNS)
        assert upgrade.count("op.create_table(") == 1
        for name in _FILE_ONLY_COLUMNS:
            assert ClientMovement.__table__.c[name].nullable is True, name

    def test_migration_encadeia_na_da_14_4(self) -> None:
        mig = _load_migration()
        assert mig.revision == "d9e4a1b57c26"
        assert mig.down_revision == "c7d3f8a24e61"

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
        """Reversível de verdade: derruba a tabela, o CHECK e as TRÊS colunas."""
        downgrade = _source().split("def downgrade()", 1)[1]
        assert "op.drop_table(_IMPORTS)" in downgrade
        # Pelo LABEL: a naming convention prefixa também no drop (o `--sql` pegou o
        # nome final duplicado, `ck_client_movements_ck_client_movements_…`).
        assert "op.drop_constraint(_CK_DESCRIPTION_PAIR_LABEL, _MOVEMENTS" in downgrade
        assert "op.drop_constraint(_CK_DESCRIPTION_PAIR_NAME" not in downgrade
        for name in _FILE_ONLY_COLUMNS:
            assert f'op.drop_column(_MOVEMENTS, "{name}")' in downgrade, name

    def test_migration_nao_importa_app(self) -> None:
        """Snapshots copiados, nunca `from app…` no topo (ver `d5c81a4e9b27`)."""
        assert "from app" not in _source()
        assert "import app" not in _source()


class TestBaseDeMovimentosDeclaraAsGarantias:
    def test_tabela_declara_o_check_do_par(self) -> None:
        checks = _checks(ClientMovement.__table__)
        assert checks[MOVEMENT_DESCRIPTION_PAIR_CONSTRAINT] == movement_description_pair_check()

    def test_descricao_so_existe_cifrada(self) -> None:
        """Nunca `description` em claro: é texto do cliente final (§4.5)."""
        columns = ClientMovement.__table__.c
        assert "description" not in columns
        assert isinstance(columns.description_encrypted.type, Text)
        iv = columns.description_iv
        assert isinstance(iv.type, String)
        assert iv.type.length == IV_HEX_LENGTH

    def test_documento_e_texto_curto_em_claro_com_o_teto_da_carteira(self) -> None:
        """Identificador, não nome (ADR-082-BE) — e o MESMO teto de `client_titles`."""
        document = ClientMovement.__table__.c.document
        assert isinstance(document.type, String)
        assert document.type.length == MAX_MOVEMENT_DOCUMENT_CHARS
        assert MAX_MOVEMENT_DOCUMENT_CHARS == MAX_TITLE_DOCUMENT_CHARS
        assert not document.name.endswith("_encrypted")


class TestRegistroDeclaraAsGarantias:
    def test_o_409_do_reenvio_e_a_unique_do_banco(self) -> None:
        unique = next(
            c
            for c in ClientFileImport.__table__.constraints
            if isinstance(c, UniqueConstraint)
            and c.name == UQ_CLIENT_FILE_IMPORT_CLIENT_COMPETENCE_HASH
        )
        assert [c.name for c in unique.columns] == ["client_id", "competence", "file_hash"]

    def test_tabela_declara_o_check_de_competencia(self) -> None:
        checks = _checks(ClientFileImport.__table__)
        assert checks[FILE_IMPORT_COMPETENCE_CONSTRAINT] == FILE_IMPORT_COMPETENCE_CHECK

    def test_fks_com_ondelete_declarado(self) -> None:
        table = ClientFileImport.__table__
        fk_client = next(iter(table.c.client_id.foreign_keys))
        assert fk_client.column.table.name == "clients"
        assert fk_client.ondelete == "CASCADE"
        fk_mapping = next(iter(table.c.mapping_id.foreign_keys))
        assert fk_mapping.column.table.name == "client_input_mappings"
        assert fk_mapping.ondelete == "SET NULL"
        fk_author = next(iter(table.c.created_by.foreign_keys))
        assert fk_author.column.table.name == "users"
        assert fk_author.ondelete == "RESTRICT"

    def test_colunas_e_tipos(self) -> None:
        table = ClientFileImport.__table__
        assert set(table.c.keys()) == {
            "id",
            "client_id",
            "competence",
            "file_hash",
            "mapping_id",
            "rows",
            "created_by",
            "processed_at",
        }
        assert isinstance(table.c.competence.type, Date)
        assert isinstance(table.c.rows.type, Integer)
        assert table.c.file_hash.type.length == FILE_HASH_HEX_LENGTH
        assert table.c.file_hash.nullable is False
        assert table.c.mapping_id.nullable is True
        assert table.c.processed_at.server_default is not None

    def test_nenhum_nome_de_arquivo_nem_conteudo(self) -> None:
        """Só o hash identifica o conteúdo; nome de arquivo é texto do usuário."""
        columns = set(ClientFileImport.__table__.c.keys())
        for forbidden in ("filename", "file_name", "name", "content", "description"):
            assert forbidden not in columns, forbidden
        for column in columns:
            assert not column.endswith("_encrypted"), column

    def test_append_only_sem_updated_at(self) -> None:
        assert "updated_at" not in ClientFileImport.__table__.c


class TestPurgaEExclusao:
    def test_close_client_purge_declara_a_tabela(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert "delete(ClientFileImport)" in source

    def test_exclusao_definitiva_apaga_os_registros_antes_dos_usuarios(self) -> None:
        """`created_by` é RESTRICT: a ordem é a do mapeamento e do de-para (ADR-074-BE)."""
        source = inspect.getsource(ClientRepository.delete_client_cascade)
        assert source.index("delete(ClientFileImport)") < source.index("delete(User)")
