"""O plano contábil do cliente tem DUAS fontes: modelo e migration (BACK 16.1).

O autogenerate do Alembic não compara CHECK — nada avisaria se o predicado do par
do nome ou do tipo mudasse num lado só. Este módulo trava também as leis do R1 que
o schema carrega:

1. `UNIQUE(client_id, code)`; FK para `clients` CASCADE; autoria RESTRICT;
2. o nome só existe CIFRADO (par ciphertext/IV com CHECK) e o código fica em claro;
3. o nome da tabela não colide com o plano da origem (S10) nem com o catálogo da org;
4. purga no encerramento, ordem da exclusão definitiva e AAD 15 → 16.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.core import crypto_service
from app.db.models import (
    ACCOUNTING_ACCOUNT_NAME_PAIR_CONSTRAINT,
    ACCOUNTING_ACCOUNT_TYPE_CONSTRAINT,
    MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS,
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    UQ_ACCOUNTING_ACCOUNT_CLIENT_CODE,
    AccountingAccountType,
    ClientAccountingAccount,
    ClientCategory,
    ClientChartOfAccount,
    accounting_account_name_pair_check,
    accounting_account_type_check,
)
from app.db.models.client import IV_HEX_LENGTH
from app.modules.clients.repository import ClientRepository

_MIGRATION = "e3a7c1f95b40_s16_client_accounting_accounts.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_e3a7c1f95b40", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_source() -> str:
    source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
    return source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]


class TestModeloEMigrationBatem:
    def test_predicados_dos_checks_batem(self) -> None:
        mig = _load_migration()
        assert accounting_account_name_pair_check() == mig._CK_NAME_PAIR
        assert accounting_account_type_check() == mig._CK_TYPE

    def test_nomes_e_tamanhos_batem(self) -> None:
        mig = _load_migration()
        assert mig._UQ_CLIENT_CODE == UQ_ACCOUNTING_ACCOUNT_CLIENT_CODE
        prefix = "ck_client_accounting_accounts_"
        assert prefix + mig._CK_NAME_PAIR_LABEL == ACCOUNTING_ACCOUNT_NAME_PAIR_CONSTRAINT
        assert prefix + mig._CK_TYPE_LABEL == ACCOUNTING_ACCOUNT_TYPE_CONSTRAINT
        table = ClientAccountingAccount.__table__
        assert mig._CODE_MAX == MAX_ACCOUNTING_ACCOUNT_CODE_CHARS
        assert mig._CLASSIFICATION_MAX == MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS
        assert table.c.account_type.type.length == mig._TYPE_MAX
        assert mig._IV_HEX_LENGTH == IV_HEX_LENGTH

    def test_colunas_do_modelo_e_da_migration_batem(self) -> None:
        """Drift de coluna: o conjunto criado pela migration, mais o que as migrations
        SEGUINTES da tabela acrescentaram, é EXATAMENTE o do modelo."""
        upgrade = _upgrade_source()
        model_columns = {column.name for column in ClientAccountingAccount.__table__.c}
        migration_columns = set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', upgrade))
        # `sort_key` nasceu depois, em `b2f7c9e41d06` (86e3n70p9); a comparação dela
        # com o modelo é de `tests/unit/test_accounting_chart_sort_key.py`.
        assert migration_columns | {"sort_key"} == model_columns

    def test_nomes_de_constraint_cabem_no_postgres(self) -> None:
        """Nome > 63 caracteres é truncado em SILÊNCIO pelo Postgres (ADR-074-BE)."""
        mig = _load_migration()
        names = [
            mig._UQ_CLIENT_CODE,
            mig._FK_CLIENT,
            mig._FK_CREATED_BY,
            mig._FK_UPDATED_BY,
            ACCOUNTING_ACCOUNT_NAME_PAIR_CONSTRAINT,
            ACCOUNTING_ACCOUNT_TYPE_CONSTRAINT,
        ]
        for name in names:
            assert len(name) <= 63, name

    def test_migration_encadeia_na_da_14_3(self) -> None:
        mig = _load_migration()
        assert mig.revision == "e3a7c1f95b40"
        assert mig.down_revision == "d9e4a1b57c26"

    def test_nenhuma_outra_migration_encadeia_no_mesmo_pai(self) -> None:
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
        assert "op.drop_table(_TABLE)" in source.split("def downgrade()", 1)[1]

    def test_migration_nao_importa_codigo_da_app(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        assert "from app" not in source
        assert "import app" not in source


class TestModeloDeclaraAsGarantias:
    def test_unicidade_e_cliente_mais_codigo(self) -> None:
        unique = next(
            c
            for c in ClientAccountingAccount.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_ACCOUNTING_ACCOUNT_CLIENT_CODE
        )
        assert [c.name for c in unique.columns] == ["client_id", "code"]

    def test_checks_declarados(self) -> None:
        checks = {
            str(c.name): str(c.sqltext)
            for c in ClientAccountingAccount.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert checks == {
            ACCOUNTING_ACCOUNT_NAME_PAIR_CONSTRAINT: accounting_account_name_pair_check(),
            ACCOUNTING_ACCOUNT_TYPE_CONSTRAINT: accounting_account_type_check(),
        }

    def test_tipo_e_o_vocabulario_fechado(self) -> None:
        assert {t.value for t in AccountingAccountType} == {"analitica", "sintetica"}
        assert accounting_account_type_check() == "account_type IN ('analitica', 'sintetica')"

    def test_nome_so_existe_cifrado_e_codigo_em_claro(self) -> None:
        table = ClientAccountingAccount.__table__
        columns = set(table.c.keys())
        assert {"name_encrypted", "name_iv", "code"} <= columns
        for forbidden in ("name", "nome", "label", "name_hash"):
            assert forbidden not in columns, forbidden
        assert table.c.name_encrypted.nullable is False
        assert table.c.name_iv.nullable is False
        assert table.c.name_iv.type.length == IV_HEX_LENGTH
        assert table.c.code.nullable is False

    def test_fk_do_cliente_cascade_e_autoria_restrict(self) -> None:
        table = ClientAccountingAccount.__table__
        fk_client = next(iter(table.c.client_id.foreign_keys))
        assert fk_client.column.table.name == "clients"
        assert fk_client.ondelete == "CASCADE"
        for column in ("created_by", "updated_by"):
            fk = next(iter(table.c[column].foreign_keys))
            assert fk.column.table.name == "users", column
            assert fk.ondelete == "RESTRICT", column

    def test_conta_nasce_ativa_pelo_default_do_banco(self) -> None:
        active = ClientAccountingAccount.__table__.c.active
        assert active.nullable is False
        assert active.server_default is not None

    def test_nome_nao_colide_com_o_plano_da_origem_nem_com_o_catalogo(self) -> None:
        """A tabela da S16 é outra entidade: nome de tabela e módulo não colidem."""
        name = ClientAccountingAccount.__tablename__
        assert name == "client_accounting_accounts"
        assert name not in {ClientChartOfAccount.__tablename__, ClientCategory.__tablename__}


class TestPurgaExclusaoEAad:
    def test_close_client_purge_declara_a_tabela(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert "delete(ClientAccountingAccount)" in source

    def test_purga_depois_das_decisoes_do_de_para(self) -> None:
        """A 16.2 aponta a decisão para a conta: a decisão tem de sair ANTES."""
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert source.index("delete(ClientMappingDecision)") < source.index(
            "delete(ClientAccountingAccount)"
        )

    def test_exclusao_definitiva_apaga_o_plano_antes_dos_usuarios(self) -> None:
        """`created_by`/`updated_by` RESTRICT: o plano sai ANTES dos usuários do tenant."""
        source = inspect.getsource(ClientRepository.delete_client_cascade)
        plano = source.index("delete(ClientAccountingAccount)")
        assert source.index("delete(ClientMappingDecision)") < plano
        assert plano < source.index("delete(User)")

    def test_o_par_de_aad_e_a_16a_constante(self) -> None:
        assert crypto_service.AAD_ACCOUNTING_ACCOUNT_NAME == (
            "client_accounting_accounts",
            "name_encrypted",
        )
        declared = [n for n in vars(crypto_service) if n.startswith("AAD_")]
        assert declared.index("AAD_ACCOUNTING_ACCOUNT_NAME") == 15
