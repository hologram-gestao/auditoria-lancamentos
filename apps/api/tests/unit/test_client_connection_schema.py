"""As garantias das conexões têm DUAS fontes: modelo e migration `a7f2c1d93e84` (BACK 09.1).

O autogenerate do Alembic **não** compara CHECK constraint (`check_constraints`
é refletido, mas nenhum comparador o usa) — então nada avisaria se o predicado
do status, o predicado do par de credenciais ou o nome da UNIQUE mudasse num
lado só, e o banco passaria a proteger outra coisa. Mesmo padrão de
`test_client_assignment_schema.py` e `test_organization_schema.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.models import (
    CONNECTION_CREDENTIALS_PAIR_CONSTRAINT,
    CONNECTION_STATUS_CONSTRAINT,
    MAX_CONNECTION_LABEL_CHARS,
    UQ_CLIENT_CONNECTION_CLIENT_PROVIDER_LABEL,
    Client,
    ClientConnection,
    ConnectionStatus,
    OmieAccountCache,
    ProviderType,
    connection_credentials_pair_check,
    connection_status_check,
)
from app.db.models.client import IV_HEX_LENGTH

_MIGRATION = "a7f2c1d93e84_s9_client_connections.py"

_CREDENTIAL_COLUMNS = (
    "omie_app_key_encrypted",
    "omie_app_key_iv",
    "omie_app_secret_encrypted",
    "omie_app_secret_iv",
)


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
    def test_predicado_do_check_de_status_bate(self) -> None:
        assert connection_status_check() == _load_migration(_MIGRATION)._CK_STATUS

    def test_predicado_do_check_do_par_de_credencial_bate(self) -> None:
        migration = _load_migration(_MIGRATION)
        assert connection_credentials_pair_check() == migration._CK_CREDENTIALS_PAIR

    def test_nomes_das_garantias_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._UQ_CONNECTION == UQ_CLIENT_CONNECTION_CLIENT_PROVIDER_LABEL
        assert f"ck_client_connections_{module._CK_STATUS_LABEL}" == CONNECTION_STATUS_CONSTRAINT
        assert (
            f"ck_client_connections_{module._CK_CREDENTIALS_PAIR_LABEL}"
            == CONNECTION_CREDENTIALS_PAIR_CONSTRAINT
        )

    def test_tamanhos_e_default_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._LABEL_MAX == MAX_CONNECTION_LABEL_CHARS
        assert module._IV_HEX_LENGTH == IV_HEX_LENGTH
        default_status: str = ConnectionStatus.ATIVA.value
        assert default_status == module._DEFAULT_STATUS

    def test_colunas_de_credencial_do_downgrade_sao_as_do_modelo(self) -> None:
        """O downgrade devolve para `NOT NULL` EXATAMENTE as 4 colunas que o upgrade afrouxou."""
        module = _load_migration(_MIGRATION)
        assert tuple(name for name, _ in module._CREDENTIAL_COLUMNS) == _CREDENTIAL_COLUMNS


class TestModeloDeclaraAsGarantias:
    def test_check_de_status_cobre_todos_os_valores_do_enum(self) -> None:
        """Os literais do CHECK são os valores do enum — mudar um sem o outro é drift."""
        predicate = connection_status_check()
        for status in ConnectionStatus:
            assert f"'{status.value}'" in predicate
        assert {s.value for s in ConnectionStatus} == {"ativa", "inativa", "erro"}

    def test_tabela_declara_o_check_de_status(self) -> None:
        """A NAMING_CONVENTION do `Base` já expande o rótulo para o nome final."""
        check = next(
            c
            for c in ClientConnection.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == CONNECTION_STATUS_CONSTRAINT
        )
        assert str(check.sqltext) == connection_status_check()

    def test_tabela_declara_o_check_do_par_de_credencial(self) -> None:
        check = next(
            c
            for c in ClientConnection.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == CONNECTION_CREDENTIALS_PAIR_CONSTRAINT
        )
        assert str(check.sqltext) == connection_credentials_pair_check()

    def test_unicidade_e_cliente_tipo_rotulo(self) -> None:
        """`(cliente, tipo)` seria errado: duas contas no mesmo ERP são legítimas."""
        unique = next(
            c
            for c in ClientConnection.__table__.constraints
            if isinstance(c, UniqueConstraint)
            and c.name == UQ_CLIENT_CONNECTION_CLIENT_PROVIDER_LABEL
        )
        assert [c.name for c in unique.columns] == ["client_id", "provider_type", "label"]

    def test_provider_type_nao_tem_check_no_banco(self) -> None:
        """Provedor novo é linha de código, não migration (padrão de `usage_events.event`)."""
        checks = [
            c for c in ClientConnection.__table__.constraints if isinstance(c, CheckConstraint)
        ]
        assert all("provider_type" not in str(c.sqltext) for c in checks)
        assert ProviderType.OMIE.value == "omie"

    def test_fk_para_clients_tem_ondelete_declarado(self) -> None:
        column = ClientConnection.__table__.c.client_id
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "clients"
        assert fk.ondelete == "CASCADE"

    def test_conexao_nao_tem_soft_delete(self) -> None:
        """Divergência DELIBERADA do padrão: remover conexão é exclusão definitiva,
        para que reconectar o mesmo (tipo, rótulo) não esbarre na UNIQUE."""
        assert "deleted_at" not in ClientConnection.__table__.c

    def test_credenciais_sao_nulaveis_e_tem_par_ciphertext_iv(self) -> None:
        table = ClientConnection.__table__
        assert table.c.credentials_encrypted.nullable is True
        assert table.c.credentials_iv.nullable is True
        assert table.c.credentials_iv.type.length == IV_HEX_LENGTH

    def test_carimbos_de_estado_da_conexao_nascem_vazios(self) -> None:
        table = ClientConnection.__table__
        assert table.c.last_checked_at.nullable is True
        assert table.c.accounts_synced_at.nullable is True
        assert table.c.status.nullable is False
        assert str(table.c.status.server_default.arg) == ConnectionStatus.ATIVA.value

    def test_as_quatro_colunas_de_credencial_do_cliente_sao_nulaveis(self) -> None:
        """R1: a linha de `clients` passa a existir com as 4 colunas nulas."""
        for name in _CREDENTIAL_COLUMNS:
            assert Client.__table__.c[name].nullable is True, name

    def test_cache_de_contas_aponta_para_a_conexao(self) -> None:
        column = OmieAccountCache.__table__.c.connection_id
        assert column.nullable is True
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "client_connections"
        assert fk.ondelete == "CASCADE"
