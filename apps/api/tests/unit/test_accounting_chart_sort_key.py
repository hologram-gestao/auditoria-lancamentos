"""A chave da ORDEM DA CLASSIFICAÇÃO do plano contábil (86e3n70p9).

O que este módulo afirma:
  - `chart_sort_key` é a função PURA única: `1.1.10` sai DEPOIS de `1.1.2`; `1 <
    1.1 < 1.1.1`; código `10` depois de `9`; segmento com letra vai em minúsculas e
    ordena depois dos numéricos do mesmo nível; segmento mais largo que 6 nunca é
    truncado;
  - a chave cabe na coluna no pior caso que os tetos de classificação e código
    permitem;
  - o modelo e a migration `b2f7c9e41d06` concordam (largura da coluna, nome do
    índice, largura do segmento), e a migration não importa código da app.

A paridade Python x SQL do backfill é provada contra Postgres em
`tests/integration/test_migrations.py` (`TestOrdemDoPlanoContabilRoundTrip`).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Index

from app.db.models import (
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
            ("1.1.10", "7", "000001.000001.000010"),
            ("1.1.2", "8", "000001.000001.000002"),
            ("1", "1", "000001"),
            ("1.1.1.02.001", "649", "000001.000001.000001.000002.000001"),
            # Sem classificação, a MESMA regra sobre o código reduzido.
            (None, "10", "000010"),
            ("", "9", "000009"),
            (None, "2.04.78", "000002.000004.000078"),
            # Segmento com letra: minúsculas, como está.
            ("1.1.A", "7", "000001.000001.a"),
            (None, "AB-12", "ab-12"),
            # Mais largo que a largura: nunca truncado.
            ("1.1234567", "7", "000001.1234567"),
            # Segmento vazio fica vazio (a mesma coisa que o SQL faz).
            ("1..2", "7", "000001..000002"),
        ],
    )
    def test_regra(self, classification: str | None, code: str, expected: str) -> None:
        assert chart_sort_key(classification, code) == expected

    def test_a_ordem_de_texto_da_chave_e_a_ordem_da_classificacao(self) -> None:
        """`1.1.10` depois de `1.1.2`; `1 < 1.1 < 1.1.1`; `10` depois de `9`; letra
        depois de número no mesmo nível."""
        classifications = ["1.1.10", "1.1.2", "1.1.1", "1", "1.1", "2", "1.1.a", "1.10"]
        ordered = sorted(classifications, key=lambda c: chart_sort_key(c, "x"))
        assert ordered == ["1", "1.1", "1.1.1", "1.1.2", "1.1.10", "1.1.a", "1.10", "2"]

        codes = ["10", "9", "101", "11", "1"]
        assert sorted(codes, key=lambda c: chart_sort_key(None, c)) == ["1", "9", "10", "11", "101"]

    def test_digito_e_so_ascii(self) -> None:
        """`str.isdigit` aceita `²` e dígitos de outros alfabetos; o `lpad` do SQL não.
        As duas fontes têm de concordar: o que não é `0-9` vai como letra."""
        assert chart_sort_key("1.²", "7") == "000001.²"
        assert chart_sort_key("1.٣", "7") == "000001.٣"

    def test_pior_caso_cabe_na_coluna(self) -> None:
        """40 caracteres de classificação viram no máximo 20 segmentos de 1 dígito."""
        worst_classification = ".".join(["1"] * 20) + "1"  # 40 caracteres
        assert len(worst_classification) == MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS
        worst_code = ".".join(["1"] * 10) + "1"  # 20 caracteres
        assert len(worst_code) == MAX_ACCOUNTING_ACCOUNT_CODE_CHARS
        assert (
            len(chart_sort_key(worst_classification, "x")) <= MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        )
        assert len(chart_sort_key(None, worst_code)) <= MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        # Classificação mais larga que a coluna seria recusada antes (`classificacao_longa`),
        # mas um segmento largo NÃO cresce: a largura só preenche.
        assert len(chart_sort_key("1" * 40, "x")) == 40


class TestModeloEMigrationBatem:
    def test_coluna_e_indice_no_modelo(self) -> None:
        table = ClientAccountingAccount.__table__
        column = table.c.sort_key
        assert column.nullable is True
        assert column.type.length == MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        index = next(
            i
            for i in table.indexes
            if isinstance(i, Index) and i.name == IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY
        )
        assert [c.name for c in index.columns] == ["client_id", "sort_key"]

    def test_snapshots_da_migration_batem(self) -> None:
        mig = _load_migration()
        assert mig._SORT_KEY_MAX == MAX_ACCOUNTING_ACCOUNT_SORT_KEY_CHARS
        assert mig._SORT_SEGMENT_WIDTH == SORT_SEGMENT_WIDTH
        assert mig._IX_CLIENT_SORT_KEY == IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY
        assert len(IX_ACCOUNTING_ACCOUNT_CLIENT_SORT_KEY) <= 63
        assert mig.revision == "b2f7c9e41d06"
        assert mig.down_revision == "d881eabdceb7"

    def test_backfill_e_idempotente_e_segue_a_mesma_fonte(self) -> None:
        mig = _load_migration()
        assert "WHERE sort_key IS NULL" in mig._BACKFILL
        assert "COALESCE(NULLIF(classification, ''), code)" in mig._BACKFILL
        assert "'^[0-9]+$'" in mig._BACKFILL, "dígito é só ASCII, como no Python"
        assert "greatest(length(seg), 6)" in mig._BACKFILL, "lpad nunca encurta"

    def test_downgrade_e_real_e_a_migration_nao_importa_a_app(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        downgrade = source.split("def downgrade()", 1)[1]
        assert "op.drop_index(" in downgrade
        assert "op.drop_column(" in downgrade
        assert not re.search(r"^(from|import) app", source, re.MULTILINE)
