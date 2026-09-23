"""As garantias do plano de contas têm DUAS fontes: modelo e migration (BACK 10.1).

O autogenerate do Alembic **não** compara CHECK constraint — então nada avisaria
se o predicado do `status` ou o nome da UNIQUE mudasse num lado só, e o banco
passaria a proteger outra coisa. Mesmo padrão de
`test_client_connection_schema.py` e `test_organization_schema.py`.

Este módulo também trava a regra que o PRD chama de invariante e o primer §4.5
chama de lei: **nenhuma coluna de nome ou descrição**. É o teste que substitui o
"grep no modelo" do critério de aceite, e que falha se alguém acrescentar
`descricao`, `descricao_dre` ou `conta_contabil_tag` "só para a tela não precisar
do cache".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.models import (
    CHART_OF_ACCOUNTS_STATUS_CONSTRAINT,
    MAX_ACCOUNT_CODE_CHARS,
    UQ_CHART_OF_ACCOUNTS_CLIENT_CODE,
    ChartOfAccountsStatus,
    Client,
    ClientChartOfAccount,
    chart_of_accounts_status_check,
)

_MIGRATION = "f1b7a52c8e60_s10_client_chart_of_accounts.py"

#: As duas colunas de estado da sincronização que a migration acrescenta em
#: `clients`. Os nomes são o contrato entre a 10.2 (quem carimba) e a 10.3
#: (quem exibe).
_CLIENT_SYNC_COLUMNS = (
    "chart_of_accounts_synced_at",
    "chart_of_accounts_sync_failed_at",
)

#: Qualquer coluna cujo nome sugira NOME/DESCRIÇÃO. A §4.5 mantém nome de
#: categoria e de conta fora do disco em claro; a tela resolve pelo cache.
_FORBIDDEN_NAME_LIKE_COLUMNS = (
    "descricao",
    "description",
    "name",
    "nome",
    "descricao_dre",
    "dre_description",
    "conta_contabil_tag",
    "tag_conta_contabil",
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
        assert chart_of_accounts_status_check() == _load_migration(_MIGRATION)._CK_STATUS

    def test_nomes_das_garantias_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._UQ_CHART_OF_ACCOUNTS == UQ_CHART_OF_ACCOUNTS_CLIENT_CODE
        assert (
            f"ck_client_chart_of_accounts_{module._CK_STATUS_LABEL}"
            == CHART_OF_ACCOUNTS_STATUS_CONSTRAINT
        )

    def test_tamanhos_e_default_batem(self) -> None:
        module = _load_migration(_MIGRATION)
        assert module._CODE_MAX == MAX_ACCOUNT_CODE_CHARS
        default_status: str = ChartOfAccountsStatus.ATIVA.value
        assert default_status == module._DEFAULT_STATUS

    def test_colunas_de_estado_do_sync_sao_as_do_modelo(self) -> None:
        """O downgrade derruba EXATAMENTE as duas colunas que o upgrade criou."""
        module = _load_migration(_MIGRATION)
        assert tuple(module._CLIENT_SYNC_COLUMNS) == _CLIENT_SYNC_COLUMNS
        for name in _CLIENT_SYNC_COLUMNS:
            assert name in Client.__table__.c, name

    def test_downgrade_e_real(self) -> None:
        """Migration reversível não é opcional (§4 do primer).

        O `downgrade()` precisa desfazer as duas coisas que o `upgrade()` fez —
        um `pass` aqui passaria despercebido até o dia do rollback.
        """
        source = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
        ).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert 'op.drop_table("client_chart_of_accounts")' in downgrade
        assert "op.drop_column" in downgrade


class TestModeloDeclaraAsGarantias:
    def test_check_de_status_cobre_todos_os_valores_do_enum(self) -> None:
        """Os literais do CHECK são os valores do enum — mudar um sem o outro é drift."""
        predicate = chart_of_accounts_status_check()
        for status in ChartOfAccountsStatus:
            assert f"'{status.value}'" in predicate
        assert {s.value for s in ChartOfAccountsStatus} == {
            "ativa",
            "inativa",
            "ausente_na_origem",
        }

    def test_tabela_declara_o_check_de_status(self) -> None:
        """A NAMING_CONVENTION do `Base` já expande o rótulo para o nome final."""
        check = next(
            c
            for c in ClientChartOfAccount.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == CHART_OF_ACCOUNTS_STATUS_CONSTRAINT
        )
        assert str(check.sqltext) == chart_of_accounts_status_check()

    def test_unicidade_e_cliente_mais_codigo(self) -> None:
        """É a identidade da linha no tenant — e o alvo do `ON CONFLICT` do upsert."""
        unique = next(
            c
            for c in ClientChartOfAccount.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CHART_OF_ACCOUNTS_CLIENT_CODE
        )
        assert [c.name for c in unique.columns] == ["client_id", "category_code"]

    def test_fk_para_clients_tem_ondelete_declarado(self) -> None:
        column = ClientChartOfAccount.__table__.c.client_id
        fk = next(iter(column.foreign_keys))
        assert fk.column.table.name == "clients"
        assert fk.ondelete == "CASCADE"

    def test_nenhuma_coluna_de_nome_ou_descricao(self) -> None:
        """§4.5: nome de categoria e de conta NÃO encostam no disco em claro.

        A tabela guarda código, situação e flags. O nome vem do cache de 6h que
        já serve a tela de revisão — é o mesmo caminho, e é isso que impede a
        tela nova de divergir da antiga.
        """
        columns = set(ClientChartOfAccount.__table__.c.keys())
        for forbidden in _FORBIDDEN_NAME_LIKE_COLUMNS:
            assert forbidden not in columns, (
                f"coluna `{forbidden}` no plano de contas: nome de categoria/conta "
                "não persiste em claro (§4.5). O nome é resolvido em runtime."
            )

    def test_colunas_sao_codigos_flags_e_situacao(self) -> None:
        """O inventário fechado da tabela — coluna nova entra aqui conscientemente."""
        assert set(ClientChartOfAccount.__table__.c.keys()) == {
            "id",
            "client_id",
            "category_code",
            "parent_code",
            "dre_code",
            "dre_level",
            "dre_sign",
            "conta_contabil_code",
            "totalizadora",
            "transferencia",
            "nao_exibir",
            "status",
            "synced_at",
            "created_at",
            "updated_at",
        }

    def test_destino_e_hierarquia_sao_nulaveis(self) -> None:
        """NULL em `dre_code` é "sem destino declarado" — estado legítimo, não erro."""
        table = ClientChartOfAccount.__table__
        assert table.c.dre_code.nullable is True
        assert table.c.parent_code.nullable is True
        assert table.c.conta_contabil_code.nullable is True
        assert table.c.category_code.nullable is False

    def test_flags_nao_sao_nulaveis(self) -> None:
        """Três estados numa flag booleana é um estado a mais do que a origem tem."""
        table = ClientChartOfAccount.__table__
        for flag in ("totalizadora", "transferencia", "nao_exibir"):
            assert table.c[flag].nullable is False, flag

    def test_situacao_nao_tem_flag_booleana_gemea(self) -> None:
        """`inativa` é VALOR de `status`, não coluna.

        As duas juntas seriam o mesmo fato em dois lugares, com um `UPDATE`
        capaz de deixá-las discordando.
        """
        assert "inativa" not in ClientChartOfAccount.__table__.c
        assert ChartOfAccountsStatus.INATIVA.value == "inativa"

    def test_estado_do_sync_mora_no_cliente_e_nasce_vazio(self) -> None:
        """Nunca sincronizou = NULL nas duas. Não derivar de `MAX(synced_at)`."""
        for name in _CLIENT_SYNC_COLUMNS:
            assert Client.__table__.c[name].nullable is True, name
