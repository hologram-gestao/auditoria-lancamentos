"""A chave da ORDEM DA CLASSIFICAÇÃO do plano contábil (86e3n70p9).

O que este módulo afirma:
  - `chart_sort_key` é a função PURA única. Com classificação, a chave é a PRÓPRIA
    classificação como texto (a ordem do Domínio): sob o mesmo pai, a família `00001…`
    vem inteira antes da família `001…`, nunca intercalada; `1 < 1.1 < 1.1.1`; e
    `1.1.10` vem antes de `1.1.2`, como no Domínio. Sem classificação, o código
    reduzido com os segmentos numéricos preenchidos: `9` antes de `10`;
  - as duas fixtures do Domínio (`plano_esperado.csv`) saem na ORDEM DO ARQUIVO quando
    ordenadas pela chave — sem banco, a mesma prova que a integração faz pela rota;
  - a chave cabe na coluna;
  - o modelo e a migration `b2f7c9e41d06` concordam (largura, collation `C`, nome do
    índice, largura do segmento), e a migration não importa código da app.

A paridade Python x SQL do backfill é provada contra Postgres em
`tests/integration/test_migrations.py` (`TestOrdemDoPlanoContabilRoundTrip`).
"""

from __future__ import annotations

import csv
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Index

from app.db.models import (
    ACCOUNTING_ACCOUNT_SORT_KEY_COLLATION,
    IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY,
    MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS,
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS,
    ClientAccountingAccount,
)
from app.modules.client_accounting_chart import chart_sort_key
from app.modules.client_accounting_chart.sort_key import SORT_SEGMENT_WIDTH

_MIGRATION = "b2f7c9e41d06_s16_accounting_chart_sort_key.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
#: As duas amostras reais (anonimizadas) do plano como o Domínio o lista.
_DOMINIO_EXPECTED = (
    _FIXTURES / "accounting_chart_dominio" / "plano_esperado.csv",
    _FIXTURES / "accounting_chart_dominio_xls" / "plano_esperado.csv",
)


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_b2f7c9e41d06", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestChaveDeOrdem:
    @pytest.mark.parametrize(
        ("classification", "code", "expected"),
        [
            # Com classificação: a PRÓPRIA classificação, sem preenchimento nem caixa.
            ("1.1.10", "7", "1.1.10"),
            ("1.1.2", "8", "1.1.2"),
            ("1", "1", "1"),
            ("1.1.1.02.001", "649", "1.1.1.02.001"),
            ("1.1.2.01.00001", "773", "1.1.2.01.00001"),
            ("1.1.A", "7", "1.1.A"),
            # Sem classificação, o código reduzido com os segmentos numéricos preenchidos.
            (None, "10", "000010"),
            ("", "9", "000009"),
            (None, "2.04.78", "000002.000004.000078"),
            # Segmento com letra: minúsculas, como está.
            (None, "AB-12", "ab-12"),
            # Mais largo que a largura: nunca truncado.
            (None, "1234567", "1234567"),
            # Segmento vazio fica vazio (a mesma coisa que o SQL faz).
            (None, "1..2", "000001..000002"),
        ],
    )
    def test_regra(self, classification: str | None, code: str, expected: str) -> None:
        assert chart_sort_key(classification, code) == expected

    def test_familias_sob_o_mesmo_pai_nao_se_intercalam(self) -> None:
        """O caso do plano do Gabriel: sob `1.1.2.01`, `00001…` e `001…` são famílias
        distintas, e o Domínio lista uma inteira antes da outra. O preenchimento
        numérico (a primeira versão) intercalava `00001, 001, 00002, 002`."""
        classifications = [
            "1.1.2.01.002",
            "1.1.2.01.00002",
            "1.1.2.01",
            "1.1.2.01.001",
            "1.1.2.01.00001",
        ]
        ordered = sorted(classifications, key=lambda c: chart_sort_key(c, "x"))
        assert ordered == [
            "1.1.2.01",
            "1.1.2.01.00001",
            "1.1.2.01.00002",
            "1.1.2.01.001",
            "1.1.2.01.002",
        ]

    def test_hierarquia_e_texto_como_no_dominio(self) -> None:
        """Sintética antes das filhas; `1.1.10` antes de `1.1.2` (texto, como o Domínio).
        Planilha à mão usa largura fixa por nível: `1.1.02` antes de `1.1.10`."""
        classifications = ["1.1.10", "1.1.2", "1.1.1", "1", "1.1", "2", "1.1.02"]
        ordered = sorted(classifications, key=lambda c: chart_sort_key(c, "x"))
        assert ordered == ["1", "1.1", "1.1.02", "1.1.1", "1.1.10", "1.1.2", "2"]

    def test_codigo_sem_classificacao_ordena_como_numero(self) -> None:
        codes = ["10", "9", "101", "11", "1"]
        assert sorted(codes, key=lambda c: chart_sort_key(None, c)) == ["1", "9", "10", "11", "101"]

    def test_digito_do_codigo_e_so_ascii(self) -> None:
        """`str.isdigit` aceita `²` e dígitos de outros alfabetos; o `lpad` do SQL não.
        As duas fontes têm de concordar: o que não é `0-9` vai como letra."""
        assert chart_sort_key(None, "1.²") == "000001.²"
        assert chart_sort_key(None, "1.٣") == "000001.٣"

    @pytest.mark.parametrize("expected_csv", _DOMINIO_EXPECTED, ids=["xlsx", "xls"])
    def test_as_amostras_do_dominio_saem_na_ordem_do_arquivo(self, expected_csv: Path) -> None:
        """O guarda contra uma regra "mais inteligente": embaralhada e reordenada pela
        chave, cada amostra volta à ordem em que o Domínio a exportou (coluna `linha`)."""
        with expected_csv.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle, delimiter=";"))
        file_order = [r["codigo_reduzido"] for r in sorted(rows, key=lambda r: int(r["linha"]))]
        shuffled = sorted(rows, key=lambda r: r["codigo_reduzido"])
        by_key = sorted(
            shuffled,
            key=lambda r: (
                chart_sort_key(r["classificacao"] or None, r["codigo_reduzido"]),
                r["codigo_reduzido"],
            ),
        )
        assert [r["codigo_reduzido"] for r in by_key] == file_order

    def test_pior_caso_cabe_na_coluna(self) -> None:
        worst_classification = "1" * MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS
        assert (
            len(chart_sort_key(worst_classification, "x")) <= MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        )
        worst_code = ".".join(["1"] * 10) + "1"  # 20 caracteres, 11 segmentos
        assert len(worst_code) == MAX_ACCOUNTING_ACCOUNT_CODE_CHARS
        assert len(chart_sort_key(None, worst_code)) <= MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS


class TestModeloEMigrationBatem:
    def test_coluna_e_indice_no_modelo(self) -> None:
        table = ClientAccountingAccount.__table__
        column = table.c.sort_key
        assert column.nullable is True
        assert column.type.length == MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        assert column.type.collation == ACCOUNTING_ACCOUNT_SORT_KEY_COLLATION == "C", (
            "a collation do sistema ignora a pontuação e embaralha a classificação"
        )
        index = next(
            i
            for i in table.indexes
            if isinstance(i, Index) and i.name == IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY
        )
        assert [c.name for c in index.columns] == ["client_id", "sort_key"]

    def test_snapshots_da_migration_batem(self) -> None:
        mig = _load_migration()
        assert mig._SORT_KEY_MAX == MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        assert mig._SORT_KEY_COLLATION == ACCOUNTING_ACCOUNT_SORT_KEY_COLLATION
        assert mig._SORT_SEGMENT_WIDTH == SORT_SEGMENT_WIDTH
        assert mig._IX_CLIENT_SORT_KEY == IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY
        assert len(IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY) <= 63
        assert mig.revision == "b2f7c9e41d06"
        assert mig.down_revision == "d881eabdceb7"

    def test_backfill_e_idempotente_e_segue_a_mesma_fonte(self) -> None:
        mig = _load_migration()
        assert "WHERE sort_key IS NULL" in mig._BACKFILL
        assert "COALESCE(NULLIF(classification, ''), " in mig._BACKFILL, (
            "a classificação entra CRUA; só o código é preenchido"
        )
        assert "string_to_array(code, '.')" in mig._BACKFILL
        assert "string_to_array(classification" not in mig._BACKFILL
        assert "'^[0-9]+$'" in mig._BACKFILL, "dígito é só ASCII, como no Python"
        assert "greatest(length(seg), 6)" in mig._BACKFILL, "lpad nunca encurta"

    def test_downgrade_e_real_e_a_migration_nao_importa_a_app(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert "op.drop_index(" in downgrade
        assert "op.drop_column(" in downgrade
        assert not re.search(r"^(from|import) app", source, re.MULTILINE)
