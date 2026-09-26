"""As garantias da base de movimentos têm DUAS fontes: modelo e migration (BACK 12.1).

O autogenerate do Alembic **não** compara CHECK constraint nem índice composto
declarado à mão — então nada avisaria se o predicado de `status`, o CHECK da
competência ou o nome da UNIQUE mudassem num lado só. Mesmo padrão de
`test_client_titles_schema.py`.

Este módulo também trava as leis do R0 que o PRD chama de invariante:

1. **só códigos** — nenhuma coluna de nome, descrição ou texto livre;
2. **agnóstica de origem** — `source_type` é o TIPO do provedor, nunca FK de
   `client_connections` (conexão é removida e recriada; a base fica);
3. **movimento não se apaga** — a saída da origem é um ESTADO da linha;
4. **purga no encerramento** — a tabela está na lista declarada de
   `close_client_purge`.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, Date, Numeric, UniqueConstraint

from app.db.models import (
    IX_CLIENT_MOVEMENT_CLIENT_COMPETENCE,
    MAX_MOVEMENT_CATEGORY_CODE_CHARS,
    MAX_MOVEMENT_REF_CHARS,
    MAX_MOVEMENT_SOURCE_ID_CHARS,
    MAX_MOVEMENT_SOURCE_TYPE_CHARS,
    MOVEMENT_COMPETENCE_CHECK,
    MOVEMENT_COMPETENCE_CONSTRAINT,
    MOVEMENT_STATUS_CONSTRAINT,
    UQ_CLIENT_MOVEMENT_SOURCE,
    UQ_CLIENT_MOVEMENT_SYNC,
    ClientMovement,
    ClientMovementSync,
    MovementStatus,
    movement_status_check,
)
from app.modules.clients.repository import ClientRepository

_MIGRATION = "d3a8f5c21e47_s12_client_movements.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

#: Qualquer coluna cujo nome sugira NOME/DESCRIÇÃO/TEXTO LIVRE. §4.5: a descrição
#: do lançamento (`cObservacoes` no Omie) ecoa nome de fornecedor.
_FORBIDDEN_NAME_LIKE_COLUMNS = (
    "descricao",
    "description",
    "name",
    "nome",
    "supplier_name",
    "customer_name",
    "razao_social",
    "category_name",
    "observacao",
    "observation",
    "notes",
    "label",
    "memo",
    "history",
)


def _load_migration(filename: str) -> ModuleType:
    """Carrega a migration pelo CAMINHO — `alembic/versions/` não é pacote importável."""
    path = _VERSIONS / filename
    spec = importlib.util.spec_from_file_location(f"_migration_{filename[:12]}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModeloEMigrationBatem:
    def test_predicado_do_check_de_status_bate(self) -> None:
        assert movement_status_check() == _load_migration(_MIGRATION)._CK_STATUS

    def test_predicado_do_check_de_competencia_bate(self) -> None:
        assert MOVEMENT_COMPETENCE_CHECK == _load_migration(_MIGRATION)._CK_COMPETENCE

    def test_nomes_das_garantias_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._UQ_MOVEMENT == UQ_CLIENT_MOVEMENT_SOURCE
        assert module._IX_MOVEMENT_COMPETENCE == IX_CLIENT_MOVEMENT_CLIENT_COMPETENCE
        assert module._UQ_MOVEMENT_SYNC == UQ_CLIENT_MOVEMENT_SYNC
        assert f"ck_client_movements_{module._CK_STATUS_LABEL}" == MOVEMENT_STATUS_CONSTRAINT
        assert (
            f"ck_client_movements_{module._CK_COMPETENCE_LABEL}" == MOVEMENT_COMPETENCE_CONSTRAINT
        )

    def test_tamanhos_e_default_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._SOURCE_TYPE_MAX == MAX_MOVEMENT_SOURCE_TYPE_CHARS
        assert module._SOURCE_ID_MAX == MAX_MOVEMENT_SOURCE_ID_CHARS
        assert module._CATEGORY_CODE_MAX == MAX_MOVEMENT_CATEGORY_CODE_CHARS
        assert module._REF_MAX == MAX_MOVEMENT_REF_CHARS
        default_status: str = MovementStatus.PRESENTE.value
        assert default_status == module._DEFAULT_STATUS

    def test_colunas_do_modelo_e_da_migration_batem(self) -> None:
        """Drift de coluna: o conjunto criado pela migration é o do modelo.

        Lido do código-fonte do `upgrade()`, porque o teste não sobe banco. Uma
        coluna nova no modelo sem a migration (ou o inverso) quebra aqui.
        """
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        movements_block = upgrade.split('"client_movements",', 1)[1].split("op.create_index", 1)[0]
        syncs_block = upgrade.split('"client_movement_syncs",', 1)[1]
        for table, block in (
            (ClientMovement.__table__, movements_block),
            (ClientMovementSync.__table__, syncs_block),
        ):
            for column in table.c:
                if column.name in {"created_at", "updated_at"}:
                    assert "*_timestamps()" in block
                    continue
                assert f'"{column.name}"' in block, column.name

    def test_migration_encadeia_no_head_anterior(self) -> None:
        """Uma cabeça só: a S12 nasce em cima da S15 (último head da `main`)."""
        module = _load_migration(_MIGRATION)
        assert module.revision == "d3a8f5c21e47"
        assert module.down_revision == "b8ee8f368914"

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
        """Migration reversível não é opcional: o `downgrade()` desfaz as TRÊS coisas."""
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert 'op.drop_table("client_movements")' in downgrade
        assert 'op.drop_table("client_movement_syncs")' in downgrade
        assert "op.drop_index" in downgrade


class TestModeloDeclaraAsGarantias:
    def test_check_de_status_cobre_os_dois_estados(self) -> None:
        predicate = movement_status_check()
        for status in MovementStatus:
            assert f"'{status.value}'" in predicate
        assert {s.value for s in MovementStatus} == {"presente", "ausente_na_origem"}

    def test_tabela_declara_os_dois_checks(self) -> None:
        por_nome = {
            c.name: c
            for c in ClientMovement.__table__.constraints
            if isinstance(c, CheckConstraint)
        }
        assert str(por_nome[MOVEMENT_STATUS_CONSTRAINT].sqltext) == movement_status_check()
        assert str(por_nome[MOVEMENT_COMPETENCE_CONSTRAINT].sqltext) == MOVEMENT_COMPETENCE_CHECK

    def test_unicidade_e_cliente_tipo_de_origem_e_identificador(self) -> None:
        """A chave do R0 — e o alvo do `ON CONFLICT` do ciclo."""
        unique = next(
            c
            for c in ClientMovement.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_MOVEMENT_SOURCE
        )
        assert [c.name for c in unique.columns] == [
            "client_id",
            "source_type",
            "source_movement_id",
        ]

    def test_indice_e_cliente_mais_competencia(self) -> None:
        index = next(
            i
            for i in ClientMovement.__table__.indexes
            if i.name == IX_CLIENT_MOVEMENT_CLIENT_COMPETENCE
        )
        assert [c.name for c in index.columns] == ["client_id", "competence"]

    def test_estado_da_sincronizacao_e_unico_por_cliente_e_competencia(self) -> None:
        unique = next(
            c
            for c in ClientMovementSync.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_MOVEMENT_SYNC
        )
        assert [c.name for c in unique.columns] == ["client_id", "competence"]

    def test_fks_para_clients_tem_ondelete_declarado(self) -> None:
        for table in (ClientMovement.__table__, ClientMovementSync.__table__):
            fk = next(iter(table.c.client_id.foreign_keys))
            assert fk.column.table.name == "clients"
            assert fk.ondelete == "CASCADE"

    def test_source_type_nao_e_fk_de_conexao(self) -> None:
        """O de-para é do cliente: trocar a conexão não pode levar a base junto."""
        source_type = ClientMovement.__table__.c.source_type
        assert not source_type.foreign_keys
        assert source_type.nullable is False
        referenciadas = {
            fk.column.table.name
            for column in ClientMovement.__table__.c
            for fk in column.foreign_keys
        }
        assert referenciadas == {"clients"}

    def test_nenhuma_coluna_de_nome_descricao_ou_texto_livre(self) -> None:
        columns = set(ClientMovement.__table__.c.keys())
        for forbidden in _FORBIDDEN_NAME_LIKE_COLUMNS:
            assert forbidden not in columns, (
                f"coluna `{forbidden}` na base de movimentos: nome e texto livre da "
                "origem não persistem (§4.5)."
            )

    def test_colunas_sao_codigos_valores_e_situacao(self) -> None:
        """O inventário fechado da tabela — coluna nova entra aqui conscientemente.

        As colunas que só o arquivo tem (descrição cifrada, documento) são da
        Sprint 14, na migration dela.
        """
        assert set(ClientMovement.__table__.c.keys()) == {
            "id",
            "client_id",
            "source_type",
            "source_movement_id",
            "competence",
            "movement_date",
            "amount",
            "category_code",
            "supplier_code",
            "source_account_id",
            "status",
            "last_synced_at",
            "created_at",
            "updated_at",
        }

    def test_dinheiro_e_numeric_14_2_nunca_float(self) -> None:
        amount = ClientMovement.__table__.c.amount
        assert isinstance(amount.type, Numeric)
        assert (amount.type.precision, amount.type.scale) == (14, 2)
        assert amount.type.asdecimal is True
        assert amount.type.python_type is Decimal
        assert amount.nullable is False

    def test_datas_sao_date_puro(self) -> None:
        for name in ("competence", "movement_date"):
            column = ClientMovement.__table__.c[name]
            assert isinstance(column.type, Date), name
            assert column.nullable is False, name

    def test_categoria_fornecedor_e_conta_sao_nulaveis(self) -> None:
        """Categoria nula é o "sem categoria de origem" do R3; conta nula é o arquivo."""
        table = ClientMovement.__table__
        for name in ("category_code", "supplier_code", "source_account_id"):
            assert table.c[name].nullable is True, name
            assert table.c[name].type.python_type is str, name

    def test_carimbos_nascem_vazios(self) -> None:
        for name in ("synced_at", "sync_failed_at"):
            column = ClientMovementSync.__table__.c[name]
            assert column.nullable is True, name
            assert column.server_default is None, name


class TestPurgaNoEncerramento:
    def test_close_client_purge_declara_as_duas_tabelas(self) -> None:
        """A lista declarada é a fonte (§4.12) — o teste de integração prova o efeito."""
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert "delete(ClientMovement)" in source
        assert "delete(ClientMovementSync)" in source
