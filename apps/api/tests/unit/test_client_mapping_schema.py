"""As garantias do de-para têm DUAS fontes: modelo e migration (BACK 12.3).

O autogenerate do Alembic não compara CHECK nem índice composto declarado à mão,
e nada avisaria se o CHECK de coerência alvo x tipo, a UNIQUE da vigência ou o
seed dos cinco destinos mudassem num lado só. Este módulo trava também as leis do
PRD que o schema carrega:

1. a chave `(cliente, tipo de origem, categoria, destino)` é única POR VIGÊNCIA;
2. `nao_mapear` é linha, "sem decisão" é ausência de linha — só dois tipos;
3. `source_type` não referencia `client_connections`;
4. a materialização guarda SNAPSHOT, sem FK para `client_movements`;
5. o tipo de destino NÃO é enum/CHECK dos cinco (o sexto é cadastro).
"""

from __future__ import annotations

import importlib.util
import inspect
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Numeric, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.models import (
    DEFAULT_DESTINATION_TYPES,
    ClientMappingDecision,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    DecisionOrigin,
    DecisionType,
    MappingDestination,
    MappingTarget,
    MaterializedSituation,
)
from app.db.models import client_mapping as cm
from app.db.models import mapping_catalog as mc
from app.modules.clients.repository import ClientRepository

_MIGRATION = "e6b2c9d47f13_s12_client_mapping.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_TABLES = (
    MappingDestination,
    MappingTarget,
    ClientMappingDecision,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
)


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_e6b2c9d47f13", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_names(model: type) -> set[str]:
    table = model.__table__  # type: ignore[attr-defined]
    return {str(c.name) for c in table.constraints} | {str(i.name) for i in table.indexes}


class TestModeloEMigrationBatem:
    def test_predicados_dos_checks_batem(self) -> None:
        mig = _load_migration()
        assert cm.decision_type_check() == mig._CK_DECISION_TYPE
        assert cm.decision_origin_check() == mig._CK_ORIGIN
        assert cm.materialized_situation_check() == mig._CK_SITUATION
        assert cm.DECISION_TARGET_COHERENT_CHECK == mig._CK_DECISION_TARGET
        assert cm.ITEM_TARGET_COHERENT_CHECK == mig._CK_ITEM_TARGET
        assert cm.DECISION_EFFECTIVE_FROM_CHECK == mig._CK_EFFECTIVE_FROM
        assert cm.MATERIALIZATION_COMPETENCE_CHECK == mig._CK_COMPETENCE
        assert cm.MATERIALIZATION_VERSION_CHECK == mig._CK_VERSION
        assert mc.DESTINATION_TYPE_CHECK == mig._CK_DESTINATION_TYPE

    def test_nomes_das_garantias_batem(self) -> None:
        mig = _load_migration()
        assert mig._UQ_DESTINATION == mc.UQ_MAPPING_DESTINATION_ORG_TYPE
        assert mig._UQ_TARGET == mc.UQ_MAPPING_TARGET_DESTINATION_CODE
        assert mig._UQ_DECISION == cm.UQ_CLIENT_MAPPING_DECISION
        assert mig._IX_DECISION == cm.IX_CLIENT_MAPPING_DECISION_CLIENT_DESTINATION
        assert mig._UQ_MATERIALIZATION == cm.UQ_CLIENT_MAPPING_MATERIALIZATION
        assert mig._IX_ITEM == cm.IX_MATERIALIZATION_ITEM_MATERIALIZATION
        assert mig._FK_MATERIALIZATION_DESTINATION == cm.FK_MATERIALIZATION_DESTINATION
        assert mig._FK_ITEM_MATERIALIZATION == cm.FK_MATERIALIZATION_ITEM_MATERIALIZATION

    def test_tamanhos_batem(self) -> None:
        mig = _load_migration()
        assert mig._DESTINATION_TYPE_MAX == mc.MAX_DESTINATION_TYPE_CHARS
        assert mig._DESTINATION_NAME_MAX == mc.MAX_DESTINATION_NAME_CHARS
        assert mig._TARGET_CODE_MAX == mc.MAX_TARGET_CODE_CHARS
        assert mig._TARGET_NAME_MAX == mc.MAX_TARGET_NAME_CHARS

    def test_seed_da_migration_e_o_do_modelo(self) -> None:
        """O seed das orgs existentes e o da org nova são a MESMA lista."""
        assert tuple(_load_migration()._DEFAULT_DESTINATIONS) == DEFAULT_DESTINATION_TYPES

    def test_ddl_do_modelo_usa_os_nomes_que_a_migration_cria(self) -> None:
        """Todo nome de constraint/índice que o modelo gera aparece na migration."""
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        mig = _load_migration()
        literais = {
            value for name, value in vars(mig).items() if name.startswith(("_UQ", "_IX", "_FK"))
        }
        for model in _TABLES:
            for name in _constraint_names(model):
                if name.startswith("ck_"):
                    label = name.removeprefix(f"ck_{model.__tablename__}_")  # type: ignore[attr-defined]
                    assert f'"{label}"' in source or label in {
                        v for k, v in vars(mig).items() if k.endswith("_LABEL")
                    }, name
                    continue
                if name.startswith("pk_"):
                    assert f'"{name}"' in source, name
                    continue
                assert name in literais or f'"{name}"' in source, name

    def test_nenhum_identificador_passa_de_63_caracteres(self) -> None:
        """O Postgres TRUNCA em silêncio; o `alembic --sql` recusa. Os dois são ruins."""
        for model in _TABLES:
            ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
            assert ddl
            for name in _constraint_names(model):
                assert len(name) <= 63, name
            for index in model.__table__.indexes:  # type: ignore[attr-defined]
                assert str(CreateIndex(index).compile(dialect=postgresql.dialect()))

    def test_encadeia_na_migration_da_12_1(self) -> None:
        mig = _load_migration()
        assert mig.revision == "e6b2c9d47f13"
        assert mig.down_revision == "d3a8f5c21e47"

    def test_downgrade_derruba_as_cinco_tabelas(self) -> None:
        mig = _load_migration()
        assert set(mig._TABLES_IN_DROP_ORDER) == {m.__tablename__ for m in _TABLES}  # type: ignore[attr-defined]
        downgrade = inspect.getsource(mig.downgrade)
        assert "op.drop_table" in downgrade
        assert "op.drop_index" in downgrade


class TestLeisDoPrd:
    def test_chave_da_decisao_e_unica_por_vigencia(self) -> None:
        unique = next(
            c
            for c in ClientMappingDecision.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == cm.UQ_CLIENT_MAPPING_DECISION
        )
        assert [c.name for c in unique.columns] == [
            "client_id",
            "source_type",
            "category_code",
            "destination_id",
            "effective_from",
        ]

    def test_indice_por_cliente_e_destino(self) -> None:
        index = next(
            i
            for i in ClientMappingDecision.__table__.indexes
            if i.name == cm.IX_CLIENT_MAPPING_DECISION_CLIENT_DESTINATION
        )
        assert [c.name for c in index.columns] == ["client_id", "destination_id"]

    def test_nao_mapear_e_linha_e_sem_decisao_nao_e_tipo(self) -> None:
        """ "Sem decisão" é AUSÊNCIA de linha — nunca um terceiro valor persistido."""
        assert {t.value for t in DecisionType} == {"alvo", "nao_mapear"}
        assert {o.value for o in DecisionOrigin} == {"herdada", "confirmada"}

    def test_check_de_coerencia_alvo_x_tipo_esta_declarado(self) -> None:
        por_nome = {
            c.name: c
            for c in ClientMappingDecision.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert str(por_nome[cm.DECISION_TARGET_CONSTRAINT].sqltext) == (
            cm.DECISION_TARGET_COHERENT_CHECK
        )

    def test_source_type_nao_e_fk_de_conexao(self) -> None:
        """Trocar a conexão (remover e recriar) não pode levar o de-para junto."""
        referenciadas = {
            fk.referred_table.name
            for fk in ClientMappingDecision.__table__.constraints
            if isinstance(fk, ForeignKeyConstraint)
        }
        assert "client_connections" not in referenciadas
        assert not ClientMappingDecision.__table__.c.source_type.foreign_keys

    def test_alvo_referenciado_e_restrict(self) -> None:
        """Apagar alvo em uso é 409 — e o banco também recusa."""
        fk = next(iter(ClientMappingDecision.__table__.c.target_id.foreign_keys))
        assert fk.column.table.name == "mapping_targets"
        assert fk.ondelete == "RESTRICT"

    def test_itens_sao_snapshot_sem_fk_para_movimentos_nem_decisao(self) -> None:
        referenciadas = {
            fk.referred_table.name
            for fk in ClientMappingMaterializationItem.__table__.constraints
            if isinstance(fk, ForeignKeyConstraint)
        }
        assert referenciadas == {"client_mapping_materializations", "clients"}
        colunas = set(ClientMappingMaterializationItem.__table__.c.keys())
        assert {"source_movement_id", "category_code", "target_code", "amount"} <= colunas
        assert "decision_id" not in colunas
        assert "movement_id" not in colunas

    def test_materializacao_unica_por_cliente_destino_competencia_versao(self) -> None:
        unique = next(
            c
            for c in ClientMappingMaterialization.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == cm.UQ_CLIENT_MAPPING_MATERIALIZATION
        )
        assert [c.name for c in unique.columns] == [
            "client_id",
            "destination_id",
            "competence",
            "version",
        ]

    def test_dinheiro_e_numeric_14_2(self) -> None:
        colunas = [
            ClientMappingMaterializationItem.__table__.c.amount,
            *(
                ClientMappingMaterialization.__table__.c[name]
                for name in (
                    "mapped_amount",
                    "not_mapped_amount",
                    "undecided_amount",
                    "uncategorized_amount",
                )
            ),
        ]
        for column in colunas:
            assert isinstance(column.type, Numeric), column.name
            assert (column.type.precision, column.type.scale) == (14, 2), column.name
            assert column.type.python_type is Decimal

    def test_quatro_situacoes_da_previa(self) -> None:
        assert {s.value for s in MaterializedSituation} == {
            "alvo",
            "nao_mapear",
            "sem_decisao",
            "sem_categoria",
        }

    def test_tipo_de_destino_nao_e_enum_nem_check_dos_cinco(self) -> None:
        """O sexto tipo é CADASTRO: o único CHECK é de formato (slug)."""
        checks = [
            str(c.sqltext)
            for c in MappingDestination.__table__.constraints
            if isinstance(c, CheckConstraint)
        ]
        assert checks == [mc.DESTINATION_TYPE_CHECK]
        for destination_type, _ in DEFAULT_DESTINATION_TYPES:
            assert destination_type not in mc.DESTINATION_TYPE_CHECK

    def test_seed_tem_os_cinco_tipos_do_prd(self) -> None:
        assert [t for t, _ in DEFAULT_DESTINATION_TYPES] == [
            "demonstrativo_gerencial",
            "demonstrativo_contabil",
            "conta_contabil",
            "natureza_fiscal",
            "fluxo_de_caixa",
        ]
        assert mc.INHERITING_DESTINATION_TYPE == "demonstrativo_contabil"

    def test_destino_unico_por_organizacao_e_tipo_e_alvo_por_codigo(self) -> None:
        dest_uq = next(
            c for c in MappingDestination.__table__.constraints if isinstance(c, UniqueConstraint)
        )
        assert [c.name for c in dest_uq.columns] == ["organization_id", "destination_type"]
        target_uq = next(
            c for c in MappingTarget.__table__.constraints if isinstance(c, UniqueConstraint)
        )
        assert [c.name for c in target_uq.columns] == ["destination_id", "code"]


class TestEncerramentoERetencao:
    def test_decisoes_saem_no_encerramento_e_materializacoes_ficam(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert "delete(ClientMappingDecision)" in source
        assert "ClientMappingMaterialization" not in source.split('"""')[-1]

    def test_exclusao_definitiva_apaga_o_de_para_antes_dos_usuarios(self) -> None:
        """`author_id` é RESTRICT: o de-para tem de sair antes do `DELETE` de users."""
        source = inspect.getsource(ClientRepository.delete_client_cascade)
        corpo = source.split('"""')[-1]
        pos_mat = corpo.index("delete(ClientMappingMaterialization)")
        pos_dec = corpo.index("delete(ClientMappingDecision)")
        pos_users = corpo.index("delete(User)")
        assert pos_mat < pos_users
        assert pos_dec < pos_users
