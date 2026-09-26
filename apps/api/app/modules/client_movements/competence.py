"""Competência: o MÊS como unidade — um lugar só decide o que ela é (Sprint 12).

A competência é guardada como o dia 1 do mês (`DATE`), e o período que ela cobre é
do primeiro ao último dia **daquele mês**. Não é a janela expandida de ±3 dias da
§5.3: aquela é do matcher, que precisa enxergar lançamento de fronteira para casar
com o extrato. Aqui a pergunta é "o que aconteceu em junho", e um movimento de 1º de
julho não é de junho.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta, timezone

#: A competência na BORDA (URL, corpo, evento): `YYYY-MM`. Fonte ÚNICA do formato
#: — a rota valida com ele (valor fora = 400 `VALIDATION_ERROR` do handler
#: global) e o sink de métrica também. Não comporta nome nem descrição.
#: Ano de 1000 a 9999: `0000` estourava `date(0, …)` (500) e `0001` a `0999` saíam
#: do `strftime` sem o zero à esquerda (`'999-06'`), recusados pelas props do evento.
COMPETENCE_PATTERN = r"^[1-9]\d{3}-(0[1-9]|1[0-2])$"


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


# UTC-3 fixo, no MESMO molde do export da conciliação (`reconciliations/export/
# service.py`): o Brasil não tem horário de verão desde 2019, e evita `zoneinfo`.
_BRT = timezone(timedelta(hours=-3))


def today_brt(now: datetime | None = None) -> date:
    """O dia de HOJE no fuso do Brasil. `now` (aware) só para teste.

    Em UTC, das 21h às 23h59 BRT do último dia do mês o servidor já está no mês
    seguinte — e a competência corrente viraria o mês seguinte antes da hora.
    """
    return (now or datetime.now(UTC)).astimezone(_BRT).date()


def current_competence(today: date | None = None) -> date:
    """A competência corrente do SERVIDOR (dia 1 do mês, fuso do Brasil).

    Lugar ÚNICO: escrita de decisão (12.4), leitura/importação (12.5) e o que mais
    precisar de "o mês de agora" reusam daqui. `today` só para teste.
    """
    return competence_of(today or today_brt())


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
