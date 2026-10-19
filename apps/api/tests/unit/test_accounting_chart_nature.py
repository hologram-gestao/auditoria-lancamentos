"""A natureza da conta derivada da classificação (86e3qhhrr), sem banco.

A regra: o grupo é o primeiro dígito significativo da classificação; 1 devedora, 2 e
3 credora, 4 em diante devedora; sem grupo, `None`. A paridade com o backfill em SQL
da migration é de `tests/integration/test_accounting_chart_omie_export.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.db.models.client_accounting_account import AccountingAccountNature
from app.modules.client_accounting_chart.nature import derive_nature, resolve_nature

D = AccountingAccountNature.DEVEDORA
C = AccountingAccountNature.CREDORA


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("1", D),
        ("1.1.01.001.001", D),
        ("2", C),
        ("2.1.1.01", C),
        ("3.1", C),
        ("4.2.1", D),
        ("5", D),
        ("9.9", D),
        ("01.1", D),
        ("002.1", C),
        ("11201", D),
        (" 3.1", C),
        (None, None),
        ("", None),
        ("0", None),
        ("0.1", None),
        ("A.1", None),
        ("-1", None),
    ],
)
def test_derive_nature(
    classification: str | None, expected: AccountingAccountNature | None
) -> None:
    assert derive_nature(classification) == expected


class TestResolveNature:
    def test_automatica_acompanha_a_classificacao(self) -> None:
        assert (
            resolve_nature(classification="2.1", current="devedora", overridden=False) == "credora"
        )
        assert resolve_nature(classification=None, current="devedora", overridden=False) is None

    def test_escolhida_a_mao_fica(self) -> None:
        assert (
            resolve_nature(classification="2.1", current="devedora", overridden=True) == "devedora"
        )


def test_a_migration_e_o_modelo_usam_o_mesmo_vocabulario() -> None:
    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    path = next(versions.glob("5d8e2b7c4a91_*.py"))
    spec = importlib.util.spec_from_file_location("mig_nature", path)
    assert spec is not None
    assert spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    # O backfill escreve os MESMOS valores do enum, e só os dois.
    assert "'credora'" in mig._BACKFILL
    assert "'devedora'" in mig._BACKFILL
    assert "IN ('2', '3')" in mig._BACKFILL
    assert {m.value for m in AccountingAccountNature} == {"devedora", "credora"}
