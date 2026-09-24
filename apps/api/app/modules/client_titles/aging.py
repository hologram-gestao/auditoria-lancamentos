"""O vocabulário do AGING da carteira — fonte ÚNICA dos baldes (Sprint 11).

Os quatro baldes (**1-30, 31-60, 61-90, 90+**) não são invenção nossa: são a
mesma segmentação que o escritório parceiro já faz à mão na planilha que motivou
a sprint (base consolidada de 17/06/2026 — `aging 90+ = 82.865,50` de um total de
`107.413,10` em 148 títulos). Trazer a segmentação de lá é o que permite comparar
o número da plataforma com o número da reunião.

**Por que um módulo só, sem SQL dentro.** Quem precisa dos limites é o filtro da
lista (11.5), a agregação dos baldes (11.4) e o schema de resposta — três
consumidores. Se cada um escrevesse o seu `BETWEEN`, bastaria um `>=` virar `>`
num deles para a soma dos baldes deixar de bater com o total vencido, e ninguém
veria. Aqui mora só o vocabulário e os limites; o SQL que os aplica mora no
repositório, num lugar só.

**A partição é EXATA, e isso é uma asserção de teste, não um comentário.**
Com `dias = (hoje - vencimento)`:

    dias <= 0  → `a_vencer`  (ainda não venceu: entra no total em aberto e
                              **fica fora** dos quatro baldes)
    1  … 30    → `1_30`
    31 … 60    → `31_60`
    61 … 90    → `61_90`
    dias >= 91 → `90_mais`

Os quatro baldes de vencidos cobrem `dias >= 1` sem sobreposição e sem buraco —
por isso `1_30 + 31_60 + 61_90 + 90_mais == total vencido`, sempre.

**A referência é a data corrente do SERVIDOR** (R3), nunca uma data recebida do
cliente: aging é sobre "hoje", e deixar o navegador escolher o "hoje" deixaria
o mesmo título em baldes diferentes para duas pessoas olhando a mesma tela.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class AgingBucket(StrEnum):
    """Balde de aging — enum FECHADO, usado como filtro de query e como chave
    dos agregados.

    `a_vencer` existe como valor de propósito: sem ele, "não vencido" seria a
    ausência de balde, e um filtro por balde não teria como pedir os títulos que
    ainda vão vencer. Ele **não** é um dos quatro baldes de vencidos — ver
    `OVERDUE_BUCKETS`.
    """

    A_VENCER = "a_vencer"
    D1_30 = "1_30"
    D31_60 = "31_60"
    D61_90 = "61_90"
    D90_MAIS = "90_mais"


#: Os QUATRO baldes de vencidos, na ordem em que a tela e o relatório os exibem.
#: São estes — e só estes — que somam o total vencido.
OVERDUE_BUCKETS: Final[tuple[AgingBucket, ...]] = (
    AgingBucket.D1_30,
    AgingBucket.D31_60,
    AgingBucket.D61_90,
    AgingBucket.D90_MAIS,
)

#: Limites de cada balde em DIAS de atraso, **inclusivos nos dois lados**.
#: `None` no limite superior = sem teto (`90+`). `a_vencer` não aparece aqui: ele
#: é o complemento (`dias <= 0`), e listá-lo como um intervalo o faria parecer
#: um quinto balde de vencidos na hora de somar.
AGING_BUCKET_BOUNDS: Final[dict[AgingBucket, tuple[int, int | None]]] = {
    AgingBucket.D1_30: (1, 30),
    AgingBucket.D31_60: (31, 60),
    AgingBucket.D61_90: (61, 90),
    AgingBucket.D90_MAIS: (91, None),
}


def bucket_for_days(days_overdue: int) -> AgingBucket:
    """O balde de um atraso, em Python — a MESMA partição que o SQL aplica.

    Existe porque a resposta de cada LINHA carrega o balde dela (a tela agrupa e
    filtra por ele), e a agregação por balde é SQL. Se as duas fossem escritas
    separadamente, uma linha poderia aparecer num balde cuja soma não a inclui —
    e a tela pareceria certa. Aqui e no `_bucket_predicates` do repositório os
    limites saem do MESMO `AGING_BUCKET_BOUNDS`.

    `days_overdue <= 0` é `a_vencer`: vencer HOJE ainda não é atraso.
    """
    if days_overdue <= 0:
        return AgingBucket.A_VENCER
    for bucket in OVERDUE_BUCKETS:
        low, high = AGING_BUCKET_BOUNDS[bucket]
        if days_overdue >= low and (high is None or days_overdue <= high):
            return bucket
    # Inalcançável: `90_mais` não tem teto, então a varredura sempre casa. O
    # `raise` existe para o mypy e para o dia em que alguém puser um teto nele.
    raise AssertionError(f"atraso de {days_overdue} dias sem balde")  # pragma: no cover
