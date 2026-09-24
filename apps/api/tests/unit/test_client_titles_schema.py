"""As garantias da carteira têm DUAS fontes: modelo e migration (BACK 11.1).

O autogenerate do Alembic **não** compara CHECK constraint — então nada avisaria
se o predicado de `status`/`title_type` ou o nome da UNIQUE mudasse num lado só,
e o banco passaria a proteger outra coisa. Mesmo padrão de
`test_chart_of_accounts_schema.py` e `test_organization_schema.py`.

Este módulo também trava as duas leis que o PRD chama de invariante:

1. **nenhuma coluna de nome** — nem do devedor, nem do fornecedor, nem da
   categoria, nem `observacao` (texto livre de terceiro é por onde nome de
   pessoa entra no banco sem ninguém decidir isso). É o teste que substitui o
   "grep no modelo" do critério de aceite;
2. **título não se apaga** — os estados de saída existem e são dois, e nenhuma
   das duas saídas é a ausência da linha.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from sqlalchemy import BigInteger, CheckConstraint, Date, Numeric, UniqueConstraint

from app.db.models import (
    CLOSED_TITLE_STATUSES,
    IX_CLIENT_TITLE_CLIENT_DUE_DATE,
    MAX_TITLE_CODE_CHARS,
    MAX_TITLE_DOCUMENT_CHARS,
    MAX_TITLE_EXTERNAL_ID_CHARS,
    TITLE_STATUS_CONSTRAINT,
    TITLE_TYPE_CONSTRAINT,
    UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID,
    Client,
    ClientTitle,
    TitleStatus,
    TitleType,
    title_status_check,
    title_type_check,
)

_MIGRATION = "c2d9e7f41ab5_s11_client_titles.py"

#: As duas colunas de estado da sincronização que a migration acrescenta em
#: `clients`. Os nomes são o contrato entre a 11.2 (quem carimba) e a 11.4/11.5
#: (quem exibe).
_CLIENT_SYNC_COLUMNS = (
    "titles_synced_at",
    "titles_sync_failed_at",
)

#: Qualquer coluna cujo nome sugira NOME/DESCRIÇÃO de pessoa, fornecedor ou
#: categoria. A §4.5 mantém tudo isso fora do disco em claro; a tela resolve em
#: runtime pelo cache que já existe. `observacao`/`descricao` entram na lista
#: porque são texto LIVRE de terceiro — o Omie ecoa nome de fornecedor ali.
_FORBIDDEN_NAME_LIKE_COLUMNS = (
    "descricao",
    "description",
    "name",
    "nome",
    "nome_fornecedor",
    "nome_cliente",
    "supplier_name",
    "customer_name",
    "razao_social",
    "descricao_categoria",
    "category_name",
    "observacao",
    "observation",
    "notes",
    "label",
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
        assert title_status_check() == _load_migration(_MIGRATION)._CK_STATUS

    def test_predicado_do_check_de_tipo_bate(self) -> None:
        assert title_type_check() == _load_migration(_MIGRATION)._CK_TYPE

    def test_nomes_das_garantias_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._UQ_CLIENT_TITLE == UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID
        assert module._IX_CLIENT_TITLE_DUE == IX_CLIENT_TITLE_CLIENT_DUE_DATE
        assert f"ck_client_titles_{module._CK_STATUS_LABEL}" == TITLE_STATUS_CONSTRAINT
        assert f"ck_client_titles_{module._CK_TYPE_LABEL}" == TITLE_TYPE_CONSTRAINT

    def test_tamanhos_e_default_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._EXTERNAL_ID_MAX == MAX_TITLE_EXTERNAL_ID_CHARS
        assert module._CODE_MAX == MAX_TITLE_CODE_CHARS
        assert module._DOCUMENT_MAX == MAX_TITLE_DOCUMENT_CHARS
        default_status: str = TitleStatus.EM_ABERTO.value
        assert default_status == module._DEFAULT_STATUS

    def test_colunas_de_estado_do_sync_sao_as_do_modelo(self) -> None:
        """O downgrade derruba EXATAMENTE as duas colunas que o upgrade criou."""
        module = _load_migration(_MIGRATION)
        assert tuple(module._CLIENT_SYNC_COLUMNS) == _CLIENT_SYNC_COLUMNS
        for name in _CLIENT_SYNC_COLUMNS:
            assert name in Client.__table__.c, name

    def test_migration_encadeia_no_head_anterior(self) -> None:
        """Uma cabeça só: a S11 nasce em cima da S10, nunca em paralelo."""
        module = _load_migration(_MIGRATION)
        assert module.revision == "c2d9e7f41ab5"
        assert module.down_revision == "f1b7a52c8e60"

    def test_downgrade_e_real(self) -> None:
        """Migration reversível não é opcional (§4 do primer).

        O `downgrade()` precisa desfazer as TRÊS coisas que o `upgrade()` fez —
        tabela, índice e colunas. Um `pass` aqui passaria despercebido até o dia
        do rollback.
        """
        source = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
        ).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert 'op.drop_table("client_titles")' in downgrade
        assert "op.drop_index" in downgrade
        assert "op.drop_column" in downgrade


class TestModeloDeclaraAsGarantias:
    def test_check_de_status_cobre_todos_os_valores_do_enum(self) -> None:
        predicate = title_status_check()
        for status in TitleStatus:
            assert f"'{status.value}'" in predicate
        assert {s.value for s in TitleStatus} == {
            "em_aberto",
            "liquidado",
            "ausente_na_origem",
        }

    def test_check_de_tipo_cobre_todos_os_valores_do_enum(self) -> None:
        predicate = title_type_check()
        for tipo in TitleType:
            assert f"'{tipo.value}'" in predicate
        assert {t.value for t in TitleType} == {"a_pagar", "a_receber"}

    def test_tabela_declara_os_dois_checks(self) -> None:
        """A NAMING_CONVENTION do `Base` já expande o rótulo para o nome final."""
        por_nome = {
            c.name: c for c in ClientTitle.__table__.constraints if isinstance(c, CheckConstraint)
        }
        assert str(por_nome[TITLE_STATUS_CONSTRAINT].sqltext) == title_status_check()
        assert str(por_nome[TITLE_TYPE_CONSTRAINT].sqltext) == title_type_check()

    def test_unicidade_e_cliente_mais_identificador_de_origem(self) -> None:
        """É a identidade da linha no tenant — e o alvo do `ON CONFLICT` do ciclo."""
        unique = next(
            c
            for c in ClientTitle.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID
        )
        assert [c.name for c in unique.columns] == ["client_id", "external_id"]

    def test_indice_do_aging_e_cliente_mais_vencimento(self) -> None:
        """Sem ele, o aging e a ordenação por vencimento varrem a tabela."""
        index = next(
            i for i in ClientTitle.__table__.indexes if i.name == IX_CLIENT_TITLE_CLIENT_DUE_DATE
        )
        assert [c.name for c in index.columns] == ["client_id", "due_date"]

    def test_fk_para_clients_tem_ondelete_declarado(self) -> None:
        column = ClientTitle.__table__.c.client_id
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "clients"
        assert fk.ondelete == "CASCADE"

    def test_nenhuma_coluna_de_nome_ou_observacao(self) -> None:
        """§4.5: nome de devedor, fornecedor e categoria NÃO encostam no disco.

        A tabela guarda código, valor, data e situação. O nome vem do
        `clientes_cache`/`ListarCategorias` que já serve a aba de divergências —
        é o mesmo caminho, e é isso que impede a tela nova de divergir da antiga.
        """
        columns = set(ClientTitle.__table__.c.keys())
        for forbidden in _FORBIDDEN_NAME_LIKE_COLUMNS:
            assert forbidden not in columns, (
                f"coluna `{forbidden}` na carteira: nome de devedor/categoria "
                "não persiste em claro (§4.5). O nome é resolvido em runtime."
            )

    def test_colunas_sao_codigos_valores_e_situacao(self) -> None:
        """O inventário fechado da tabela — coluna nova entra aqui conscientemente."""
        assert set(ClientTitle.__table__.c.keys()) == {
            "id",
            "client_id",
            "external_id",
            "title_type",
            "due_date",
            "amount",
            "status",
            "category_code",
            "supplier_code",
            "omie_conta_id",
            "document_number",
            "last_synced_at",
            "created_at",
            "updated_at",
        }

    def test_dinheiro_e_numeric_14_2_nunca_float(self) -> None:
        """§3.4: `Decimal` no Python, `DECIMAL(14,2)` no banco — em todo o caminho."""
        amount = ClientTitle.__table__.c.amount
        assert isinstance(amount.type, Numeric)
        assert (amount.type.precision, amount.type.scale) == (14, 2)
        assert amount.type.asdecimal is True
        assert amount.type.python_type is Decimal
        assert amount.nullable is False

    def test_vencimento_e_date_puro(self) -> None:
        """Vencimento é DIA. Um fuso a mais moveria títulos de balde de aging."""
        due = ClientTitle.__table__.c.due_date
        assert isinstance(due.type, Date)
        assert due.nullable is False

    def test_codigos_numericos_da_origem_sao_bigint(self) -> None:
        """A captura real traz 2624256082 e 2617722760 — acima do teto de INTEGER.

        Verificado em `tests/fixtures/omie/listar_contas_pagar.response.json`
        (`codigo_cliente_fornecedor`, `id_conta_corrente`). Um `Integer` aqui
        estouraria na primeira sincronização real.
        """
        table = ClientTitle.__table__
        for column in ("supplier_code", "omie_conta_id"):
            assert isinstance(table.c[column].type, BigInteger), column
            assert table.c[column].nullable is True, column

    def test_identificador_de_origem_e_texto(self) -> None:
        """Identificador de terceiro não é número nosso (mesma decisão do S9)."""
        external_id = ClientTitle.__table__.c.external_id
        assert external_id.type.python_type is str
        assert external_id.nullable is False

    def test_saida_do_aberto_tem_dois_estados_e_nenhum_deles_apaga(self) -> None:
        """`liquidado` ≠ `ausente_na_origem`: chamar de pago o que sumiu é mentir.

        Colapsar os dois faria a tela afirmar que um título foi quitado quando
        tudo que a plataforma sabe é que ele deixou de aparecer.
        """
        assert {
            TitleStatus.LIQUIDADO,
            TitleStatus.AUSENTE_NA_ORIGEM,
        } == CLOSED_TITLE_STATUSES
        assert TitleStatus.EM_ABERTO not in CLOSED_TITLE_STATUSES

    def test_estado_do_sync_mora_no_cliente_e_nasce_vazio(self) -> None:
        """Nunca sincronizou = NULL nas duas — o baseline 0% do Outcome.

        Não derivar de `MAX(last_synced_at)`: cliente sem nenhum título deixaria
        o MAX em NULL e "carteira vazia" viraria indistinguível de "nunca
        sincronizou", que é justamente a distinção que o R3 exige.
        """
        for name in _CLIENT_SYNC_COLUMNS:
            assert Client.__table__.c[name].nullable is True, name
            assert Client.__table__.c[name].default is None, name
