"""Heurístico de encargos de fatura de cartão (FRONT 1.8 → backend, 86e2u513f).

Encargos = IOF, juros, multa identificados pela descrição (case-insensitive,
substring). A regra morava no front (`isChargeDescription`) e veio para
`totals.py` junto com a soma — estes casos são o port 1:1 do teste que existia
em `summary-tab.test.ts`, para a mudança de lugar não mudar o comportamento.
"""

from __future__ import annotations

import pytest

from app.modules.reconciliations.totals import is_charge_description

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "description",
    [
        "IOF sobre compra internacional",
        "Juros rotativo",
        "JUROS DE MORA",
        "Multa por atraso",
        "iof",
    ],
)
def test_reconhece_encargo(description: str) -> None:
    assert is_charge_description(description) is True


@pytest.mark.parametrize(
    "description",
    ["Mercado Livre", "Posto Shell", "Estorno compra", "Netflix.com"],
)
def test_nao_e_encargo(description: str) -> None:
    assert is_charge_description(description) is False
