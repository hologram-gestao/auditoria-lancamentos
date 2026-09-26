"""Competência: o MÊS como unidade — um lugar só decide o que ela é (Sprint 12).

A competência é guardada como o dia 1 do mês (`DATE`), e o período que ela cobre é
do primeiro ao último dia **daquele mês**. Não é a janela expandida de ±3 dias da
§5.3: aquela é do matcher, que precisa enxergar lançamento de fronteira para casar
com o extrato. Aqui a pergunta é "o que aconteceu em junho", e um movimento de 1º de
julho não é de junho.
"""

from __future__ import annotations

import calendar
from datetime import date

#: A competência na BORDA (URL, corpo, evento): `YYYY-MM`. Fonte ÚNICA do formato
#: — a rota valida com ele (valor fora = 400 `VALIDATION_ERROR` do handler
#: global) e o sink de métrica também. Não comporta nome nem descrição.
COMPETENCE_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def parse_competence(value: str) -> date:
    """`'2026-06'` → `date(2026, 6, 1)`. O formato já foi validado na borda."""
    year, month = value.split("-")
    return date(int(year), int(month), 1)


def format_competence(competence: date) -> str:
    """`date(2026, 6, 1)` → `'2026-06'` — o inverso exato de `parse_competence`."""
    return competence.strftime("%Y-%m")


def competence_of(day: date) -> date:
    """A competência (dia 1 do mês) de uma data."""
    return day.replace(day=1)


def competence_bounds(competence: date) -> tuple[date, date]:
    """`(primeiro dia, último dia)` do mês da competência, inclusivos.

    Levanta `ValueError` se `competence` não for o dia 1: competência mal formada é
    erro de programação (a borda HTTP valida o `YYYY-MM` antes), e aceitá-la em
    silêncio produziria um período que começa no meio do mês.
    """
    if competence.day != 1:
        msg = f"competência precisa ser o dia 1 do mês, veio {competence.isoformat()}"
        raise ValueError(msg)
    last_day = calendar.monthrange(competence.year, competence.month)[1]
    return competence, competence.replace(day=last_day)
