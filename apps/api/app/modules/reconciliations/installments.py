"""Parcela de compra parcelada no cartão: o par `N/M` de uma linha (86e3qhhtb).

A extração passou a devolver `installment_number` e `installment_total` (regra 9
do prompt), gravados em `reconciliation_file_entries`. Linha anterior a isso, ou
que o modelo deixou sem o par, fica com as duas colunas nulas; para ela a parcela
é lida da DESCRIÇÃO decifrada, em memória, na hora de ler (nunca um backfill
cifrado em massa). Esta é a regra ÚNICA dessa leitura: revisão, provisão e o
aviso de parcela que não veio passam por `entry_installment`.

O fallback é conservador de propósito. Ele só aceita o par com marcador
(`PARC 07/10`, `Parcela 7 de 10`) ou no FIM da descrição (`LOJA X 07/10`), e só
com `1 <= N <= M` e `2 <= M <= 48`. Uma data `05/09` no fim da descrição ainda
passaria: o efeito é a gaveta OFERECER as parcelas restantes (desligado por
padrão, com a lista de vencimentos para conferir), nunca criar título sozinha.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

#: Teto das colunas (`SmallInteger` com CHECK): parcela 99 não existe no cartão.
INSTALLMENT_MAX = 99
#: Teto do FALLBACK pela descrição: o maior parcelamento de cartão que se vê no
#: Brasil é 24x, com folga. Acima disso, `NN/MM` no texto é mais provável data.
_FALLBACK_TOTAL_MAX = 48

_MARKED = re.compile(r"\bPARC(?:ELA)?\.?\s*(\d{1,2})\s*(?:/|DE)\s*(\d{1,2})\b", re.IGNORECASE)
_TRAILING = re.compile(r"(?:^|\s)(\d{1,2})\s*/\s*(\d{1,2})\s*$")


@dataclass(frozen=True, slots=True)
class Installment:
    """Parcela `number` de `total` (`1 <= number <= total`, `total >= 2`)."""

    number: int
    total: int

    @property
    def has_future(self) -> bool:
        """Há parcelas DEPOIS desta (`N < M`)."""
        return self.number < self.total


class _HasInstallmentColumns(Protocol):
    installment_number: int | None
    installment_total: int | None


def valid_pair(number: int | None, total: int | None) -> Installment | None:
    """O par como `Installment`, ou `None` se faltar um lado ou ele não fizer sentido."""
    if number is None or total is None:
        return None
    if not (1 <= number <= total and 2 <= total <= INSTALLMENT_MAX):
        return None
    return Installment(number=number, total=total)


def parse_installment(description: str | None) -> Installment | None:
    """O `N/M` escrito na descrição, ou `None`. Nunca loga a descrição (§4.5)."""
    if not description:
        return None
    match = _MARKED.search(description) or _TRAILING.search(description.strip())
    if match is None:
        return None
    pair = valid_pair(int(match.group(1)), int(match.group(2)))
    if pair is None or pair.total > _FALLBACK_TOTAL_MAX:
        return None
    return pair


def entry_installment(entry: _HasInstallmentColumns, description: str | None) -> Installment | None:
    """A parcela da linha: as colunas da extração e, sem elas, a descrição decifrada."""
    from_columns = valid_pair(entry.installment_number, entry.installment_total)
    if from_columns is not None:
        return from_columns
    if entry.installment_number is not None or entry.installment_total is not None:
        # Colunas preenchidas e incoerentes não acontecem (CHECK no banco); se um
        # dia acontecer, não adivinhar pela descrição.
        return None
    return parse_installment(description)
