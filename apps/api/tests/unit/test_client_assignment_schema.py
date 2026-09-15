"""As garantias da carteira compartilhada têm DUAS fontes: modelo e migration (86e390kz8).

O autogenerate do Alembic NÃO compara o predicado de índice parcial
(`postgresql_where`), então nada avisaria se `is_primary` mudasse de nome no
modelo e a migration `6bb85e6b7d72` continuasse com o predicado antigo — o
índice do banco protegeria a coluna errada. Mesmo padrão do dedup de
`usage_events` (`test_predicado_bate_com_o_snapshot_da_migration`).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import UniqueConstraint

from app.db.models import (
    UQ_CLIENT_ASSIGNMENT_CLIENT_USER,
    UQ_CLIENT_ASSIGNMENT_PRIMARY,
    ClientAssignment,
    primary_assignment_index_predicate,
)

_MIGRATION = "6bb85e6b7d72_s6_client_assignments_shared_portfolio.py"


def _load_migration() -> ModuleType:
    """Carrega a migration pelo CAMINHO — `alembic/versions/` não é pacote importável."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
    spec = importlib.util.spec_from_file_location("_s6_shared_portfolio_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModeloEMigrationBatem:
    def test_predicado_do_indice_parcial_bate_com_o_snapshot_da_migration(self) -> None:
        assert primary_assignment_index_predicate() == _load_migration()._PRIMARY_PREDICATE

    def test_nomes_das_garantias_batem_com_a_migration(self) -> None:
        module = _load_migration()
        assert module._UQ_PRIMARY == UQ_CLIENT_ASSIGNMENT_PRIMARY
        assert module._UQ_CLIENT_USER == UQ_CLIENT_ASSIGNMENT_CLIENT_USER

    def test_modelo_declara_o_indice_parcial_unico_do_responsavel(self) -> None:
        table = ClientAssignment.__table__
        index = next(i for i in table.indexes if i.name == UQ_CLIENT_ASSIGNMENT_PRIMARY)
        assert index.unique is True
        assert [c.name for c in index.columns] == ["client_id"]
        assert str(index.dialect_options["postgresql"]["where"]) == (
            primary_assignment_index_predicate()
        )

    def test_modelo_declara_a_unique_do_par_cliente_usuario(self) -> None:
        table = ClientAssignment.__table__
        unique = next(
            c
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_ASSIGNMENT_CLIENT_USER
        )
        assert [c.name for c in unique.columns] == ["client_id", "user_id"]

    def test_client_id_nao_tem_mais_indice_proprio(self) -> None:
        """A carteira deixou de ser 1:1, e a UNIQUE do par cobre a busca por cliente."""
        table = ClientAssignment.__table__
        assert table.c.client_id.unique is not True
        assert table.c.client_id.index is not True
        so_client_id = [
            i
            for i in table.indexes
            if [c.name for c in i.columns] == ["client_id"]
            and i.name != UQ_CLIENT_ASSIGNMENT_PRIMARY
        ]
        assert so_client_id == []

    def test_defaults_do_responsavel_sao_diferentes_de_proposito(self) -> None:
        """ORM: FALSE (o código marca explicitamente). Banco: TRUE (linha sem o campo
        é a forma antiga da tabela — pré-migration e API antiga na janela de deploy)."""
        column = ClientAssignment.__table__.c.is_primary
        assert column.nullable is False
        assert column.default is not None
        assert column.default.arg is False
        assert column.server_default is not None
        assert str(column.server_default.arg).lower() == "true"
