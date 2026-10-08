"""O vocabulário do FLUXO PREVISTO da carteira — fonte ÚNICA das faixas (86e3k1q4g).

O aging (`aging.py`) olha para TRÁS: há quantos dias o título venceu. O fluxo
previsto olha para a FRENTE: em quantos dias o título em aberto vence. É o que
o painel do cliente desenha como "o que entra e o que sai nas próximas
semanas" — soma de títulos por faixa de vencimento, nunca saldo de conta (a
plataforma não lê saldo).

**A partição é EXATA, e isso é uma asserção de teste, não um comentário.**
Com `dias = (vencimento - hoje)`:

    dias < 0     → `vencidos`  (o MESMO "vencido" do aging: `due_date < hoje`)
    0  … 7       → `ate_7`     (vence hoje inclusive)
    8  … 30      → `8_30`
    31 … 60      → `31_60`
    61 … 90      → `61_90`
    dias >= 91   → `90_mais`

As seis faixas cobrem todos os inteiros sem sobreposição e sem buraco, então a
soma delas é o total em aberto do `/summary` para cada lado, sempre. E
`vencidos` é o total vencido do aging, pela mesma régua (`due_date < hoje`).

**A referência é a data do SERVIDOR**, a mesma do aging: deixar o navegador
escolher o "hoje" poria o mesmo título em faixas diferentes para duas pessoas.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class FlowBucket(StrEnum):
    """Faixa de vencimento do fluxo previsto — enum FECHADO.

    A ordem de declaração é a ordem da resposta e do gráfico.
    """

    VENCIDOS = "vencidos"
    ATE_7 = "ate_7"
    D8_30 = "8_30"
    D31_60 = "31_60"
    D61_90 = "61_90"
    D90_MAIS = "90_mais"


#: Limites de cada faixa em DIAS ATÉ O VENCIMENTO, **inclusivos nos dois lados**.
#: `None` = sem limite daquele lado. `vencidos` é tudo abaixo de zero; `90_mais`
#: não tem teto. Repositório (SQL) e `bucket_for_days_until_due` (Python) leem
#: DAQUI: dois `BETWEEN` escritos à mão divergiriam num `>` trocado por `>=`.
FLOW_BUCKET_BOUNDS: Final[dict[FlowBucket, tuple[int | None, int | None]]] = {
    FlowBucket.VENCIDOS: (None, -1),
    FlowBucket.ATE_7: (0, 7),
    FlowBucket.D8_30: (8, 30),
    FlowBucket.D31_60: (31, 60),
    FlowBucket.D61_90: (61, 90),
    FlowBucket.D90_MAIS: (91, None),
}


def bucket_for_days_until_due(days_until_due: int) -> FlowBucket:
    """A faixa de um título, em Python — a MESMA partição que o SQL aplica.

    Existe para o teste provar o particionamento nos limites sem banco; a rota
    agrega no banco a partir dos mesmos `FLOW_BUCKET_BOUNDS`.
    """
    for bucket, (low, high) in FLOW_BUCKET_BOUNDS.items():
        if (low is None or days_until_due >= low) and (high is None or days_until_due <= high):
            return bucket
    # Inalcançável: as faixas cobrem todos os inteiros. O `raise` existe para o
    # mypy e para o dia em que alguém abrir um buraco nos limites.
    raise AssertionError(f"{days_until_due} dias sem faixa")  # pragma: no cover
