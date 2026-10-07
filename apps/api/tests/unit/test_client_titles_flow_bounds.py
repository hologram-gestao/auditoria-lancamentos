"""As faixas do fluxo previsto particionam a carteira em aberto EXATAMENTE (86e3k1q4g).

A promessa da tela é que as seis faixas somam o total em aberto de cada lado e
que `vencidos` é o total vencido do aging. Ela só vale se os limites de
`flow.py` não se sobrepuserem nem deixarem buraco, e se `vencidos` for a mesma
régua do aging (`due_date < hoje`). Este teste é sobre o VOCABULÁRIO; o SQL é
provado contra o banco em `tests/integration/test_client_titles_flow.py`.
"""

from __future__ import annotations

from app.modules.client_titles.aging import AgingBucket, bucket_for_days
from app.modules.client_titles.flow import (
    FLOW_BUCKET_BOUNDS,
    FlowBucket,
    bucket_for_days_until_due,
)


def test_sao_seis_faixas_na_ordem_do_grafico() -> None:
    assert [b.value for b in FlowBucket] == [
        "vencidos",
        "ate_7",
        "8_30",
        "31_60",
        "61_90",
        "90_mais",
    ]
    assert list(FLOW_BUCKET_BOUNDS) == list(FlowBucket)


def test_todo_dia_cai_em_exatamente_uma_faixa() -> None:
    """Sem sobreposição e sem buraco, de 400 dias vencido a 400 dias à frente."""
    for days in range(-400, 401):
        casam = [
            bucket
            for bucket, (low, high) in FLOW_BUCKET_BOUNDS.items()
            if (low is None or days >= low) and (high is None or days <= high)
        ]
        assert len(casam) == 1, (days, casam)
        assert bucket_for_days_until_due(days) is casam[0]


def test_limites_de_cada_faixa_hoje_inclusive() -> None:
    """Os dias exatos de virada: vencer HOJE ainda é `ate_7`, ontem é `vencidos`."""
    esperado = {
        -1: FlowBucket.VENCIDOS,
        0: FlowBucket.ATE_7,
        7: FlowBucket.ATE_7,
        8: FlowBucket.D8_30,
        30: FlowBucket.D8_30,
        31: FlowBucket.D31_60,
        60: FlowBucket.D31_60,
        61: FlowBucket.D61_90,
        90: FlowBucket.D61_90,
        91: FlowBucket.D90_MAIS,
    }
    for days, bucket in esperado.items():
        assert bucket_for_days_until_due(days) is bucket, days


def test_vencidos_e_o_mesmo_corte_do_aging() -> None:
    """`vencidos` no fluxo ⇔ qualquer balde de atraso no aging, dia a dia."""
    for days_until_due in range(-200, 201):
        no_fluxo = bucket_for_days_until_due(days_until_due) is FlowBucket.VENCIDOS
        no_aging = bucket_for_days(-days_until_due) is not AgingBucket.A_VENCER
        assert no_fluxo == no_aging, days_until_due
