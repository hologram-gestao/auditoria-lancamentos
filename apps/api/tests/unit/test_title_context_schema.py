"""As garantias do contexto de título têm DUAS fontes: modelo e migration (BACK 15.1).

O autogenerate do Alembic **não** compara CHECK constraint — então nada avisaria
se o predicado de `context_type` mudasse num lado só, e o banco passaria a
proteger outra coisa. Mesmo padrão de `test_client_titles_schema.py`.

Este módulo também trava as leis que o PRD (CONTEXT.md, Sprint 15) chama de
invariante:

1. **append-only** — sem `updated_at` (nunca há UPDATE) e os três FKs com
   `ondelete` declarado;
2. **cifrado** — o texto livre nunca fica em claro, ao contrário de
   `client_titles` (que só guarda código).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, Text

from app.db.models import (
    CONTEXT_TYPES_NOT_DELINQUENT,
    IX_TITLE_CONTEXT_TITLE_ID_CREATED_AT,
    TITLE_CONTEXT_TYPE_CONSTRAINT,
    TitleContext,
    TitleContextType,
    title_context_type_check,
)

_MIGRATION = "b8ee8f368914_s15_title_contexts.py"


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
    def test_predicado_do_check_bate(self) -> None:
        assert title_context_type_check() == _load_migration(_MIGRATION)._CK_TYPE

    def test_nome_da_garantia_bate(self) -> None:
        module = _load_migration(_MIGRATION)
        assert f"ck_title_contexts_{module._CK_TYPE_LABEL}" == TITLE_CONTEXT_TYPE_CONSTRAINT

    def test_indice_do_historico_bate(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._IX_TITLE_CONTEXT_TITLE_CREATED == IX_TITLE_CONTEXT_TITLE_ID_CREATED_AT

    def test_migration_encadeia_no_head_anterior(self) -> None:
        """Uma cabeça só: a S15 nasce em cima da S11, nunca em paralelo."""
        module = _load_migration(_MIGRATION)
        assert module.revision == "b8ee8f368914"
        assert module.down_revision == "c2d9e7f41ab5"

    def test_downgrade_e_real(self) -> None:
        """Migration reversível não é opcional (§4 do primer).

        O `downgrade()` precisa desfazer as TRÊS coisas que o `upgrade()` fez —
        os dois índices e a tabela. Um `pass` aqui passaria despercebido até o
        dia do rollback.
        """
        source = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
        ).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert 'op.drop_table("title_contexts")' in downgrade
        assert downgrade.count("op.drop_index") == 2

    def test_fks_da_migration_tem_ondelete_declarado(self) -> None:
        source = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
        ).read_text(encoding="utf-8")
        upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        assert upgrade.count('ondelete="CASCADE"') == 2
        assert upgrade.count('ondelete="RESTRICT"') == 1


class TestModeloDeclaraAsGarantias:
    def test_check_cobre_todos_os_valores_do_enum(self) -> None:
        predicate = title_context_type_check()
        for tipo in TitleContextType:
            assert f"'{tipo.value}'" in predicate

    def test_tabela_declara_o_check(self) -> None:
        """A NAMING_CONVENTION do `Base` já expande o rótulo para o nome final."""
        por_nome = {
            c.name: c for c in TitleContext.__table__.constraints if isinstance(c, CheckConstraint)
        }
        assert str(por_nome[TITLE_CONTEXT_TYPE_CONSTRAINT].sqltext) == title_context_type_check()

    def test_indice_do_historico_e_titulo_mais_data(self) -> None:
        """Sem ele, o histórico "mais recente primeiro" varre a tabela."""
        index = next(
            i
            for i in TitleContext.__table__.indexes
            if i.name == IX_TITLE_CONTEXT_TITLE_ID_CREATED_AT
        )
        assert [c.name for c in index.columns] == ["title_id", "created_at"]

    def test_fk_para_clients_e_cascade(self) -> None:
        column = TitleContext.__table__.c.client_id
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "clients"
        assert fk.ondelete == "CASCADE"

    def test_fk_para_client_titles_e_cascade(self) -> None:
        column = TitleContext.__table__.c.title_id
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "client_titles"
        assert fk.ondelete == "CASCADE"

    def test_fk_para_users_e_restrict(self) -> None:
        column = TitleContext.__table__.c.author_id
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "users"
        assert fk.ondelete == "RESTRICT"

    def test_sem_updated_at_a_tabela_e_append_only(self) -> None:
        """Nenhum caminho do service faz `UPDATE` — `updated_at` mentiria."""
        assert "updated_at" not in TitleContext.__table__.c

    def test_texto_e_sempre_cifrado_nunca_em_claro(self) -> None:
        """Ao contrário de `client_titles` (só código), o texto livre é PII em
        potencial e nasce cifrado — nunca uma coluna de texto em claro."""
        columns = set(TitleContext.__table__.c.keys())
        assert "text_encrypted" in columns
        assert "text_iv" in columns
        assert "text" not in columns
        text_column = TitleContext.__table__.c.text_encrypted
        assert isinstance(text_column.type, Text)
        assert text_column.nullable is False

    def test_perda_provavel_e_o_unico_tipo_que_conta_como_inadimplencia(self) -> None:
        """R4: os outros cinco tiram o título do grupo de inadimplência real."""
        assert TitleContextType.PERDA_PROVAVEL not in CONTEXT_TYPES_NOT_DELINQUENT
        assert len(CONTEXT_TYPES_NOT_DELINQUENT) == len(TitleContextType) - 1

    def test_colunas_sao_o_inventario_fechado(self) -> None:
        """Coluna nova entra aqui conscientemente."""
        assert set(TitleContext.__table__.c.keys()) == {
            "id",
            "client_id",
            "title_id",
            "context_type",
            "text_encrypted",
            "text_iv",
            "author_id",
            "created_at",
        }
